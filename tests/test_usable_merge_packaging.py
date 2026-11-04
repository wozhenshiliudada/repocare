"""The clean builder must carry the installed-Pi build hook and its actual inputs."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer.pi_distribution import stage_pi_runtime


class CleanPiBuildTests(unittest.TestCase):
    def test_clean_stage_can_execute_original_pi_hook_without_source_fallback(self):
        root = Path(__file__).resolve().parents[1]
        scripts = root / 'scripts'
        with patch.object(sys, 'path', [str(scripts), *sys.path]):
            spec = importlib.util.spec_from_file_location('clean_wheel_build', scripts / 'build-workbench-wheel.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        class CapturedBuild(Exception):
            pass
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'new-build'
            def inspect_stage(command, **kwargs):
                stage = Path(command[-1])
                self.assertEqual((root/'setup.py').read_bytes(), (stage/'setup.py').read_bytes(),
                                 'The clean builder discarded the original Pi build hook')
                package = Path(tmp) / 'installed' / 'repo_maintainer' / 'pi_runtime'
                stage_pi_runtime(stage, package)
                manifest = json.loads((package/'MANIFEST.json').read_text(encoding='utf-8'))
                for name in ('task-session.mjs', 'validation-plan.mjs', 'connectivity.mjs', 'behavior-runner.mjs'):
                    self.assertIn(name, manifest['files'])
                    self.assertEqual((root/'runtime/pi'/name).read_bytes(), (package/name).read_bytes())
                self.assertTrue(manifest['licenses']['complete'])
                for name in ('.gitignore','calculator.py','tests/test_calculator.py'):
                    self.assertEqual((root/'examples/buggy_repo'/name).read_bytes(),
                                     (stage/'examples/buggy_repo'/name).read_bytes())
                self.assertFalse((stage/'runtime/pi/node_modules').exists())
                raise CapturedBuild()
            with patch.object(module.subprocess, 'run', inspect_stage):
                with self.assertRaises(CapturedBuild):
                    module.build(output, no_build_isolation=True)


if __name__ == '__main__':
    unittest.main()
