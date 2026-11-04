import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from repo_maintainer.issue_comment_continuity import CommentContinuity, KIND
from repo_maintainer.issue_intake import IssueReader, specification
from repo_maintainer.issue_progress import IssueProgress
from repo_maintainer.models import SafetyError
from repo_maintainer.ui_registry import UIRegistry
from test_issue_intake import FakeGitHub
from test_issue_progress import Remote


class CommentRemote(Remote):
    def __init__(self, github):
        super().__init__()
        self.github, self.after, self.started, self.release = github, None, None, None

    def comment_actor(self):
        return {'id': self.actor, 'login': 'maintainer'}

    def create_comment(self, repository, number, body, actor, *, expected_issue_state='open'):
        self.preflight(repository, number, actor, expected_issue_state=expected_issue_state)
        self.posts += 1
        stamp = f'2026-10-28T01:00:{self.posts:02d}Z'
        row = {'id': 100+self.posts, 'body': body, 'user': {'id': self.actor}, 'created_at': stamp, 'updated_at': stamp,
               'html_url': f'https://github.com/{repository}/issues/{number}#issuecomment-{100+self.posts}'}
        self.rows.append(row)
        self.github.issue['updated_at'] = stamp
        if self.started:
            self.started.set()
            if not self.release.wait(5):
                raise TimeoutError('test release missing')
        if self.after:
            self.after()
        if self.mode == 'timeout_created':
            raise TimeoutError('simulated lost POST response')
        return copy.deepcopy(row)


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = UIRegistry(self.root, {'provider': {'request_budget': 0}})
        self.addCleanup(lambda: self.registry.close())
        self.github = FakeGitHub('a'*40)
        self.spec = specification('https://github.com/example/project', 'main', ['.'], ['maintainer'])
        self.reader = lambda: IssueReader(self.github, self.spec, registry=self.registry)
        self.original = self.reader().snapshot(7)
        self.remote = CommentRemote(self.github)
        self.continuity = CommentContinuity(self.registry, self.github, self.spec)

    def service(self, stage='accepted'):
        def observe(repository, number, run_id, event_id):
            if self.reader().snapshot(number)['token'] != self.original['token']:
                raise SafetyError('original source changed')
            return dict(repository=repository, issue_number=number, run_id=run_id, event_id=event_id,
                        stage=stage, summary='Original evidence only')
        return IssueProgress(self.registry, self.remote, observe, continuity=self.continuity)

    def post(self, stage='accepted'):
        service = self.service(stage)
        row = service.prepare('example/project', 7, 'run-1', stage, actor_id=42)
        return service.send(row['id'], approve_sha256=row['approval_sha256'], expected_actor_id=42)

    def test_only_witnessed_own_timestamp_normalizes_without_changing_original_token(self):
        result = self.post()
        self.assertEqual(result['status'], 'posted')
        self.assertEqual(self.reader().snapshot(7), self.original)
        self.assertNotEqual(self.reader().raw_snapshot(7)['token'], self.original['token'])
        self.assertEqual(len(self.registry.load(KIND)), 1)
        self.assertEqual(self.registry.budget()['requests_used'], 0)

    def test_multiple_approved_comments_form_exact_chain(self):
        self.post()
        self.post('investigated')
        self.assertEqual(self.reader().snapshot(7), self.original)
        self.assertEqual(len(self.registry.load(KIND)), 2)
        self.assertEqual(self.remote.posts, 2)

    def test_unknown_write_reconciles_once_and_restores_source_continuity(self):
        self.remote.mode = 'timeout_created'
        service = self.service()
        row = service.prepare('example/project', 7, 'run-1', 'accepted', actor_id=42)
        with self.assertRaises(TimeoutError):
            service.send(row['id'], approve_sha256=row['approval_sha256'], expected_actor_id=42)
        self.assertNotEqual(self.reader().snapshot(7)['token'], self.original['token'])
        self.assertEqual(service.reconcile(row['id'])['status'], 'posted')
        self.assertEqual(self.reader().snapshot(7), self.original)
        self.assertEqual(self.remote.posts, 1)

    def test_restart_preserves_witnesses_and_approvals(self):
        row = self.post()
        self.registry.close()
        self.registry = UIRegistry(self.root, {'provider': {'request_budget': 0}})
        self.assertEqual(self.reader().snapshot(7), self.original)
        self.assertEqual(self.registry.load('issue_progress')[row['id']]['approval_sha256'], row['approval_sha256'])

    def test_human_metadata_update_is_not_ignored(self):
        self.post()
        self.github.issue['updated_at'] = '2026-10-28T02:00:00Z'
        self.assertNotEqual(self.reader().snapshot(7)['token'], self.original['token'])

    def test_body_status_labels_target_and_trust_changes_do_not_gain_witness(self):
        for field in ('body', 'state', 'labels', 'target', 'trust'):
            with self.subTest(field=field):
                issue, head, events = copy.deepcopy(self.github.issue), self.github.head, copy.deepcopy(self.github.events)
                def changed():
                    if field == 'target': self.github.head = 'b'*40
                    elif field == 'trust': self.github.events.append(self.github.event(2, 'unlabeled', 'outsider'))
                    elif field == 'labels': self.github.issue['labels'].append({'name': 'blocked'})
                    elif field == 'state': self.github.issue['state'] = 'closed'
                    else: self.github.issue['body'] += ' human edit'
                self.remote.after = changed
                # Distinct events simulate distinct approved original tasks; no source relaxation.
                service = self.service()
                row = service.prepare('example/project', 7, 'run-1', field, actor_id=42)
                result = service.send(row['id'], approve_sha256=row['approval_sha256'], expected_actor_id=42)
                self.assertEqual(result['status'], 'posted-review-required')
                self.assertFalse(self.registry.load(KIND))
                self.github.issue, self.github.head, self.github.events = issue, head, events

    def test_missing_or_edited_comment_timestamp_cannot_explain_change(self):
        self.remote.after = lambda: self.remote.rows[0].update(updated_at='2026-10-28T02:00:00Z')
        result = self.post()
        self.assertEqual(result['status'], 'posted-review-required')
        self.assertFalse(self.registry.load(KIND))

    def test_witness_corruption_fails_closed(self):
        row = self.post()
        witness = self.registry.load(KIND)[row['id']]
        witness['after_updated_at'] = '2026-10-28T03:00:00Z'
        self.registry.put(KIND, row['id'], witness)
        with self.assertRaises(SafetyError): self.reader().snapshot(7)

    def test_snapshot_waits_for_atomic_comment_receipt_and_witness(self):
        self.remote.started, self.remote.release = threading.Event(), threading.Event()
        completed, errors, seen = threading.Event(), [], []
        def send():
            try: self.post()
            except Exception as e: errors.append(e)
        def read():
            try: seen.append(self.reader().snapshot(7))
            except Exception as e: errors.append(e)
            completed.set()
        sender = threading.Thread(target=send)
        sender.start()
        self.assertTrue(self.remote.started.wait(5))
        reader = threading.Thread(target=read)
        reader.start()
        self.assertFalse(completed.wait(.1))
        self.remote.release.set()
        sender.join(5); reader.join(5)
        self.assertFalse(sender.is_alive() or reader.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(seen, [self.original])
