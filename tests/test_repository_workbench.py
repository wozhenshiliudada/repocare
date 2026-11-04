import io
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from starlette.testclient import TestClient
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.github_connection import GitHubConnection, github_url
from repo_maintainer.local_ui import create_app
from repo_maintainer.models import SafetyError
from repo_maintainer.maintenance_limits import LIMITS
from repo_maintainer.repository_workbench import RepositoryWorkbench, inspect_repository, create_workbench_app


class FakeServer:
    def __init__(self, session):
        self.session = session
        self.url = 'http://127.0.0.1:12346/#session=' + session.token
    def close(self):
        self.session.close()


class FakeGitHub:
    def __init__(self):
        self.clones = self.logins = 0
    def account(self):
        return {'login': 'example'}
    def repositories(self, page):
        return {'page': page, 'has_next': False, 'repositories': [{'name': 'example/private',
                'url': 'https://github.com/example/private.git', 'private': True, 'archived': False}]}
    def login(self, update):
        self.logins += 1
        update({'verification_url': 'https://github.com/login/device', 'user_code': 'ABCD-EFGH'})
        return self.account()
    def clone(self, url, destination):
        self.clones += 1
        create_fixture(destination)
        RepositoryGateway(destination).command('remote', 'add', 'origin', url)


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / 'source')
        self.config = self.root / '.env.provider.local'
        self.config.write_text('not read by onboarding', encoding='utf-8')
        self.github = FakeGitHub()
        self.hub = self.new_hub()
        self.addCleanup(self.finish)
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.headers = {'Authorization': 'Bearer ' + self.hub.token}
    def new_hub(self, **kwargs):
        kwargs.setdefault('request_budget', 6)  # Frozen legacy-ledger regression, independent of new defaults.
        hub = RepositoryWorkbench(self.root / 'hub', self.config, github=self.github,
                server_factory=FakeServer, picker=lambda: {'cancelled': True}, **kwargs)
        hub.url = 'http://127.0.0.1:12345/#session=' + hub.token
        return hub
    def finish(self):
        self.client.close()
        self.hub.close()
    def execute(self, action, payload=None, identity=None):
        response = self.client.post('/api/actions/' + action, headers=self.headers,
                    json={'request_id': identity or uuid.uuid4().hex, **(payload or {})})
        self.assertEqual(response.status_code, 202, response.text)
        identity = response.json()['id']
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = next(j for j in self.client.get('/api/state', headers=self.headers).json()['jobs'] if j['id'] == identity)
            if job['status'] != 'running':
                return job
            time.sleep(.01)
        self.fail('Operation timeout')
    def scope(self):
        source = inspect_repository(self.repo)
        return {'path': str(self.repo), 'revision': source['revision'], 'allowed_paths': ['calculator.py'],
                'readonly_paths': ['tests/test_calculator.py'], 'allowed_tests': [list(DEMO_TEST)],
                'timeout_seconds': 30, 'allow_auto': False, 'confirmed': True}
    def connect(self):
        job = self.execute('connect', self.scope())
        self.assertEqual(job['status'], 'finished', job)
        return self.hub.sessions[str(self.repo)]
    def test_local_preview_no_changes_no_model(self):
        before = RepositoryGateway(self.repo).snapshot()
        job = self.execute('local', {'path': str(self.repo)})
        self.assertEqual(job['status'], 'finished')
        self.assertTrue(job['result']['clean'])
        self.assertIn('calculator.py', job['result']['files'])
        self.assertEqual(before, RepositoryGateway(self.repo).snapshot())
        self.assertFalse(self.hub.sessions)
    def test_cancel_native_picker_has_no_session(self):
        self.assertTrue(self.execute('pick')['result']['cancelled'])
        self.assertFalse(self.hub.sessions)

    def test_preview_large_tracked_catalog_does_not_grant_wider_execution_scope(self):
        notes = self.repo / 'evidence'
        notes.mkdir()
        for i in range(1001):
            (notes / f'note-{i}.txt').write_text('fixed evidence\n', encoding='utf-8')
        repository = RepositoryGateway(self.repo)
        repository.command('add', 'evidence')
        repository.command('-c', 'user.name=Catalog Fixture', '-c', 'user.email=catalog@example.invalid',
                           'commit', '-m', 'large read-only evidence catalog')
        preview = inspect_repository(self.repo)
        self.assertTrue(preview['clean'])
        self.assertGreater(len(preview['files']), 1000)
        self.assertFalse(self.hub.sessions)
        self.assertEqual(self.hub.request_budget, 6)
        scope = self.scope()
        self.assertEqual(scope['allowed_paths'], ['calculator.py'])
        self.assertEqual(scope['readonly_paths'], ['tests/test_calculator.py'])

    def test_catalog_count_and_path_bytes_still_have_separate_hard_limits(self):
        original = RepositoryGateway.command
        for catalog in (b'x\0' * (LIMITS['catalog_max_files'] + 1), b'x' * (LIMITS['catalog_max_path_bytes'] + 1) + b'\0'):
            def command(repository, *args, **kwargs):
                if args == ('ls-files', '-z'):
                    return catalog
                return original(repository, *args, **kwargs)
            with self.subTest(bytes=len(catalog)), patch.object(RepositoryGateway, 'command', command):
                with self.assertRaisesRegex(SafetyError, '容量'):
                    inspect_repository(self.repo)
    def test_dirty_preview_but_connection_preserves_work(self):
        path = self.repo / 'calculator.py'
        path.write_text('user changes', encoding='utf-8')
        preview = self.execute('local', {'path': str(self.repo)})
        self.assertFalse(preview['result']['clean'])
        self.assertEqual(self.execute('connect', self.scope())['status'], 'failed')
        self.assertEqual(path.read_text(), 'user changes')
    def test_changed_preview_rejected(self):
        scope = self.scope()
        repo = RepositoryGateway(self.repo)
        repo.command('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'advance')
        self.assertEqual(self.execute('connect', scope)['status'], 'failed')
        self.assertFalse(self.hub.sessions)
    def test_confirmation_and_file_scope_required(self):
        for change in [{'confirmed': False}, {'allowed_paths': ['../outside.py']},
                       {'allowed_paths': ['calculator.py'], 'readonly_paths': ['calculator.py']},
                       {'allowed_paths': ['.env.provider.local']}, {'allow_auto': True}]:
            with self.subTest(change=change):
                self.assertEqual(self.execute('connect', {**self.scope(), **change})['status'], 'failed')
    def test_session_opens_no_model_and_reuses(self):
        server = self.connect()
        self.assertEqual(server.session.registry.budget()['requests_used'], 0)
        self.assertFalse(server.session.plans)
        self.assertEqual(server.session.state()['repository_picker_url'], self.hub.url)
        again = self.execute('connect', self.scope())
        self.assertTrue(again['result']['reused'])
        self.assertEqual(len(self.hub.sessions), 1)
    def test_policy_cannot_change_and_reset_budget(self):
        server = self.connect()
        server.session.registry.reserve()
        altered = {**self.scope(), 'timeout_seconds': 31}
        self.assertEqual(self.execute('connect', altered)['status'], 'failed')
        self.assertEqual(server.session.registry.budget()['requests_reserved_unknown'], 6)
    def test_restart_preserves_unknown_and_replaces_capabilities(self):
        server = self.connect()
        server.session.registry.reserve()
        old_token = server.session.token
        self.client.close()
        self.hub.close()
        self.hub = self.new_hub()
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.headers = {'Authorization': 'Bearer ' + self.hub.token}
        resumed = self.connect().session
        self.assertNotEqual(resumed.token, old_token)
        self.assertEqual(resumed.registry.budget()['requests_reserved_unknown'], 6)
        self.assertEqual(resumed.registry.budget()['requests_remaining'], 0)
    def test_metadata_rebind_and_duplicate_owner_refused(self):
        with self.assertRaises(SafetyError):
            self.new_hub()
        self.hub.close()
        with self.assertRaises(SafetyError):
            self.new_hub(request_budget=7)
        self.hub = self.new_hub()
    def test_github_clone_fixed_root_and_reuse(self):
        first = self.execute('clone', {'url': 'https://github.com/example/private.git'})
        second = self.execute('clone', {'url': 'https://github.com/example/private.git'})
        self.assertEqual(first['status'], 'finished', first)
        self.assertEqual(first['result']['path'], second['result']['path'])
        self.assertEqual(self.github.clones, 1)
        self.assertTrue(Path(first['result']['path']).is_relative_to(self.hub.root / 'clones'))
    def test_partial_clone_is_not_overwritten(self):
        def failed(url, path):
            path.mkdir()
            (path / 'preserve').write_text('partial')
            raise SafetyError('clone failed')
        with patch.object(self.github, 'clone', side_effect=failed):
            job = self.execute('clone', {'url': 'https://github.com/example/private.git'})
        self.assertEqual(job['status'], 'failed')
        job = self.execute('clone', {'url': 'https://github.com/example/private.git'})
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(next((self.hub.root / 'clones').glob('*/preserve')).read_text(), 'partial')
    def test_idempotent_login_single_dispatch(self):
        identity = uuid.uuid4().hex
        self.assertEqual(self.execute('login', identity=identity)['status'], 'finished')
        self.execute('login', identity=identity)
        self.assertEqual(self.github.logins, 1)
        response = self.client.post('/api/actions/account', headers=self.headers, json={'request_id': identity})
        self.assertEqual(response.status_code, 409)
    def test_account_and_repository_metadata_only(self):
        self.assertEqual(self.execute('account')['result'], {'login': 'example'})
        result = self.execute('repositories', {'page': 1})['result']
        self.assertTrue(result['repositories'][0]['private'])
    def test_security_origin_auth_strict_body(self):
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.assertEqual(self.client.get('/api/state', headers={**self.headers, 'Origin': 'https://outside.invalid'}).status_code, 403)
        self.assertEqual(self.client.get('/api/state', headers={**self.headers, 'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        for body in ['{"request_id":"'+uuid.uuid4().hex+'","request_id":"duplicate"}',
                     '{"request_id":"'+uuid.uuid4().hex+'","extra":"value"}',
                     '{"request_id":"'+uuid.uuid4().hex+'","page":NaN}', 'x'*17000]:
            response = self.client.post('/api/actions/account', headers={**self.headers, 'Content-Type': 'application/json'}, content=body)
            self.assertIn(response.status_code, {409, 413})
    def test_relative_nonrepo_and_nested_root_rejected(self):
        for path in [Path('relative'), self.root, self.repo / 'tests']:
            with self.assertRaises(SafetyError):
                inspect_repository(path)
    def test_symlink_root_rejected(self):
        link = self.root / 'alias'
        try:
            link.symlink_to(self.repo, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not permit symlink creation')
        with self.assertRaises(SafetyError):
            inspect_repository(link)


class GitHubConnectionTests(unittest.TestCase):
    def test_url_parser_rejects_transport_credentials_and_options(self):
        self.assertEqual(github_url('https://github.com/example/project.git')[0], 'example/project')
        for value in ['http://github.com/example/project', 'https://token@github.com/example/project',
                      'https://github.com/example/project?token=secret', 'https://github.com/example/project#fragment',
                      'https://github.com:443/example/project', 'https://evil.invalid/a/b',
                      'file:///a/b', 'https://github.com/a/..', 'https://github.com/a/b/c', '--upload-pack=evil']:
            with self.subTest(value=value), self.assertRaises(SafetyError):
                github_url(value)
    def test_api_no_redirect_and_no_credential_in_result(self):
        connection = GitHubConnection()
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"login":"example"}'
        opener = unittest.mock.MagicMock()
        opener.open.return_value = response
        with patch.object(connection, 'credential', return_value='syntheticNeverRealCredential'), patch(
                'repo_maintainer.github_connection.build_opener', return_value=opener):
            self.assertEqual(connection.account(), {'login': 'example'})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/user')
        self.assertEqual(request.get_header('Authorization'), 'Bearer syntheticNeverRealCredential')
        with self.assertRaises(SafetyError):
            connection.api('/repos/anything')
    def test_helper_failure_redacts_raw_error(self):
        result = unittest.mock.MagicMock(returncode=1, stdout=b'password=syntheticNeverRealCredential')
        with patch('repo_maintainer.github_connection.subprocess.run', return_value=result):
            with self.assertRaises(SafetyError) as error:
                GitHubConnection().credential()
        self.assertNotIn('syntheticNeverRealCredential', str(error.exception))
    def test_device_login_only_exports_code_and_uses_official_helper(self):
        process = unittest.mock.MagicMock()
        process.stdout = io.BytesIO(b'private_token=syntheticNeverRealCredential\nFirst copy your one-time code: ABCD-EFGH\n')
        process.wait.return_value = 0
        process.poll.return_value = 0
        connection = GitHubConnection()
        events = []
        with patch('repo_maintainer.github_connection.subprocess.Popen', return_value=process) as start, patch.object(
                connection, 'account', return_value={'login': 'example'}):
            self.assertEqual(connection.login(events.append), {'login': 'example'})
        self.assertEqual(events, [{'verification_url': 'https://github.com/login/device', 'user_code': 'ABCD-EFGH'}])
        self.assertIn('--device', start.call_args.args[0])
        self.assertNotIn('--force', start.call_args.args[0])
        self.assertNotIn('syntheticNeverRealCredential', json.dumps(events))
    def test_clone_command_blocks_hooks_filters_and_redirects(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / 'new'
            result = unittest.mock.MagicMock(returncode=0, stdout=b'')
            with patch('repo_maintainer.github_connection.subprocess.run', return_value=result) as run:
                GitHubConnection().clone('https://github.com/example/project', destination)
            command = run.call_args_list[0].args[0]
            self.assertIn('--no-checkout', command)
            self.assertIn('protocol.file.allow=never', command)
            self.assertIn('http.followRedirects=false', command)
            self.assertEqual(run.call_args_list[0].kwargs['env']['GIT_CONFIG_GLOBAL'], os.devnull)
            destination.mkdir()
            with self.assertRaises(SafetyError):
                GitHubConnection().clone('https://github.com/example/project', destination)


class ChildListenerTests(unittest.TestCase):
    def test_actual_listener_close_releases_owner_and_keeps_unknown_budget(self):
        import http.client
        from repo_maintainer.repository_workbench import SessionServer
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            source = create_fixture(root / 'source')
            config = root / '.env.local'
            config.write_text('synthetic; not read', encoding='utf-8')
            preview = inspect_repository(source)
            scope = {'path': str(source), 'revision': preview['revision'], 'allowed_paths': ['calculator.py'],
                     'readonly_paths': ['tests/test_calculator.py'], 'allowed_tests': [list(DEMO_TEST)],
                     'timeout_seconds': 30, 'allow_auto': False, 'confirmed': True}
            old_token = None
            for attempt in range(2):
                hub = RepositoryWorkbench(root / 'hub', config, github=FakeGitHub(), request_budget=6,
                                          server_factory=SessionServer)
                hub.url = 'http://127.0.0.1:12345/#session=' + hub.token
                try:
                    hub.executor.submit(hub.connect, scope).result(timeout=15)
                    child = hub.sessions[str(source)]
                    connection = http.client.HTTPConnection('127.0.0.1', child.port, timeout=5)
                    connection.request('GET', '/api/state', headers={'Authorization': 'Bearer ' + child.session.token})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    state = json.loads(response.read())
                    connection.close()
                    self.assertEqual(state['requests_used'], 0)
                    self.assertFalse(state['plans'])
                    if attempt == 0:
                        child.session.registry.reserve()
                        old_token = child.session.token
                    else:
                        self.assertNotEqual(old_token, child.session.token)
                        self.assertEqual(state['requests_reserved_unknown'], 6)
                        self.assertEqual(state['requests_remaining'], 0)
                finally:
                    hub.close()


if __name__ == '__main__':
    unittest.main()
