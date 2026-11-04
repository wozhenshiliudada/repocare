"""Real workbench HTTP entrypoint boundaries; no repository or external model."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import tomllib
import unittest
from unittest.mock import Mock, patch
import uuid

from starlette.testclient import TestClient

from repo_maintainer import cli
from repo_maintainer.assistant import AssistantControl
from repo_maintainer.help_docs import help_document
from repo_maintainer.models import Policy
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app
from repo_maintainer.ui_registry import UIRegistry


class ExperienceEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='repocare-experience-http-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hub = RepositoryWorkbench(self.root/'workbench', request_budget=6, ui='legacy')
        self.addCleanup(self.hub.close)
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer '+self.hub.token}
        self.profile = self.hub.model_profiles.create({'revision': self.hub.model_profiles.view()['revision'],
            'action': 'create', 'name': 'entrypoint synthetic profile', 'api_key': 'synthetic-entrypoint-key',
            'base_url': 'https://provider.example.invalid/v1', 'model': 'entrypoint-fixture'})['created_profile_id']
        self.repair_native = self.root/'retained-repair-native.jsonl'
        self.repair_native.write_bytes(b'{"original":"private fixture Native bytes"}\n')
        self.original_native = self.repair_native.read_bytes()

    def get(self, path, **kwargs):
        return self.client.get(path, headers={**self.headers, **kwargs.pop('headers', {})}, **kwargs)

    def post(self, path, value=None, *, headers=None, content=None):
        request_headers = {**self.headers, **(headers or {})}
        if content is not None:
            request_headers.setdefault('Content-Type', 'application/json')
            return self.client.post(path, headers=request_headers, content=content)
        return self.client.post(path, headers=request_headers, json=value)

    def wait_assistant(self, identity):
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            response = self.get('/api/assistant/state')
            self.assertEqual(response.status_code, 200)
            state = response.json()
            result = next((job for job in state['jobs'] if job['id'] == identity), None)
            if result is not None and result['status'] != 'running' and state['active'] is None:
                return result
            time.sleep(.01)
        self.fail('Assistant operation did not finish within the bounded wait')

    def create(self, profile=None, request_id=None):
        value = {'request_id': request_id or uuid.uuid4().hex, 'profile_id': profile or self.profile}
        response = self.post('/api/assistant/actions/create', value)
        self.assertEqual(response.status_code, 202, response.text)
        result = self.wait_assistant(value['request_id'])
        self.assertEqual(result['status'], 'finished', result)
        return result, value

    @staticmethod
    def fake_conversation(control, profile, directory, record, payload, allowance):
        sessions = directory/'sessions'
        sessions.mkdir(parents=True, exist_ok=True)
        name = record['native_file'] or 'entrypoint-fixture.jsonl'
        with (sessions/name).open('ab') as stream:
            stream.write(b'{"message":"synthetic unit response"}\n')
        return {'schema_version': 1, 'requests': 1, 'text': '仅供讨论，未执行。',
                'active_tools': ['get_help', 'propose_form_values'], 'proposal': None, 'native_file': name}

    def assert_no_repository_or_native_change(self):
        self.assertEqual(self.hub.sessions, {})
        self.assertEqual(self.hub.workspaces, {})
        self.assertFalse(any(self.root.rglob('.git')))
        self.assertEqual(self.repair_native.read_bytes(), self.original_native)

    def test_help_is_same_packaged_guide_bytes_hash_and_original_332_ids(self):
        package = Path(__file__).resolve().parents[1]/'src/repo_maintainer/help_assets'
        original = json.loads((Path(__file__).resolve().parents[1]/'frontend/src/capabilities.json').read_text(encoding='utf-8'))
        for language in ('zh-CN', 'en'):
            response = self.get('/api/help?language='+language)
            self.assertEqual(response.status_code, 200)
            value = response.json()
            self.assertEqual(value, help_document(language))
            content = (package/f'user-guide.{language}.md').read_bytes()
            self.assertEqual(value['content'].encode('utf-8'), content)
            self.assertEqual(value['sha256'], hashlib.sha256(content).hexdigest())
            self.assertEqual(len(value['catalog']), 332)
            self.assertEqual([row['id'] for row in value['catalog']], [row['id'] for row in original])
        self.assertFalse((self.hub.root/'assistant').exists())

    def test_help_and_assistant_require_original_auth_host_origin(self):
        for path in ('/api/help', '/api/assistant/state'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                self.assertEqual(self.get(path, headers={'Authorization': 'Bearer invalid'}).status_code, 401)
                self.assertEqual(self.get(path, headers={'Origin': 'https://foreign.example.invalid'}).status_code, 403)
                self.assertEqual(self.get(path, headers={'Host': 'localhost:12345'}).status_code, 403)
                self.assertEqual(self.get(path, headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.assertFalse((self.hub.root/'assistant').exists())

    def test_readonly_language_and_query_schema_do_not_create_assistant(self):
        for query in ('language=fr', 'language=zh-CN&language=en', 'path=C:/private', 'language=en&unknown=1'):
            with self.subTest(query=query):
                self.assertEqual(self.get('/api/help?'+query).status_code, 409)
        with patch.object(AssistantControl, '_converse', autospec=True) as converse:
            for _ in range(2):
                response = self.get('/api/assistant/state')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['sessions'], [])
                self.assertEqual(response.json()['jobs'], [])
                self.assertEqual(response.json()['budgets'], {})
            self.assertEqual(self.get('/api/assistant/state?path=C:/private').status_code, 409)
            converse.assert_not_called()
        self.assertFalse((self.hub.root/'assistant').exists())
        self.assert_no_repository_or_native_change()

    def test_create_and_send_reject_extra_fields_duplicate_json_and_large_body(self):
        create = {'request_id': uuid.uuid4().hex, 'profile_id': self.profile}
        send = {'request_id': uuid.uuid4().hex, 'session_id': 'b'*32, 'text': 'test', 'form_context': None, 'language': 'zh-CN'}
        for action, value in (('create', create), ('send', send)):
            path = '/api/assistant/actions/'+action
            for change in ({'command': 'execute'}, {'repository': 'C:/private'}, {'decision': 'approve'}, {'confirmed': True}):
                self.assertEqual(self.post(path, dict(value, **change)).status_code, 409)
            duplicate = json.dumps(value)[:-1]+',"request_id":"'+'c'*32+'"}'
            self.assertEqual(self.post(path, content=duplicate.encode()).status_code, 409)
            self.assertEqual(self.post(path, content=b' '*256001).status_code, 413)
            self.assertEqual(self.post(path, content=b'{}', headers={'Content-Type': 'text/plain'}).status_code, 415)
            self.assertEqual(self.post(path+'?path=C:/private', value).status_code, 409)
        for value in (dict(create, profile_id=[]), dict(create, request_id=True),
                      {'request_id': create['request_id']}):
            self.assertEqual(self.post('/api/assistant/actions/create', value).status_code, 409)
        invalid_form = {'schema_version': 1, 'form_id': 'd'*32, 'revision': 'e'*64,
                        'fields': [{'id': 'f'*32, 'label': '任务描述', 'type': [], 'value': ''}]}
        for value in (dict(send, text=[]), dict(send, language=[]), dict(send, form_context=invalid_form)):
            self.assertEqual(self.post('/api/assistant/actions/send', value).status_code, 409)
        self.assertEqual(self.post('/api/assistant/actions/approve', create).status_code, 409)
        self.assertFalse((self.hub.root/'assistant').exists())
        self.assert_no_repository_or_native_change()

    def test_repository_free_create_is_zero_model_and_duplicate_id_is_original_job(self):
        profile_bytes = {path: path.read_bytes() for path in (self.hub.root/'model-profiles').rglob('*') if path.is_file()}
        with patch.object(AssistantControl, '_converse', autospec=True) as converse:
            first, payload = self.create()
            second = self.post('/api/assistant/actions/create', payload)
            self.assertEqual(second.status_code, 202)
            duplicate = self.wait_assistant(payload['request_id'])
            self.assertEqual(first['session_id'], duplicate['session_id'])
            self.assertEqual(len(self.get('/api/assistant/state').json()['sessions']), 1)
            converse.assert_not_called()
        self.assertFalse((self.hub.root/'assistant/models').exists())
        self.assertEqual({path: path.read_bytes() for path in profile_bytes}, profile_bytes)
        self.assert_no_repository_or_native_change()

    def test_send_duplicate_uses_original_account_and_does_not_repeat_conversation(self):
        created, _ = self.create()
        payload = {'request_id': uuid.uuid4().hex, 'session_id': created['session_id'], 'text': '只讨论当前草稿',
                   'form_context': None, 'language': 'zh-CN'}
        with patch.object(AssistantControl, '_converse', autospec=True, side_effect=self.fake_conversation) as converse:
            self.assertEqual(self.post('/api/assistant/actions/send', payload).status_code, 202)
            first = self.wait_assistant(payload['request_id'])
            self.assertEqual(first['status'], 'finished', first)
            before = self.get('/api/assistant/state').json()
            self.assertEqual(self.post('/api/assistant/actions/send', payload).status_code, 202)
            self.assertEqual(self.wait_assistant(payload['request_id']), first)
            self.assertEqual(self.post('/api/assistant/actions/send', dict(payload, text='同ID更换内容')).status_code, 409)
            self.assertEqual(converse.call_count, 1)
            self.assertEqual(self.get('/api/assistant/state').json(), before)
            self.assertEqual(before['budgets'][self.profile]['requests_used'], 1)
        self.assert_no_repository_or_native_change()

    def test_service_counts_active_chat_as_busy_and_original_stop_refuses(self):
        created, _ = self.create()
        entered, release = threading.Event(), threading.Event()

        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(10))
            return self.fake_conversation(*args)

        payload = {'request_id': uuid.uuid4().hex, 'session_id': created['session_id'], 'text': '只讨论',
                   'form_context': None, 'language': 'zh-CN'}
        stopped = Mock()
        self.hub.stop_callback = stopped
        with patch.object(AssistantControl, '_converse', autospec=True, side_effect=blocked):
            try:
                self.assertEqual(self.post('/api/assistant/actions/send', payload).status_code, 202)
                self.assertTrue(entered.wait(5))
                service = self.get('/api/service').json()
                self.assertTrue(service['workspace_active'])
                self.assertEqual(service['assistant_active'], payload['request_id'])
                request_id = uuid.uuid4().hex
                response = self.post('/api/actions/stop_workbench', {'request_id': request_id, 'revision': service['stop_revision']})
                self.assertEqual(response.status_code, 202)
                deadline = time.monotonic()+5
                while time.monotonic() < deadline:
                    operation = next(job for job in self.get('/api/state').json()['jobs'] if job['id'] == request_id)
                    if operation['status'] != 'running':
                        break
                    time.sleep(.01)
                self.assertEqual(operation['status'], 'failed', operation)
                stopped.assert_not_called()
            finally:
                release.set()
                self.wait_assistant(payload['request_id'])
        self.assertFalse(self.get('/api/service').json()['workspace_active'])
        # The accepted original stop closes the admission gate before the
        # listener drains; a new repository-free chat must not enter that gap.
        service = self.get('/api/service').json()
        stop_id = uuid.uuid4().hex
        response = self.post('/api/actions/stop_workbench', {'request_id': stop_id, 'revision': service['stop_revision']})
        self.assertEqual(response.status_code, 202)
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            operation = next(job for job in self.get('/api/state').json()['jobs'] if job['id'] == stop_id)
            if operation['status'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(operation['status'], 'finished', operation)
        self.assertTrue(self.hub.stopping)
        stopped.assert_called_once()
        previous_jobs = self.get('/api/assistant/state').json()['jobs']
        self.assertEqual(self.post('/api/assistant/actions/create',
            {'request_id': uuid.uuid4().hex, 'profile_id': self.profile}).status_code, 409)
        self.assertEqual(self.get('/api/assistant/state').json()['jobs'], previous_jobs)
        self.assert_no_repository_or_native_change()

    def provider_save(self, key):
        revision = self.get('/api/provider-config').json()['revision']
        return self.post('/api/provider-config', {'revision': revision, 'api_key': key,
            'base_url': 'https://legacy.example.invalid/v1', 'model': 'legacy-fixture'})

    def test_legacy_chat_history_blocks_legacy_provider_overwrite(self):
        self.assertEqual(self.provider_save('synthetic-original-legacy-key').status_code, 200)
        original = self.hub.provider_config.read_bytes()
        self.create(profile='legacy')
        self.assertFalse(self.get('/api/provider-config').json()['editable'])
        self.assertEqual(self.provider_save('synthetic-replacement-legacy-key').status_code, 409)
        self.assertEqual(self.hub.provider_config.read_bytes(), original)
        self.assert_no_repository_or_native_change()

    def test_nonlegacy_chat_does_not_block_unconfigured_legacy_first_setup(self):
        self.assertFalse(self.hub.provider_config.exists())
        self.create()
        self.assertTrue(self.get('/api/provider-config').json()['editable'])
        self.assertEqual(self.provider_save('synthetic-first-legacy-key').status_code, 200)
        self.assertTrue(self.hub.provider_config.is_file())
        self.assert_no_repository_or_native_change()

    def test_legacy_binding_survives_more_than_one_page_of_newer_nonlegacy_chats(self):
        self.assertEqual(self.provider_save('synthetic-paged-original-legacy-key').status_code, 200)
        original = self.hub.provider_config.read_bytes()
        self.create(profile='legacy')
        # Every row is created by the real HTTP/controller path. An older
        # binding must remain effective after it leaves the 100-row UI page.
        with patch.object(AssistantControl, '_converse', autospec=True) as converse:
            for _ in range(100):
                self.create()
            converse.assert_not_called()
        self.assertFalse(self.get('/api/provider-config').json()['editable'])
        self.assertEqual(self.provider_save('synthetic-paged-replacement-key').status_code, 409)
        self.assertEqual(self.hub.provider_config.read_bytes(), original)
        self.assert_no_repository_or_native_change()

    def test_damaged_assistant_locks_editing_but_preserves_original_repair_provider_and_journal(self):
        self.assertEqual(self.provider_save('synthetic-retained-original-key').status_code, 200)
        provider_bytes = self.hub.provider_config.read_bytes()
        repair_root = self.root/'existing-original-repair'
        registry = UIRegistry(repair_root, {'provider': {'request_budget': 10}})
        try:
            used, _ = registry.reserve(maximum=2)
            registry.settle(used, {'requests': 2}, success=True)
            unknown, _ = registry.reserve(maximum=3)
            registry.settle(unknown, {'requests': None}, success=False)
            self.assertEqual(registry.budget(), {'request_budget': 10, 'requests_used': 2,
                'requests_reserved_unknown': 3, 'requests_remaining': 5})
        finally:
            registry.close()
        native = repair_root/'native.jsonl'
        native.write_bytes(b'{"original":"retained repair Native fixture"}\n')
        original_repair = {path.relative_to(repair_root): path.read_bytes()
                           for path in repair_root.rglob('*') if path.is_file()}
        assistant = self.hub.root/'assistant'
        database = assistant/'catalog/ui-state/registry.sqlite3'
        database.parent.mkdir(parents=True)
        database.write_bytes(b'damaged assistant SQLite; never a repair journal')
        marker = self.hub.root/'assistant-binding.json'
        policy = Policy(('calculator.py',), (('{python}', '-m', 'unittest'),))

        def assert_original_provider_available():
            response = self.get('/api/provider-config')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['configured'])
            self.assertFalse(response.json()['editable'])
            self.hub.provider_settings.require_ready()
            provider = self.hub.monitor._provider(policy)
            self.assertIsInstance(provider, PiProvider)
            self.assertEqual(provider.config_file, self.hub.provider_config)
            self.assertEqual(self.hub.provider_config.read_bytes(), provider_bytes)
            self.assertEqual({path: (repair_root/path).read_bytes() for path in original_repair}, original_repair)

        # Without an already connected repair, malformed marker and malformed
        # catalog both conservatively lock edits while the real config works.
        for marker_content in ('not-json', json.dumps({'schema_version': 1, 'root': str(assistant)})):
            with self.subTest(marker=marker_content):
                marker.write_text(marker_content, encoding='utf-8')
                assert_original_provider_available()
        # Existing repair ownership must short-circuit assistant reads entirely.
        project = self.hub.root/'projects'/('a'*16)
        project.mkdir(parents=True)
        (project/'project.json').write_text('{}', encoding='utf-8')
        with patch('repo_maintainer.assistant.catalog_integrity', side_effect=AssertionError('Assistant must not gate existing repair')) as inspecting:
            assert_original_provider_available()
            inspecting.assert_not_called()
        self.assert_no_repository_or_native_change()


class ExperienceCliEntrypointTests(unittest.TestCase):
    def test_root_help_setup_gateway_mcp_and_console_alias_are_registered(self):
        for arguments in (['--help'], ['setup', '--help'], ['gateway', '--help'], ['mcp', '--help']):
            output = io.StringIO()
            with self.subTest(arguments=arguments), contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exited:
                cli.main(arguments)
            self.assertEqual(exited.exception.code, 0)
            if arguments == ['--help']:
                for name in ('setup', 'gateway', 'mcp', 'workbench'):
                    self.assertIn(name, output.getvalue())
        project = tomllib.loads((Path(__file__).resolve().parents[1]/'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(project['project']['scripts']['repocare'], 'repo_maintainer.cli:main')
        self.assertEqual(project['project']['scripts']['repo-maintainer'], 'repo_maintainer.cli:main')
        self.assertIn('help_assets/*.md', project['tool']['setuptools']['package-data']['repo_maintainer'])


if __name__ == '__main__':
    unittest.main()
