"""Setup checks must distinguish missing prerequisites from model/workflow success."""
from importlib.metadata import PackageNotFoundError
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('repocare_setup_doctor', ROOT/'scripts/doctor.py')
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class SetupDoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'pyproject.toml').write_bytes((ROOT/'pyproject.toml').read_bytes())
        target = self.root/'runtime/pi'
        target.mkdir(parents=True)
        for name in ['package.json', 'package-lock.json']:
            (target/name).write_bytes((ROOT/'runtime/pi'/name).read_bytes())
        self.packages = {}
        import tomllib
        config = tomllib.loads((self.root/'pyproject.toml').read_text())['project']
        for declaration in config['dependencies'] + config['optional-dependencies']['ui'] + config['optional-dependencies']['test']:
            name, version = declaration.split('==')
            self.packages[name] = version
        dependencies = json.loads((target/'package.json').read_text())['dependencies']
        for name, version in dependencies.items():
            directory = target/'node_modules'/name
            directory.mkdir(parents=True)
            (directory/'package.json').write_text(json.dumps({'version': version}))
        # A valid web installation includes built assets under the new #9
        # requirement. Keep these existing fixtures valid before the Agent runs;
        # missing/broken assets are tested in the read-only acceptance suite.
        ui = self.root/'src/repo_maintainer/workbench_ui'
        (ui/'assets').mkdir(parents=True)
        (ui/'index.html').write_text('<html><script type="module" src="/assets/fixture.js"></script>'
                                    '<link rel="stylesheet" href="/assets/fixture.css"></html>',encoding='utf-8')
        (ui/'assets/fixture.js').write_text('/* never executed */',encoding='utf-8')
        (ui/'assets/fixture.css').write_text('/* fixture */',encoding='utf-8')

    def version(self, name):
        if name not in self.packages:
            raise PackageNotFoundError(name)
        return self.packages[name]

    def execute(self, argv, **kwargs):
        self.assertEqual(argv[1:], ['--version'])
        self.assertEqual(kwargs['timeout'], 5)
        return subprocess.CompletedProcess(argv, 0, b'git version 2.53.0' if argv[0] == 'git' else b'v24.16.0', b'')

    def inspect(self, profile='web', **kwargs):
        return doctor.inspect_environment(self.root, profile, package_version=self.version,
                                          which=lambda n: n, execute=kwargs.pop('execute', self.execute),
                                          python_version=(3, 13, 3), **kwargs)

    def test_web_ready_does_not_claim_model_or_workflow_validation(self):
        result = self.inspect()
        self.assertTrue(result['ready'])
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(result['provider_files_read'], 0)
        self.assertIn('not a workflow', result['scope'])

    def test_core_does_not_require_ui_node_or_pi(self):
        self.packages.pop('starlette')
        self.packages.pop('httpx')
        result = self.inspect('core')
        self.assertTrue(result['ready'])
        self.assertNotIn('node', [r['name'] for r in result['checks']])
        self.assertFalse(self.inspect()['ready'])

    def test_dev_requires_test_extra_not_needed_for_web(self):
        self.packages.pop('httpx')
        self.assertTrue(self.inspect()['ready'])
        self.assertFalse(self.inspect('dev')['ready'])

    def test_declared_version_mismatch_and_missing_pi_are_reported(self):
        self.packages['filelock'] = '0.0.0'
        (self.root/'runtime/pi/node_modules/typebox/package.json').unlink()
        failed = {r['name'] for r in self.inspect()['checks'] if r['status'] != 'ok'}
        self.assertEqual(failed, {'filelock', 'Pi dependency typebox'})

    def test_node_too_old_is_not_ready(self):
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, b'v22.18.0' if argv[0] == 'node' else b'git version 2.53.0', b'')
        self.assertFalse(self.inspect(execute=run)['ready'])

    def test_timeout_and_unexpected_output_do_not_leak_raw_data(self):
        def timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 5)
        self.assertFalse(self.inspect(execute=timeout)['ready'])
        def unexpected(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, b'PRIVATE_MARKER\nFAIL', b'PRIVATE_MARKER')
        result = self.inspect(execute=unexpected)
        self.assertFalse(result['ready'])
        self.assertNotIn('PRIVATE_MARKER', json.dumps(result))

    def test_absent_tools_report_missing_and_do_not_attempt_install(self):
        result = doctor.inspect_environment(self.root, 'web', package_version=self.version,
            which=lambda n: None, execute=lambda *a, **k: self.fail('must not execute'), python_version=(3, 13, 3))
        self.assertFalse(result['ready'])

    def test_cli_exit_code_reflects_setup_only(self):
        with patch.object(doctor, 'inspect_environment', return_value={'ready': False}):
            self.assertEqual(doctor.main(['--json']), 1)
        with patch.object(doctor, 'inspect_environment', return_value={'ready': True}):
            self.assertEqual(doctor.main(['--json']), 0)


if __name__ == '__main__':
    unittest.main()
