"""Source doctor compatibility and wheel runtime checks use the same code."""
from importlib.metadata import PackageNotFoundError
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer import installation_check as checker


ROOT = Path(__file__).resolve().parents[1]
DECLARATIONS = ['filelock==4.0.12', 'psutil==7.2.2', 'junitparser==5.0.3',
                'starlette==1.7.0; extra == "ui"', 'uvicorn==0.53.0; extra == "ui"',
                'httpx==0.28.1; extra == "test"', 'onnxruntime==1.20.1; extra == "semantic"']


class InstallationCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'repo_maintainer'
        ui = self.package / 'workbench_ui'
        (ui / 'assets').mkdir(parents=True)
        (ui / 'index.html').write_text('<script src="/assets/app.js"></script>'
                                       '<link href="/assets/app.css" rel="stylesheet">', encoding='utf-8')
        (ui / 'assets/app.js').write_text('/* not executed */', encoding='utf-8')
        (ui / 'assets/app.css').write_text('/* not executed */', encoding='utf-8')
        self.versions = {'filelock': '4.0.12', 'psutil': '7.2.2', 'junitparser': '5.0.3',
                         'starlette': '1.7.0', 'uvicorn': '0.53.0'}
        self.probes = []

    def package_version(self, name):
        if name not in self.versions:
            raise PackageNotFoundError(name)
        return self.versions[name]

    def execute(self, argv, **kwargs):
        self.probes.append(argv)
        self.assertEqual(['--version'], argv[1:])
        self.assertEqual(5, kwargs['timeout'])
        return subprocess.CompletedProcess(argv, 0, b'git version 2.53.0' if argv[0] == 'git' else b'v24.16.0', b'')

    def inspect(self, profile='web', **kwargs):
        return checker.inspect_environment(self.root, profile, installation_kind='installed',
            package_root=self.package, package_version=self.package_version, which=lambda name: name,
            execute=self.execute, distribution_requirements=lambda name: DECLARATIONS,
            python_version=(3, 13, 3), **kwargs)

    def test_installed_web_needs_no_source_files_or_development_dependencies(self):
        with patch.object(checker.tomllib, 'loads', side_effect=AssertionError('no source pyproject read')):
            result = self.inspect()
        self.assertEqual('installed', result['installation_kind'])
        self.assertFalse(result['ready'])
        self.assertEqual(0, result['model_calls'])
        self.assertEqual(0, result['provider_files_read'])
        checks = {row['name']: row for row in result['checks']}
        self.assertNotIn('httpx', checks)
        self.assertNotIn('onnxruntime', checks)
        self.assertNotIn('Source development checkout', checks)
        for name in self.versions:
            self.assertEqual('ok', checks[name]['status'])
        failed = {name for name, row in checks.items() if row['status'] != 'ok'}
        self.assertEqual({'Pi npm lock', 'Pi dependency @earendil-works/pi-coding-agent', 'Pi dependency typebox'}, failed)
        self.assertIn('not distributed', checks['Pi npm lock']['detail'])
        self.assertEqual('ok', checks['React workbench index']['status'])
        self.assertEqual(2, len([name for name in checks if name.startswith('React asset ')]))

    def test_installed_core_does_not_require_web_node_pi_or_frontend(self):
        self.versions.pop('starlette')
        self.versions.pop('uvicorn')
        result = self.inspect('core')
        self.assertTrue(result['ready'])
        self.assertEqual([['git', '--version']], self.probes)
        self.assertFalse(any(row['name'].startswith(('React ', 'Pi ')) for row in result['checks']))

    def test_installed_dev_is_explicitly_separate_and_never_claimed_runtime_requirement(self):
        result = self.inspect('dev')
        checks = {row['name']: row['status'] for row in result['checks']}
        self.assertEqual('missing_or_incompatible', checks['Source development checkout'])
        self.assertEqual('missing_or_incompatible', checks['httpx'])
        self.assertFalse(result['ready'])

    def test_missing_metadata_and_unsupported_markers_are_visible_failures(self):
        def missing(name):
            raise PackageNotFoundError(name)
        for metadata in (missing, lambda name: [], lambda name: ['filelock==4.0.12; python_version > "3"']):
            result = checker.inspect_environment(self.root, 'core', installation_kind='installed',
                package_root=self.package, package_version=self.package_version, which=lambda name: None,
                distribution_requirements=metadata)
            self.assertFalse(result['ready'])
            self.assertTrue(any(row['name'] == 'Package dependency metadata' and row['status'] != 'ok'
                                for row in result['checks']))

    def test_installed_assets_are_checked_in_package_and_links_are_rejected(self):
        missing = self.package / 'workbench_ui/assets/app.js'
        missing.unlink()
        result = self.inspect()
        self.assertEqual('missing_or_incompatible', next(row['status'] for row in result['checks']
                                                       if row['name'] == 'React asset /assets/app.js'))
        with patch.object(checker, '_linked', return_value=True):
            rows = []
            checker.check_workbench_assets(self.root, lambda name, ok, detail: rows.append((name, ok)),
                                            package_root=self.package)
        self.assertEqual([('React workbench index', False)], rows)

    def test_source_entrypoint_reuses_package_checker_and_node_baseline(self):
        spec = importlib.util.spec_from_file_location('shared_doctor', ROOT / 'scripts/doctor.py')
        script = importlib.util.module_from_spec(spec)
        before = sys.path[:]
        try:
            spec.loader.exec_module(script)
            self.assertIs(checker.inspect_environment, script.inspect_environment)
            self.assertIs(checker.check_workbench_assets, script.check_workbench_assets)
        finally:
            sys.path[:] = before
        declared = json.loads((ROOT / 'runtime/pi/package.json').read_text(encoding='utf-8'))['engines']['node']
        self.assertEqual('>=' + '.'.join(map(str, checker.MINIMUM_NODE)), declared)

    def test_working_directory_checker_and_pyproject_are_not_loaded(self):
        (self.root / 'scripts').mkdir()
        marker = self.root / 'unexpected-execution'
        (self.root / 'scripts/doctor.py').write_text(f'open({str(marker)!r}, "w").write("bad")\n', encoding='utf-8')
        (self.root / 'pyproject.toml').write_text('definitely not TOML', encoding='utf-8')
        result = self.inspect('core')
        self.assertTrue(result['ready'])
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
