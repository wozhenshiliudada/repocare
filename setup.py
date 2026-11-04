"""Minimal setuptools hook: stage the tracked Pi runtime into the wheel.

The runtime stays at ``runtime/pi`` in a source checkout. ``build_py`` copies a
strict whitelist into ``repo_maintainer/pi_runtime`` and writes the integrity
manifest; ``pi_distribution`` owns the rules and is standard-library only.
"""
from pathlib import Path
import sys

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))


class PiBuildPy(build_py):
    def run(self):
        super().run()
        from repo_maintainer.pi_distribution import stage_pi_runtime
        from repo_maintainer.path_safety import is_junction
        # setuptools reuses build/lib and does not remove renamed Vite files.
        # Prune only stale files in this build's UI directory; never mutate
        # the generated source assets or recursively delete a computed path.
        build_root = Path(self.build_lib).resolve()
        ui = Path(self.build_lib) / 'repo_maintainer' / 'workbench_ui'
        source_ui = ROOT / 'src' / 'repo_maintainer' / 'workbench_ui'
        if (not ui.resolve().is_relative_to(build_root)
                or ui.resolve() == source_ui.resolve()):
            raise RuntimeError('Unsafe workbench build destination')
        for ancestor in (ui, ui.parent, Path(self.build_lib)):
            if ancestor.is_symlink() or is_junction(ancestor):
                raise RuntimeError('Linked workbench build destination')
        for path in sorted(ui.rglob('*')):
            if path.is_symlink() or is_junction(path):
                raise RuntimeError('Linked workbench build resource')
            if (path.is_file() and not (source_ui / path.relative_to(ui)).is_file()):
                if not path.resolve().is_relative_to(ui.resolve()):
                    raise RuntimeError('Workbench build resource escaped its directory')
                path.unlink()
        self._pi_outputs = stage_pi_runtime(
            ROOT, Path(self.build_lib) / 'repo_maintainer' / 'pi_runtime')
        # The public demo command must not reach back into a source checkout.
        for name in ('.gitignore', 'calculator.py', 'tests/test_calculator.py'):
            source = ROOT / 'examples' / 'buggy_repo' / name
            target = Path(self.build_lib) / 'repo_maintainer' / 'demo_assets' / 'buggy_repo' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            self._pi_outputs.append(target)

    def get_outputs(self, include_bytecode=1):
        outputs = super().get_outputs(include_bytecode)
        outputs.extend(str(path) for path in getattr(self, '_pi_outputs', ()))
        return outputs


setup(cmdclass={'build_py': PiBuildPy})
