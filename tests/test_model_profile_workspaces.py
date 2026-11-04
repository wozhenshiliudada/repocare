"""Independent HTTP, legacy-ledger and model-workspace compatibility checks.

Only disposable repositories and synthetic credentials are used. These checks do
not call a model or replace the original UIRegistry/UISession implementations.
"""
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
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider_config import load_provider_config
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app, inspect_repository
from repo_maintainer.task_session import KIND
from test_repository_workbench import FakeGitHub
from test_task_session import transcript, raw


class ModelProfileWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='model-lanes-')
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / 'source')
        self.config = self.root / 'synthetic-provider.env'
        self.secret_a = 'synthetic-profile-A-never-return'
        self.secret_b = 'synthetic-profile-B-never-return'
        self.config.write_text('PI_MAINTENANCE_API_KEY=' + self.secret_a + '\n'
                               'PI_MAINTENANCE_BASE_URL=https://a.example.invalid/v1\n'
                               'PI_MAINTENANCE_MODEL=model-a\n', encoding='utf-8')
        self.hub = self.client = None
        self.budget = 50
        self.start()
        self.no_model = patch.object(PiProvider, 'plan', side_effect=AssertionError('Unexpected model call'))
        self.no_model.start()

    def tearDown(self):
        self.no_model.stop()
        if self.client is not None:
            self.client.close()
        if self.hub is not None:
            self.hub.close()
        self.temp.cleanup()

    def start(self):
        self.hub = RepositoryWorkbench(self.root / 'state', self.config,
                                       request_budget=self.budget, github=FakeGitHub())
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.headers = {'Authorization': 'Bearer ' + self.hub.token, 'Origin': 'http://127.0.0.1:12345'}

    def restart(self):
        self.client.close()
        self.hub.close()
        self.start()

    def profiles(self):
        response = self.client.get('/api/model-profiles', headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual('no-store', response.headers['cache-control'])
        return response.json()

    def create(self, **changes):
        value = {'revision': self.profiles()['revision'], 'action': 'create', 'name': 'Synthetic B',
                 'base_url': 'https://b.example.invalid/v1', 'model': 'model-b', 'api_key': self.secret_b,
                 **changes}
        response = self.client.post('/api/model-profiles', headers=self.headers, json=value)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()['created_profile_id']

    def default(self, profile):
        response = self.client.post('/api/model-profiles', headers=self.headers,
                                   json={'revision': self.profiles()['revision'], 'action': 'set_default',
                                         'profile_id': profile})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def scope(self, repo=None, **changes):
        repo = repo or self.repo
        return {'path': str(repo), 'revision': inspect_repository(repo)['revision'],
                'allowed_paths': ['calculator.py'], 'readonly_paths': ['tests/test_calculator.py'],
                'allowed_tests': [list(DEMO_TEST)], 'timeout_seconds': 30, 'allow_auto': False,
                'confirmed': True, **changes}

    def action(self, name, payload):
        identity = uuid.uuid4().hex
        response = self.client.post('/api/actions/' + name, headers=self.headers,
                                    json={'request_id': identity, **payload})
        self.assertEqual(202, response.status_code, response.text)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = self.client.get('/api/state', headers=self.headers).json()
            job = next(j for j in state['jobs'] if j['id'] == identity)
            if job['status'] != 'running':
                return job
            time.sleep(.01)
        self.fail('Disposable workspace operation did not finish')

    def connect(self, repo=None, **changes):
        job = self.action('connect', self.scope(repo, **changes))
        self.assertEqual('finished', job['status'], job)
        return self.hub.sessions[str(repo or self.repo)]

    def switch(self, workspace, profile):
        job = self.action('task_model_workspace', {'workspace_id': workspace.identity,
                          'profile_id': profile, 'confirmed': True})
        self.assertEqual('finished', job['status'], job)
        result = job['result']
        return self.hub.workspaces[result['workspace']['id']], result

    def project(self, repo=None):
        return self.hub.root / 'projects' / digest(os.path.normcase(str(repo or self.repo)).encode())[:16]

    def checkpoint(self, workspace):
        """Persist a valid native checkpoint via the existing controller store, with no SDK call."""
        session = workspace.session
        planner = session.task_planner
        scope, context = planner.current_scope('Synthetic historical task')
        record = planner.store.allocate(scope, context)
        directory = planner.store.directory(record['task_id'])
        (directory / 'workspace').mkdir()
        (directory / 'sessions').mkdir()
        entries = transcript(directory / 'workspace')
        for entry in entries:
            if entry['type'] == 'model_change':
                entry.update(provider=context['provider'], modelId=context['model'])
            if entry['type'] == 'message' and entry['message']['role'] == 'assistant':
                entry['message'].update(provider=context['provider'], model=context['model'])
        native = directory / 'sessions' / 'fixture.jsonl'
        native.write_bytes(raw(entries))
        attempt, _ = session.registry.reserve(maximum=2)
        session.registry.settle(attempt, {'requests': 2}, success=True)
        with planner.store.lease(record['task_id'], scope, 0) as lease:
            committed = lease.commit(native.name, entries[-1]['id'], 'b' * 64, attempt)
        unknown, _ = session.registry.reserve(maximum=3)
        session.registry.settle(unknown, {'requests': None}, success=False)
        return {'task_id': record['task_id'], 'scope': scope, 'revision': committed['revision'],
                'native': native, 'bytes': native.read_bytes(), 'budget': session.registry.budget(),
                'attempts': session.registry.load('attempt'), 'tasks': session.registry.load(KIND),
                'provider': session.provider.inner.task_identity(), 'config_file': session.provider.inner.config_file,
                'journal': session.registry.path}

    def assert_history(self, workspace, saved):
        session = workspace.session
        self.assertEqual(saved['journal'], session.registry.path)
        self.assertEqual(saved['budget'], session.registry.budget())
        self.assertEqual(saved['attempts'], session.registry.load('attempt'))
        self.assertEqual(saved['tasks'], session.registry.load(KIND))
        self.assertEqual(saved['provider'], session.provider.inner.task_identity())
        self.assertEqual(saved['config_file'], session.provider.inner.config_file)
        self.assertEqual(saved['bytes'], saved['native'].read_bytes())
        with session.task_planner.store.lease(saved['task_id'], saved['scope'], saved['revision']) as lease:
            self.assertEqual(saved['revision'], lease.descriptor()['revision'])

    def test_get_is_readonly_bounded_descriptor_and_does_not_create_profiles_or_tasks(self):
        before = {str(p.relative_to(self.hub.root)) for p in self.hub.root.rglob('*')}
        with patch('repo_maintainer.pi_provider.subprocess.Popen') as process:
            view = self.profiles()
            self.assertEqual(view, self.profiles())
            process.assert_not_called()
        self.assertEqual(before, {str(p.relative_to(self.hub.root)) for p in self.hub.root.rglob('*')})
        self.assertEqual('legacy', view['default_profile_id'])
        self.assertEqual([], list(self.hub.sessions))
        self.assertEqual([], list(self.hub.jobs))
        self.assertGreater(view['max_profiles'], 0)
        expected = {'id', 'name', 'base_url', 'model', 'key_set', 'legacy', 'selectable', 'archived',
                    'context_window_tokens', 'max_output_tokens', 'limits_explicit',
                    'reasoning_protocol', 'reasoning_effort', 'reasoning_explicit', 'reasoning_levels'}
        self.assertEqual(expected, set(view['profiles'][0]))
        self.assertEqual('model-a', view['profiles'][0]['model'])
        self.assertNotIn(self.secret_a, json.dumps(view))
        self.assertNotIn(str(self.config), json.dumps(view))

    def test_profile_http_auth_origin_host_content_limits_and_duplicate_keys(self):
        payload = {'revision': self.profiles()['revision'], 'action': 'create', 'name': 'Synthetic B',
                   'base_url': 'https://b.example.invalid/v1', 'model': 'model-b', 'api_key': self.secret_b}
        before = self.profiles()
        for method in ('get', 'post'):
            call = getattr(self.client, method)
            args = {'json': payload} if method == 'post' else {}
            self.assertEqual(401, call('/api/model-profiles', **args).status_code)
            for extra in ({'Origin': 'https://foreign.invalid'}, {'Host': 'foreign.invalid'},
                          {'Sec-Fetch-Site': 'cross-site'}):
                self.assertEqual(403, call('/api/model-profiles', headers={**self.headers, **extra}, **args).status_code)
        self.assertEqual(415, self.client.post('/api/model-profiles', headers=self.headers, content='{}').status_code)
        headers = {**self.headers, 'Content-Type': 'application/json'}
        self.assertEqual(413, self.client.post('/api/model-profiles', headers=headers, content='x' * 16385).status_code)
        duplicate = json.dumps(payload)[:-1] + ',"api_key":"duplicate"}'
        self.assertEqual(409, self.client.post('/api/model-profiles', headers=headers, content=duplicate).status_code)
        self.assertEqual(409, self.client.get('/api/model-profiles?path=outside', headers=self.headers).status_code)
        self.assertEqual(before, self.profiles())
        self.assertFalse(self.hub.jobs)

    def test_profile_create_default_and_errors_keep_credentials_out_of_jobs_state_and_service(self):
        original = self.config.read_bytes()
        profile = self.create()
        self.assertEqual(profile, self.create(name='Same config different display name'))
        self.default(profile)
        stale = {'revision': '0' * 64, 'action': 'set_default', 'profile_id': 'legacy'}
        bad = [stale, {'revision': self.profiles()['revision'], 'action': 'set_default', 'profile_id': '../outside'},
               {'revision': self.profiles()['revision'], 'action': 'create', 'name': 'Leak',
                'base_url': 'https://b.example.invalid/v1', 'model': 'bad\nmodel', 'api_key': self.secret_b}]
        before = self.profiles()
        for value in bad:
            response = self.client.post('/api/model-profiles', headers=self.headers, json=value)
            self.assertEqual(409, response.status_code, response.text)
            self.assertNotIn(self.secret_b, response.text)
            self.assertEqual(before, self.profiles())
        for endpoint in ('/api/model-profiles', '/api/state', '/api/service'):
            response = self.client.get(endpoint, headers=self.headers)
            for secret in (self.secret_a, self.secret_b):
                self.assertNotIn(secret, response.text)
        self.assertEqual(original, self.config.read_bytes())
        self.assertFalse(self.hub.jobs)
        self.restart()
        self.assertEqual(before, self.profiles())

    def test_legacy_50_and_100_budget_native_tasks_and_original_provider_survive_add_default_switch_restart(self):
        # Both historical ceilings are independent cases, not a changed running ledger.
        for budget in (50, 100):
            with self.subTest(ceiling=budget):
                if budget != self.budget:
                    self.client.close()
                    self.hub.close()
                    self.root = Path(self.temp.name) / 'budget100'
                    self.root.mkdir()
                    self.repo = create_fixture(self.root / 'source')
                    self.budget = budget
                    self.start()
                a = self.connect()
                self.assertEqual('legacy', a.view()['model_profile_id'])
                self.assertEqual('model-a', a.view()['model_name'])
                saved_a = self.checkpoint(a)
                project_bytes = (self.project() / 'project.json').read_bytes()
                provider_bytes = self.config.read_bytes()
                profile_b = self.create()
                self.default(profile_b)
                self.assert_history(a, saved_a)
                self.assertEqual(project_bytes, (self.project() / 'project.json').read_bytes())
                self.assertEqual(provider_bytes, self.config.read_bytes())
                self.assertFalse(self.client.get('/api/provider-config', headers=self.headers).json()['editable'])
                b, first = self.switch(a, profile_b)
                self.assertFalse(first['reused'])
                self.assertIsNot(a.session, b.session)
                self.assertEqual(profile_b, b.view()['model_profile_id'])
                self.assertEqual('model-b', b.view()['model_name'])
                self.assertEqual(a.session.policy, b.session.policy)
                self.assertEqual(self.project() / 'model-workspaces' / profile_b / 'runs', b.session.runs_dir)
                self.assertNotEqual(saved_a['journal'], b.session.registry.path)
                self.assertEqual({'request_budget': budget, 'requests_used': 0,
                                  'requests_reserved_unknown': 0, 'requests_remaining': budget}, b.session.registry.budget())
                saved_b = self.checkpoint(b)
                back, result = self.switch(b, 'legacy')
                self.assertTrue(result['reused'])
                self.assertIs(a, back)
                self.assert_history(a, saved_a)
                self.assert_history(b, saved_b)
                old_tokens = (a.session.token, b.session.token)
                self.restart()
                self.assertEqual(profile_b, self.profiles()['default_profile_id'])
                a = self.connect()  # Old connect omits profile; old primary must stay A despite default B.
                self.assert_history(a, saved_a)
                self.assertEqual(project_bytes, (self.project() / 'project.json').read_bytes())
                b, _ = self.switch(a, profile_b)
                self.assert_history(b, saved_b)
                self.assertNotEqual(old_tokens, (a.session.token, b.session.token))
                again, result = self.switch(b, 'legacy')
                self.assertIs(a, again)
                self.assertTrue(result['reused'])

    def test_new_primary_profile_remains_selected_when_default_changes_and_on_restart(self):
        profile = self.create()
        primary = self.connect(profile_id=profile)
        self.assertEqual(profile, primary.view()['model_profile_id'])
        self.assertEqual(self.project() / 'runs', primary.session.runs_dir)
        self.assertEqual(profile, json.loads((self.project() / 'project.json').read_text())['model_profile_id'])
        saved = self.checkpoint(primary)
        self.default('legacy')
        self.restart()
        primary = self.connect()
        self.assertEqual(profile, primary.view()['model_profile_id'])
        self.assert_history(primary, saved)
        a, result = self.switch(primary, 'legacy')
        self.assertFalse(result['reused'])
        self.assertNotEqual(a.session.runs_dir, primary.session.runs_dir)
        primary_again, result = self.switch(a, profile)
        self.assertTrue(result['reused'])
        self.assertIs(primary, primary_again)

    def test_first_run_can_select_new_profile_without_configuring_legacy_provider(self):
        self.client.close()
        self.hub.close()
        self.hub = RepositoryWorkbench(self.root / 'unconfigured-state', request_budget=self.budget,
                                       github=FakeGitHub())
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.headers = {'Authorization': 'Bearer ' + self.hub.token, 'Origin': 'http://127.0.0.1:12345'}
        self.assertFalse(self.hub.provider_config.exists())
        profile = self.create()
        self.default(profile)
        selected = self.connect(profile_id=profile)
        self.assertEqual(profile, selected.view()['model_profile_id'])
        self.assertEqual('model-b', selected.session.provider.inner.task_identity()[0]['model'])
        self.assertFalse(self.hub.provider_config.exists())
        self.assertEqual(0, selected.session.registry.budget()['requests_used'])
        self.assertEqual(self.secret_b, load_provider_config(selected.session.provider.inner.config_file)['PI_MAINTENANCE_API_KEY'])

    def test_workspace_tokens_are_separate_and_bad_selection_inputs_do_not_mutate_sessions_or_ledgers(self):
        a = self.connect()
        saved_a = self.checkpoint(a)
        profile = self.create()
        b, _ = self.switch(a, profile)
        saved_b = self.checkpoint(b)
        for source, other in ((a, b), (b, a)):
            own = {**self.headers, 'Authorization': 'Bearer ' + source.session.token}
            self.assertEqual(200, self.client.get(source.view()['api_prefix'] + 'state', headers=own).status_code)
            self.assertEqual(401, self.client.get(other.view()['api_prefix'] + 'state', headers=own).status_code)
        self.assertEqual(401, self.client.get(a.view()['api_prefix'] + 'state', headers=self.headers).status_code)
        before_sessions = list(self.hub.sessions)
        before_files = {str(p.relative_to(self.project())) for p in self.project().rglob('*')}
        for change in ({'workspace_id': 'f' * 32}, {'profile_id': 'e' * 32}, {'profile_id': '../outside'},
                       {'workspace_id': '../outside'}, {'confirmed': False}, {'api_key': self.secret_b},
                       {'path': str(self.root / 'outside')}, {'revision': '0' * 64}):
            payload = {'workspace_id': a.identity, 'profile_id': profile, 'confirmed': True, **change}
            response = self.client.post('/api/actions/task_model_workspace', headers=self.headers,
                                       json={'request_id': uuid.uuid4().hex, **payload})
            self.assertIn(response.status_code, (202, 409), response.text)
            if response.status_code == 202:
                deadline = time.monotonic() + 10
                identity = response.json()['id']
                while time.monotonic() < deadline:
                    job = next(j for j in self.hub.state()['jobs'] if j['id'] == identity)
                    if job['status'] != 'running':
                        break
                    time.sleep(.01)
                self.assertEqual('failed', job['status'], job)
            self.assertEqual(before_sessions, list(self.hub.sessions))
            self.assertEqual(before_files, {str(p.relative_to(self.project())) for p in self.project().rglob('*')})
            self.assert_history(a, saved_a)
            self.assert_history(b, saved_b)
            self.assertNotIn(self.secret_b, json.dumps(self.hub.state()))
        state = self.hub.state()
        self.assertEqual({'legacy', profile}, {w['model_profile_id'] for w in state['workspaces']})

    def test_saved_profile_tampering_blocks_existing_workspace_before_model_or_new_task(self):
        a = self.connect()
        profile = self.create()
        b, _ = self.switch(a, profile)
        saved = self.checkpoint(b)
        config = b.session.provider.inner.config_file
        original = config.read_bytes()
        self.assertIn(self.secret_b.encode(), original)
        config.write_bytes(original.replace(self.secret_b.encode(), b'synthetic-replacement-key'))
        before_budget = b.session.registry.budget()
        before_tasks = b.session.registry.load(KIND)
        before_attempts = b.session.registry.load('attempt')
        with patch.object(PiProvider, 'plan', side_effect=AssertionError('Tampered model must not run')) as model:
            with self.assertRaises(SafetyError):
                self.hub.executor.submit(b.session._operate, 'plan',
                                         {'issue': 'Synthetic new task after credential tamper'}).result()
            model.assert_not_called()
        self.assertEqual(before_budget, b.session.registry.budget())
        self.assertEqual(before_tasks, b.session.registry.load(KIND))
        self.assertEqual(before_attempts, b.session.registry.load('attempt'))
        self.assertEqual(saved['bytes'], saved['native'].read_bytes())
        self.assertNotIn('synthetic-replacement-key', json.dumps(self.hub.state()))

    def test_default_readiness_follows_profile_across_restart_while_monitor_provider_stays_original(self):
        a = self.connect()
        policy = a.session.policy
        before = self.hub.monitor._provider(policy)
        original_identity = before.task_identity()
        profile = self.create()
        self.default(profile)
        selected_path = self.hub.model_profiles.resolve(profile)['path']
        self.assertEqual(selected_path, self.hub.readiness.provider_config)
        self.assertEqual(self.config, self.hub.provider_config)
        self.assertEqual(original_identity, self.hub.monitor._provider(policy).task_identity())
        self.restart()
        self.assertEqual(selected_path, self.hub.readiness.provider_config)
        self.assertEqual(self.config, self.hub.monitor._provider(policy).config_file)
        self.assertEqual(original_identity, self.hub.monitor._provider(policy).task_identity())

    def test_missing_project_metadata_cannot_rebind_old_unknown_ledger_or_create_another_legacy_lane(self):
        a = self.connect()
        saved = self.checkpoint(a)
        profile = self.create()
        self.default(profile)
        project = self.project()
        (project / 'project.json').unlink()  # Disposable corruption fixture, never live state.
        original_journal = saved['journal'].read_bytes()
        owner_lock = saved['journal'].parent / 'owner.lock'
        # FileLock may remove its transient owner file on a normal service close.
        def durable_files():
            return {str(p.relative_to(project)) for p in project.rglob('*') if p != owner_lock}
        original_files = durable_files()

        def reject_both_connect_forms():
            for selection in ({}, {'profile_id': 'legacy'}):
                job = self.action('connect', self.scope(**selection))
                self.assertEqual('failed', job['status'], job)
                self.assertFalse((project / 'project.json').exists())
                self.assertFalse((project / 'model-workspaces').exists())
                self.assertEqual(original_files, durable_files())
                self.assertEqual(original_journal, saved['journal'].read_bytes())
                self.assertEqual(saved['bytes'], saved['native'].read_bytes())

        reject_both_connect_forms()
        self.assert_history(a, saved)
        self.restart()
        reject_both_connect_forms()
        self.assertFalse(self.hub.sessions)


if __name__ == '__main__':
    unittest.main()
