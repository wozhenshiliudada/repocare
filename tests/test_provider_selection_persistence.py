"""Provider editor persistence regression using disposable Hub state and fake keys only.

No provider directory or model endpoint is contacted. These fixtures exercise the
existing immutable-profile API; a provider group is a UI projection, not new state.
"""
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from repo_maintainer.assistant import AssistantControl
from repo_maintainer.demo import create_fixture
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider_config import load_provider_config
import test_model_profile_workspaces as workspaces


class ProviderSelectionPersistenceTests(unittest.TestCase):
    start = workspaces.ModelProfileWorkspaceTests.start
    restart = workspaces.ModelProfileWorkspaceTests.restart
    profiles = workspaces.ModelProfileWorkspaceTests.profiles
    scope = workspaces.ModelProfileWorkspaceTests.scope
    action = workspaces.ModelProfileWorkspaceTests.action
    connect = workspaces.ModelProfileWorkspaceTests.connect
    project = workspaces.ModelProfileWorkspaceTests.project
    checkpoint = workspaces.ModelProfileWorkspaceTests.checkpoint
    assert_history = workspaces.ModelProfileWorkspaceTests.assert_history

    endpoint = 'https://fire-provider.example.invalid/api/v3'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='provider-selection-')
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root/'source')
        self.config = self.root/'synthetic-original-huoshan.env'
        self.config.write_text('PI_MAINTENANCE_API_KEY="synthetic-original-huoshan-key"\n'
                               f'PI_MAINTENANCE_BASE_URL="{self.endpoint}"\n'
                               'PI_MAINTENANCE_MODEL="synthetic-huoshan-original"\n', encoding='utf-8')
        self.hub = self.client = None
        self.budget = 50
        self.start()
        self.no_model = patch.object(PiProvider, 'plan', side_effect=AssertionError('Unexpected model call'))
        self.no_assistant_model = patch.object(AssistantControl, '_converse', side_effect=AssertionError('Unexpected assistant call'))
        self.model_mock = self.no_model.start()
        self.assistant_mock = self.no_assistant_model.start()

    def tearDown(self):
        try:
            self.model_mock.assert_not_called()
            self.assistant_mock.assert_not_called()
            if self.client is not None: self.client.close()
            if self.hub is not None: self.hub.close()
        finally:
            self.no_model.stop()
            self.no_assistant_model.stop()
            self.temp.cleanup()

    def post(self, action, **value):
        return self.client.post('/api/model-profiles', headers=self.headers,
            json={'revision': self.profiles()['revision'], 'action': action, **value})

    def save(self, model, **changes):
        response = self.post('create', **{'name':'Fire '+model, 'base_url':self.endpoint, 'model':model,
            'context_window_tokens':65536, 'max_output_tokens':2048,
            'api_key':'synthetic-shared-fire-key', **changes})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()['created_profile_id']

    def assistant_create(self, profile):
        control = self.hub.assistant
        result = control.submit('create', {'request_id': uuid.uuid4().hex, 'profile_id': profile})
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            job = next(row for row in control.view()['jobs'] if row['id']==result['id'])
            if job['status']!='running' and not control.active:
                self.assertEqual('finished', job['status'], job)
                return job['session_id']
            time.sleep(.01)
        self.fail('Local assistant metadata operation timed out')

    @staticmethod
    def durable(root):
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*')
                if path.is_file() and path.name != 'owner.lock'}

    def test_four_selected_models_persist_after_hub_restart_without_changing_original_huoshan(self):
        original_workspace = self.connect()
        history = self.checkpoint(original_workspace)
        old_assistant = self.assistant_create('legacy')
        originals = self.durable(self.project())
        assistant_bytes = self.durable(self.hub.assistant.root)
        config_bytes = self.config.read_bytes()
        monitor = self.hub.monitor._provider(original_workspace.session.policy).task_identity()
        before = self.profiles()
        unchanged_legacy = self.post('create', name='Same implicit original config', base_url=self.endpoint,
            model='synthetic-huoshan-original', source_profile_id='legacy', api_key='')
        self.assertEqual(200, unchanged_legacy.status_code, unchanged_legacy.text)
        self.assertEqual('legacy', unchanged_legacy.json()['created_profile_id'])
        self.assertEqual(before['revision'], unchanged_legacy.json()['revision'])
        self.assertFalse(self.hub.model_profiles.root.exists())
        ids = [self.save('synthetic-fire-'+str(index)) for index in range(4)]
        self.assertEqual(4, len(set(ids)))
        self.assertEqual('legacy', self.profiles()['default_profile_id'])
        self.assertEqual(originals, self.durable(self.project()))
        self.assertEqual(assistant_bytes, self.durable(self.hub.assistant.root))
        self.assert_history(original_workspace, history)
        self.restart()
        view = self.profiles()
        self.assertEqual(set(ids), {row['id'] for row in view['profiles'] if not row['legacy']})
        self.assertTrue(all(row['selectable'] for row in view['profiles']))
        self.assertEqual('legacy', view['default_profile_id'])
        self.assertEqual(config_bytes, self.config.read_bytes())
        self.assertEqual(assistant_bytes, self.durable(self.hub.assistant.root))
        self.assertEqual([old_assistant], [row['id'] for row in self.hub.assistant.view()['sessions']])
        reopened = self.connect()
        self.assert_history(reopened, history)
        self.assertEqual(originals, self.durable(self.project()))
        self.assertEqual(monitor, self.hub.monitor._provider(reopened.session.policy).task_identity())
        # Saving model settings creates no per-model repair workspace or assistant allowance.
        self.assertFalse((self.project()/'model-workspaces').exists())
        self.assertFalse((self.hub.assistant.root/'models').exists())

    def test_same_endpoint_edit_reuses_exact_profiles_own_key_and_never_group_first_key(self):
        first = self.save('synthetic-fire-0', api_key='synthetic-key-first')
        second = self.save('synthetic-fire-1', api_key='synthetic-key-second')
        self.save('synthetic-fire-2', api_key='synthetic-key-third')
        self.save('synthetic-fire-3', api_key='synthetic-key-fourth')
        originals = {row['id']: self.hub.model_profiles.resolve(row['id'])['path'].read_bytes()
                     for row in self.profiles()['profiles']}
        response = self.post('create', name='Edited second', base_url=self.endpoint, model='synthetic-fire-1',
            source_profile_id=second, api_key='', context_window_tokens=131072, max_output_tokens=4096)
        self.assertEqual(200, response.status_code, response.text)
        edited = response.json()['created_profile_id']
        self.assertNotIn(edited, originals)
        resolved = self.hub.model_profiles.resolve(edited)
        config = load_provider_config(resolved['path'])
        self.assertEqual('synthetic-key-second', config['PI_MAINTENANCE_API_KEY'])
        self.assertEqual('synthetic-key-first', load_provider_config(self.hub.model_profiles.resolve(first)['path'])['PI_MAINTENANCE_API_KEY'])
        self.assertEqual(originals, {identity:self.hub.model_profiles.resolve(identity)['path'].read_bytes()
                                     for identity in originals})
        before = self.hub.model_profiles.catalog_path.read_bytes()
        denied = self.post('create', name='Wrong endpoint', base_url='https://another.example.invalid/v1',
            model='synthetic-fire-1', source_profile_id=second, api_key='')
        self.assertEqual(409, denied.status_code)
        self.assertEqual(before, self.hub.model_profiles.catalog_path.read_bytes())
        self.restart()
        self.assertEqual('synthetic-key-second', load_provider_config(self.hub.model_profiles.resolve(edited)['path'])['PI_MAINTENANCE_API_KEY'])
        self.assertEqual('legacy', self.profiles()['default_profile_id'])

    def test_unchanged_save_is_identity_noop_and_archived_old_version_keeps_history(self):
        profile = self.save('synthetic-fire-history')
        workspace = self.connect(profile_id=profile)
        saved = self.checkpoint(workspace)
        assistant = self.assistant_create(profile)
        protected = self.durable(self.project())
        catalog = self.hub.model_profiles.catalog_path.read_bytes()
        provider = self.hub.model_profiles.resolve(profile)
        original_env = provider['path'].read_bytes()
        self.assertEqual(profile, self.save('synthetic-fire-history', name='Renamed display only'))
        self.assertEqual(catalog, self.hub.model_profiles.catalog_path.read_bytes())
        self.assertEqual(protected, self.durable(self.project()))
        self.assert_history(workspace, saved)
        updated = self.post('create', name='New immutable version', base_url=self.endpoint,
            model='synthetic-fire-history', source_profile_id=profile, api_key='',
            context_window_tokens=131072, max_output_tokens=4096)
        self.assertEqual(200, updated.status_code, updated.text)
        latest = updated.json()['created_profile_id']
        self.assertNotEqual(profile, latest)
        self.assertEqual(200, self.post('archive', profile_id=profile).status_code)
        self.assertEqual(original_env, self.hub.model_profiles.resolve(profile)['path'].read_bytes())
        self.assertEqual(protected, self.durable(self.project()))
        self.assert_history(workspace, saved)
        self.restart()
        rows = {row['id']: row for row in self.profiles()['profiles']}
        self.assertTrue(rows[profile]['archived'])
        self.assertFalse(rows[profile]['selectable'])
        self.assertTrue(rows[latest]['selectable'])
        self.assertEqual([assistant], [row['id'] for row in self.hub.assistant.view()['sessions']])
        self.assertEqual(original_env, self.hub.model_profiles.resolve(profile)['path'].read_bytes())
        reopened = self.connect()  # Saved primary binding resumes even though removed from new choices.
        self.assert_history(reopened, saved)
        self.assertEqual(protected, self.durable(self.project()))
        self.assertFalse((self.project()/'model-workspaces').exists())
        self.assertFalse((self.hub.assistant.root/'models').exists())
