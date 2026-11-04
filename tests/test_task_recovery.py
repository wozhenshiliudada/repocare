"""Real finalization crash and conservative feedback promotion; no paid model traffic."""
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import test_task_feedback as feedback_fixture
import test_task_planning as fixture
import test_task_planning_process as process_fixture
from repo_maintainer.models import SafetyError
from repo_maintainer.task_feedback import KIND,SEAL_KIND
from repo_maintainer.task_session import TaskSessionLease
from repo_maintainer.job_control import OperationStopped
from repo_maintainer.local_ui import validate_payload

CRASH_WORKER=process_fixture.WORKER.replace("'decision':'reject'","'decision':'approve'").replace(
    "before=session.registry.budget()","before=session.registry.budget()\n    session.failure_feedback.capture=lambda _identity:__import__('os')._exit(73)")

class TaskRecoveryTests(unittest.TestCase):
    setUp=feedback_fixture.TaskFeedbackTests.setUp
    open=feedback_fixture.TaskFeedbackTests.open
    restart=feedback_fixture.TaskFeedbackTests.restart
    plan=feedback_fixture.TaskFeedbackTests.plan
    decision=feedback_fixture.TaskFeedbackTests.decision
    record=feedback_fixture.TaskFeedbackTests.record
    session_file=feedback_fixture.TaskFeedbackTests.session_file
    resume=feedback_fixture.TaskFeedbackTests.resume
    failed=feedback_fixture.TaskFeedbackTests.failed

    def gap(self):
        run=self.failed()
        with self.session.registry.transaction() as db:
            db.execute('DELETE FROM objects WHERE kind=? AND id=?',(KIND,run['run_id']))
        return run

    def recover(self,run,preview=None):
        value=preview or self.session.failure_feedback.recovery_preview(run['run_id'])
        return self.session._operate('recover_feedback',{'run_id':run['run_id'],'revision':value['revision']})

    def test_real_process_exit_after_final_seal_open_only_recover_then_new_approval_green(self):
        self.session.close();self.model.scenario='revise'
        proc=subprocess.run([sys.executable,'-X','utf8','-c',CRASH_WORKER],
            input=json.dumps({'root':str(self.root),'url':self.model.url,'issue':fixture.ISSUE,'phase':'create'}),
            text=True,encoding='utf-8',capture_output=True,env=os.environ.copy(),timeout=90)
        self.assertEqual(73,proc.returncode,proc.stderr)
        self.open();run={'run_id':next(iter(self.session.registry.load('run')))}
        self.assertEqual(2,len(self.model.requests))
        self.assertFalse(self.session.registry.load(KIND))
        self.assertIn(run['run_id'],self.session.registry.load(SEAL_KIND))
        before=self.session.registry.budget();original=self.source.joinpath('calculator.py').read_bytes()
        view=self.session.state()['runs'][0]
        self.assertFalse(view['continuation']['available'])
        self.assertEqual([1],view['failure_recovery']['test_exits'])
        receipt=self.session.registry.load(SEAL_KIND)[run['run_id']]
        self.assertTrue(self.recover(run)['feedback_recovered'])
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
        self.assertEqual(receipt,self.session.registry.load(KIND)[run['run_id']])
        with self.assertRaises(SafetyError):self.recover(run,view['failure_recovery'])
        plan=self.resume(run)
        self.assertEqual('awaiting_approval',self.session.plans[plan['plan_id']]['view']['status'])
        self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])
        self.assertEqual(original,self.source.joinpath('calculator.py').read_bytes())
        self.assertEqual(4,self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])

    def test_readonly_preview_does_not_rebuild_missing_feedback_or_mutate_ledger(self):
        run=self.gap();before=self.session.registry.budget();seal=self.session.registry.load(SEAL_KIND)
        self.restart()
        for _ in range(2):self.session.state()
        self.assertEqual(seal,self.session.registry.load(SEAL_KIND));self.assertFalse(self.session.registry.load(KIND))
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))

    def test_missing_final_seal_never_adopts_old_or_unfinished_evidence(self):
        run=self.gap();before=self.session.registry.budget()
        with self.session.registry.transaction() as db:
            db.execute('DELETE FROM objects WHERE kind=? AND id=?',(SEAL_KIND,run['run_id']))
        self.restart()
        with self.assertRaisesRegex(SafetyError,'finalization seal'):self.recover(run)
        self.assertFalse(self.session.registry.load(KIND));self.assertEqual(before,self.session.registry.budget())

    def test_changed_report_execution_native_or_source_blocks_without_requests(self):
        run=self.gap();before=self.session.registry.budget()
        directory=self.root/'state'/run['run_id']
        files=[directory/'run.json',directory/'repair-execution.json',self.session_file(),self.source/'calculator.py']
        for file in files:
            with self.subTest(file=file.name):
                original=file.read_bytes()
                try:
                    if file.suffix=='.json':
                        value=json.loads(original);value['tampered_marker']=True
                        file.write_text(json.dumps(value))
                    else:file.write_bytes(original+b'broken\n')
                    with self.assertRaises(SafetyError):self.recover(run)
                    self.assertFalse(self.session.registry.load(KIND));self.assertEqual(before,self.session.registry.budget())
                finally:file.write_bytes(original)
        self.assertEqual(2,len(self.model.requests))

    def test_unknown_model_consumption_is_not_resolved_by_feedback_recovery(self):
        run=self.gap();self.session.registry.reserve();before=self.session.registry.budget()
        self.restart()
        with self.assertRaisesRegex(SafetyError,'Unknown model consumption'):self.recover(run)
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(48,before['requests_reserved_unknown'])
        self.assertEqual(2,len(self.model.requests))

    def test_old_session_challenge_and_changed_scope_are_refused(self):
        run=self.gap();preview=self.session.failure_feedback.recovery_preview(run['run_id']);before=self.session.registry.budget()
        self.restart()
        with self.assertRaisesRegex(SafetyError,'session changed'):self.recover(run,preview)
        original=self.inner.mock_url;self.inner.mock_url='http://127.0.0.1:1/changed'
        try:
            with self.assertRaises(SafetyError):self.recover(run)
        finally:self.inner.mock_url=original
        self.assertEqual(before,self.session.registry.budget());self.assertFalse(self.session.registry.load(KIND))

    def test_native_written_but_checkpoint_commit_failed_preserves_consumption_and_safe_leaf(self):
        run=self.failed();first=self.record();original=self.session_file().read_bytes()
        with patch.object(TaskSessionLease,'commit',side_effect=SafetyError('injected CAS gap')):
            with self.assertRaisesRegex(SafetyError,'CAS gap'):self.resume(run)
        self.assertEqual(first['revision'],self.record()['revision'])
        raw=self.session_file().read_bytes();self.assertTrue(raw.startswith(original));self.assertGreater(len(raw),len(original))
        self.assertEqual(4,self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])
        self.restart();self.assertEqual(4,len(self.model.requests))
        second=self.resume(run)
        # The abandoned round remains on disk, but only committed ancestors enter this next model request.
        self.assertEqual(2,len([m for m in self.model.requests[4]['messages'] if m['role']=='user']))
        self.assertEqual('candidate_ready',self.decision(second,'approve')['workflow_status'])
        self.assertEqual(6,self.session.registry.budget()['requests_used'])

    def test_stop_before_reservation_and_invalid_recovery_payload_do_not_call_model(self):
        self.inner._cancel_event=threading.Event();self.inner._cancel_event.set()
        with self.assertRaises(OperationStopped):self.plan()
        self.assertEqual(0,len(self.model.requests));self.assertEqual(0,self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])
        with self.assertRaises(SafetyError):validate_payload('recover_feedback',{
            'request_id':'a'*32,'run_id':'b'*32,'revision':'c'*64,'receipt':{'forge':True}})
