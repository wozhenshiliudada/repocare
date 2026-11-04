import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from starlette.testclient import TestClient
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.models import SafetyError
from repo_maintainer.provider_config import load_provider_config
from repo_maintainer.runtime_limits import MAX_REQUESTS
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app, inspect_repository
from test_repository_workbench import FakeServer, FakeGitHub


class WebProviderSettingsTests(unittest.TestCase):
    def test_short_cli_and_legacy_command_accept_optional_file_and_state_directory(self):
        from repo_maintainer.cli import main
        for command in ('web', 'workbench'):
            with self.subTest(command=command), patch('repo_maintainer.repository_workbench.RepositoryWorkbench') as hub, \
                    patch('repo_maintainer.repository_workbench.serve_workbench') as serve:
                self.assertEqual(0, main([command]))
                self.assertEqual((Path.home() / '.repocare' / 'workbench', None), hub.call_args.args)
                self.assertEqual(MAX_REQUESTS, hub.call_args.kwargs['request_budget'])
                serve.assert_called_once()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hub = RepositoryWorkbench(self.root / 'state', request_budget=7,
                                      github=FakeGitHub(), server_factory=FakeServer)
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.headers = {'Authorization': 'Bearer ' + self.hub.token, 'Origin': 'http://127.0.0.1:12345'}
        self.secret = 'synthetic-web-key-do-not-echo'

    def tearDown(self):
        self.client.close()
        self.hub.close()
        self.temp.cleanup()

    def view(self):
        response = self.client.get('/api/provider-config', headers=self.headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual('no-store', response.headers['cache-control'])
        return response.json()

    def payload(self, **changes):
        return {'revision': self.view()['revision'], 'api_key': self.secret,
                'base_url': 'https://provider.example.invalid/v1', 'model': 'fixture-model', **changes}

    def save(self, payload=None):
        return self.client.post('/api/provider-config', headers=self.headers, json=payload or self.payload())

    def test_first_run_without_credentials_cannot_connect_or_start_monitor(self):
        self.assertFalse(self.view()['configured'])
        self.assertFalse(self.hub.provider_config.exists())
        with self.assertRaises(SafetyError):
            self.hub.connect({})
        with self.assertRaises(SafetyError):
            self.hub.monitor._provider(None)
        self.assertFalse((self.hub.root / 'projects').exists())
        self.assertEqual([], list(self.hub.monitor.root.iterdir()))

    def test_save_is_private_persistent_and_never_calls_model_or_enters_jobs(self):
        with patch('repo_maintainer.pi_provider.subprocess.Popen') as process:
            response = self.save()
            self.assertEqual(200, response.status_code, response.text)
            process.assert_not_called()
        self.assertTrue(response.json()['key_set'])
        self.assertEqual(self.secret, load_provider_config(self.hub.provider_config)['PI_MAINTENANCE_API_KEY'])
        for endpoint in ('/api/provider-config', '/api/state', '/api/service'):
            self.assertNotIn(self.secret, self.client.get(endpoint, headers=self.headers).text)
        self.assertFalse(self.hub.jobs)
        self.assertEqual([], list(self.hub.root.glob('.provider-*')))
        if os.name != 'nt':
            self.assertEqual(0o600, self.hub.provider_config.stat().st_mode & 0o777)
        self.client.close()
        self.hub.close()
        self.hub = RepositoryWorkbench(self.root / 'state', request_budget=7, github=FakeGitHub(), server_factory=FakeServer)
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.headers['Authorization'] = 'Bearer ' + self.hub.token
        self.assertTrue(self.view()['configured'])

    def test_auth_origin_body_limits_and_duplicate_fields(self):
        payload = self.payload()
        self.assertEqual(401, self.client.post('/api/provider-config', json=payload).status_code)
        self.assertEqual(403, self.client.post('/api/provider-config', headers={**self.headers, 'Origin': 'https://outside.invalid'}, json=payload).status_code)
        self.assertEqual(415, self.client.post('/api/provider-config', headers=self.headers, content='{}').status_code)
        headers = {**self.headers, 'Content-Type': 'application/json'}
        self.assertEqual(413, self.client.post('/api/provider-config', headers=headers, content='x'*16385).status_code)
        duplicate = json.dumps(payload)[:-1] + ',"model":"other"}'
        self.assertEqual(409, self.client.post('/api/provider-config', headers=headers, content=duplicate).status_code)
        self.assertFalse(self.hub.provider_config.exists())

    def test_invalid_settings_leave_original_bytes_and_never_echo_secret(self):
        self.assertEqual(200, self.save().status_code)
        before = self.hub.provider_config.read_bytes()
        for changes in ({'base_url': 'http://provider.invalid'}, {'base_url': 'https://u:password@provider.invalid'},
                        {'base_url': 'https://provider.invalid/?key='+self.secret}, {'base_url': 'https://provider.invalid:bad/v1'},
                        {'model': 'line\nPI_MAINTENANCE_API_KEY=oops'}, {'api_key': 'line\nsecret'},
                        {'extra': self.secret}, {'model': ''}):
            with self.subTest(fields=list(changes)):
                response = self.save(self.payload(**changes))
                self.assertEqual(409, response.status_code)
                self.assertNotIn(self.secret, response.text)
                self.assertEqual(before, self.hub.provider_config.read_bytes())

    def test_retain_key_requires_same_endpoint_and_stale_tabs_cannot_overwrite(self):
        stale = self.payload()
        self.assertEqual(200, self.save(stale).status_code)
        self.assertEqual(409, self.save(stale).status_code)
        self.assertEqual(200, self.save(self.payload(api_key='', model='second-model')).status_code)
        self.assertEqual(self.secret, load_provider_config(self.hub.provider_config)['PI_MAINTENANCE_API_KEY'])
        self.assertEqual(409, self.save(self.payload(api_key='', base_url='https://other.invalid/v1')).status_code)

    def test_atomic_write_failure_preserves_original_and_removes_temporary_secret(self):
        self.save()
        before = self.hub.provider_config.read_bytes()
        with patch('repo_maintainer.web_provider_settings.os.replace', side_effect=OSError(self.secret)):
            response = self.save(self.payload(model='changed'))
        self.assertEqual(409, response.status_code)
        self.assertNotIn(self.secret, response.text)
        self.assertEqual(before, self.hub.provider_config.read_bytes())
        self.assertFalse(list(self.hub.root.glob('.provider-*')))

    def test_concurrent_updates_only_one_revision_wins(self):
        payload = self.payload()
        def save():
            try:
                self.hub.provider_settings.save(payload)
                return 'saved'
            except SafetyError:
                return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(['saved', 'stale'], list(pool.map(lambda _: save(), range(2))))

    def test_existing_repository_binding_locks_settings_and_preserves_budget(self):
        self.save()
        repo = create_fixture(self.root / 'source')
        source = inspect_repository(repo)
        payload = {'path': str(repo), 'revision': source['revision'], 'confirmed': True,
                   'allowed_paths': ['calculator.py'], 'readonly_paths': ['tests/test_calculator.py'],
                   'allowed_tests': [list(DEMO_TEST)], 'timeout_seconds': 30, 'allow_auto': False}
        # Real registry creation stays on the original owning executor.
        self.hub.executor.submit(self.hub.connect, payload).result()
        self.assertFalse(self.view()['editable'])
        before = self.hub.provider_config.read_bytes()
        self.assertEqual(409, self.save().status_code)
        self.assertEqual(before, self.hub.provider_config.read_bytes())
        self.assertEqual(0, self.hub.sessions[str(repo)].session.registry.budget()['requests_used'])
        self.hub.executor.submit(lambda: [s.close() for s in self.hub.sessions.values()]).result()
        self.hub.sessions.clear()
        self.assertFalse(self.view()['editable'])

    def test_explicit_file_remains_readonly_and_is_not_overwritten(self):
        self.save()
        self.hub.provider_settings.managed = False
        before = self.hub.provider_config.read_bytes()
        self.assertFalse(self.view()['editable'])
        self.assertEqual(409, self.save().status_code)
        self.assertEqual(before, self.hub.provider_config.read_bytes())

    def test_busy_workbench_does_not_change_configuration(self):
        self.hub.active = 'synthetic-active-operation'
        self.assertEqual(409, self.save().status_code)
        self.assertFalse(self.hub.provider_config.exists())
        self.hub.active = None


if __name__ == '__main__':
    unittest.main()
