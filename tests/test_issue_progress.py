import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from repo_maintainer.github_issue_comments import GitHubIssueComments
from repo_maintainer.issue_progress import CommentPermissionDenied, IssueProgress
from repo_maintainer.models import SafetyError
from repo_maintainer.ui_registry import UIRegistry
from repo_maintainer.issue_intake import fingerprint


class Remote:
    def __init__(self):
        self.rows, self.posts = [], 0
        self.permission, self.actor, self.mode = True, 42, 'normal'
        self.issue_state = 'open'

    def preflight(self, repo, number, actor, *, expected_issue_state='open'):
        if not self.permission:
            raise CommentPermissionDenied('permission denied')
        if actor != self.actor:
            raise SafetyError('permission/account unavailable')
        if expected_issue_state != self.issue_state:
            raise SafetyError('Issue state changed')

    def create_comment(self, repo, number, body, actor, *, expected_issue_state='open'):
        self.preflight(repo, number, actor, expected_issue_state=expected_issue_state)
        self.posts += 1
        if self.mode == 'timeout_absent':
            raise TimeoutError('secret raw error must not be stored')
        row = dict(id=123, body=body, user={'id': self.actor},
                   html_url=f'https://github.com/{repo}/issues/{number}#issuecomment-123')
        self.rows.append(row)
        if self.mode == 'timeout_created':
            raise TimeoutError()
        return row

    def comments(self, repo, number):
        return copy.deepcopy(self.rows)


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binding = {'provider': {'request_budget': 0}}
        self.registry = UIRegistry(self.root, self.binding)
        self.addCleanup(lambda: self.registry.close())
        self.remote = Remote()
        self.facts = dict(repository='example/project', issue_number=6, run_id='run-1', event_id='accepted-1',
                          stage='accepted', summary='开始调查')
        self.observe = lambda *args: copy.deepcopy(self.facts)
        self.service = IssueProgress(self.registry, self.remote, self.observe, known_secrets=('opaque-private-value',))

    def prepare(self):
        return self.service.prepare('example/project', 6, 'run-1', self.facts['event_id'])

    def send(self, row):
        return self.service.send(row['id'], approve_sha256=row['approval_sha256'], expected_actor_id=42)

    def test_normal_and_duplicate_trigger(self):
        row = self.prepare()
        self.assertEqual(self.prepare(), row)
        result = self.send(row)
        self.assertEqual(result['status'], 'posted')
        self.assertIn('已接手', self.remote.rows[0]['body'])
        self.assertIn('run-1', self.remote.rows[0]['body'])
        self.assertEqual(self.prepare()['status'], 'posted')
        with self.assertRaises(SafetyError):
            self.send(row)
        self.assertEqual(self.remote.posts, 1)
        self.assertEqual(self.registry.budget()['requests_used'], 0)

    def test_timeout_created_reconciles_after_restart_without_post(self):
        self.remote.mode = 'timeout_created'
        row = self.prepare()
        with self.assertRaises(TimeoutError):
            self.send(row)
        self.registry.close()
        self.registry = UIRegistry(self.root, self.binding)
        self.service = IssueProgress(self.registry, self.remote, self.observe)
        self.assertEqual(self.service.view(row['id'])['status'], 'post_unknown')
        self.assertEqual(self.service.reconcile(row['id'])['status'], 'posted')
        self.assertEqual(self.remote.posts, 1)

    def test_timeout_absent_never_retries_even_after_empty_observation(self):
        self.remote.mode = 'timeout_absent'
        row = self.prepare()
        with self.assertRaises(TimeoutError):
            self.send(row)
        with self.assertRaises(SafetyError):
            self.service.reconcile(row['id'])
        with self.assertRaises(SafetyError):
            self.send(row)
        saved = json.dumps(self.service.view(row['id']))
        self.assertNotIn('secret raw error', saved)
        self.assertEqual(self.remote.posts, 1)

    def test_permission_denied_preflight_has_no_intent_or_post(self):
        self.remote.permission = False
        row = self.prepare()
        with self.assertRaises(SafetyError):
            self.send(row)
        self.assertEqual(self.service.view(row['id'])['status'], 'permission_denied')
        self.assertEqual(self.remote.posts, 0)

    def test_remote_marker_without_local_intent_does_not_duplicate(self):
        row = self.prepare()
        self.remote.rows = [{'body': row['core']['body']}]
        with self.assertRaises(SafetyError):
            self.send(row)
        self.assertEqual(self.remote.posts, 0)
        self.assertEqual(self.service.view(row['id'])['status'], 'prepared')

    def test_private_key_and_mentions_are_inert(self):
        self.facts['summary'] = '-----BEGIN RSA PRIVATE KEY-----\nprivate\n-----END RSA PRIVATE KEY----- @everyone'
        body = self.prepare()['core']['body']
        self.assertNotIn('\nprivate\n', body)
        self.assertNotIn('@everyone', body)

    def test_approval_and_account_are_required(self):
        row = self.prepare()
        with self.assertRaises(SafetyError):
            self.service.send(row['id'], approve_sha256='fake', expected_actor_id=42)
        self.remote.actor = 43
        with self.assertRaises(SafetyError):
            self.send(row)
        self.assertEqual(self.remote.posts, 0)

    def test_source_change_before_send_does_not_write(self):
        row = self.prepare()
        self.facts['summary'] = 'changed'
        with self.assertRaises(SafetyError):
            self.send(row)
        with self.assertRaises(SafetyError):
            self.prepare()
        self.assertEqual(self.remote.posts, 0)

    def test_sensitive_information_and_markdown_filtered_before_persistence(self):
        self.facts['summary'] = ('opaque-private-value ghp_ABCDEFGHIJKLMNO api_key="supersecret" '
                                 'Bearer private123 https://host.test/?token=private '
                                 'C:\\private\\user\\file [click](http://evil.test) <script> @everyone')
        row = self.prepare()
        serialized = json.dumps(row, ensure_ascii=False)
        for secret in ['opaque-private-value', 'ABCDEFGHIJKLMNO', 'supersecret', 'private123',
                       'host.test', 'C:\\private', 'evil.test', '<script>']:
            self.assertNotIn(secret, serialized)
        self.send(row)
        self.assertEqual(self.remote.rows[0]['body'], row['core']['body'])

    def test_candidate_passed_is_not_delivered(self):
        self.facts.update(stage='candidate_passed', candidate_checks_verified=True, candidate_sha='a'*40,
                          branch='maintenance/run-1', links=['https://github.com/example/project/pull/7'],
                          checks=[dict(name='unit', passed=5, failed=0, skipped=1)])
        row = self.prepare()
        self.assertIn('候选通过，尚未交付', row['core']['body'])
        self.assertNotIn('已交付（', row['core']['body'])
        self.assertIn('通过 5，失败 0，跳过 1', row['core']['body'])
        self.send(row)

    def test_candidate_needs_real_check_evidence(self):
        self.facts.update(stage='candidate_passed', candidate_sha='a'*40)
        with self.assertRaises(SafetyError):
            self.prepare()

    def test_empty_or_failed_checks_cannot_be_advertised_as_passing(self):
        self.facts.update(stage='candidate_passed', candidate_sha='a'*40, candidate_checks_verified=True,
                          branch='maintenance/run-1')
        for checks in [[], [dict(name='unit', passed=0, failed=0, skipped=0)],
                       [dict(name='unit', passed=5, failed=1, skipped=0)]]:
            with self.subTest(checks=checks):
                self.facts['checks'] = checks
                with self.assertRaises(SafetyError):
                    self.prepare()

    def test_delivered_requires_original_verified_proof(self):
        self.facts.update(stage='delivered', summary='tests passed')
        with self.assertRaises(SafetyError):
            self.prepare()
        self.facts.update(delivery_verified=True, delivered_sha='b'*40, delivery_proof_sha256='c'*64)
        self.assertIn('已交付（', self.prepare()['core']['body'])

    def test_verified_delivery_can_comment_on_already_closed_issue_without_reopening(self):
        self.facts.update(stage='delivered', issue_state='closed', delivery_verified=True,
                          delivered_sha='b'*40, delivery_proof_sha256='c'*64)
        self.remote.issue_state = 'closed'
        result = self.send(self.prepare())
        self.assertEqual(result['status'], 'posted')
        self.assertEqual(self.remote.issue_state, 'closed')
        self.assertEqual(self.remote.posts, 1)

    def test_nondelivery_stage_cannot_request_closed_issue_comment(self):
        self.facts['issue_state'] = 'closed'
        with self.assertRaises(SafetyError):
            self.prepare()

    def test_human_close_after_approval_blocks_ordinary_progress_write(self):
        row = self.prepare()
        self.remote.issue_state = 'closed'
        with self.assertRaises(SafetyError):
            self.send(row)
        self.assertEqual(self.remote.posts, 0)

    def test_existing_open_only_proposal_keeps_original_approval_and_no_migration(self):
        row = self.prepare()
        del row['core']['issue_state']  # v1 journal shape before closed-delivery support.
        row['approval_sha256'] = fingerprint(row['core'])
        with self.registry.transaction() as db:
            self.registry.put('issue_progress', row['id'], row, db)
        self.assertEqual(self.prepare(), row)
        result = self.send(row)
        self.assertEqual(result['status'], 'posted')
        self.assertNotIn('issue_state', result['core'])
        self.assertEqual(result['approval_sha256'], row['approval_sha256'])

    def test_each_stage_has_separate_event(self):
        for stage in ['accepted', 'investigated', 'blocked', 'awaiting_review']:
            self.facts.update(stage=stage, event_id=stage, candidate_checks_verified=True, candidate_sha='a'*40,
                              branch='maintenance/run-1', checks=[dict(name='unit', passed=5, failed=0, skipped=0)])
            self.send(self.prepare())
        self.assertEqual(self.remote.posts, 4)

    def test_unsafe_links_rejected(self):
        for link in ['https://github.com/example/project/pull/7?token=x',
                     'https://github.com/other/project/pull/7', 'http://github.com/example/project/pull/7',
                     'https://github.com/example/project/blob/main/../../secret', 'https://local/#capability']:
            with self.subTest(link=link):
                self.facts['links'] = [link]
                with self.assertRaises(SafetyError):
                    self.prepare()

    def test_reconcile_rejects_spoof_edit_duplicate_and_wrong_issue(self):
        row = self.prepare()
        self.send(row)
        original = copy.deepcopy(self.remote.rows)
        for mode in ['author', 'body', 'duplicate', 'url']:
            with self.subTest(mode=mode):
                self.remote.rows = copy.deepcopy(original)
                if mode == 'author':
                    self.remote.rows[0]['user']['id'] = 43
                elif mode == 'body':
                    self.remote.rows[0]['body'] += '\nedited'
                elif mode == 'duplicate':
                    self.remote.rows *= 2
                else:
                    self.remote.rows[0]['html_url'] = 'https://github.com/example/project/issues/7#issuecomment-123'
                with self.assertRaises(SafetyError):
                    self.service.reconcile(row['id'])
        self.assertEqual(self.remote.posts, 1)

    def test_concurrent_senders_post_once(self):
        row = self.prepare()
        errors = []
        barrier = threading.Barrier(2)
        def send():
            barrier.wait()
            try:
                self.send(row)
            except SafetyError as error:
                errors.append(error)
        threads = [threading.Thread(target=send) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.remote.posts, 1)
        self.assertEqual(len(errors), 1)


class TransportTests(unittest.TestCase):
    def connection(self):
        client = GitHubIssueComments()
        client.credential = lambda: 'test-only-token'
        self.calls = []
        self.pages = {}
        self.account = 42
        self.permission = True
        def request(path, **kwargs):
            self.calls.append((path, kwargs))
            if path == '/user':
                return {'id': self.account}
            if path == '/repos/example/project':
                return {'full_name': 'example/project', 'permissions': {'push': self.permission}}
            if path.endswith('/issues/6'):
                return {'number': 6, 'state': 'open', 'locked': False,
                        'html_url': 'https://github.com/example/project/issues/6'}
            if kwargs.get('payload'):
                return {'id': 123}
            return self.pages.get(path, [])
        client._request = request
        return client

    def test_fixed_create_uses_same_credential_no_other_writes(self):
        client = self.connection()
        client.create_comment('example/project', 6, 'approved body', 42)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(c[1]['_token'] == 'test-only-token' for c in self.calls))
        self.assertEqual(self.calls[-1], ('/repos/example/project/issues/6/comments',
                                         {'payload': {'body': 'approved body'}, '_token': 'test-only-token'}))

    def test_final_preflight_permission_and_actor(self):
        client = self.connection()
        for account, permission in [(43, True), (42, False)]:
            self.account, self.permission = account, permission
            with self.assertRaises(SafetyError):
                client.create_comment('example/project', 6, 'body', 42)
        self.assertFalse(any('payload' in kwargs for _, kwargs in self.calls))

    def test_incomplete_pagination_is_not_absence(self):
        client = self.connection()
        self.pages = {f'/repos/example/project/issues/6/comments?per_page=100&page={i}': [{}]*100 for i in range(1, 4)}
        with self.assertRaises(SafetyError):
            client.comments('example/project', 6)
        self.assertEqual(len(self.calls), 3)


if __name__ == '__main__':
    unittest.main()
