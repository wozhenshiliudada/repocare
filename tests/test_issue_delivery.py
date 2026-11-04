"""Real repair/sealing/Git/SQLite; constructed claim and simulated GitHub, zero fees."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.github_pr import GitHubPullRequests
from repo_maintainer.integration import prepare_integration, integrate, verified_publication
from repo_maintainer.issue_delivery import IssueDelivery
from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.issue_intake import IssueReader, specification, fingerprint
from repo_maintainer.maintenance_rules import maintenance_rules
from repo_maintainer.models import SafetyError, Policy
from repo_maintainer.provider import MockProvider
from repo_maintainer.ui_registry import UIRegistry
from repo_maintainer.workflow import repair, cleanup_run
from test_issue_intake import FakeGitHub, SLUG, BASE


class FakePulls:
    def __init__(self, github, head, commit):
        self.github, self.head, self.commit = github, head, commit
        self.rows, self.writes = [], 0
        self.before_post = None
        self.mode = 'normal'
        self.default = 'main'

    def api(self, path):
        result = self.github.api(path)
        return {**result, 'default_branch': self.default} if path == BASE else result

    def branch_head(self, url, branch):
        if branch == 'main':
            return self.github.head
        if branch == self.head and self.commit:
            return self.commit
        raise SafetyError('Missing remote branch')

    def pulls_for_head(self, slug, head):
        assert slug == SLUG and head == self.head
        return copy.deepcopy(self.rows)

    def create_draft(self, slug, payload):
        if self.before_post:
            self.before_post()
        self.writes += 1
        if self.mode == 'no_write_timeout':
            raise TimeoutError('unknown')
        value = {'id': 901, 'number': 9, 'html_url': 'https://github.com/'+slug+'/pull/9',
                 'state': 'open', 'merged_at': None, **copy.deepcopy(payload),
                 'head': {'ref': payload['head'], 'sha': self.commit, 'repo': {'id': 123, 'full_name': slug}},
                 'base': {'ref': payload['base'], 'sha': self.github.head, 'repo': {'id': 123, 'full_name': slug}}}
        self.rows.append(value)
        if self.mode == 'write_timeout':
            raise TimeoutError('unknown')
        if self.mode == 'issue_changes':
            self.github.issue['updated_at'] = '2026-10-21T03:00:00Z'
        if self.mode == 'malformed':
            return {'unexpected': True}
        return copy.deepcopy(value)


class IssueDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = RepositoryGateway(create_fixture(self.root/'source'))
        self.repo.command('remote', 'add', 'origin', 'https://github.com/'+SLUG+'.git')
        self.before = self.repo.snapshot()
        self.github = FakeGitHub(self.repo.head)
        self.spec = specification('https://github.com/'+SLUG, 'main', ['.'], ['maintainer'])
        self.rules = maintenance_rules(self.repo, read_roots=['.'], write_roots=['.'], check_ids=['fixture'])
        self.registry = UIRegistry(self.root/'state', {'repository': str(self.repo.root),
                                                     'provider': {'request_budget': 50}})
        self.addCleanup(self.registry.close)
        self.snapshot = IssueReader(self.github, self.spec).snapshot(7)
        self.identity, task = 'a'*32, 'b'*32
        binding = {'execution_id': self.identity, 'task_id': task, 'issue_number': 7,
                   'issue_token': self.snapshot['token'], 'rules_sha256': self.rules['rules_sha256'],
                   'policy_binding_sha256': 'c'*64}
        self.record = repair(self.repo.root, 'addition', MockProvider(), Policy(('calculator.py',), (DEMO_TEST,)),
                             'auto', self.registry.root.parent, issue_binding=binding)
        self.assertEqual(self.record['status'], 'candidate_ready', self.record.get('error'))
        self.run = self.registry.root.parent/self.record['run_id']
        self.addCleanup(cleanup_run, self.run)
        review = prepare_integration(self.run)
        self.assertEqual(integrate(self.run, review['review_sha256'])['status'], 'published')
        self.head, self.commit = review['review']['target_branch'], review['review']['commit']
        self.execution = IssueExecution.__new__(IssueExecution)
        self.execution.registry, self.execution.connection = self.registry, self.github
        self.execution.source, self.execution.rules = self.repo, self.rules
        self.execution.spec, self.execution.runs = self.spec, self.registry.root.parent
        row = {'id': self.identity, 'revision': 0, 'status': 'awaiting-review', 'issue_key': 'fixture',
               'investigation_id': 'diagnosis', 'repository_id': 123, 'issue_number': 7,
               'run_id': self.record['run_id'], 'task_id': task, 'issue_token': self.snapshot['token'],
               'spec_sha256': fingerprint(self.spec), 'rules_sha256': self.rules['rules_sha256'],
               'policy_binding': {'binding_sha256': 'c'*64}}
        with self.registry.transaction() as db:
            self.registry.put('issue_execution', self.identity, row, db)
            self.registry.put('issue_diagnosis', 'diagnosis', {'snapshot': self.snapshot}, db)
            self.registry.put('issue_head', 'fixture', {'token': self.snapshot['token']}, db)
            self.registry.put('issue_execution_head', 'fixture', {'execution_id': self.identity}, db)
        self.remote = FakePulls(self.github, self.head, self.commit)
        self.service = IssueDelivery(self.execution, self.remote)
        self.budget = self.registry.budget()

    def prepared(self):
        return self.service.prepare(self.identity)

    def create(self, prepared=None):
        row = prepared or self.prepared()
        return self.service.create(row['id'], row['challenge'], approve_sha256=row['approval_sha256'], actor='fixture')

    def preserved(self):
        self.assertEqual(self.budget, self.registry.budget())
        self.assertEqual(self.github.issue['state'], 'open')
        self.assertEqual(self.execution.view(self.identity)['status'], 'awaiting-review')
        self.assertEqual(self.before, self.repo.snapshot(exclude_branches=(self.record['branch'], self.head)))

    def tearDown(self):
        capture = os.environ.get('REPOCARE_DELIVERY_EVIDENCE')
        if capture:
            # Optional author-run evidence only, captured before owned fixtures
            # are cleaned. This does not alter assertions or production paths.
            directory = Path(capture)
            directory.mkdir(parents=True, exist_ok=True)
            selected = {'test': self.id(), 'deliveries': self.registry.load('issue_delivery'),
                        'budget_before': self.budget, 'budget_after': self.registry.budget(),
                        'issue_state': self.github.issue['state'],
                        'execution': self.execution.view(self.identity),
                        'remote_posts_observed_in_parent': self.remote.writes,
                        'simulated_remote_rows': self.remote.rows,
                        'source_before': self.before,
                        'source_after': self.repo.snapshot(exclude_branches=(self.record['branch'], self.head))}
            remote_file = self.root/'remote.json'
            if remote_file.exists():
                selected['exited_worker_remote'] = json.loads(remote_file.read_text(encoding='utf-8'))
            target = directory/(fingerprint(self.id())[:12]+'.json')
            target.write_text(json.dumps(selected, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')

    def test_exact_draft_payload_and_durable_intent_then_receipt_without_closing_issue(self):
        prepared = self.prepared()
        self.assertEqual(prepared, self.prepared())
        def check_intent():
            row = self.service.view(self.identity)
            self.assertEqual(row['status'], 'create_intent')
            self.assertEqual(row['authorization']['approval_sha256'], prepared['approval_sha256'])
        self.remote.before_post = check_intent
        result = self.create(prepared)
        self.assertEqual(result['status'], 'awaiting-review')
        self.assertEqual(result['receipt']['url'], 'https://github.com/example/project/pull/9')
        self.assertIn('Refs #7', prepared['core']['payload']['body'])
        self.assertNotIn(self.github.issue['body'], prepared['core']['payload']['body'])
        self.assertNotIn(str(self.repo.root), prepared['core']['payload']['body'])
        self.assertEqual(self.remote.writes, 1)
        with self.assertRaises(SafetyError):
            self.create(prepared)
        self.assertEqual(self.remote.writes, 1)
        self.preserved()

    def test_unknown_response_reconciles_existing_pr_without_second_write_after_restart(self):
        self.remote.mode = 'write_timeout'
        prepared = self.prepared()
        with self.assertRaises(TimeoutError):
            self.create(prepared)
        self.assertEqual(self.service.view(self.identity)['status'], 'creation_unknown')
        self.registry.close()
        self.registry = UIRegistry(self.root/'state', {'repository': str(self.repo.root), 'provider': {'request_budget': 50}})
        self.addCleanup(self.registry.close)
        self.execution.registry = self.registry
        self.service = IssueDelivery(self.execution, self.remote)
        self.assertEqual(self.service.reconcile(self.identity)['status'], 'awaiting-review')
        self.assertEqual(self.remote.writes, 1)
        self.preserved()

    def test_unknown_no_match_never_retries_post(self):
        self.remote.mode = 'no_write_timeout'
        prepared = self.prepared()
        with self.assertRaises(TimeoutError):
            self.create(prepared)
        for _ in range(2):
            with self.assertRaises(SafetyError):
                self.service.reconcile(self.identity)
        with self.assertRaises(SafetyError):
            self.create(prepared)
        self.assertEqual(self.remote.writes, 1)
        self.assertEqual(self.service.view(self.identity)['status'], 'creation_unknown')

    def test_real_creator_process_exit_retains_intent_and_restart_only_observes_remote_pr(self):
        prepared = self.prepared()
        config = {'source': str(self.repo.root), 'state': str(self.root/'state'),
                  'rules': self.rules, 'spec': self.spec, 'identity': self.identity,
                  'head': self.head, 'commit': self.commit, 'prepared': prepared}
        path = self.root/'worker.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        script = '''import json, os, sys
from pathlib import Path
from test_issue_delivery import FakeGitHub, FakePulls
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.issue_delivery import IssueDelivery
from repo_maintainer.ui_registry import UIRegistry
c=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
repo=RepositoryGateway(Path(c['source']))
registry=UIRegistry(Path(c['state']),{'repository':str(repo.root),'provider':{'request_budget':50}})
execution=IssueExecution.__new__(IssueExecution)
execution.source,execution.registry,execution.runs=repo,registry,registry.root.parent
execution.spec,execution.rules=c['spec'],c['rules']
execution.connection=FakeGitHub(repo.head)
remote=FakePulls(execution.connection,c['head'],c['commit'])
original=remote.create_draft
def crash(slug,payload):
    result=original(slug,payload)
    Path(sys.argv[2]).write_text(json.dumps({'writes':remote.writes,'rows':remote.rows}),encoding='utf-8')
    os._exit(47)
remote.create_draft=crash
service=IssueDelivery(execution,remote)
p=c['prepared']
service.create(c['identity'],p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
'''
        self.registry.close()
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent.resolve()))
        result = subprocess.run([sys.executable, '-c', script, str(path), str(self.root/'remote.json')],
                                capture_output=True, timeout=90, env=env)
        self.assertEqual(result.returncode, 47, result.stderr.decode(errors='replace'))
        self.registry = UIRegistry(self.root/'state', {'repository': str(self.repo.root), 'provider': {'request_budget': 50}})
        self.addCleanup(self.registry.close)
        self.execution.registry = self.registry
        self.service = IssueDelivery(self.execution, self.remote)
        self.assertEqual(self.service.view(self.identity)['status'], 'create_intent')
        stored = json.loads((self.root/'remote.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['writes'], 1)
        self.remote.rows = stored['rows']
        self.assertEqual(self.service.reconcile(self.identity)['status'], 'awaiting-review')
        self.assertEqual(self.remote.writes, 0)
        self.preserved()

    def test_human_closed_or_edited_issue_and_changed_branch_stop_before_post(self):
        prepared = self.prepared()
        changes = [(self.github.issue, 'state', 'closed'), (self.github.issue, 'body', 'different'),
                   (self.github.issue, 'updated_at', '2026-10-21T03:00:00Z')]
        for target, key, changed in changes:
            original = target[key]
            target[key] = changed
            with self.assertRaises(SafetyError):
                self.create(prepared)
            target[key] = original
        original = self.github.head
        self.github.head = 'd'*40
        with self.assertRaises(SafetyError):
            self.create(prepared)
        self.github.head = original
        self.remote.commit = 'e'*40
        with self.assertRaises(SafetyError):
            self.create(prepared)
        self.assertEqual(self.remote.writes, 0)

    def test_missing_remote_head_and_existing_closed_pr_are_not_pushed_or_reopened(self):
        self.remote.commit = None
        with self.assertRaises(SafetyError):
            self.prepared()
        self.remote.commit = self.commit
        self.remote.rows = [{'state': 'closed'}]
        with self.assertRaises(SafetyError):
            self.prepared()
        self.assertEqual(self.remote.writes, 0)

    def test_changed_default_scope_claim_and_source_or_candidate_block_write(self):
        prepared = self.prepared()
        self.remote.default = 'trunk'
        with self.assertRaises(SafetyError):
            self.create(prepared)
        self.remote.default = 'main'
        path = self.repo.root/'calculator.py'
        original = path.read_bytes()
        path.write_bytes(original+b'# external edit\n')
        with self.assertRaises(SafetyError):
            self.create(prepared)
        path.write_bytes(original)
        self.registry.put('issue_execution_head', 'fixture', {'execution_id': 'd'*32})
        with self.assertRaises(SafetyError):
            self.create(prepared)
        self.assertEqual(self.remote.writes, 0)

    def test_changed_issue_after_write_preserves_pr_and_requires_review_not_fresh_authorization(self):
        self.remote.mode = 'issue_changes'
        result = self.create()
        self.assertEqual(result['status'], 'remote-created-review-required')
        self.assertEqual(result['receipt']['number'], 9)
        self.assertEqual(self.remote.writes, 1)
        self.preserved()

    def test_malformed_response_is_unknown_then_read_only_reconciliation(self):
        self.remote.mode = 'malformed'
        with self.assertRaises(SafetyError):
            self.create()
        self.assertEqual(self.service.view(self.identity)['status'], 'creation_unknown')
        self.assertEqual(self.service.reconcile(self.identity)['status'], 'awaiting-review')
        self.assertEqual(self.remote.writes, 1)

    def test_ambiguous_tampered_or_other_repository_pr_remains_unknown(self):
        self.remote.mode = 'write_timeout'
        with self.assertRaises(TimeoutError):
            self.create()
        original = copy.deepcopy(self.remote.rows)
        self.remote.rows *= 2
        with self.assertRaises(SafetyError):
            self.service.reconcile(self.identity)
        self.remote.rows = copy.deepcopy(original)
        self.remote.rows[0]['head']['repo']['id'] = 999
        with self.assertRaises(SafetyError):
            self.service.reconcile(self.identity)
        self.remote.rows = copy.deepcopy(original)
        self.remote.rows[0]['body'] += '\nmanual edit'
        with self.assertRaises(SafetyError):
            self.service.reconcile(self.identity)
        self.assertEqual(self.remote.writes, 1)
        self.assertEqual(self.service.view(self.identity)['status'], 'creation_unknown')

    def test_closed_pr_reconciliation_is_record_only_never_issue_closure_or_reopen(self):
        self.remote.mode = 'write_timeout'
        with self.assertRaises(TimeoutError):
            self.create()
        self.remote.rows[0]['state'] = 'closed'
        result = self.service.reconcile(self.identity)
        self.assertEqual(result['status'], 'remote-closed-review')
        self.preserved()

    def test_two_creators_one_post_and_live_intent_cannot_reconcile(self):
        prepared = self.prepared()
        entered, release = threading.Event(), threading.Event()
        results = []
        def wait():
            entered.set()
            self.assertTrue(release.wait(30))
        self.remote.before_post = wait
        worker = threading.Thread(target=lambda: results.append(self.create(prepared)))
        worker.start()
        try:
            self.assertTrue(entered.wait(45))
            with self.assertRaises(SafetyError):
                self.create(prepared)
            with self.assertRaises(SafetyError):
                self.service.reconcile(self.identity)
        finally:
            release.set()
            worker.join(45)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]['status'], 'awaiting-review')
        self.assertEqual(self.remote.writes, 1)

    def test_wrong_approval_never_granted_by_label(self):
        prepared = self.prepared()
        with self.assertRaises(SafetyError):
            self.service.create(self.identity, prepared['challenge'], approve_sha256='f'*64, actor='fixture')
        self.assertEqual(self.remote.writes, 0)

    def test_pr_receipt_accepts_repository_letter_case_but_rejects_other_hosts_and_suffixes(self):
        prepared = self.prepared()
        value = self.remote.create_draft(SLUG, prepared['core']['payload'])
        value['html_url'] = 'https://github.com/EXAMPLE/Project/pull/9'
        value['head']['repo']['full_name'] = 'EXAMPLE/Project'
        value['base']['repo']['full_name'] = 'EXAMPLE/Project'
        self.assertEqual(self.service._receipt(value, prepared['core'])['number'], 9)
        for url in ('https://evil.example/example/project/pull/9',
                    'https://github.com/example/project/pull/9?other=1',
                    'https://github.com/example/project/pull/90'):
            value['html_url'] = url
            with self.assertRaises(SafetyError):
                self.service._receipt(value, prepared['core'])

    def test_readonly_publication_proof_keeps_all_original_evidence_bytes(self):
        before = {str(p): p.read_bytes() for p in self.run.rglob('*') if p.is_file() and '.lock' not in p.name}
        proof = verified_publication(self.run)
        self.assertEqual(proof['review']['commit'], self.commit)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.run.rglob('*') if p.is_file() and '.lock' not in p.name})
        self.preserved()


class GitHubPullTransportTests(unittest.TestCase):
    def test_transport_post_is_once_and_error_does_not_echo_credential_or_body(self):
        remote = GitHubPullRequests()
        payload = {'title': 'candidate', 'body': 'Refs #7', 'head': 'integration/a', 'base': 'main', 'draft': True}
        with patch.object(remote, 'credential', return_value='private-test-sentinel'), \
             patch('repo_maintainer.github_connection.build_opener') as opener:
            opener.return_value.open.side_effect = URLError('connection lost')
            with self.assertRaises(SafetyError) as caught:
                remote.create_draft(SLUG, payload)
            self.assertNotIn('private-test-sentinel', str(caught.exception))
            self.assertNotIn('Refs #7', str(caught.exception))
            self.assertEqual(opener.return_value.open.call_count, 1)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.github.com'+BASE+'/pulls')
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(json.loads(request.data), payload)
        for slug in ('https://evil.example/x/y', 'example/project?x=1', 'example/project/extra'):
            with self.assertRaises(SafetyError):
                remote.create_draft(slug, payload)

    def test_fixed_draft_post_and_existing_readonly_api_keeps_write_paths_closed(self):
        remote = GitHubPullRequests()
        payload = {'title': 'candidate', 'body': 'Refs #7', 'head': 'integration/'+'a'*32, 'base': 'main', 'draft': True}
        with patch.object(remote, '_request', return_value={'number': 9}) as request:
            self.assertEqual(remote.create_draft(SLUG, payload), {'number': 9})
            request.assert_called_once_with(BASE+'/pulls', payload=payload)
            for changed in ({**payload, 'draft': False}, {**payload, 'merge': True}, {**payload, 'base': payload['head']}):
                with self.assertRaises(SafetyError):
                    remote.create_draft(SLUG, changed)
            for path in (BASE+'/pulls', BASE+'/issues/7/comments', BASE+'/pulls/9/merge'):
                with self.assertRaises(SafetyError):
                    remote.api(path)

    def test_pagination_all_states_exact_encoded_head_and_incomplete_refusal(self):
        remote = GitHubPullRequests()
        with patch.object(remote, '_request', side_effect=[[{}]*30, [{'number': 9}]]) as request:
            self.assertEqual(len(remote.pulls_for_head(SLUG, 'integration/a')), 31)
            self.assertIn('state=all&head=example%3Aintegration%2Fa', request.call_args_list[0].args[0])
        with patch.object(remote, '_request', return_value=[{}]*30) as request:
            with self.assertRaises(SafetyError):
                remote.pulls_for_head(SLUG, 'integration/a')
            self.assertEqual(request.call_count, 3)


if __name__ == '__main__':
    unittest.main()
