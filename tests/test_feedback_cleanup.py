"""Authenticated failed-run cleanup preserves factual memory and the original ledger."""
import copy
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_task_feedback as fixture
import test_task_planning as planning
import test_task_planning_process as process_fixture
from repo_maintainer.feedback_cleanup import KIND as CLEANUP
from repo_maintainer.task_feedback import KIND,SEAL_KIND
from repo_maintainer.local_ui import json_digest,validate_payload
from repo_maintainer.models import SafetyError
from repo_maintainer.workflow import cleanup_run

class FailureCleanupTests(unittest.TestCase):
    setUp=fixture.TaskFeedbackTests.setUp
    open=fixture.TaskFeedbackTests.open
    restart=fixture.TaskFeedbackTests.restart
    plan=fixture.TaskFeedbackTests.plan
    decision=fixture.TaskFeedbackTests.decision
    record=fixture.TaskFeedbackTests.record
    session_file=fixture.TaskFeedbackTests.session_file
    resume=fixture.TaskFeedbackTests.resume
    failed=fixture.TaskFeedbackTests.failed

    def cleanup(self,run):
        identity=run['run_id']
        if identity not in self.session.claimed_runs:
            self.session._operate('claim_run',{'run_id':identity,'revision':json_digest(self.session._run_view(identity))})
        return self.session._operate('cleanup',{'run_id':identity,'revision':json_digest(self.session._run_view(identity))})

    def pending(self):
        run=self.failed()
        with patch.object(self.session.feedback_cleanup,'finish',side_effect=SafetyError('injected after cleanup exit')):
            with self.assertRaisesRegex(SafetyError,'after cleanup exit'):self.cleanup(run)
        return run

    def test_normal_cleanup_and_repeat_preserve_original_memory_then_new_approval_green(self):
        run=self.failed();seal=copy.deepcopy(self.session.registry.load(SEAL_KIND));feedback=copy.deepcopy(self.session.registry.load(KIND))
        budget=self.session.registry.budget();native=self.session_file().read_bytes()
        self.assertEqual('discarded',self.cleanup(run)['workflow_status'])
        self.assertEqual(seal,self.session.registry.load(SEAL_KIND));self.assertEqual(feedback,self.session.registry.load(KIND))
        file=self.root/'state'/run['run_id']/'run.json';raw=file.read_bytes()
        self.cleanup(run);self.assertEqual(raw,file.read_bytes())
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(native,self.session_file().read_bytes())
        self.restart();next_plan=self.resume(run)
        self.assertEqual('candidate_ready',self.decision(next_plan,'approve')['workflow_status'])

    def test_cleanup_finished_before_binding_requires_explicit_reconciliation_without_side_effects(self):
        run=self.pending();before=self.session.registry.budget();self.restart()
        self.assertFalse(self.session._run_view(run['run_id'])['continuation']['available'])
        directory=self.root/'state'/run['run_id'];raw={p.name:p.read_bytes() for p in directory.glob('*.json')}
        preview=self.session.feedback_cleanup.preview(run['run_id'])
        self.assertEqual('pending',self.session.registry.load(CLEANUP)[run['run_id']]['status'])
        result=self.session._operate('reconcile_feedback_cleanup',{'run_id':run['run_id'],'revision':preview['revision']})
        self.assertTrue(result['feedback_cleanup_reconciled']);self.assertEqual(2,len(self.model.requests))
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(raw,{p.name:p.read_bytes() for p in directory.glob('*.json')})
        self.assertTrue(self.session._run_view(run['run_id'])['continuation']['available'])
        with self.assertRaises(SafetyError):self.session.feedback_cleanup.reconcile(run['run_id'],preview['revision'])

    def test_intent_before_cleanup_exit_can_be_closed_without_running_git_or_changing_files(self):
        run=self.failed();identity=run['run_id']
        from repo_maintainer.recovery import operation_lock
        with operation_lock(self.session._run_dir(identity)):self.session.feedback_cleanup.begin(identity)
        directory=self.root/'state'/identity;raw=(directory/'run.json').read_bytes();self.restart()
        preview=self.session.feedback_cleanup.preview(identity)
        self.session.feedback_cleanup.reconcile(identity,preview['revision'])
        self.assertEqual(raw,(directory/'run.json').read_bytes());self.assertEqual(2,len(self.model.requests))

    def test_unrecorded_cli_cleanup_cannot_rebind_or_overwrite_old_receipts(self):
        run=self.failed();feedback=self.session.registry.load(KIND)
        cleanup_run(self.root/'state'/run['run_id'])
        with self.assertRaises(SafetyError):self.session._resume_preview(run['run_id'])
        with self.assertRaises(SafetyError):self.session.feedback_cleanup.preview(run['run_id'])
        self.assertEqual(feedback,self.session.registry.load(KIND));self.assertFalse(self.session.registry.load(CLEANUP))

    def test_pending_conversion_rejects_changed_protected_facts_or_original_authority(self):
        run=self.pending();directory=self.root/'state'/run['run_id'];file=directory/'run.json';raw=file.read_bytes()
        for field in ['issue','diff','rounds','planning_origin','policy']:
            with self.subTest(field=field):
                record=json.loads(raw);record[field]='forged'
                file.write_text(json.dumps(record))
                try:
                    with self.assertRaises((SafetyError,TypeError,AttributeError)):self.session.feedback_cleanup.preview(run['run_id'])
                finally:file.write_bytes(raw)
        intent=copy.deepcopy(self.session.registry.load(CLEANUP)[run['run_id']])
        changed=copy.deepcopy(intent);changed['before_record_sha256']='0'*64
        self.session.registry.put(CLEANUP,run['run_id'],changed)
        with self.assertRaises(SafetyError):self.session.feedback_cleanup.preview(run['run_id'])
        self.session.registry.put(CLEANUP,run['run_id'],intent)
        self.assertEqual(2,len(self.model.requests))

    def test_cleanup_rejects_expanded_git_audit_even_with_the_longpaths_prefix(self):
        run=self.pending();file=self.root/'state'/run['run_id']/'run.json';raw=file.read_bytes()
        saved=json.loads(raw)
        before=self.session.registry.load(CLEANUP)[run['run_id']]['before_record']['command_count']
        self.assertEqual(saved['git_commands'][before]['command'][:3],['git','-c','core.longpaths=true'])
        for command in (['git','reset','--hard'],['git','-c','core.longpaths=false','status'],
                        saved['git_commands'][before]['command']+['--extra']):
            with self.subTest(command=command):
                changed=copy.deepcopy(saved)
                changed['git_commands'][before]['command']=command
                file.write_text(json.dumps(changed),encoding='utf-8')
                try:
                    with self.assertRaisesRegex(SafetyError,'Cleanup Git audit'):
                        self.session.feedback_cleanup.preview(run['run_id'])
                finally:file.write_bytes(raw)
        self.assertEqual(2,len(self.model.requests))

    def test_historical_exact_git_prefix_remains_readable_without_rewriting_original_receipts(self):
        run=self.pending();identity=run['run_id'];file=self.root/'state'/identity/'run.json';raw=file.read_bytes()
        saved=json.loads(raw)
        before=self.session.registry.load(CLEANUP)[identity]['before_record']['command_count']
        for audit in saved['git_commands'][before:]:
            self.assertEqual(audit['command'][:3],['git','-c','core.longpaths=true'])
            audit['command']=['git',*audit['command'][3:]]
        file.write_text(json.dumps(saved),encoding='utf-8')
        try:
            original=file.read_bytes();budget=self.session.registry.budget()
            receipts=copy.deepcopy(self.session.registry.load(SEAL_KIND))
            preview=self.session.feedback_cleanup.preview(identity)
            self.session.feedback_cleanup.reconcile(identity,preview['revision'])
            self.assertEqual(original,file.read_bytes())
            self.assertEqual(receipts,self.session.registry.load(SEAL_KIND))
            self.assertEqual(budget,self.session.registry.budget())
            self.assertTrue(self.session._run_view(identity)['continuation']['available'])
        finally:file.write_bytes(raw)
        self.assertEqual(2,len(self.model.requests))

    def test_unknown_or_old_session_challenge_cannot_finalize_pending_transition(self):
        run=self.pending();preview=self.session.feedback_cleanup.preview(run['run_id']);self.restart()
        with self.assertRaisesRegex(SafetyError,'session changed'):self.session.feedback_cleanup.reconcile(run['run_id'],preview['revision'])
        self.session.registry.reserve();before=self.session.registry.budget()
        with self.assertRaisesRegex(SafetyError,'Unknown consumption'):self.session.feedback_cleanup.preview(run['run_id'])
        self.assertEqual(before,self.session.registry.budget())

    def test_reconciliation_api_never_accepts_client_receipts(self):
        with self.assertRaises(SafetyError):validate_payload('reconcile_feedback_cleanup',{
            'request_id':'a'*32,'run_id':'b'*32,'revision':'c'*64,'receipt':{'forged':True}})

    def process_gap(self,phase):
        self.session.close();self.model.scenario='revise'
        worker=process_fixture.WORKER.replace("'decision':'reject'","'decision':'approve'")
        original="        run_id=run['run_id']"
        extra="""
        from repo_maintainer.local_ui import json_digest
        import os
        if data['gap']=='before':
            begin=session.feedback_cleanup.begin
            def exit_after_intent(identity):
                begin(identity)
                os._exit(77)
            session.feedback_cleanup.begin=exit_after_intent
        else:session.feedback_cleanup.finish=lambda _identity:os._exit(76)
        session._operate('cleanup',{'run_id':run_id,'revision':json_digest(session._run_view(run_id))})
"""
        worker=worker.replace(original,original+extra)
        result=subprocess.run([sys.executable,'-X','utf8','-c',worker],
            input=json.dumps({'root':str(self.root),'url':self.model.url,'issue':planning.ISSUE,'phase':'create','gap':phase}),
            capture_output=True,text=True,encoding='utf-8',env=os.environ.copy(),timeout=120)
        self.assertEqual(77 if phase=='before' else 76,result.returncode,result.stderr)
        self.open();identity=next(iter(self.session.registry.load('run')));budget=self.session.registry.budget()
        self.assertEqual('pending',self.session.registry.load(CLEANUP)[identity]['status'])
        directory=self.root/'state'/identity;raw={p.name:p.read_bytes() for p in directory.glob('*.json')}
        native=next((self.root/'state/task-sessions').glob('*/sessions/*.jsonl')).read_bytes()
        preview=self.session.feedback_cleanup.preview(identity)
        self.session.feedback_cleanup.reconcile(identity,preview['revision'])
        self.assertEqual(raw,{p.name:p.read_bytes() for p in directory.glob('*.json')})
        self.assertEqual(native,next((self.root/'state/task-sessions').glob('*/sessions/*.jsonl')).read_bytes())
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
        plan=self.resume({'run_id':identity})
        self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])

    def test_real_process_exit_after_cleanup_intent_then_explicit_reconciliation(self):
        self.process_gap('before')

    def test_real_process_exit_after_cleanup_report_then_explicit_reconciliation(self):
        self.process_gap('after')
