"""Real Python Pi planning boundary, stopped before launching any model process.

Credentials and repositories are disposable. Capturing Popen's environment proves
the launch arguments only; no SDK process, provider connection or model response
is exercised by this module.
"""
import hashlib
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app, inspect_repository
from repo_maintainer.service_state import saved_budget
from test_repository_workbench import FakeGitHub


class SyntheticLaunchStopped(RuntimeError):
    """The intended synthetic stop before a child process can start."""


class ModelProfileProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='profile-provider-')
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / 'source')
        self.config = self.root / 'original-provider.env'
        self.secret_a = 'synthetic-launch-A-private'
        self.secret_b = 'synthetic-launch-B-private'
        self.config.write_text(
            'PI_MAINTENANCE_API_KEY=' + self.secret_a + '\n'
            'PI_MAINTENANCE_BASE_URL=https://a.example.invalid/v1\n'
            'PI_MAINTENANCE_MODEL=model-a\n', encoding='utf-8')
        self.original_config = self.config.read_bytes()
        self.hub = self.client = None
        self.start()

    def tearDown(self):
        if self.client is not None:
            self.client.close()
        if self.hub is not None:
            self.hub.close()
        self.temp.cleanup()

    def start(self):
        # No provider_factory: the production PiProvider and saved_budget path run.
        self.hub = RepositoryWorkbench(self.root / 'state', self.config,
                                       request_budget=50, github=FakeGitHub())
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.headers = {'Authorization': 'Bearer ' + self.hub.token, 'Origin': 'http://127.0.0.1:12345'}

    def restart(self):
        self.client.close()
        self.hub.close()
        self.start()

    def add_b(self):
        response = self.client.post('/api/model-profiles', headers=self.headers, json={
            'action': 'create', 'revision': self.hub.model_profiles.view()['revision'],
            'name': 'Synthetic B', 'base_url': 'https://b.example.invalid/v1',
            'model': 'model-b', 'api_key': self.secret_b})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()['created_profile_id']

    def connect(self, profile_id):
        source = inspect_repository(self.repo)
        result = self.hub.executor.submit(self.hub.connect, {
            'path': str(self.repo), 'revision': source['revision'], 'profile_id': profile_id,
            'confirmed': True, 'allowed_paths': ['calculator.py'],
            'readonly_paths': ['tests/test_calculator.py'], 'allowed_tests': [list(DEMO_TEST)],
            'timeout_seconds': 30, 'allow_auto': False}).result(timeout=15)
        return self.hub.workspaces[result['workspace']['id']].session

    def session_action(self, session, action, payload):
        identity = uuid.uuid4().hex
        session.submit(identity, action, payload)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = session.registry.load('job').get(identity)
            if job and job['status'] != 'running':
                self.assertEqual('finished', job['status'], job)
                return job['result']
            time.sleep(.01)
        self.fail('Synthetic local budget operation did not finish')

    def test_real_pi_plan_passes_selected_a_b_credentials_to_popen_then_stops_before_launch(self):
        profile_b = self.add_b()
        repository = RepositoryGateway(self.repo)
        captured = []
        original_popen = subprocess.Popen

        def capture_then_stop(command, *args, **kwargs):
            # RepositoryGateway may execute local git reads during planning.
            if Path(command[0]).name.lower() in {'git', 'git.exe'}:
                return original_popen(command, *args, **kwargs)
            self.assertEqual('plan.mjs', Path(command[1]).name)
            captured.append({key: kwargs['env'][key] for key in (
                'PI_MAINTENANCE_MODEL', 'PI_MAINTENANCE_BASE_URL', 'PI_MAINTENANCE_API_KEY')})
            raise SyntheticLaunchStopped('Synthetic stop before model child launch')

        # Conflicting ambient values prove the explicit selected file wins.
        with patch.dict('os.environ', {'PI_MAINTENANCE_MODEL': 'ambient-wrong-model',
                        'PI_MAINTENANCE_BASE_URL': 'https://ambient.example.invalid',
                        'PI_MAINTENANCE_API_KEY': 'ambient-synthetic-key'}):
            with patch('repo_maintainer.pi_provider.subprocess.Popen', side_effect=capture_then_stop):
                for profile_id in ('legacy', profile_b):
                    selected = self.hub.model_profiles.resolve(profile_id)
                    provider = PiProvider(('calculator.py',), readonly_paths=('tests/test_calculator.py',),
                                          max_requests=50)
                    provider.config_file = selected['path']
                    with self.assertRaises(SyntheticLaunchStopped):
                        provider.plan(repository, 'Synthetic planning launch-boundary check', (DEMO_TEST,))
                    self.assertEqual(0, provider.requests_used)
        self.assertEqual([
            {'PI_MAINTENANCE_MODEL': 'model-a', 'PI_MAINTENANCE_BASE_URL': 'https://a.example.invalid/v1',
             'PI_MAINTENANCE_API_KEY': self.secret_a},
            {'PI_MAINTENANCE_MODEL': 'model-b', 'PI_MAINTENANCE_BASE_URL': 'https://b.example.invalid/v1',
             'PI_MAINTENANCE_API_KEY': self.secret_b}], captured)
        profile_public = self.client.get('/api/model-profiles', headers=self.headers)
        state_public = self.client.get('/api/state', headers=self.headers)
        self.assertEqual(200, profile_public.status_code)
        self.assertEqual(200, state_public.status_code)
        for secret in (self.secret_a, self.secret_b):
            self.assertNotIn(secret, profile_public.text + state_public.text)
        for profile_id in ('legacy', profile_b):
            selected = self.hub.model_profiles.resolve(profile_id)
            self.assertNotIn(str(selected['path']), profile_public.text)
            self.assertNotIn(hashlib.sha256(selected['path'].read_bytes()).hexdigest(), profile_public.text)
        self.assertEqual(self.original_config, self.config.read_bytes())
        self.assertEqual({}, self.hub.jobs)
        self.assertFalse((self.hub.root / 'projects').exists())

    def test_real_pi_profile_budget_extension_50_to_75_survives_restart_without_hub_reset(self):
        profile_b = self.add_b()
        session_a, session_b = self.connect('legacy'), self.connect(profile_b)
        self.assertIs(type(session_b.provider.inner), PiProvider)
        self.assertIsNone(self.hub.provider_factory)
        self.assertEqual(50, session_a.provider.inner.max_requests)
        self.assertEqual(50, session_b.provider.inner.max_requests)
        self.assertNotEqual(session_a.runs_dir, session_b.runs_dir)
        used, _ = session_b.registry.reserve(maximum=2)
        session_b.registry.settle(used, {'requests': 2}, success=True)
        unknown, _ = session_b.registry.reserve(maximum=3)
        session_b.registry.settle(unknown, {'requests': None}, success=False)
        attempts = session_b.registry.load('attempt')
        with patch.object(PiProvider, 'plan', side_effect=AssertionError('Budget operation cannot call a model')):
            preview = self.session_action(session_b, 'budget_preview', {'new_total': 75})['budget_preview']
            result = self.session_action(session_b, 'extend_budget', {
                'new_total': 75, 'revision': preview['revision']})['budget_change']
        self.assertEqual((50, 75, 2, 3), (result['old_total'], result['new_total'],
                                        result['requests_used'], result['requests_reserved_unknown']))
        expected_budget = {'request_budget': 75, 'requests_used': 2,
                           'requests_reserved_unknown': 3, 'requests_remaining': 70}
        self.assertEqual(expected_budget, session_b.registry.budget())
        self.assertEqual(75, session_b.provider.inner.max_requests)
        self.assertEqual(75, saved_budget(session_b.runs_dir))
        changes = session_b.registry.load('budget_change')
        directory_a, directory_b = session_a.runs_dir, session_b.runs_dir
        self.restart()
        self.assertEqual(50, self.hub.request_budget)
        with patch.object(PiProvider, 'plan', side_effect=AssertionError('Reopen cannot call a model')):
            reopened_a, reopened_b = self.connect('legacy'), self.connect(profile_b)
        self.assertEqual((directory_a, directory_b), (reopened_a.runs_dir, reopened_b.runs_dir))
        self.assertEqual(50, reopened_a.provider.inner.max_requests)
        self.assertEqual(75, reopened_b.provider.inner.max_requests)
        self.assertEqual(50, reopened_a.registry.budget()['request_budget'])
        self.assertEqual(expected_budget, reopened_b.registry.budget())
        self.assertEqual(attempts, reopened_b.registry.load('attempt'))
        self.assertEqual(changes, reopened_b.registry.load('budget_change'))
        self.assertEqual(self.config, reopened_a.provider.inner.config_file)
        self.assertEqual(self.hub.model_profiles.resolve(profile_b)['path'], reopened_b.provider.inner.config_file)
        self.assertEqual(self.original_config, self.config.read_bytes())


if __name__ == '__main__':
    unittest.main()
