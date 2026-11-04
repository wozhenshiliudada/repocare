"""Service fingerprints must work in installed wheels as well as source trees."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer import service_state
from repo_maintainer.models import SafetyError


class RuntimeLayoutTests(unittest.TestCase):
    def fixture(self, root, source):
        package = root/('src/repo_maintainer' if source else 'site-packages/repo_maintainer')
        package.mkdir(parents=True)
        for name in ('service_state.py', 'pi_runtime_limits.json', 'maintenance_limits.json'):
            (package/name).write_text('{}', encoding='utf-8')
        for folder in ('local_ui_assets', 'workbench_assets', 'workbench_ui'):
            (package/folder).mkdir()
            (package/folder/'index.html').write_text('original', encoding='utf-8')
        if source:
            runtime = root/'runtime/pi'
            runtime.mkdir(parents=True)
            (runtime/'package-lock.json').write_text('{}', encoding='utf-8')
        return package

    def test_wheel_assets_are_fingerprinted_and_changes_require_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.fixture(Path(directory), source=False)
            with patch.object(service_state, '__file__', str(package/'service_state.py')):
                runtime = service_state.RuntimeInfo()
                self.assertFalse(runtime.view()['code_changed'])
                self.assertIsNone(runtime.view()['pi_package_version'])
                (package/'workbench_ui/index.html').write_text('changed', encoding='utf-8')
                with self.assertRaisesRegex(SafetyError, 'restart'):
                    runtime.require_current()

    def test_source_runtime_lock_remains_mandatory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.fixture(root, source=True)
            with patch.object(service_state, '__file__', str(package/'service_state.py')):
                runtime = service_state.RuntimeInfo()
                self.assertFalse(runtime.view()['code_changed'])
                (root/'runtime/pi/package-lock.json').unlink()
                with self.assertRaisesRegex(SafetyError, 'restart'):
                    runtime.require_current()
                with self.assertRaises(FileNotFoundError):
                    service_state.RuntimeInfo()
