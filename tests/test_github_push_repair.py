"""Signed push -> original monitor/Pi/approval/candidate/seal, loopback only.

The GitHub adapter reads only the owned fixture repository. Model transport is
SyntheticModel SSE; Pi AgentSession, journal, rules, grants and poll are real.
"""
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
import unittest
import uuid

import test_monitor_model_authorization as original
from test_repository_workbench import FakeGitHub, FakeServer
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.github_connection import fetch_snapshot, github_url
from repo_maintainer.github_push import DELIVERIES, WORK, PushInputError
from repo_maintainer.github_push_control import GitHubPushControl
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.monitor_delivery import MonitorDelivery
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench
from repo_maintainer.task_session import TaskSessionStore


def _branch(repo):
    return repo.command('-c', 'core.longpaths=true', 'symbolic-ref', '--short', 'HEAD').decode().strip()


class LocalPushGitHub(FakeGitHub):
    """Synthetic identity/transport; snapshot and SHA are real owned local Git."""
    def __init__(self, directory):
        super().__init__()
        self.repo = RepositoryGateway(directory)
        self.calls = []

    def api(self, path):
        assert path == '/repos/example/project'
        self.calls.append(('metadata', path))
        return {'id': 123, 'full_name': 'example/project', 'private': True, 'default_branch': 'main'}

    def branch_head(self, url, branch):
        assert github_url(url)[0] == 'example/project' and branch == _branch(self.repo)
        self.calls.append(('head', self.repo.head))
        return self.repo.head

    def snapshot(self, url, branch, sha, destination):
        assert github_url(url)[0] == 'example/project' and branch == _branch(self.repo)
        self.calls.append(('snapshot', sha))
        return fetch_snapshot(self.repo.root, branch, sha, destination)

    def workflow_runs(self, url, branch, sha):
        assert github_url(url)[0] == 'example/project' and branch == _branch(self.repo)
        self.calls.append(('ci', sha))
        return []


class GitHubPushRepairTests(unittest.TestCase):
    worker = original.MonitorModelAuthorizationTests.worker
    scope = original.MonitorModelAuthorizationTests.scope
    view = original.MonitorModelAuthorizationTests.view
    preview = original.MonitorModelAuthorizationTests.preview
    configure = original.MonitorModelAuthorizationTests.configure
    grant_preview = original.MonitorModelAuthorizationTests.grant_preview
    grant = original.MonitorModelAuthorizationTests.grant
    session = original.MonitorModelAuthorizationTests.session
    preserve_cleanup = original.MonitorModelAuthorizationTests.preserve_cleanup

    def new_hub(self):
        self.github = LocalPushGitHub(self.repo)
        return RepositoryWorkbench(self.root / 'hub', self.config, request_budget=8, allow_auto=True,
            github=self.github, server_factory=FakeServer,
            provider_factory=lambda p: PiProvider(p.allowed_paths, self.model.url,
                readonly_paths=p.readonly_paths, max_requests=8))

    def setUp(self):
        # Original fixture creates real Git, SyntheticModel, Pi-backed hub and
        # stops its timer for deterministic explicit dispatch. No grant is forged.
        self.control = None
        original.MonitorModelAuthorizationTests.setUp(self)
        self.assertFalse(self.hub.monitor.thread.is_alive())
        self.assertTrue(self.hub.monitor.stopping.is_set())
        self.hub.monitor.stopping.clear()
        # Fixture-only timer isolation, not production recovery/re-authorization.
        self.gateway = RepositoryGateway(self.repo)
        self.branch = _branch(self.gateway)
        self.gateway.command('-c', 'core.longpaths=true', 'remote', 'add', 'origin',
                             'https://github.com/example/project.git')

    def finish(self):
        if self.control is not None:
            self.control.close()
        original.MonitorModelAuthorizationTests.finish(self)

    def payload(self, **overrides):
        return original.MonitorModelAuthorizationTests.payload(self,
            **{'github_url': 'https://github.com/example/project.git', **overrides})

    def save(self, name, value):
        directory = os.environ.get('PUSH_REPAIR_EVIDENCE_DIR')
        if directory:
            root = Path(directory)
            root.mkdir(parents=True, exist_ok=True)
            (root / (self._testMethodName + '-' + name + '.json')).write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def signed(self, before, after, delivery=None):
        body = json.dumps({'repository': {'id': 123, 'full_name': 'example/project', 'private': True},
            'ref': 'refs/heads/' + self.branch, 'before': before, 'after': after,
            'created': False, 'deleted': False, 'forced': False, 'head_commit': {'id': after}},
            ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        headers = [('Content-Type', 'application/json'), ('Content-Length', str(len(body))),
            ('X-GitHub-Event', 'push'), ('X-GitHub-Delivery', delivery or str(uuid.uuid4())),
            ('X-Hub-Signature-256', 'sha256=' + hmac.new(self.secret, body, hashlib.sha256).hexdigest())]
        return headers, body

    def begin(self, mode):
        mid = self.configure(allow_local_code=True, mode=mode)
        authorization = self.grant(mid)
        before = self.gateway.head
        self.gateway.command('-c', 'core.longpaths=true', 'commit', '--allow-empty', '-m', 'signed author push')
        after = self.gateway.head
        self.assertNotEqual(before, after)
        self.source_before = self.gateway.snapshot()
        self.source_config_before = (self.gateway.common_dir / 'config').read_bytes()
        self.secret = b'owned-synthetic-push-secret-32bytes-only'
        target = {'monitor_id': mid, 'repository_id': 123, 'repository': 'example/project',
                  'branch': self.branch}
        self.control = GitHubPushControl(self.hub, target, self.secret)
        registry = self.hub.monitor.registries[mid]
        self.assertIs(self.control.registry, registry)
        self.assertEqual(registry.budget()['requests_used'], 0)
        self.assertEqual(registry.load('event'), {})
        headers, body = self.signed(before, after)
        wrong = [(k, 'sha256=' + '0' * 64 if k == 'X-Hub-Signature-256' else v) for k, v in headers]
        with self.assertRaises(PushInputError) as rejected:
            self.control.receive(wrong, body)
        self.assertEqual(rejected.exception.http_status, 403)
        self.assertEqual(registry.load(DELIVERIES), {})
        self.assertEqual(registry.load(WORK), {})
        self.assertEqual(self.model.requests, [])

        # Keep the first real SDK request in flight while admitting redeliveries.
        entered, released = threading.Event(), threading.Event()
        self.model.gate = entered, released
        try:
            started = time.monotonic()
            status, result = self.control.receive(headers, body)
            ack_seconds = time.monotonic() - started
            self.assertEqual(status, 202)
            self.assertLess(ack_seconds, 2)
            self.assertTrue(entered.wait(60), 'Original Pi never reached the loopback synthetic model')
            self.assertEqual(len(self.model.requests), 1)
            self.assertEqual(self.control.receive(headers, body), (status, result))
            other, same_body = self.signed(before, after)
            second_status, second = self.control.receive(other, same_body)
            self.assertEqual(second_status, 202)
            self.assertEqual(second['receipt']['status'], 'duplicate_commit')
            self.assertEqual(second['receipt']['work_id'], result['receipt']['work_id'])
        finally:
            released.set()
        self.worker(lambda: None)  # Original serial dispatcher/monitor turn ended.
        events = registry.load('event')
        self.assertEqual(len(events), 1)
        event = events[digest(after.encode())[:16]]
        self.save('dispatch', {'receipt': result['receipt'], 'duplicate': second['receipt'],
            'ack_seconds': ack_seconds, 'source_sha': after, 'event_id': event['id'],
            'event_status': event['status'], 'preparation': event.get('root_preparation', {}).get('status'),
            'execution_status': event.get('root_execution', {}).get('status'),
            'budget': registry.budget(), 'model_requests': len(self.model.requests),
            'inbox': self.control.inbox.view(), 'paid_provider_requests': 0})
        self.assertEqual(event['sha'], after)
        self.assertEqual(event['root_preparation']['status'], 'reproduced', event)
        self.assertEqual(event['model_observation']['id'], authorization['id'])
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(len(registry.load('pi_task')), 1)
        self.assertEqual(len(registry.load(WORK)), 1)
        self.assertEqual(len(registry.load(DELIVERIES)), 2)
        self.assertEqual(self.control.inbox.view()['work'][0]['status'], 'event_linked')
        session = self.session(mid, event)
        self.preserve_cleanup(session)
        return mid, event, registry, session, headers, body, authorization

    def native(self, registry, session):
        tasks = registry.load('pi_task')
        self.assertEqual(len(tasks), 1)
        task = next(iter(tasks.values()))
        store = TaskSessionStore(registry, session.repo, RepositoryGateway(session.repo).common_dir)
        path = store.directory(task['task_id']) / 'sessions' / task['checkpoint']['file']
        raw = path.read_bytes()
        self.assertTrue(raw)
        return path, raw, {'task_id': task['task_id'], 'revision': task['revision'],
                           'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}

    def sealed_and_deduplicated(self, mid, event, registry, session, run_id, headers, body):
        directory = session.runs_dir / run_id
        record = json.loads((directory / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'candidate_ready')
        self.assertEqual(record['baseline_tests'][0]['category'], 'test_failed')
        self.assertEqual(record['candidate_tests'][0]['category'], 'passed')
        self.assertGreater(record['candidate_tests'][0]['tests_run'], 0)
        self.assertTrue(record['rounds'][0]['environment_comparison']['candidate_verified'])
        self.assertTrue(record['source_unchanged'])
        budget = registry.budget()
        self.assertEqual(budget['request_budget'], 8)
        self.assertEqual(budget['requests_used'], 2)
        self.assertEqual(budget['requests_reserved_unknown'], 0)
        self.assertEqual(budget['requests_remaining'], 6)
        self.assertEqual(session.budget_registry.path, registry.path)
        self.assertEqual(session.registry.budget()['requests_used'], 0)
        delivery = MonitorDelivery(self.hub.monitor, mid, event['id'], self.github)
        sealed = self.worker(delivery.operate, 'monitor_delivery_prepare_local',
            {'monitor_id': mid, 'event_id': event['id'], 'run_id': run_id})
        self.assertEqual(sealed['status'], 'prepared', sealed)
        self.assertTrue(sealed['review_sha256'])
        self.assertTrue(sealed['review']['tests'])
        self.assertTrue(all(t['category'] == 'passed' and t['tests_run'] > 0 for t in sealed['review']['tests']))
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(len(self.model.requests), 2)
        path, raw, native = self.native(registry, session)
        candidates = copy.deepcopy(session.runs)
        self.control.receive(headers, body)
        self.worker(lambda: None)
        self.worker(self.hub.monitor.poll, mid)  # Original timer path, same exact SHA.
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(session.runs, candidates)
        self.assertEqual(len(session.runs), 1)
        self.assertEqual(len(registry.load('event')), 1)
        self.assertEqual(len(registry.load('pi_task')), 1)
        self.assertEqual(len(registry.load(WORK)), 1)
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(self.gateway.snapshot(), self.source_before)
        self.assertEqual((self.gateway.common_dir / 'config').read_bytes(), self.source_config_before)
        self.assertEqual(self.gateway.command('-c', 'core.longpaths=true', 'status', '--porcelain'), b'')
        self.assertIn('return a - b', (self.repo / 'calculator.py').read_text(encoding='utf-8'))
        self.save('sealed', {'event_id': event['id'], 'source_sha': self.gateway.head,
            'mode': event['root_execution']['binding']['mode'], 'run_id': run_id,
            'candidate_status': record['status'], 'baseline_tests': record['baseline_tests'],
            'candidate_tests': record['candidate_tests'], 'sealed_tests': sealed['review']['tests'],
            'review_sha256': sealed['review_sha256'], 'native': native, 'budget': budget,
            'same_native_after_redelivery_poll': True, 'source_unchanged': True,
            'model_requests': len(self.model.requests), 'candidate_count': len(session.runs),
            'work_count': len(registry.load(WORK)), 'paid_provider_requests': 0,
            'remote_ci_verified': False})

    def test_manual_signed_push_requires_new_original_approval_before_candidate_and_seal(self):
        mid, event, registry, session, headers, body, authorization = self.begin('approval')
        self.assertEqual(event['root_execution']['status'], 'awaiting_approval', event)
        self.assertEqual(authorization['core']['max_attempts'], 1)
        self.assertEqual(session.runs, {})
        self.assertEqual(len(session.plans), 1)
        plan = next(iter(session.plans.values()))['view']
        self.assertEqual(plan['status'], 'awaiting_approval')
        _, before, native = self.native(registry, session)
        self.save('before-approval', {'candidate_count': 0, 'plan_status': plan['status'],
            'budget': registry.budget(), 'native': native, 'model_requests': len(self.model.requests)})
        with self.assertRaises(SafetyError):
            self.worker(session._operate, 'decision',
                {'plan_id': plan['id'], 'fingerprint': '0' * 64, 'decision': 'approve'})
        self.assertEqual(session.runs, {})
        approved = self.worker(session._operate, 'decision',
            {'plan_id': plan['id'], 'fingerprint': plan['fingerprint'], 'decision': 'approve'})
        self.preserve_cleanup(session)
        self.assertEqual(approved['workflow_status'], 'candidate_ready', approved)
        self.assertEqual(len(self.model.requests), 2, 'Saved approved patch must not call the model again')
        self.assertTrue(self.native(registry, session)[1].startswith(before))
        self.sealed_and_deduplicated(mid, event, registry, session, approved['run_id'], headers, body)

    def test_auto_signed_push_uses_preexisting_model_grant_and_single_original_candidate(self):
        mid, event, registry, session, headers, body, authorization = self.begin('auto')
        self.assertEqual(event['root_execution']['status'], 'candidate_ready', event)
        self.assertEqual(event['root_execution']['model_authorization']['id'], authorization['id'])
        self.assertEqual(event['model_dispatch']['status'], 'finished')
        self.assertEqual(len(session.runs), 1)
        self.sealed_and_deduplicated(mid, event, registry, session,
                                    event['root_execution']['run_id'], headers, body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
