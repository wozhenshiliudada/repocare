"""Provider directory reads use the original HTTP boundary and never queue keys."""
import copy
import json
import unittest
from unittest.mock import patch

from test_model_profile_workspaces import ModelProfileWorkspaceTests


class ProviderDirectoryHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ModelProfileWorkspaceTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.client = self.fixture.client
        self.headers = self.fixture.headers

    def request(self, value, **kwargs):
        return self.client.post('/api/provider-models', json=value,
                                headers=kwargs.pop('headers', self.headers), **kwargs)

    def test_existing_credentials_are_resolved_only_for_the_original_endpoint(self):
        before = copy.deepcopy(self.fixture.profiles())
        result = {'models': [{'id': 'real-id', 'name': 'Model label with spaces',
                              'context_window_tokens': 2000000, 'max_output_tokens': 1000000,
                              'reasoning_efforts': ['low', 'high', 'max']}],
                  'models_url': 'https://a.example.invalid/v1/models'}
        with patch('repo_maintainer.provider_models.discover_provider_models', return_value=result) as call:
            response = self.request({'profile_id': 'legacy'})
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(result, response.json())
            self.assertEqual('no-store', response.headers['cache-control'])
            call.assert_called_once_with({'base_url': 'https://a.example.invalid/v1',
                                          'api_key': self.fixture.secret_a})
        self.assertEqual(before, self.fixture.profiles())
        self.assertNotIn(self.fixture.secret_a, response.text)
        self.assertFalse(self.fixture.hub.assistant.root.exists())

    def test_unsaved_directory_does_not_require_a_guessed_model_id(self):
        value = {'base_url': 'https://relay.example.invalid/api/v1', 'api_key': 'synthetic-directory-key'}
        with patch('repo_maintainer.provider_models.discover_provider_models', return_value={'models': []}) as call:
            self.assertEqual(200, self.request(value).status_code)
            call.assert_called_once_with(value)

    def test_authentication_origin_and_credential_transfer_are_rejected_before_network(self):
        with patch('repo_maintainer.provider_models.discover_provider_models') as call:
            self.assertEqual(401, self.request({'profile_id': 'legacy'}, headers={}).status_code)
            for changed in ({'Origin': 'https://foreign.invalid'}, {'Host': 'foreign.invalid'}):
                self.assertEqual(403, self.request({'profile_id': 'legacy'},
                    headers={**self.headers, **changed}).status_code)
            for payload in ({'profile_id': 'legacy', 'base_url': 'https://foreign.invalid'},
                            {'profile_id': 'legacy', 'api_key': 'new-key'},
                            {'base_url': 'https://relay.invalid'}, {'profile_id': None}):
                self.assertEqual(409, self.request(payload).status_code)
            call.assert_not_called()

    def test_strict_body_and_query_limits_are_shared_with_other_settings(self):
        with patch('repo_maintainer.provider_models.discover_provider_models') as call:
            self.assertEqual(405, self.client.get('/api/provider-models', headers=self.headers).status_code)
            self.assertEqual(409, self.client.post('/api/provider-models?url=secret', json={}, headers=self.headers).status_code)
            self.assertEqual(415, self.client.post('/api/provider-models', content='{}', headers=self.headers).status_code)
            headers = {**self.headers, 'Content-Type': 'application/json'}
            self.assertEqual(413, self.client.post('/api/provider-models', content='x' * 16385, headers=headers).status_code)
            self.assertEqual(409, self.client.post('/api/provider-models',
                content='{"profile_id":"legacy","profile_id":"other"}', headers=headers).status_code)
            call.assert_not_called()

    def test_provider_exception_cannot_expose_credentials_or_create_jobs(self):
        with patch('repo_maintainer.provider_models.discover_provider_models', side_effect=OSError(self.fixture.secret_a)):
            response = self.request({'profile_id': 'legacy'})
        self.assertEqual(409, response.status_code)
        self.assertNotIn(self.fixture.secret_a, response.text)
        self.assertFalse(self.fixture.hub.assistant.root.exists())

    def test_original_monitor_preview_uses_its_own_declared_length_not_the_ui_default(self):
        self.fixture.config.write_text('PI_MAINTENANCE_API_KEY=' + self.fixture.secret_a + '\n'
            'PI_MAINTENANCE_BASE_URL=https://a.example.invalid/v1\n'
            'PI_MAINTENANCE_MODEL=model-a\n'
            'PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS=3000000\n'
            'PI_MAINTENANCE_MAX_OUTPUT_TOKENS=1500000\n', encoding='utf-8')
        limits = self.fixture.hub.original_model_limits()
        self.assertEqual(1500000, limits['max_output_tokens'])
        self.assertEqual(self.fixture.hub.runtime.view()['planning_timeout_seconds'], limits['planning_timeout_seconds'])
        self.assertEqual(128000, self.fixture.hub.runtime.view()['max_output_tokens'])

    def test_injected_offline_provider_keeps_its_original_no_env_parser_contract(self):
        self.fixture.config.write_text('synthetic; not read', encoding='utf-8')
        self.fixture.hub.provider_factory = lambda _policy: None
        self.assertEqual(128000, self.fixture.hub.original_model_limits()['max_output_tokens'])
        self.fixture.hub.provider_factory = None
        from repo_maintainer.models import SafetyError
        with self.assertRaises(SafetyError):
            self.fixture.hub.original_model_limits()

    def test_changed_pinned_file_cannot_forward_saved_credentials_to_a_new_endpoint(self):
        resolve = self.fixture.hub.model_profiles.resolve_selectable
        def swap(identity):
            profile = resolve(identity)
            source = self.fixture.config.read_text(encoding='utf-8')
            self.fixture.config.write_text(source.replace('a.example.invalid', 'foreign.example.invalid'), encoding='utf-8')
            return profile
        with patch.object(self.fixture.hub.model_profiles, 'resolve_selectable', side_effect=swap) as selected, \
                patch('repo_maintainer.provider_models.discover_provider_models') as network:
            response = self.request({'profile_id': 'legacy'})
        selected.assert_called_once_with('legacy')
        self.assertIn('foreign.example.invalid', self.fixture.config.read_text(encoding='utf-8'))
        self.assertEqual(409, response.status_code)
        network.assert_not_called()
        self.assertNotIn(self.fixture.secret_a, response.text)


if __name__ == '__main__':
    unittest.main()
