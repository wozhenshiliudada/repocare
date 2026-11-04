"""Real authenticated HTTP adapters; no model or target-code execution."""
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_repository_workbench as fixtures
from test_comparison_summary import fixture
from repo_maintainer.models import digest


class WorkbenchReportTests(unittest.TestCase):
    setUp = fixtures.WorkbenchTests.setUp
    new_hub = fixtures.WorkbenchTests.new_hub
    finish = fixtures.WorkbenchTests.finish
    execute = fixtures.WorkbenchTests.execute
    scope = fixtures.WorkbenchTests.scope
    connect = fixtures.WorkbenchTests.connect

    def run_input(self):
        path = self.root / 'evidence' / ('a' * 32)
        path.mkdir(parents=True)
        record = {'run_id': path.name, 'schema_version': 1, 'status': 'interrupted',
                  'source_repo': str(self.repo), 'issue': 'Original <script> text',
                  'secret_field': 'not-exported'}
        (path / 'run.json').write_text(json.dumps(record), encoding='utf-8')
        return path

    def summary_input(self):
        path = self.root / 'summary.json'
        data = fixture()
        data['passed'] = False
        data['cells'] = [{'secret': 'not-exported'}]
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_saved_list_uses_original_spec_then_inspection_and_explicit_connect(self):
        self.assertEqual(self.execute('saved_projects', {'page': 1})['result']['projects'], [])
        server = self.connect()
        server.session.registry.reserve()
        before = server.session.registry.budget()
        listing = self.execute('saved_projects', {'page': 1})['result']
        self.assertEqual(listing['projects'], [{'path': str(self.repo), 'name': self.repo.name}])
        preview = self.execute('local', {'path': listing['projects'][0]['path']})['result']
        self.assertEqual(preview['saved_scope']['policy']['allowed_paths'], ['calculator.py'])
        self.assertEqual(self.execute('connect', self.scope())['result']['reused'], True)
        self.assertEqual(server.session.registry.budget(), before)
        self.assertFalse(server.session.plans)

    def test_dashboard_download_is_authenticated_bound_immutable_and_idempotent(self):
        path = self.run_input()
        before = (path / 'run.json').read_bytes()
        identity = uuid.uuid4().hex
        job = self.execute('export_dashboard', {'inputs': [str(path)]}, identity)
        self.assertEqual(job['status'], 'finished', job)
        self.assertEqual(self.execute('export_dashboard', {'inputs': [str(path)]}, identity), job)
        self.assertEqual(len(list((self.hub.root / 'exports').iterdir())), 1)
        artifact = job['result']['artifacts'][0]
        self.assertEqual(self.client.get(artifact['url']).status_code, 401)
        self.assertEqual(self.client.get(artifact['url'], headers={**self.headers, 'Origin': 'https://evil.invalid'}).status_code, 403)
        response = self.client.get(artifact['url'], headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text[:100])
        self.assertIn('attachment;', response.headers['content-disposition'])
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(digest(response.content), artifact['sha256'])
        self.assertIn("connect-src 'none'", response.text)
        self.assertNotIn('not-exported', response.text)
        self.assertEqual((path / 'run.json').read_bytes(), before)
        target = Path(job['result']['directory']) / artifact['name']
        target.write_bytes(b'changed')
        self.assertEqual(self.client.get(artifact['url'], headers=self.headers).status_code, 409)

    def test_comparison_exports_only_existing_allowlisted_partial_summary(self):
        path = self.summary_input()
        before = path.read_bytes()
        with patch('repo_maintainer.comparison.run_comparison', side_effect=AssertionError('must not execute comparison')):
            job = self.execute('export_comparison', {'inputs': [str(path)]})
        self.assertEqual(job['status'], 'finished', job)
        self.assertEqual(job['result']['status'], 'partial')
        for artifact in job['result']['artifacts']:
            response = self.client.get(artifact['url'], headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('not-exported', response.text)
            self.assertNotIn('cells', response.text)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.hub.sessions)

    def test_exports_reject_unknown_records_extra_query_relative_inputs_and_fields(self):
        self.assertEqual(self.client.get('/api/exports/' + 'b' * 32 + '/dashboard.html', headers=self.headers).status_code, 409)
        for action, inputs in [('export_dashboard', ['relative']), ('export_dashboard', []),
                               ('export_comparison', [str(self.root)] * 2), ('export_comparison', [42])]:
            response = self.client.post('/api/actions/' + action, headers=self.headers,
                json={'request_id': uuid.uuid4().hex, 'inputs': inputs})
            self.assertEqual(response.status_code, 409)
        response = self.client.post('/api/actions/export_dashboard', headers=self.headers,
            json={'request_id': uuid.uuid4().hex, 'inputs': [str(self.root)], 'output': str(self.repo / 'overwrite.html')})
        self.assertEqual(response.status_code, 409)
        path = self.summary_input()
        job = self.execute('export_comparison', {'inputs': [str(path)]})
        url = job['result']['artifacts'][0]['url']
        self.assertEqual(self.client.get(url + '?path=elsewhere', headers=self.headers).status_code, 409)
        self.assertEqual(self.client.get(url.replace('comparison.json', 'dashboard.html'), headers=self.headers).status_code, 409)
        self.assertEqual(self.execute('export_dashboard', {'inputs': [str(self.root / 'missing')]})['status'], 'failed')

    def test_changed_or_missing_project_is_not_rebound_or_executed(self):
        self.connect()
        target = next((self.hub.root / 'projects').glob('*/project.json'))
        data = json.loads(target.read_text())
        data['repository'] = str(self.root / 'other')
        target.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(self.execute('saved_projects', {'page': 1})['status'], 'failed')
        self.assertEqual(len(self.hub.sessions), 1)

    def test_saved_projects_pages_do_not_skip_or_duplicate_and_missing_path_stays_readonly(self):
        expected = set()
        for index in range(31):
            path = str(self.root / ('missing-' + str(index)))
            expected.add(path)
            directory = self.hub.root / 'projects' / digest(os.path.normcase(path).encode())[:16]
            directory.mkdir(parents=True)
            (directory / 'project.json').write_text(json.dumps({'repository': path}), encoding='utf-8')
        first = self.execute('saved_projects', {'page': 1})['result']
        second = self.execute('saved_projects', {'page': 2})['result']
        self.assertEqual([len(first['projects']), len(second['projects'])], [30, 1])
        self.assertTrue(first['has_next'])
        self.assertFalse(second['has_next'])
        self.assertEqual({row['path'] for row in first['projects'] + second['projects']}, expected)
        self.assertEqual(self.execute('local', {'path': first['projects'][0]['path']})['status'], 'failed')
        self.assertFalse(self.hub.sessions)

    def test_restart_does_not_reauthorize_prior_export_or_delete_its_file(self):
        job = self.execute('export_comparison', {'inputs': [str(self.summary_input())]})
        artifact = job['result']['artifacts'][0]
        original = (Path(job['result']['directory']) / artifact['name']).read_bytes()
        self.client.close()
        self.hub.close()
        self.hub = self.new_hub()
        from starlette.testclient import TestClient
        from repo_maintainer.repository_workbench import create_workbench_app
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.assertEqual(self.client.get(artifact['url'], headers=self.headers).status_code, 401)
        self.headers = {'Authorization': 'Bearer ' + self.hub.token}
        self.assertEqual(self.client.get(artifact['url'], headers=self.headers).status_code, 409)
        self.assertEqual((Path(job['result']['directory']) / artifact['name']).read_bytes(), original)

    def test_input_symlink_or_junction_is_refused_without_export(self):
        from repo_maintainer.models import SafetyError
        from repo_maintainer import workbench_reports
        original = workbench_reports.unlinked
        def guard(path):
            if 'exports' in path.parts:
                raise SafetyError('simulated linked export root')
            return original(path)
        with patch.object(workbench_reports, 'unlinked', side_effect=guard):
            job = self.execute('export_comparison', {'inputs': [str(self.summary_input())]})
        self.assertEqual(job['status'], 'failed')
        self.assertFalse((self.hub.root / 'exports').exists())
