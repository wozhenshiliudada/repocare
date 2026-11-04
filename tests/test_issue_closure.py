"""Real Git merge/squash graphs and actual post-delivery tests; GitHub is simulated."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_issue_delivery as fixtures
from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.github_acceptance import GitHubAcceptance
from repo_maintainer.issue_acceptance import acceptance_policy, ReadWindow
from repo_maintainer.issue_closure import IssueClosure
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.issue_intake import project_issue
from repo_maintainer.models import SafetyError
from repo_maintainer.runner import run_test
from repo_maintainer.ui_registry import UIRegistry
from repo_maintainer.workflow import cleanup_run

BASE = '/repos/example/project'
ACTOR = {'id': 42, 'login': 'maintainer'}


class AcceptanceRemote:
    def __init__(self, case):
        self.case = case
        self.issue = case.github.issue
        self.issue.update(state_reason=None, closed_at=None, closed_by=None)
        self.pr = copy.deepcopy(case.remote.rows[0])
        self.pr.update(merged=False, merge_commit_sha=case.commit)
        self.default, self.account, self.calls = 'main', dict(ACTOR), []
        self.runs, self.jobs, self.writes, self.mode = [], {}, 0, 'normal'
        self.before_write, self.after_write = None, None
        self.actual_checks = []

    def merge(self, *, squash=False, ahead=True):
        source = self.case.repo
        tree = source.command('rev-parse', self.case.commit+'^{tree}').decode().strip()
        parents = ['-p', source.head]+([] if squash else ['-p', self.case.commit])
        def commit(message, parent_args):
            return source.command('-c', 'user.name=Acceptance Fixture', '-c', 'user.email=fixture@example.invalid',
                                  'commit-tree', tree, *parent_args, input_data=(message+'\n').encode()).decode().strip()
        merged = commit('squashed delivery' if squash else 'merged delivery', parents)
        head = commit('following default branch commit', ['-p', merged]) if ahead else merged
        self.case.github.head = head
        self.issue['updated_at'] = '2026-10-21T04:00:00Z'
        self.pr.update(state='closed', merged=True, draft=False, merge_commit_sha=merged,
                       merged_at='2026-10-21T04:00:00Z')
        self.pr['base']['sha'] = head
        directory = self.case.root/('accepted-'+str(len(self.actual_checks)))
        source.command('worktree', 'add', '--detach', str(directory), head)
        self.case.addCleanup(source.command, 'worktree', 'remove', '--force', str(directory))
        actual = run_test(DEMO_TEST, directory, 30)
        self.actual_checks.append(actual)
        if actual['returncode'] != 0:
            raise AssertionError(actual)
        counts = re.findall(r'\bRan (\d+) tests?\b', actual['stdout']+actual['stderr'])
        if not counts or sum(map(int, counts)) <= 0:
            raise AssertionError('No effective post-delivery fixture tests')
        self.runs = [{'id': 501, 'workflow_id': 1001, 'run_number': 8, 'run_attempt': 1, 'event': 'push',
                      'head_sha': head, 'head_branch': 'main', 'repository': {'id': 123},
                      'status': 'completed', 'conclusion': 'success', 'updated_at': '2026-10-21T04:05:00Z'}]
        self.jobs = {501: [{'id': 601, 'run_id': 501, 'run_attempt': 1, 'head_sha': head, 'name': 'unit tests',
                           'status': 'completed', 'conclusion': 'success', 'completed_at': '2026-10-21T04:05:00Z'}]}

    def read(self, path):
        self.calls.append(path)
        if path == '/user':
            return copy.deepcopy(self.account)
        if path == BASE:
            return {'id': 123, 'full_name': 'example/project', 'default_branch': self.default}
        if path == BASE+'/pulls/9':
            return copy.deepcopy(self.pr)
        if path.startswith(BASE+'/pulls?'):
            return [copy.deepcopy(self.pr)]
        if path.startswith(BASE+'/compare/'):
            pair = path.split('/compare/')[1].split('?')[0]
            ancestor, head = pair.split('...')
            actual_base = self.case.repo.command('merge-base', ancestor, head, check=False).decode().strip()
            status = 'identical' if ancestor == head else 'ahead' if actual_base == ancestor else 'diverged'
            return {'url': 'https://api.github.com'+path.split('?')[0], 'base_commit': {'sha': ancestor},
                    'merge_base_commit': {'sha': actual_base}, 'behind_by': 0 if status != 'diverged' else 1,
                    'status': status, 'ahead_by': 0 if status == 'identical' else 1}
        if path.startswith(BASE+'/actions/runs?'):
            page = int(path[-1])
            return {'total_count': len(self.runs), 'workflow_runs': copy.deepcopy(self.runs[(page-1)*30:page*30])}
        if '/attempts/' in path:
            identity = int(path.split('/runs/')[1].split('/')[0])
            values = self.jobs.get(identity, [])
            page = int(path[-1])
            return {'total_count': len(values), 'jobs': copy.deepcopy(values[(page-1)*30:page*30])}
        return self.case.github.api(path)

    def close_completed(self, repository, number, expected_actor, expected_issue):
        assert repository == 'example/project' and number == 7 and expected_actor == self.account
        assert project_issue(self.issue, repository, number) == expected_issue
        if self.before_write:
            self.before_write()
        self.writes += 1
        if self.mode == 'no_write_timeout':
            raise TimeoutError('fixture unknown without a write')
        self.issue.update(state='closed', state_reason='completed', closed_at='2026-10-21T04:10:00Z',
                          updated_at='2026-10-21T04:10:00Z', closed_by=dict(self.account))
        self.case.github.events.append({'id': 101, 'event': 'closed', 'actor': dict(self.account),
                                       'created_at': self.issue['closed_at']})
        if self.mode == 'write_timeout':
            raise TimeoutError('fixture wrote, response unknown')
        if self.mode == 'not_planned':
            self.issue['state_reason'] = 'not_planned'
        if self.after_write:
            self.after_write()
        return copy.deepcopy(self.issue)


class IssueClosureTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.IssueDeliveryTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        proposal = self.case.prepared()
        self.case.create(proposal)
        self.remote = AcceptanceRemote(self.case)
        self.remote.merge()
        self.policy = acceptance_policy([1001])
        self.service = IssueClosure(self.case.service, self.remote, self.policy)

    def prepared(self):
        return self.service.prepare(self.case.identity)

    def close(self, proposal=None):
        proposal = proposal or self.prepared()
        return self.service.close(proposal['id'], proposal['challenge'],
                                  approve_sha256=proposal['approval_sha256'], actor='fixture')

    def test_real_merged_default_graph_actual_tests_new_approval_and_original_claim_delivery(self):
        # GitHub's documented attempt-jobs response need not repeat run_attempt;
        # the fixed endpoint already selects that exact attempt.
        self.remote.jobs[501][0].pop('run_attempt')
        proposal = self.prepared()
        self.assertEqual(proposal, self.prepared())
        self.assertEqual(self.remote.writes, 0)
        def intent():
            row = self.service.view(self.case.identity)
            self.assertEqual(row['status'], 'close_intent')
            self.assertEqual(row['authorization']['approval_sha256'], proposal['approval_sha256'])
        self.remote.before_write = intent
        value = self.close(proposal)
        self.assertEqual(value['status'], 'closed-verified', value)
        self.assertEqual(self.remote.writes, 1)
        self.assertEqual(self.case.execution.view(self.case.identity)['status'], 'delivered')
        self.assertEqual(self.case.registry.budget(), self.case.budget)
        self.assertTrue(self.remote.actual_checks and all(r['returncode'] == 0 for r in self.remote.actual_checks))
        with self.assertRaises(SafetyError):
            self.close(proposal)
        self.assertEqual(self.remote.writes, 1)

    def test_squashed_delivery_uses_actual_merge_commit_not_candidate_head_ancestry(self):
        self.remote.merge(squash=True)
        common = self.case.repo.command('merge-base', self.case.commit, self.case.github.head).decode().strip()
        self.assertNotEqual(common, self.case.commit)
        self.assertEqual(self.close()['status'], 'closed-verified')

    def test_post_merge_acceptance_does_not_require_live_candidate_workspace_or_unchanged_local_checkout(self):
        cleanup_run(self.case.run)
        path = self.case.repo.root/'calculator.py'
        original = path.read_bytes()
        path.write_bytes(original+b'# user local work\n')
        try:
            self.assertEqual(self.close()['status'], 'closed-verified')
            self.assertTrue(path.read_bytes().endswith(b'# user local work\n'))
        finally:
            path.write_bytes(original)

    def test_unmerged_test_merge_sha_closed_pr_and_nondefault_target_cannot_close_issue(self):
        for field, value in (('merged', False), ('state', 'open'), ('draft', True)):
            old = self.remote.pr[field]
            self.remote.pr[field] = value
            with self.assertRaises(SafetyError):
                self.prepared()
            self.remote.pr[field] = old
        self.remote.default = 'release'
        with self.assertRaises(SafetyError):
            self.prepared()
        self.assertEqual(self.remote.writes, 0)

    def test_changed_issue_cancel_labels_relabel_reopen_and_manual_close_are_not_replayed(self):
        self.remote.issue['body'] += '\nnew requirement'
        with self.assertRaises(SafetyError):
            self.prepared()
        self.remote.issue['body'] = self.case.snapshot['issue']['body']
        self.remote.issue['labels'].append({'name': 'duplicate'})
        with self.assertRaises(SafetyError):
            self.prepared()
        self.remote.issue['labels'].pop()
        self.case.github.events.append({'id': 33, 'event': 'reopened', 'created_at': '2026-10-21T03:00:00Z'})
        with self.assertRaises(SafetyError):
            self.prepared()
        self.case.github.events.pop()
        self.remote.issue.update(state='closed', state_reason='not_planned', closed_at='2026-10-21T03:00:00Z')
        with self.assertRaises(SafetyError):
            self.prepared()
        self.assertEqual(self.remote.writes, 0)

    def test_latest_pending_or_failed_ci_cannot_hide_behind_old_green_run(self):
        older = copy.deepcopy(self.remote.runs[0])
        older['id'], older['run_number'] = 500, 7
        self.remote.runs.append(older)
        self.remote.runs[0].update(status='in_progress', conclusion=None)
        with self.assertRaises(SafetyError):
            self.prepared()
        self.remote.runs[0].update(status='completed', conclusion='failure')
        with self.assertRaises(SafetyError):
            self.prepared()
        self.assertEqual(self.remote.writes, 0)

    def test_missing_skipped_other_sha_and_incomplete_ci_jobs_refuse_closing(self):
        job = self.remote.jobs[501][0]
        for field, value in (('conclusion', 'skipped'), ('head_sha', 'a'*40), ('run_attempt', 2)):
            old = job[field]
            job[field] = value
            with self.assertRaises(SafetyError):
                self.prepared()
            job[field] = old
        self.remote.jobs[501] = []
        with self.assertRaises(SafetyError):
            self.prepared()
        self.remote.runs = []
        with self.assertRaises(SafetyError):
            self.prepared()
        self.assertEqual(self.remote.writes, 0)

    def test_prepared_evidence_ci_account_and_issue_drift_block_patch(self):
        proposal = self.prepared()
        for target, key, value in ((self.remote.issue, 'updated_at', '2026-10-21T04:06:00Z'),
                                   (self.remote.account, 'id', 99), (self.remote.runs[0], 'run_attempt', 2)):
            old = target[key]
            target[key] = value
            with self.assertRaises(SafetyError):
                self.close(proposal)
            target[key] = old
        self.assertEqual(self.remote.writes, 0)

    def test_unknown_response_registry_restart_observes_close_once_and_preserves_used_unknown_budget(self):
        used, _ = self.case.registry.reserve(3)
        self.case.registry.settle(used, {'requests': 3}, success=True)
        unknown, _ = self.case.registry.reserve(2)
        self.case.registry.settle(unknown, {'requests': None}, success=False)
        before = self.case.registry.budget()
        self.remote.mode = 'write_timeout'
        with self.assertRaises(TimeoutError):
            self.close()
        self.assertEqual(self.service.view(self.case.identity)['status'], 'close_unknown')
        self.case.registry.close()
        self.case.registry = UIRegistry(self.case.root/'state', {'repository': str(self.case.repo.root), 'provider': {'request_budget': 50}})
        self.addCleanup(self.case.registry.close)
        self.case.execution.registry = self.case.registry
        self.case.service.registry = self.case.registry
        self.service = IssueClosure(self.case.service, self.remote, self.policy)
        self.assertEqual(self.service.reconcile(self.case.identity)['status'], 'closed-verified')
        self.assertEqual(self.remote.writes, 1)
        self.assertEqual(self.case.registry.budget(), before)

    def test_absent_unknown_close_remains_held_without_repeating_or_reopening(self):
        proposal = self.prepared()
        self.remote.mode = 'no_write_timeout'
        with self.assertRaises(TimeoutError):
            self.close(proposal)
        for _ in range(2):
            with self.assertRaises(SafetyError):
                self.service.reconcile(self.case.identity)
        with self.assertRaises(SafetyError):
            self.close(proposal)
        self.assertEqual(self.remote.writes, 1)
        self.assertEqual(self.remote.issue['state'], 'open')

    def test_write_race_is_detected_and_not_falsely_reported_as_verified_completion(self):
        self.remote.after_write = lambda: self.remote.issue.update(body='human changed goal during PATCH')
        value = self.close()
        self.assertEqual(value['status'], 'closed-review-required')
        self.assertEqual(self.case.execution.view(self.case.identity)['status'], 'awaiting-review')
        self.assertEqual(self.remote.writes, 1)

    def test_wrong_close_reason_is_not_success_and_never_automatically_reopened(self):
        self.remote.mode = 'not_planned'
        value = self.close()
        self.assertEqual(value['status'], 'closed-review-required')
        self.assertEqual(self.remote.issue['state_reason'], 'not_planned')
        self.assertEqual(self.remote.writes, 1)

    def test_unknown_pr_intent_can_be_independently_accepted_after_verified_merge_without_replaying_pr(self):
        with self.case.registry.transaction() as db:
            draft = self.case.service._get(self.case.identity, db)
            draft.pop('receipt')
            draft.update(status='creation_unknown', owner_process=None, revision=draft['revision']+1)
            self.case.registry.put('issue_delivery', self.case.identity, draft, db)
        body = self.remote.pr['body']
        self.remote.pr['body'] = 'copied head without its original intent marker'
        with self.assertRaises(SafetyError):
            self.prepared()
        self.remote.pr['body'] = body
        value = self.close()
        self.assertEqual(value['status'], 'closed-verified', value)
        self.assertTrue(value['core']['proof']['pr']['from_unknown_intent'])
        self.assertEqual(self.case.service.view(self.case.identity)['status'], 'creation_unknown')
        self.assertEqual(self.case.remote.writes, 1, 'Original simulated PR write must not be replayed')

    def test_two_closers_one_patch_and_live_owner_cannot_reconcile(self):
        proposal = self.prepared()
        entered, release, result = threading.Event(), threading.Event(), []
        def wait():
            entered.set()
            self.assertTrue(release.wait(45))
        self.remote.before_write = wait
        worker = threading.Thread(target=lambda: result.append(self.close(proposal)))
        worker.start()
        try:
            self.assertTrue(entered.wait(50))
            with self.assertRaises(SafetyError):
                self.close(proposal)
            with self.assertRaises(SafetyError):
                self.service.reconcile(self.case.identity)
        finally:
            release.set()
            worker.join(60)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]['status'], 'closed-verified')
        self.assertEqual(self.remote.writes, 1)

    def test_default_branch_must_contain_actual_merge_and_pr_head_must_remain_the_verified_candidate(self):
        old = self.remote.pr['head']['sha']
        self.remote.pr['head']['sha'] = self.case.repo.head
        with self.assertRaisesRegex(SafetyError, 'PR head was changed'):
            self.prepared()
        self.remote.pr['head']['sha'] = old
        tree = self.case.repo.command('rev-parse', self.case.commit+'^{tree}').decode().strip()
        unrelated = self.case.repo.command('-c', 'user.name=Acceptance Fixture', '-c', 'user.email=fixture@example.invalid',
                                           'commit-tree', tree, input_data=b'unrelated root\n').decode().strip()
        self.case.github.head = unrelated
        with self.assertRaisesRegex(SafetyError, 'provably contain'):
            self.prepared()
        self.assertEqual(self.remote.writes, 0)

    def test_progress_stage_requires_trusted_readiness_removal_event(self):
        self.remote.issue['labels'] = [{'name': 'bug'}, {'name': 'awaiting-review'}]
        with self.assertRaises(SafetyError):
            self.prepared()
        event = self.case.github.event(2, 'unlabeled', 'outsider')
        event['created_at'] = '2026-10-21T04:01:00Z'
        self.case.github.events.append(event)
        with self.assertRaises(SafetyError):
            self.prepared()
        event['actor']['login'] = 'maintainer'
        self.assertEqual(self.close()['status'], 'closed-verified')

    def test_exact_latest_run_attempt_is_queried_and_old_job_attempt_is_not_reused(self):
        self.remote.runs[0]['run_attempt'] = 2
        with self.assertRaises(SafetyError):
            self.prepared()
        self.remote.jobs[501][0]['run_attempt'] = 2
        self.assertEqual(self.close()['status'], 'closed-verified')
        self.assertTrue(any('/runs/501/attempts/2/jobs?' in path for path in self.remote.calls))

    def crash(self, phase):
        proposal = self.prepared()
        path = self.case.root/'worker.json'
        config = {'state': str(self.case.root/'state'), 'source': str(self.case.repo.root),
                  'spec': self.case.spec, 'policy': self.policy, 'identity': self.case.identity,
                  'issue': self.remote.issue, 'events': self.case.github.events,
                  'head': self.case.head, 'commit': self.case.commit, 'default_head': self.case.github.head,
                  'pr': self.remote.pr, 'runs': self.remote.runs, 'jobs': self.remote.jobs,
                  'phase': phase, 'proposal': proposal}
        path.write_text(json.dumps(config), encoding='utf-8')
        self.case.registry.close()
        completed = subprocess.run([sys.executable, str(Path(__file__).with_name('issue_closure_crash_fixture.py')),
                                     str(path), str(self.case.root/'remote-close.json')], capture_output=True, timeout=120)
        self.assertEqual(completed.returncode, 50 if phase == 'remote' else 51, completed.stderr.decode(errors='replace'))
        observed = json.loads((self.case.root/'remote-close.json').read_text(encoding='utf-8'))
        self.remote.issue.clear()
        self.remote.issue.update(observed['issue'])
        self.case.github.events = observed['events']
        self.case.registry = UIRegistry(self.case.root/'state', {'repository': str(self.case.repo.root), 'provider': {'request_budget': 50}})
        self.addCleanup(self.case.registry.close)
        self.case.execution.registry = self.case.registry
        self.case.service.registry = self.case.registry
        self.service = IssueClosure(self.case.service, self.remote, self.policy)
        self.worker_exit = {'returncode': completed.returncode, 'worker_patch_calls': observed['writes'], 'phase': phase}

    def test_real_owner_exit_after_remote_close_reconciles_without_second_patch(self):
        self.crash('remote')
        self.assertEqual(self.service.view(self.case.identity)['status'], 'close_intent')
        self.assertEqual(self.service.reconcile(self.case.identity)['status'], 'closed-verified')
        self.assertEqual(self.remote.writes, 0)
        self.assertEqual(self.case.execution.view(self.case.identity)['status'], 'delivered')

    def test_real_exit_after_receipt_keeps_closed_state_and_reverse_claim_update_atomic(self):
        self.crash('receipt')
        self.assertEqual(self.service.view(self.case.identity)['status'], 'closed-verified')
        self.assertEqual(self.case.execution.view(self.case.identity)['status'], 'delivered')
        with self.assertRaises(SafetyError):
            self.service.reconcile(self.case.identity)
        self.assertEqual(self.remote.writes, 0)

    def tearDown(self):
        capture = os.environ.get('REPOCARE_CLOSURE_EVIDENCE')
        if capture:
            target = Path(capture)
            target.mkdir(parents=True, exist_ok=True)
            value = {'test': self.id(), 'closures': self.case.registry.load('issue_closure'),
                     'execution': self.case.execution.view(self.case.identity),
                     'budget': self.case.registry.budget(), 'remote_issue': self.remote.issue,
                     'remote_pr': self.remote.pr, 'workflow_runs': self.remote.runs, 'jobs': self.remote.jobs,
                     'actual_checks': self.remote.actual_checks, 'patch_calls': self.remote.writes}
            if hasattr(self, 'worker_exit'):
                value['worker_exit'] = self.worker_exit
            (target/(fingerprint(self.id())[:12]+'.json')).write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


class AcceptanceTransportTests(unittest.TestCase):
    def test_actual_http_requests_keep_one_credential_and_send_only_fixed_completed_patch(self):
        remote = GitHubAcceptance()
        raw = fixtures.FakeGitHub('a'*40).issue
        expected = project_issue(raw, 'example/project', 7)
        closed = {**raw, 'state': 'closed', 'state_reason': 'completed'}
        with patch.object(remote, 'credential', return_value='synthetic-token') as credential, \
             patch('repo_maintainer.github_connection.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.side_effect = [json.dumps(value).encode() for value in (ACTOR, raw, closed)]
            self.assertEqual(remote.close_completed('example/project', 7, ACTOR, expected), closed)
            requests = [call.args[0] for call in opener.return_value.open.call_args_list]
            self.assertEqual([request.get_method() for request in requests], ['GET', 'GET', 'PATCH'])
            self.assertTrue(all(request.full_url.startswith('https://api.github.com/') for request in requests))
            self.assertEqual(json.loads(requests[-1].data), {'state': 'closed', 'state_reason': 'completed'})
            self.assertEqual(credential.call_count, 1)

    def test_acceptance_read_and_pagination_bounds_prevent_partial_proof(self):
        class Remote:
            def read(self, path):
                return {'total_count': 91, 'jobs': []}
        reader = ReadWindow(Remote())
        with self.assertRaises(SafetyError):
            reader.pages('/jobs?page=', 'jobs')
        reader.calls = 96
        with self.assertRaises(SafetyError):
            reader.api('/user')

    def test_bound_account_fixed_patch_no_merge_reopen_or_old_api_widening(self):
        remote = GitHubAcceptance()
        raw = fixtures.FakeGitHub('a'*40).issue
        expected_issue = project_issue(raw, 'example/project', 7)
        with patch.object(remote, 'credential', return_value='synthetic-token'), \
             patch.object(remote, '_request', side_effect=[dict(ACTOR), raw, {'state': 'closed'}]) as request:
            self.assertEqual(remote.close_completed('example/project', 7, ACTOR, expected_issue), {'state': 'closed'})
            self.assertEqual(request.call_args.kwargs, {'method': 'PATCH', '_token': 'synthetic-token',
                                                       'payload': {'state': 'closed', 'state_reason': 'completed'}})
        with patch.object(remote, 'credential', return_value='synthetic-token'), \
             patch.object(remote, '_request', return_value={'id': 99, 'login': 'other'}) as request:
            with self.assertRaises(SafetyError):
                remote.close_completed('example/project', 7, ACTOR, expected_issue)
            self.assertEqual(request.call_count, 1)
        with patch.object(remote, 'credential', return_value='synthetic-token'), \
             patch.object(remote, '_request', side_effect=[dict(ACTOR), {**raw, 'body': 'changed'}]) as request:
            with self.assertRaises(SafetyError):
                remote.close_completed('example/project', 7, ACTOR, expected_issue)
            self.assertEqual(request.call_count, 2, 'Changed last read must prevent PATCH')
        for path in (BASE+'/pulls/9/merge', BASE+'/actions/runs/501/rerun', BASE+'/issues/7/comments'):
            with self.assertRaises(SafetyError):
                remote.read(path)
        with self.assertRaises(SafetyError):
            remote.api(BASE+'/pulls/9')

    def test_new_read_paths_are_fixed_and_policy_cannot_be_empty_or_label_provided(self):
        remote = GitHubAcceptance()
        sha = 'a'*40
        for path in (BASE+'/pulls/9', BASE+'/compare/'+sha+'...'+sha+'?per_page=1',
                     BASE+'/actions/runs?branch=main&head_sha='+sha+'&per_page=30&page=1',
                     BASE+'/actions/runs/501/attempts/2/jobs?per_page=30&page=1'):
            with patch.object(remote, '_request', return_value={}) as call:
                self.assertEqual(remote.read(path), {})
                call.assert_called_once_with(path)
        for ids in ([], ['ready-for-agent'], [True], [1, 1]):
            with self.assertRaises(SafetyError):
                acceptance_policy(ids)


if __name__ == '__main__':
    unittest.main()
