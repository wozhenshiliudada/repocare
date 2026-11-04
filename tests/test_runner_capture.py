"""Real child processes: bounded capture must retain exit and timeout semantics."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer.recovery import process_identity
from repo_maintainer.runner import PREVIEW_LIMIT, run_test


class RunnerCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_large_both_pipes_are_drained_and_nonzero_exit_is_preserved(self):
        result = run_test(('{python}', '-c',
            "import os; os.write(1,b'x'*2097152); os.write(2,b'y'*1048576); "
            "os.write(1,b'STDOUT_END'); os.write(2,b'STDERR_END'); raise SystemExit(17)"), self.root, 20)
        self.assertEqual(17, result['returncode'])
        self.assertFalse(result['timed_out'])
        self.assertTrue(result['output_truncated'])
        self.assertLessEqual(len(result['stdout'].encode()), PREVIEW_LIMIT)
        self.assertLessEqual(len(result['stderr'].encode()), PREVIEW_LIMIT)
        self.assertTrue(result['stdout'].endswith('STDOUT_END'))
        self.assertTrue(result['stderr'].endswith('STDERR_END'))

    def test_quiet_success_keeps_output_and_does_not_inherit_provider_secret(self):
        with patch.dict('os.environ', {'REPOCARE_CAPTURE_SECRET': 'synthetic-not-a-real-key'}):
            result = run_test(('{python}', '-c',
                "import os; print('REPOCARE_CAPTURE_SECRET' in os.environ)"), self.root, 10)
        self.assertEqual(0, result['returncode'])
        self.assertEqual('False', result['stdout'].strip())
        self.assertFalse(result['output_truncated'])

    def test_timeout_stops_the_owned_process_and_preserves_partial_output(self):
        identities = []
        result = run_test(('{python}', '-c', "import os,time; os.write(1,b'started'); time.sleep(30)"),
                          self.root, 1, on_start=lambda pid: identities.append(process_identity(pid)))
        self.assertTrue(result['timed_out'])
        self.assertIsNotNone(result['returncode'])
        self.assertNotEqual(0, result['returncode'])
        self.assertEqual('started', result['stdout'])
        self.assertIsNone(process_identity(identities[0]['pid']))
