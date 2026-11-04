"""Real junctions must be rejected with and without pathlib's newer API."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from repo_maintainer.dashboard import unlinked as dashboard_unlinked
from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.path_safety import is_junction
from repo_maintainer import path_safety
from repo_maintainer.recovery import unlinked as recovery_unlinked

spec = importlib.util.spec_from_file_location('junction_test_doctor', ROOT/'scripts/doctor.py')
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class JunctionDetectionTests(unittest.TestCase):
    def test_doctor_still_reports_missing_dependencies_without_site_packages(self):
        result = subprocess.run([sys.executable, '-S', str(ROOT/'scripts/doctor.py'),
                                 '--profile', 'core', '--json'], capture_output=True, text=True)
        self.assertEqual(1, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertFalse(value['ready'])
        self.assertEqual(0, value['model_calls'])
        self.assertEqual(0, value['provider_files_read'])

    def test_missing_api_distinguishes_junction_from_other_reparse_tags(self):
        # These inputs simulate Windows lstat results. POSIX stat intentionally
        # has no Windows reparse constants, so supply the fixture explicitly.
        with patch.object(stat, 'IO_REPARSE_TAG_MOUNT_POINT', 0xA0000003, create=True):
            for tag, expected in ((0xA0000003, True), (0xA000000C, False), (0, False)):
                with self.subTest(tag=tag):
                    path = SimpleNamespace(lstat=Mock(return_value=SimpleNamespace(st_reparse_tag=tag)))
                    self.assertEqual(expected, is_junction(path))
        self.assertFalse(is_junction(SimpleNamespace(lstat=Mock(return_value=SimpleNamespace()))))

    def test_posix_metadata_without_windows_constants_is_not_a_junction(self):
        path = SimpleNamespace(lstat=Mock(return_value=SimpleNamespace(st_mode=stat.S_IFREG)))
        with patch.object(path_safety, 'stat', SimpleNamespace()):
            self.assertFalse(is_junction(path))

    def test_regular_file_fallback_uses_actual_nonfollowing_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'regular.txt'
            path.write_text('ordinary file', encoding='utf-8')
            self.assertFalse(is_junction(SimpleNamespace(lstat=path.lstat)))

    def test_missing_path_is_allowed_but_unreadable_metadata_is_not(self):
        for error in (FileNotFoundError(), NotADirectoryError()):
            self.assertFalse(is_junction(SimpleNamespace(lstat=Mock(side_effect=error))))
        with self.assertRaises(PermissionError):
            is_junction(SimpleNamespace(lstat=Mock(side_effect=PermissionError())))

    @unittest.skipUnless(os.name == 'nt', 'Windows junction fixture')
    def test_real_junction_blocks_source_dashboard_recovery_and_doctor(self):
        self.check_real_junction(force_fallback=False)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction fixture')
    def test_real_junction_blocks_all_guards_without_pathlib_api(self):
        with patch.object(Path, 'is_junction', None, create=True):
            self.check_real_junction(force_fallback=True)

    def check_real_junction(self, force_fallback):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = create_fixture(root/'repo')
            target = repo/'actual'
            target.mkdir()
            sentinel = target/'keep.txt'
            sentinel.write_bytes(b'must never be read through the junction or changed')
            link = repo/'linked'
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                           check=True, capture_output=True)
            try:
                self.assertTrue(is_junction(link))
                self.assertFalse(is_junction(target))
                for guard in (dashboard_unlinked, recovery_unlinked):
                    for path in (link, link/'keep.txt', link/'not-created-yet'):
                        with self.subTest(guard=guard.__module__, path=path, fallback=force_fallback):
                            with self.assertRaises(SafetyError):
                                guard(path)
                with self.assertRaisesRegex(SafetyError, 'Junction'):
                    RepositoryGateway(repo).safe_file('linked/keep.txt')
                self.assertTrue(doctor._linked(link/'keep.txt'))
                self.assertEqual(repo, doctor._local_asset(repo, 'linked/keep.txt'))
                self.assertEqual(b'must never be read through the junction or changed', sentinel.read_bytes())
                self.assertFalse((target/'not-created-yet').exists())
            finally:
                # Remove only our link entry, never recurse through its target.
                link.rmdir()
