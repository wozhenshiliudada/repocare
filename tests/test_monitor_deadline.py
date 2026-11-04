"""Deadline arming must not reacquire the worker's historical publication lock."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace

from repo_maintainer.models import SafetyError
from repo_maintainer.monitor_model_authorization import MonitorModelAuthorization
from repo_maintainer.recovery import operation_lock


class MonitorDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / ('a' * 32)
        self.run.mkdir()
        (self.run / 'run.json').write_text(json.dumps({'run_id': self.run.name}), encoding='utf-8')
        self.manager = object.__new__(MonitorModelAuthorization)
        self.clock = [100]
        self.manager.clock = lambda: self.clock[0]
        self.calls = []

        def authorize(*args, **kwargs):
            with operation_lock(self.run):
                self.calls.append((args, kwargs))
                return {'issued_epoch': 90, 'core': {'expires_epoch': 110}}
        self.manager.authorize = authorize
        self.event = {'root_execution': {'model_authorization': {'id': 'original'}}}

    def test_arm_does_not_repeat_proof_while_worker_owns_the_historical_run(self):
        arm = self.manager.prepare_deadline('monitor', self.event, object(), 'exact-job')
        entered, release = threading.Event(), threading.Event()

        def worker():
            with operation_lock(self.run):
                entered.set()
                release.wait(3)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            # Reproduce the previous submit-then-deadline ordering against a
            # barrier-held OS lock; this is an actual lock collision.
            with self.assertRaisesRegex(SafetyError, 'Another operation'):
                self.manager.deadline('monitor', self.event, object(), 'exact-job')
            timer = arm()  # A second authorization here would deterministically fail.
            timer.cancel()
            self.assertEqual(len(self.calls), 1)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_same_thread_independent_operation_lock_still_rejects(self):
        with operation_lock(self.run):
            with self.assertRaisesRegex(SafetyError, 'Another operation'):
                with operation_lock(self.run):
                    self.fail('must not weaken the original exclusive lock')

    def test_original_expiry_stops_only_the_exact_job_after_arming(self):
        stopped = threading.Event()
        calls = []
        control = SimpleNamespace(cancel=threading.Event())

        def submit(request_id, action, payload):
            calls.append((action, payload))
            control.cancel.set()
            stopped.set()
        session = SimpleNamespace(lock=threading.RLock(), active='exact-job',
            control=control, submit=submit, _stop_revision=lambda: 'original-revision')
        arm = self.manager.prepare_deadline('monitor', self.event, session, 'exact-job')
        timer = arm()
        try:
            self.clock[0] = 111
            self.assertTrue(stopped.wait(2))
            self.assertEqual(calls, [('stop_job', {'job_id': 'exact-job', 'revision': 'original-revision'})])
            self.assertEqual(len(self.calls), 1)
        finally:
            timer.cancel()


if __name__ == '__main__':
    unittest.main()
