"""T0 only: real Pi persisted sessions + original UIRegistry, no product recovery wiring."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.models import SafetyError
from repo_maintainer.ui_registry import UIRegistry

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'runtime' / 'pi' / 'test' / 'persistence-spike-fixture.mjs'
ORIGINAL = 'def add(a, b):\n    return a - b\n'
CANDIDATE = 'def add(a, b):\n    return a * b\n'
FEEDBACK = {'command': ['python', '-m', 'unittest'], 'returncode': 1,
            'failure_id': 'test_add', 'assertion': 'Expected 5, got 6', 'previous_plan': 'first-proposal'}


class TaskSessionSpikeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pi-persistence-budget-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binding = {'repository': 'synthetic-repository', 'policy': {'fixture': True},
                        'provider': {'type': 'T0SyntheticFixture', 'request_budget': 50}}
        self.registry = UIRegistry(self.root, self.binding)
        self.addCleanup(lambda: self.registry.close())

    def restart(self):
        self.registry.close()
        self.registry = UIRegistry(self.root, self.binding)

    def probe(self, phase, allowance, session_file=None):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; do not skip the native SDK probe')
        payload = {'root': str(self.root), 'phase': phase, 'request_budget': allowance,
                   'content': ORIGINAL if phase == 'create' else CANDIDATE,
                   'feedback': FEEDBACK if phase == 'continue' else None}
        if session_file:
            payload['session_file'] = session_file
        env = {key: value for key, value in os.environ.items() if key.upper() in
               {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA',
                'LOCALAPPDATA', 'COMSPEC', 'PATHEXT'}}
        result = subprocess.run([node, str(FIXTURE)], input=json.dumps(payload), encoding='utf-8',
                                capture_output=True, env=env, timeout=40)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['requests_started'], receipt['observed_http_requests'])
        return receipt

    def test_native_reopen_and_continue_keep_original_ceiling_and_monotonic_consumption(self):
        first_id, allowance = self.registry.reserve()
        self.assertEqual(50, allowance)
        first = self.probe('create', allowance)
        self.assertEqual('completed', first['status'])
        self.registry.settle(first_id, {'requests': first['requests_started']}, success=True)
        self.restart()
        saved = self.registry.budget()
        self.assertEqual({'request_budget': 50, 'requests_used': 3,
                          'requests_reserved_unknown': 0, 'requests_remaining': 47}, saved)
        opened = self.probe('open', saved['requests_remaining'], first['session_file'])
        self.assertEqual(0, opened['observed_http_requests'])
        self.assertEqual(saved, self.registry.budget())
        second_id, allowance = self.registry.reserve()
        self.assertEqual(47, allowance)
        second = self.probe('continue', allowance, first['session_file'])
        self.assertEqual('completed', second['status'])
        self.assertEqual(47, second['latest_request_budget'])
        self.assertEqual(FEEDBACK, second['feedback_seen'])
        self.assertEqual(first['session_id'], second['session_id'])
        self.registry.settle(second_id, {'requests': second['requests_started']}, success=True)
        self.restart()
        self.assertEqual({'request_budget': 50, 'requests_used': 6,
                          'requests_reserved_unknown': 0, 'requests_remaining': 44}, self.registry.budget())
        self.assertEqual(2, len(self.registry.load('attempt')))

    def test_unknown_attempt_stays_reserved_even_if_native_session_opens_successfully(self):
        first_id, allowance = self.registry.reserve()
        first = self.probe('create', allowance)
        self.registry.settle(first_id, {'requests': first['requests_started']}, success=True)
        second_id, allowance = self.registry.reserve()
        self.registry.settle(second_id, {'requests': None}, success=False)
        self.restart()
        saved = self.registry.budget()
        self.assertEqual({'request_budget': 50, 'requests_used': 3,
                          'requests_reserved_unknown': 47, 'requests_remaining': 0}, saved)
        # Opening is read-only and does not resolve the uncertain provider outcome.
        opened = self.probe('open', 0, first['session_file'])
        self.assertEqual('opened', opened['status'])
        self.assertEqual(0, opened['observed_http_requests'])
        self.assertEqual(saved, self.registry.budget())
        with self.assertRaisesRegex(SafetyError, 'exhausted'):
            self.registry.reserve()


if __name__ == '__main__':
    unittest.main()
