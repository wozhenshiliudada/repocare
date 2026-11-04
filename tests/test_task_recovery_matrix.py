"""T3 process, cancellation and scope matrix using real Pi and isolated test repositories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch
import test_task_feedback as fixture
import test_task_planning as planning
import test_task_planning_process as process_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError,Policy
from repo_maintainer.task_feedback import KIND,SEAL_KIND
from repo_maintainer.repair_recovery import inspect_repair,recover_repair
from repo_maintainer.local_ui import UISession
from repo_maintainer.pi_provider import PiProvider

class RecoveryMatrixTests(unittest.TestCase):
    setUp=fixture.TaskFeedbackTests.setUp
    open=fixture.TaskFeedbackTests.open
    restart=fixture.TaskFeedbackTests.restart
    plan=fixture.TaskFeedbackTests.plan
    decision=fixture.TaskFeedbackTests.decision
    record=fixture.TaskFeedbackTests.record
    session_file=fixture.TaskFeedbackTests.session_file
    resume=fixture.TaskFeedbackTests.resume
    failed=fixture.TaskFeedbackTests.failed

    def test_live_real_pi_stop_and_provider_error_keep_unknown_no_replay(self):
        entered,release=threading.Event(),threading.Event();self.model.gate=(entered,release)
        job=self.session.submit(uuid.uuid4().hex,'plan',{'issue':planning.ISSUE})
        self.assertTrue(entered.wait(25),'real Pi did not reach the synthetic endpoint')
        target=self.session.state()['task_control']
        self.session.submit(uuid.uuid4().hex,'stop_job',{'job_id':target['job_id'],'revision':target['revision']})
        deadline=time.monotonic()+20
        while self.session.active and time.monotonic()<deadline:time.sleep(.05)
        release.set();self.model.gate=None
        self.assertIsNone(self.session.active)
        self.assertEqual('stopped',self.session.jobs[job['id']]['status'])
        self.assertFalse(self.session.plans)
        budget=self.session.registry.budget();self.assertEqual(50,budget['requests_reserved_unknown'])
        count=len(self.model.requests);self.restart()
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))
        self.assertFalse(self.session.state()['plans'])

    def test_real_planning_child_nonzero_retains_original_attempt_and_native_checkpoint(self):
        run=self.failed();old=self.record();budget=self.session.registry.budget();source=RepositoryGateway(self.source).snapshot()
        # A real child exits unsuccessfully; no fabricated Pi success/evidence can settle it.
        original_popen=subprocess.Popen
        def launch(argv,**kwargs):
            if Path(argv[0]).name.lower() in {'node','node.exe'}:
                argv=[sys.executable,'-c','import sys; sys.stdin.read(); sys.exit(19)']
            return original_popen(argv,**kwargs)
        with patch('repo_maintainer.pi_provider.subprocess.Popen',side_effect=launch):
            with self.assertRaises(SafetyError):self.resume(run)
        self.assertEqual(old['revision'],self.record()['revision']);self.assertEqual(48,self.session.registry.budget()['requests_reserved_unknown'])
        self.assertEqual(budget['requests_used'],self.session.registry.budget()['requests_used'])
        self.assertEqual(source,RepositoryGateway(self.source).snapshot())

    def crash(self,hook):
        self.session.close();self.model.scenario='revise'
        worker=process_fixture.WORKER.replace("'decision':'reject'","'decision':'approve'")
        injection="""
    from unittest.mock import patch
    import os
    from repo_maintainer.repair_recovery import ExecutionJournal
    hook=data['hook']
    if hook=='before_seal':
        session.failure_feedback.seal=lambda _record:os._exit(74)
    else:
        original=ExecutionJournal.phase
        def crash_phase(journal,value):
            original(journal,value)
            if value==hook:os._exit(75)
        ExecutionJournal.phase=crash_phase
"""
        worker=worker.replace('before=session.registry.budget()', 'before=session.registry.budget()'+injection)
        proc=subprocess.run([sys.executable,'-X','utf8','-c',worker],
            input=json.dumps({'root':str(self.root),'url':self.model.url,'issue':planning.ISSUE,'phase':'create','hook':hook}),
            encoding='utf-8',text=True,capture_output=True,env=os.environ.copy(),timeout=90)
        self.assertIn(proc.returncode,{74,75},proc.stderr)
        self.open();identity=next(iter(self.session.registry.load('run')))
        return identity,self.root/'state'/identity

    def test_real_exit_before_seal_preserves_final_failure_without_automatic_adoption(self):
        identity,directory=self.crash('before_seal');budget=self.session.registry.budget()
        raw=(directory/'run.json').read_bytes()
        self.assertFalse(self.session.registry.load(SEAL_KIND));self.assertFalse(self.session.registry.load(KIND))
        self.assertFalse(self.session._run_view(identity)['continuation']['available'])
        with self.assertRaises(SafetyError):self.session.failure_feedback.recovery_preview(identity)
        self.assertEqual(raw,(directory/'run.json').read_bytes());self.assertEqual(budget,self.session.registry.budget())
        self.assertEqual(2,len(self.model.requests))

    def test_real_exit_after_test_receipt_requires_explicit_abort_not_model_or_test_replay(self):
        identity,directory=self.crash('test_finished');before=self.session.registry.budget()
        preview=inspect_repair(directory);self.assertTrue(preview['can_abort'],preview)
        raw=(directory/'run.json').read_bytes()
        with patch('repo_maintainer.workflow.run_test',side_effect=AssertionError('Do not replay')):
            result=recover_repair(directory,preview['revision'],'test-operator')
        self.assertEqual('repair_aborted',result['status']);self.assertTrue(result['source_unchanged'])
        self.assertNotEqual(raw,(directory/'run.json').read_bytes())
        self.assertFalse(self.session.registry.load(SEAL_KIND));self.assertFalse(self.session.registry.load(KIND))
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
        self.assertNotIn('failure_feedback',self.session._run_view(identity)['continuation'])

    def test_real_exit_candidate_drift_retains_files_and_blocks_abort(self):
        identity,directory=self.crash('workspace_ready');workspace=directory/'workspace'
        file=workspace/'calculator.py';file.write_bytes(file.read_bytes()+b'# USER CHANGE\n');expected=file.read_bytes()
        preview=inspect_repair(directory);self.assertFalse(preview['can_abort'])
        with self.assertRaises(SafetyError):recover_repair(directory,preview['revision'])
        self.assertEqual(expected,file.read_bytes());self.assertEqual(2,len(self.model.requests))
        # Undo only the test's own marker, then use the existing explicitly approved abort to release its fixture.
        file.write_bytes(expected.removesuffix(b'# USER CHANGE\n'))
        fresh=inspect_repair(directory);recover_repair(directory,fresh['revision'])

    def test_policy_tests_provider_and_task_links_cannot_expand_recovery_scope(self):
        run=self.failed();preview=self.session._resume_preview(run['run_id']);before=self.session.registry.budget()
        original=self.session.policy
        for changed in [Policy(('calculator.py','tests/test_calculator.py'),original.allowed_tests),
                        Policy(original.allowed_paths,((sys.executable,'-c','print(1)'),))]:
            with self.subTest(policy=changed):
                self.session.policy=changed;self.session.task_planner.policy=changed
                with self.assertRaises(SafetyError):self.session._resume_preview(run['run_id'])
        self.session.policy=original;self.session.task_planner.policy=original
        original_url=self.inner.mock_url;self.inner.mock_url+='different'
        with self.assertRaises(SafetyError):self.resume(run)
        self.inner.mock_url=original_url
        self.session.registry.put('pi_task_link','run:'+run['run_id'],{'task_id':'f'*32})
        with self.assertRaises(SafetyError):self.session._resume_preview(run['run_id'])
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))

    def test_manual_continuation_cannot_grant_auto_or_reuse_previous_approval(self):
        run=self.failed();preview=self.session._resume_preview(run['run_id'])
        with self.assertRaises(SafetyError):self.session._operate('resume_run',{
            'run_id':run['run_id'],'revision':preview['revision'],'mode':'auto'})
        self.assertEqual(2,len(self.model.requests));self.assertEqual(1,len(self.session.runs))

    def test_stop_while_actual_candidate_test_alive_waits_then_safely_rolls_back(self):
        running=self.root/'test-running';release=self.root/'test-release'
        test=self.source/'tests/test_calculator.py'
        test.write_text(test.read_text()+"\nif add(1,2)==3:\n    import pathlib,time\n    pathlib.Path("+
            repr(str(running))+").write_text('running')\n    while not pathlib.Path("+repr(str(release))+
            ").exists(): time.sleep(0.05)\n")
        repository=RepositoryGateway(self.source)
        repository.command('add','tests/test_calculator.py')
        repository.command('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','slow test fixture')
        self.restart();plan=self.plan();before=repository.snapshot()
        job=self.session.submit(uuid.uuid4().hex,'decision',{**plan,'decision':'approve'})
        try:
            deadline=time.monotonic()+40
            while not running.exists() and self.session.active and time.monotonic()<deadline:time.sleep(.05)
            self.assertTrue(running.exists(),'candidate test did not become live')
            identity=next(iter(self.session.registry.load('run')))
            # The live controller owns the original operation lock; even inspection must not race its test.
            with self.assertRaisesRegex(SafetyError,'Another operation owns'):
                inspect_repair(self.root/'state'/identity)
            from repo_maintainer.recovery import process_identity
            execution=json.loads((self.root/'state'/identity/'repair-execution.json').read_text())
            child=execution['inflight']['identity']
            self.assertEqual(child,process_identity(child['pid']))
            target=self.session.state()['task_control']
            self.session.submit(uuid.uuid4().hex,'stop_job',{'job_id':target['job_id'],'revision':target['revision']})
        finally:release.write_text('release')
        deadline=time.monotonic()+25
        while self.session.active and time.monotonic()<deadline:time.sleep(.05)
        self.assertIsNone(self.session.active)
        self.assertEqual('stopped',self.session.jobs[job['id']]['status'])
        record=json.loads((self.root/'state'/identity/'run.json').read_text())
        self.assertEqual('rolled_back',record['status']);self.assertTrue(record['operator_stop'])
        self.assertFalse(Path(record['workspace']).exists());self.assertEqual(before,repository.snapshot())
        self.assertEqual(2,self.session.registry.budget()['requests_used'])
        with self.assertRaises(SafetyError):self.decision(plan,'approve')

    def test_cross_repository_journal_cannot_open_same_task_or_budget(self):
        self.failed();other=planning.create_fixture(self.root/'other')
        before=self.session.registry.budget();self.session.close()
        with self.assertRaises(SafetyError):UISession(other,self.session.policy,
            PiProvider(('calculator.py',),self.model.url,readonly_paths=('tests/test_calculator.py',),max_requests=50),self.root/'state')
        self.open();self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
