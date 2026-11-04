import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_repository_workbench as fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.repository_workbench import inspect_repository


class CheckOnboardingTests(unittest.TestCase):
    setUp = fixture.WorkbenchTests.setUp
    finish = fixture.WorkbenchTests.finish
    new_hub = fixture.WorkbenchTests.new_hub
    execute = fixture.WorkbenchTests.execute
    scope = fixture.WorkbenchTests.scope
    connect = fixture.WorkbenchTests.connect

    def test_readonly_api_has_no_session_provider_or_budget(self):
        before = RepositoryGateway(self.repo).snapshot()
        preview = inspect_repository(self.repo)
        with patch('repo_maintainer.repository_workbench.PiProvider', side_effect=AssertionError('no model')):
            value = self.execute('analyze_repo', {'path': str(self.repo), 'revision': preview['revision']})
        self.assertEqual(value['status'], 'finished', value)
        self.assertEqual(value['result']['verification'], 'not_run')
        self.assertFalse(self.hub.sessions)
        self.assertFalse(list((self.hub.root/'projects').glob('*/runs/ui-state/registry.sqlite3')))
        self.assertEqual(before, RepositoryGateway(self.repo).snapshot())

    def test_dirty_repository_readonly_preserves_changes(self):
        (self.repo/'calculator.py').write_text('def broken(:')
        before = RepositoryGateway(self.repo).snapshot()
        preview = inspect_repository(self.repo)
        value = self.execute('analyze_repo', {'path': str(self.repo), 'revision': preview['revision']})
        self.assertEqual(value['status'], 'finished', value)
        self.assertEqual(value['result']['syntax_results'][0]['status'], 'syntax_error')
        self.assertEqual(before, RepositoryGateway(self.repo).snapshot())

    def test_stale_readonly_preview_rejected(self):
        preview = inspect_repository(self.repo)
        (self.repo/'calculator.py').write_text('def add(a,b): return a+b')
        value = self.execute('analyze_repo', {'path': str(self.repo), 'revision': preview['revision']})
        self.assertEqual(value['status'], 'failed')

    def test_saved_scope_recommended_without_resetting_unknown(self):
        session = self.connect().session
        session.registry.reserve()
        before = session.registry.budget()
        preview = self.execute('local', {'path': str(self.repo)})['result']
        self.assertEqual(preview['saved_scope']['policy']['timeout_seconds'], 30)
        self.assertEqual(preview['saved_scope']['policy']['allowed_paths'], ['calculator.py'])
        self.assertEqual(before, session.registry.budget())

    def test_no_editable_error_is_actionable(self):
        value = self.execute('connect', {**self.scope(), 'allowed_paths': []})
        self.assertEqual(value['status'], 'failed')
        self.assertIn('先只读分析', value['result']['error'])

    def test_missing_tests_still_block_repair(self):
        response = self.client.post('/api/actions/connect', headers=self.headers,
            json={'request_id': 'a'*32, **self.scope(), 'allowed_tests': []})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.hub.sessions)

    def test_readonly_api_does_not_bypass_authentication(self):
        response = self.client.post('/api/actions/analyze_repo', json={'request_id': 'b'*32,
            'path': str(self.repo), 'revision': inspect_repository(self.repo)['revision']})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.hub.jobs)
