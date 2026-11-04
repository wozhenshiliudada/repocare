import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.github_connection import GitHubConnection, branch_name, fetch_snapshot
from repo_maintainer.models import SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench, inspect_repository, create_workbench_app, validate_request
from test_repository_workbench import FakeServer, FakeGitHub


class CountingProvider(MockProvider):
    max_requests = 6
    requests_used = 0
    calls = 0
    def plan(self, *args):
        type(self).calls += 1
        self.evidence = {"requests": 2, "transport": "synthetic-counting"}
        return super().plan(*args)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / 'source')
        RepositoryGateway(self.repo).command('config', 'user.name', 'Monitor Test')
        RepositoryGateway(self.repo).command('config', 'user.email', 'monitor@example.invalid')
        self.config = self.root / '.env.provider.local'
        self.config.write_text('synthetic; not read')
        CountingProvider.calls = 0
        self.hub = self.new_hub()
        self.hub.monitor.stop()  # Deterministic dispatch; separate test exercises live timer.
        self.addCleanup(self.finish)
    def new_hub(self):
        return RepositoryWorkbench(self.root / 'hub', self.config, github=FakeGitHub(),
            provider_factory=lambda policy: CountingProvider(), server_factory=FakeServer)
    def finish(self):
        self.hub.close()
        self.temp.cleanup()
    def worker(self, function, *args):
        return self.hub.executor.submit(function, *args).result(timeout=30)
    def scope(self):
        source = inspect_repository(self.repo)
        return {'path': str(self.repo), 'revision': source['revision'], 'allowed_paths': ['calculator.py'],
            'readonly_paths': ['tests/test_calculator.py'], 'allowed_tests': [list(DEMO_TEST)],
            'timeout_seconds': 30, 'confirmed': True, 'branch': source['branch'],
            'github_url': '', 'interval_seconds': 60}
    def start(self):
        identity = self.worker(self.hub.monitor.start, self.scope())['monitor_id']
        self.worker(self.hub.monitor.poll, identity)
        return identity
    def view(self):
        return self.hub.monitor.view()[0]
    def server(self, identity):
        event = self.view()['events'][-1]
        return self.hub.monitor.servers[identity, event['id']]
    def commit(self):
        repo = RepositoryGateway(self.repo)
        repo.command('commit', '--allow-empty', '-m', 'new submitted version')
        return repo.head
    def test_red_plan_approval_green_and_source_untouched(self):
        before = RepositoryGateway(self.repo).snapshot()
        identity = self.start()
        view = self.view()
        self.assertEqual(view['events'][0]['status'], 'awaiting_approval', view)
        self.assertEqual(view['requests_used'], 2)
        self.assertEqual(view['requests_remaining'], 4)
        self.assertNotEqual(view['events'][0]['tests'][0]['returncode'], 0)
        server = self.server(identity)
        self.assertEqual(RepositoryGateway(server.session.repo).read('calculator.py'), RepositoryGateway(self.repo).read('calculator.py'))
        plan = next(iter(server.session.plans.values()))['view']
        result = server.session._operate('decision', {'plan_id': plan['id'], 'fingerprint': plan['fingerprint'], 'decision': 'approve'})
        self.assertEqual(result['workflow_status'], 'candidate_ready')
        run = server.session.state()['runs'][0]
        self.assertTrue(run['source_unchanged'])
        self.assertEqual(RepositoryGateway(self.repo).snapshot(), before)
        self.assertEqual(self.view()['requests_used'], 2)  # Saved patch never reruns paid diagnosis.
    def test_dedup_across_poll_and_restart_keeps_budget_and_new_claim(self):
        identity = self.start()
        first = self.server(identity).session.token
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(CountingProvider.calls, 1)
        self.hub.close()
        self.hub = self.new_hub()
        self.hub.monitor.stop()
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(CountingProvider.calls, 1)
        self.assertEqual(self.view()['requests_used'], 2)
        self.assertEqual(self.view()['requests_remaining'], 4)
        event = self.view()['events'][0]
        self.worker(self.hub.monitor.operate, 'monitor_open', {'monitor_id': identity, 'event_id': event['id']})
        session = self.server(identity).session
        self.assertNotEqual(session.token, first)
        self.assertEqual(session.claimed_plans, set())
    def test_consumed_event_restart_is_report_history_not_pending_approval(self):
        identity = self.start()
        session = self.server(identity).session
        plan = next(iter(session.plans.values()))['view']
        session._operate('decision', {'plan_id': plan['id'], 'fingerprint': plan['fingerprint'], 'decision': 'reject'})
        self.hub.close(); self.hub = self.new_hub(); self.hub.monitor.stop()
        event = self.view()['events'][0]
        self.assertEqual(event['display_status'], 'consumed')
        self.assertEqual(CountingProvider.calls, 1)
        self.assertEqual(self.view()['requests_used'], 2)
    def test_new_commit_invalidates_old_approval_before_next_poll(self):
        identity = self.start()
        session = self.server(identity).session
        plan = next(iter(session.plans.values()))['view']
        self.commit()
        with self.assertRaises(SafetyError):
            session._operate('decision', {'plan_id': plan['id'], 'fingerprint': plan['fingerprint'], 'decision': 'approve'})
        self.assertEqual(session.runs, {})
        self.worker(self.hub.monitor.poll, identity)
        view = self.view()
        self.assertEqual(view['events'][0]['status'], 'stale')
        self.assertEqual(view['events'][1]['status'], 'awaiting_approval')
        self.assertEqual(view['requests_used'], 4)
        self.assertEqual(CountingProvider.calls, 2)
    def test_pause_blocks_pending_approval_and_resume_preserves_budget(self):
        identity = self.start()
        session = self.server(identity).session
        self.worker(self.hub.monitor.operate, 'monitor_pause', {'monitor_id': identity})
        with self.assertRaises(SafetyError):
            session._checked_plan(next(iter(session.plans)))
        self.commit()
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(CountingProvider.calls, 1)
        self.worker(self.hub.monitor.operate, 'monitor_resume', {'monitor_id': identity})
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(self.view()['requests_used'], 4)
    def test_dirty_local_files_are_preserved_and_committed_revision_checked(self):
        (self.repo / 'calculator.py').write_text('my unsaved work\n')
        before = RepositoryGateway(self.repo).snapshot()
        identity = self.start()
        self.assertEqual(self.view()['events'][0]['status'], 'awaiting_approval')
        self.assertEqual(RepositoryGateway(self.repo).snapshot(), before)
        self.assertIn(b'return a - b', RepositoryGateway(self.server(identity).session.repo).read('calculator.py'))
    def test_green_commit_skips_model_and_ci_failure_later_notifies_once(self):
        repo = RepositoryGateway(self.repo)
        (self.repo / 'calculator.py').write_bytes(repo.read('calculator.py').replace(b'return a - b', b'return a + b'))
        repo.command('add', 'calculator.py'); repo.command('commit', '-m', 'fix fixture')
        repo.command('remote', 'add', 'origin', 'https://github.com/example/private.git')
        payload = self.scope(); payload['github_url'] = 'https://github.com/example/private.git'
        failures = []
        self.hub.github.branch_head = lambda url, branch: repo.head
        self.hub.github.workflow_runs = lambda *args: list(failures)
        self.hub.github.snapshot = lambda url, branch, sha, dest: fetch_snapshot(self.repo, branch, sha, dest)
        identity = self.worker(self.hub.monitor.start, payload)['monitor_id']
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(self.view()['events'][0]['status'], 'healthy')
        self.assertEqual(CountingProvider.calls, 0)
        failures.append({'id': 10, 'attempt': 1, 'name': 'CI', 'conclusion': 'failure', 'url': 'https://github.com/example/private/actions/runs/10'})
        self.worker(self.hub.monitor.poll, identity)
        event = self.view()['events'][0]
        self.assertEqual(event['status'], 'ci_attention')
        notification = event['notification']
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(self.view()['events'][0]['notification'], notification)
        self.assertEqual(CountingProvider.calls, 0)
        failures.clear()
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(self.view()['events'][0]['status'], 'healthy')
    def test_unknown_requests_and_interrupted_event_survive_restart_no_replay(self):
        with patch.object(CountingProvider, 'plan', side_effect=SafetyError('unknown transport outcome')):
            identity = self.start()
        self.assertEqual(self.view()['requests_reserved_unknown'], 6)
        event = self.view()['events'][0]
        event['status'] = 'diagnosing'
        self.worker(self.hub.monitor._event, identity, event)
        self.hub.close(); self.hub = self.new_hub(); self.hub.monitor.stop()
        self.assertEqual(self.view()['events'][0]['status'], 'interrupted')
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(self.view()['requests_remaining'], 0)
        self.assertEqual(CountingProvider.calls, 0)
    def test_authorization_rebind_and_interval_validation(self):
        self.start()
        payload = self.scope(); payload['branch'] = 'other'
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.start, payload)
        for interval in [1, 59, True, 3601]:
            payload = {**self.scope(), 'interval_seconds': interval, 'request_id': uuid.uuid4().hex}
            with self.assertRaises(SafetyError):
                validate_request('monitor_start', payload)
        for branch in ['-main', 'main..other', 'main/.secret', 'main@{1}', 'refs/x.lock', 'a//b']:
            with self.assertRaises(SafetyError):
                branch_name(branch)
    def test_test_side_effect_retained_no_model(self):
        payload = self.scope()
        payload['allowed_tests'] = [['{python}', '-c', "from pathlib import Path;Path('calculator.py').write_text('changed')"]]
        before = RepositoryGateway(self.repo).snapshot()
        identity = self.worker(self.hub.monitor.start, payload)['monitor_id']
        self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(self.view()['events'][0]['status'], 'blocked')
        self.assertEqual(CountingProvider.calls, 0)
        self.assertEqual(RepositoryGateway(self.repo).snapshot(), before)
        self.assertTrue(next((self.hub.root / 'monitors').glob('*/events/*/check/calculator.py')).exists())
    def test_timer_automatically_checks_after_enable(self):
        self.hub.close(); self.hub = self.new_hub()
        self.worker(self.hub.monitor.start, self.scope())
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.view()['events'] and self.view()['events'][0]['status'] == 'awaiting_approval':
                break
            time.sleep(.05)
        self.assertEqual(self.view()['events'][0]['status'], 'awaiting_approval')
    def test_bad_patch_rolls_back_and_rejection_never_edits(self):
        with patch.object(CountingProvider, 'plan', lambda p, *a: MockProvider('broken').plan(*a)):
            # Synthetic provider must report exact consumption even for an invalid proposal.
            def broken(provider, *args):
                provider.evidence = {'requests': 2}
                return MockProvider('broken').plan(*args)
            with patch.object(CountingProvider, 'plan', broken):
                identity = self.start()
        session = self.server(identity).session
        plan = next(iter(session.plans.values()))['view']
        result = session._operate('decision', {'plan_id': plan['id'], 'fingerprint': plan['fingerprint'], 'decision': 'approve'})
        self.assertEqual(result['workflow_status'], 'rolled_back')
        self.assertFalse((session.runs_dir / result['run_id'] / 'workspace').exists())
        self.commit(); self.worker(self.hub.monitor.poll, identity)
        session = self.server(identity).session
        plan = next(iter(session.plans.values()))['view']
        result = session._operate('decision', {'plan_id': plan['id'], 'fingerprint': plan['fingerprint'], 'decision': 'reject'})
        self.assertEqual(result['workflow_status'], 'rejected')
        self.assertFalse((session.runs_dir / result['run_id'] / 'workspace').exists())
    def test_missing_budget_journal_refuses_restart(self):
        self.start(); self.hub.close()
        path = next((self.hub.root / 'monitors').glob('*/ui-state/registry.sqlite3'))
        path.rename(path.with_suffix('.preserved'))
        with self.assertRaises(SafetyError):
            self.new_hub()
    def test_commit_changes_during_model_response_block_plan_without_refund(self):
        original = CountingProvider.plan
        def changes(provider, *args):
            plan = original(provider, *args)
            self.commit()
            return plan
        with patch.object(CountingProvider, 'plan', changes):
            identity = self.start()
        self.assertEqual(self.view()['events'][0]['status'], 'blocked')
        self.assertEqual(self.view()['requests_used'], 2)
        self.assertEqual(self.server(identity).session.plans, {})
    def test_exhaustion_on_new_commit_never_resets_allowance(self):
        identity = self.start()
        for _ in range(3):
            self.commit(); self.worker(self.hub.monitor.poll, identity)
        self.assertEqual(self.view()['requests_used'], 6)
        self.assertEqual(self.view()['requests_remaining'], 0)
        self.assertEqual(self.view()['events'][-1]['status'], 'blocked')
        self.assertEqual(CountingProvider.calls, 3)
    def test_commit_arrives_during_baseline_test_blocks_edit_and_rolls_back(self):
        from repo_maintainer.runner import run_test
        identity = self.start()
        session = self.server(identity).session
        plan = next(iter(session.plans.values()))['view']
        def changed(command, workspace, timeout, **kwargs):
            result = run_test(command, workspace, timeout, **kwargs)
            self.commit()
            return result
        with patch('repo_maintainer.workflow.run_test', changed):
            result = session._operate('decision', {'plan_id': plan['id'], 'fingerprint': plan['fingerprint'], 'decision': 'approve'})
        self.assertEqual(result['workflow_status'], 'rolled_back')
        self.assertFalse((session.runs_dir / result['run_id'] / 'workspace').exists())
        self.assertEqual(self.view()['requests_used'], 2)
    def test_api_monitor_requires_capability_and_explicit_fields(self):
        client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.addCleanup(client.close)
        payload = {**self.scope(), 'request_id': uuid.uuid4().hex}
        self.assertEqual(client.post('/api/actions/monitor_start', json=payload).status_code, 401)
        headers = {'Authorization': 'Bearer ' + self.hub.token}
        self.assertEqual(client.post('/api/actions/monitor_start', json={**payload, 'confirmed': False}, headers=headers).status_code, 202)
        self.worker(lambda: None)
        self.assertEqual(self.hub.state()['jobs'][-1]['status'], 'failed')
        self.assertEqual(self.hub.monitor.view(), [])


class GitHubMonitorAPITests(unittest.TestCase):
    def test_branch_and_ci_projection_exact_sha_safe_url(self):
        github = GitHubConnection(); sha = 'a' * 40
        calls = []
        def api(path):
            calls.append(path)
            if '/branches/' in path:
                return {'commit': {'sha': sha}}
            return {'workflow_runs': [{'id': 123, 'run_attempt': 2, 'head_sha': sha, 'head_branch': 'feat/test',
                'status': 'completed', 'conclusion': 'failure', 'name': '<script>', 'html_url': 'https://evil.invalid'}]}
        with patch.object(github, 'api', side_effect=api):
            self.assertEqual(github.branch_head('https://github.com/example/private', 'feat/test'), sha)
            runs = github.workflow_runs('https://github.com/example/private', 'feat/test', sha)
        self.assertIn('feat%2Ftest', calls[0])
        self.assertEqual(runs[0]['url'], 'https://github.com/example/private/actions/runs/123')
        with patch.object(github, 'credential', side_effect=AssertionError('must not read credentials')):
            for path in ['/repos/x/y/actions/runs?evil=1', '/repos/x/y/branches/main?x=1', '/user/token']:
                with self.assertRaises(SafetyError):
                    github.api(path)
