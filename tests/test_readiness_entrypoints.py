import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from repo_maintainer.cli import main
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app
from repo_maintainer.service_readiness import ServiceReadiness


class ReadinessEntrypointsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'provider.local'
        self.secret = 'synthetic-entrypoint-key-never-expose'
        self.config.write_text('PI_MAINTENANCE_API_KEY=' + self.secret + '\n'
                               'PI_MAINTENANCE_BASE_URL=https://example.invalid/v1\n'
                               'PI_MAINTENANCE_MODEL=model\n', encoding='utf-8')

    def test_cli_without_configuration_reports_local_only_no_discovery(self):
        output = io.StringIO()
        with patch('repo_maintainer.service_readiness.load_provider_config', side_effect=AssertionError('no discovery')), \
             contextlib.redirect_stdout(output):
            code = main(['setup-check', '--json'])
        self.assertEqual(1, code)
        report = json.loads(output.getvalue())
        self.assertEqual(0, report['model_calls'])
        self.assertFalse(report['network_checked'])
        self.assertEqual('unknown', next(row for row in report['checks'] if row['id'] == 'provider_config')['status'])

    def test_real_cli_checker_rejects_example_without_echoing_credentials(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['setup-check', '--provider-config', str(self.config), '--json'])
        self.assertEqual(1, code)
        value = output.getvalue()
        self.assertNotIn(self.secret, value)
        self.assertNotIn(str(self.config), value)
        report = json.loads(value)
        self.assertEqual('blocked', report['status'])
        self.assertFalse(report['network_checked'])

    def test_authenticated_http_explicit_read_does_not_start_task_or_change_budget(self):
        hub = RepositoryWorkbench(self.root / 'hub', self.config, request_budget=4)
        self.addCleanup(hub.close)
        client = TestClient(create_workbench_app(hub, 12345), base_url='http://127.0.0.1:12345')
        self.addCleanup(client.close)
        headers = {'Authorization': 'Bearer ' + hub.token}
        before_config = self.config.read_bytes()
        with patch.object(hub.readiness, 'inspect', wraps=hub.readiness.inspect) as inspect:
            for _ in range(3):
                service = client.get('/api/service', headers=headers)
                self.assertEqual(200, service.status_code)
                self.assertEqual('unknown', service.json()['readiness']['status'])
            inspect.assert_not_called()
            self.assertEqual(401, client.get('/api/readiness').status_code)
            self.assertEqual(403, client.get('/api/readiness', headers={**headers, 'Origin': 'https://wrong.invalid'}).status_code)
            self.assertEqual(400, client.get('/api/readiness?path=elsewhere', headers=headers).status_code)
            inspect.assert_not_called()
            result = client.get('/api/readiness', headers=headers)
            self.assertEqual(200, result.status_code, result.text)
            self.assertEqual(1, inspect.call_count)
            self.assertEqual('blocked', result.json()['status'])
            self.assertFalse(result.json()['network_checked'])
            self.assertEqual(0, result.json()['model_calls'])
            self.assertNotIn(self.secret, result.text)
            self.assertEqual(result.json(), client.get('/api/service', headers=headers).json()['readiness'])
        self.assertEqual(before_config, self.config.read_bytes())
        self.assertEqual({}, hub.workspaces)
        self.assertEqual({}, hub.sessions)
        self.assertEqual({}, hub.jobs)
        self.assertEqual(4, hub.request_budget)


if __name__ == '__main__':
    unittest.main()
