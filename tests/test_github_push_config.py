"""Explicit local configuration boundaries; no network, provider, or listener."""
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from repo_maintainer import github_push_control as control
from repo_maintainer.models import SafetyError


class PushConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.secret = self.root / 'push.secret'
        self.secret.write_bytes(b'x' * 32)
        self.config = self.root / 'push.json'
        self.target = {'monitor_id': 'a' * 16, 'repository_id': 123,
                       'repository': 'owner/private-project', 'branch': 'main'}
        self.write_config(str(self.secret))

    def write_config(self, secret):
        self.config.write_text(json.dumps({'schema_version': 1, 'target': self.target,
                                          'secret_file': secret, 'port': 0}), encoding='utf-8')

    def test_regular_explicit_files_remain_usable(self):
        self.assertEqual((self.target, b'x' * 32, 0), control.load_configuration(self.config))

    def test_network_and_device_paths_refused_before_filesystem_traversal(self):
        original = Path.is_symlink
        def guarded(path):
            if str(path).startswith(('\\\\', '//')):
                self.fail('Nonlocal name reached filesystem traversal')
            return original(path)
        for remote in [r'\\untrusted.invalid\share\push.json', r'\\.\pipe\push-secret',
                       r'\\?\C:\push.json', '//untrusted.invalid/share/push.json']:
            for field in ['config', 'secret']:
                with self.subTest(remote=remote, field=field):
                    self.write_config(remote if field == 'secret' else str(self.secret))
                    with patch.object(Path, 'is_symlink', autospec=True, side_effect=guarded):
                        with self.assertRaises(SafetyError):
                            control.load_configuration(remote if field == 'config' else self.config)

    def test_nonregular_file_refused_before_open(self):
        for mode in [stat.S_IFIFO, stat.S_IFCHR, stat.S_IFDIR, stat.S_IFSOCK]:
            with self.subTest(mode=mode):
                with patch.object(control, '_unlinked_local', return_value=True), \
                     patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=mode, st_size=0)), \
                     patch.object(control.os, 'open') as opened:
                    with self.assertRaises(SafetyError):
                        control.load_configuration(self.config)
                    opened.assert_not_called()

    def test_opened_handle_must_still_be_regular(self):
        with patch.object(control.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0)):
            with self.assertRaises(SafetyError):
                control.load_configuration(self.config)

    def test_oversized_file_refused_before_read(self):
        self.config.write_bytes(b'x' * 16385)
        with patch.object(control.os, 'open') as opened:
            with self.assertRaises(SafetyError):
                control.load_configuration(self.config)
            opened.assert_not_called()

    def test_relative_paths_refused_and_private_values_never_reflected(self):
        with self.assertRaises(SafetyError):
            control.load_configuration('relative.json')
        self.write_config('relative.secret')
        with self.assertRaises(SafetyError) as caught:
            control.load_configuration(self.config)
        self.assertNotIn('relative.secret', str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))


if __name__ == '__main__':
    unittest.main()
