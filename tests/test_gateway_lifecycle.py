"""Gateway CLI contracts; all state lives in isolated temporary directories."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer import gateway
from repo_maintainer.models import SafetyError


class GatewayConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='repocare-gateway-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'private'

    def call(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = gateway.main([*arguments, '--home', str(self.home), '--json'])
        return result, json.loads(output.getvalue())

    def test_setup_without_key_and_idempotent(self):
        self.assertEqual(self.call('setup')[0], 0)
        configuration = gateway.load_configuration(self.home)
        self.assertIsNone(configuration['provider_config'])
        self.assertFalse((self.home/'workbench').exists())
        before = (self.home/'gateway'/'config.json').read_bytes()
        self.assertEqual(self.call('setup')[0], 0)
        self.assertEqual(before, (self.home/'gateway'/'config.json').read_bytes())

    def test_status_before_setup_does_not_create_anything(self):
        result, status = self.call('gateway', 'status')
        self.assertEqual(result, 1)
        self.assertEqual(status['status'], 'not_configured')
        self.assertFalse(self.home.exists())

    def test_unknown_configuration_fields_fail_closed(self):
        self.call('setup')
        target = self.home/'gateway'/'config.json'
        value = json.loads(target.read_text())
        value['command'] = 'execute anything'
        target.write_text(json.dumps(value))
        with self.assertRaises(SafetyError):
            gateway.load_configuration(self.home)

    def test_existing_workbench_metadata_is_adopted_and_never_rewritten(self):
        root = Path(self.temporary.name)/'original-state'
        root.mkdir()
        metadata = {'schema_version': 1, 'provider_config': str(root/'provider.env'),
                    'request_budget': 37, 'allow_auto': False, 'layout': 'compact-v1'}
        path = root/'workbench.json'
        path.write_text(json.dumps(metadata))
        before = path.read_bytes()
        self.assertEqual(self.call('setup', '--workspace-root', str(root))[0], 0)
        configuration = gateway.load_configuration(self.home)
        self.assertEqual(configuration['request_budget'], 37)
        self.assertIsNone(configuration['provider_config'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.call('setup', '--request-budget', '500')[0], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_bad_paths_and_non_loopback_receipts_rejected(self):
        for value in ('relative', '//server/share', '\\\\server\\share', 'x\npath'):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                gateway.local_path(value)
        self.call('setup')
        configuration = gateway.load_configuration(self.home)
        receipt = {'host': 'example.invalid'}
        with self.assertRaises(SafetyError):
            gateway.validate_receipt(receipt, configuration)

    def test_posix_private_permissions_or_windows_dacl(self):
        self.call('setup')
        gateway.check_private(self.home/'gateway', directory=True)
        gateway.check_private(self.home/'gateway'/'config.json')
        if os.name != 'nt':
            os.chmod(self.home/'gateway'/'config.json', 0o644)
            with self.assertRaises(SafetyError):
                gateway.load_configuration(self.home)

    def test_real_symlink_or_windows_junction_is_rejected(self):
        target = Path(self.temporary.name)/'target'
        target.mkdir()
        link = Path(self.temporary.name)/'linked'
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.returncode, 0)
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        else:
            link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(SafetyError):
            gateway.local_path(link/'gateway')

    def test_wrong_field_types_and_unknown_commands_are_rejected(self):
        self.call('setup')
        configuration = gateway.load_configuration(self.home)
        for changes in ({'ui': {}}, {'request_budget': True}, {'port': False}, {'allow_auto': 1}):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                gateway.validate_configuration(dict(configuration, **changes))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as rejected:
            gateway.main(['gateway', 'force-kill', '--home', str(self.home)])
        self.assertEqual(rejected.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
