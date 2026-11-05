"""Run records survive Windows readers without replaying the recorded action."""
import copy
import errno
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from repo_maintainer.store import RunStore


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.record = {'run_id': 'a' * 32, 'status': 'candidate_ready', 'mode': 'approval', 'events': []}
        self.store = RunStore(self.root, self.record)
        self.store.save()
        self.target = self.root / 'run.json'

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing semantics')
    def test_real_reader_release_allows_atomic_save_and_one_event(self):
        old = self.target.read_bytes()
        denied = threading.Event()
        errors = []
        calls = []
        replace = Path.replace
        reader = self.target.open('rb')

        def observed(path, target):
            try:
                calls.append(target)
                return replace(path, target)
            except PermissionError as error:
                self.assertIn(error.winerror, {5, 32})
                denied.set()
                raise

        def publish_record():
            try:
                self.store.event('integrated', commit='b' * 40)
            except BaseException as error:
                errors.append(error)

        with patch.object(Path, 'replace', observed):
            worker = threading.Thread(target=publish_record)
            worker.start()
            try:
                self.assertTrue(denied.wait(5), 'real file sharing denial was not observed')
                self.assertEqual(self.target.read_bytes(), old)
            finally:
                reader.close()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(calls), 2)
        saved = json.loads(self.target.read_bytes())
        self.assertEqual(saved, self.record)
        self.assertEqual(len(saved['events']), 1)
        self.assertEqual(saved['events'][0]['commit'], 'b' * 40)
        self.assertFalse((self.root / 'run.json.tmp').exists())
        self.assertIn('integrated', (self.root / 'report.md').read_text())

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing semantics')
    def test_persistent_reader_keeps_original_record_and_pending_bytes(self):
        old = self.target.read_bytes()
        report = (self.root / 'report.md').read_bytes()
        self.record['status'] = 'integrated'
        replace = Path.replace
        with self.target.open('rb'), patch.object(Path, 'replace', autospec=True, side_effect=replace) as operation, \
                patch('repo_maintainer.store.time.sleep') as sleep:
            with self.assertRaises(PermissionError) as failure:
                self.store.save()
        self.assertIn(failure.exception.winerror, {5, 32})
        self.assertEqual(operation.call_count, 8)
        self.assertEqual(sleep.call_count, 7)
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual((self.root / 'report.md').read_bytes(), report)
        self.assertEqual(json.loads((self.root / 'run.json.tmp').read_bytes()), self.record)

    def test_other_filesystem_errors_are_not_retried_or_hidden(self):
        old = self.target.read_bytes()
        for error in (PermissionError(errno.EACCES, 'ordinary permission denial'),
                      OSError(errno.ENOSPC, 'disk full'), FileNotFoundError(errno.ENOENT, 'missing source')):
            with self.subTest(error=type(error).__name__), patch.object(Path, 'replace', side_effect=error) as operation, \
                    patch('repo_maintainer.store.time.sleep') as sleep:
                with self.assertRaises(type(error)) as failure:
                    self.store.save()
                self.assertIs(failure.exception, error)
                self.assertEqual(operation.call_count, 1)
                sleep.assert_not_called()
            self.assertEqual(self.target.read_bytes(), old)

    def test_save_preserves_record_and_does_not_touch_other_ledgers(self):
        ledger = self.root / 'budget.json'
        ledger.write_bytes(b'{"used": 17, "held": 2}\n')
        original = copy.deepcopy(self.record)
        self.store.save()
        self.assertEqual(self.record, original)
        self.assertEqual(json.loads(self.target.read_bytes()), original)
        self.assertEqual(ledger.read_bytes(), b'{"used": 17, "held": 2}\n')


if __name__ == '__main__':
    unittest.main()
