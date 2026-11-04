"""Actual original controller -> native Pi summary -> settled checkpoint -> same-task repair."""
import copy
import json
import unittest
import uuid
from unittest.mock import patch
import test_task_planning as fixture
from repo_maintainer.models import SafetyError,digest
from repo_maintainer.compaction_config import validate_config
from repo_maintainer.task_session import fingerprint,validate_transcript

CONFIG={'schema_version':1,'enabled':True,'context_window_tokens':1048576,'trigger_tokens':700000,
        'keep_recent_tokens':256,'summary_max_output_tokens':4096}


class CompactionTests(unittest.TestCase):
    setUp=fixture.TaskPlanningTests.setUp
    open=fixture.TaskPlanningTests.open
    restart=fixture.TaskPlanningTests.restart
    plan=fixture.TaskPlanningTests.plan
    decision=fixture.TaskPlanningTests.decision
    record=fixture.TaskPlanningTests.record
    session_file=fixture.TaskPlanningTests.session_file

    def configure(self,config=None):
        return self.session._operate('context_settings',{'config':config or copy.deepcopy(CONFIG),
                    'revision':self.session.compaction.settings_revision()})

    def resume(self,run):
        preview=self.session._resume_preview(run['run_id'])
        return self.session._operate('resume_run',{'run_id':run['run_id'],'revision':preview['revision'],'mode':'approval'})

    def seed(self,config=None):
        self.configure(config);self.model.summary_text='SUMMARY_ONE: Preserve signed addition and failure references. Read current source and run current tests.'
        self.model.scenario='repeat-failure';first=self.plan();run=self.decision(first,'approve')
        self.model.scenario='correct';second=self.resume(run)
        self.assertEqual(2,self.record()['revision']);return run,second,self.record()['task_id']

    def compact(self,identity):
        preview=self.session._operate('compaction_preview',{'task_id':identity})['compaction_preview']
        self.assertTrue(preview['can_compact'],preview)
        return self.session._operate('compact_task',{'task_id':identity,'revision':preview['revision']})

    def test_two_native_compactions_restart_same_task_new_plan_and_approval_green(self):
        run,second,identity=self.seed();file=self.session_file();before=file.read_bytes();first_session=self.record()['checkpoint']['session_id']
        original_budget=self.session.registry.budget();first=self.compact(identity)
        self.assertEqual(2,self.record()['revision']);self.assertEqual(first_session,self.record()['checkpoint']['session_id'])
        self.assertEqual(before,file.read_bytes()[:len(before)]);self.assertEqual('compaction',json.loads(file.read_text().splitlines()[-1])['type'])
        self.assertEqual('awaiting_approval',self.session.state()['plans'][-1]['status'])
        self.assertGreater(self.session.registry.budget()['requests_used'],original_budget['requests_used'])
        self.assertEqual(1,len(self.session.registry.load('pi_compaction_receipt')))
        count=len(self.model.requests);self.restart();self.assertEqual(count,len(self.model.requests))
        third=self.resume(run);self.assertEqual(identity,self.record()['task_id']);self.assertEqual(3,self.record()['revision'])
        prefix=file.read_bytes();self.model.summary_text='SUMMARY_TWO: Keep the same goal, original failed evidence and latest plan; verify independently.'
        previous_summary_calls=len([r for r in self.model.requests if not r.get('tools')])
        self.compact(identity);self.assertEqual(3,self.record()['revision']);self.assertEqual(prefix,file.read_bytes()[:len(prefix)])
        summary_requests=[r for r in self.model.requests if not r.get('tools')]
        # Pi may summarize history and a split turn separately; with no new history
        # it directly retains previousSummary and requests only a turn-prefix summary.
        current_summary_requests=summary_requests[previous_summary_calls:]
        self.assertTrue(current_summary_requests)
        latest_compaction=json.loads(file.read_bytes().splitlines()[-1])
        self.assertEqual('compaction',latest_compaction['type'])
        self.assertIn('SUMMARY_TWO',latest_compaction['summary'])
        supplied_prior='SUMMARY_ONE' in json.dumps([r['messages'] for r in current_summary_requests])
        retained_prior='SUMMARY_ONE' in latest_compaction['summary']
        self.assertTrue(supplied_prior or retained_prior,'Previous summary was neither supplied nor retained')
        self.assertTrue(all(0<r['max_tokens']<=4096 for r in summary_requests))
        self.assertEqual(2,len(self.session.registry.load('pi_compaction_receipt')))
        spent=self.session.registry.budget();self.restart();self.assertEqual(spent,self.session.registry.budget())
        fourth=self.resume(run);self.assertEqual(4,self.record()['revision']);self.assertEqual(identity,self.record()['task_id'])
        self.assertEqual('candidate_ready',self.decision(fourth,'approve')['workflow_status'])
        self.assertEqual(len(self.model.requests),self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])
        self.assertTrue(self.session.state()['runs'][-1]['source_unchanged'])

    def test_schema_rejections_survive_two_compactions_restart_and_new_approval(self):
        self.model.schema_calls=[('read_source',{'query':'def im('}),
            ('propose_repair',{'diagnosis':'format error','risk':'fixture','edits':['not an object']})]
        self.test_two_native_compactions_restart_same_task_new_plan_and_approval_green()

    def test_truncated_or_empty_summary_leaves_original_native_and_checkpoint_unknown_budget(self):
        run,second,identity=self.seed();file=self.session_file();raw=file.read_bytes();record=copy.deepcopy(self.record())
        self.model.summary_mode='length'
        with self.assertRaises(SafetyError):self.compact(identity)
        self.assertEqual(raw,file.read_bytes());self.assertEqual(record,self.record())
        self.assertFalse(self.session.registry.load('pi_compaction_receipt'))
        self.assertEqual(4,self.session.registry.budget()['requests_used'])
        self.assertEqual(46,self.session.registry.budget()['requests_reserved_unknown'])

    def test_changed_source_stale_preview_or_exhausted_budget_cannot_call_summary(self):
        run,second,identity=self.seed();preview=self.session.compaction.detail(identity,fixture.ISSUE);count=len(self.model.requests)
        file=self.source/'calculator.py';raw=file.read_bytes();file.write_bytes(raw+b'# user changed\n')
        try:
            with self.assertRaises(SafetyError):self.compact(identity)
        finally:file.write_bytes(raw)
        with self.assertRaises(SafetyError):self.session._operate('compact_task',{'task_id':identity,'revision':'a'*64})
        attempt,allowance=self.session.registry.reserve();self.session.registry.settle(attempt,{'requests':allowance},success=True)
        with self.assertRaises(SafetyError):self.session._operate('compact_task',{'task_id':identity,'revision':preview['revision']})
        self.assertEqual(count,len(self.model.requests));self.assertEqual(50,self.session.registry.budget()['requests_used'])

    def test_future_settings_never_enrich_old_task_and_get_is_readonly(self):
        self.plan();identity=self.record()['task_id'];old=self.session.registry.load('pi_context_policy')[identity]
        self.configure();self.assertEqual(old,self.session.registry.load('pi_context_policy')[identity])
        self.assertFalse(self.session.compaction.detail(identity,fixture.ISSUE)['can_compact'])
        before=self.session.registry.path.read_bytes();count=len(self.model.requests)
        self.session.state();self.session.state();self.assertEqual(before,self.session.registry.path.read_bytes())
        self.assertEqual(count,len(self.model.requests))

    def test_tampered_summary_or_deleted_metered_receipt_blocks_native_before_new_reservation(self):
        run,second,identity=self.seed();self.compact(identity);file=self.session_file();raw=file.read_bytes();count=len(self.model.requests)
        rows=[json.loads(line) for line in raw.splitlines()];rows[-1]['summary']='Invented replacement summary'
        file.write_bytes(b''.join(json.dumps(r).encode()+b'\n' for r in rows))
        try:
            with self.assertRaises(SafetyError):self.session.task_planner.reference('run',run['run_id'],fixture.ISSUE)
        finally:file.write_bytes(raw)
        with self.session.registry.transaction() as db:db.execute("DELETE FROM objects WHERE kind='pi_compaction_receipt'")
        with self.assertRaises(SafetyError):self.session.task_planner.reference('run',run['run_id'],fixture.ISSUE)
        self.assertEqual(count,len(self.model.requests))

    def test_threshold_automatically_compacts_between_rounds_using_the_same_ledger(self):
        config={**CONFIG,'trigger_tokens':2048};run,second,identity=self.seed(config)
        before=len(self.model.requests);third=self.resume(run)
        self.assertEqual(3,self.record()['revision']);self.assertEqual(identity,self.record()['task_id'])
        self.assertEqual(1,len(self.session.registry.load('pi_compaction_receipt')))
        self.assertGreater(len(self.model.requests)-before,2)
        self.assertEqual(len(self.model.requests),self.session.registry.budget()['requests_used'])
        self.assertEqual('awaiting_approval',self.session.state()['plans'][-1]['status'])

    def test_disabled_compaction_context_limit_stops_before_any_new_reservation(self):
        self.configure({**CONFIG,'enabled':False,'context_window_tokens':145000,'trigger_tokens':2000})
        self.model.scenario='repeat-failure';plan=self.plan();run=self.decision(plan,'approve')
        record=copy.deepcopy(self.record());file=self.session_file();raw=file.read_bytes();budget=self.session.registry.budget()
        attempts=self.session.registry.load('attempt');count=len(self.model.requests)
        with self.assertRaisesRegex(SafetyError,'Context capacity'):self.resume(run)
        self.assertEqual(record,self.record());self.assertEqual(raw,file.read_bytes());self.assertEqual(budget,self.session.registry.budget())
        self.assertEqual(attempts,self.session.registry.load('attempt'));self.assertEqual(count,len(self.model.requests))

    def test_empty_or_tool_call_summary_never_changes_original_checkpoint(self):
        run,second,identity=self.seed();record=copy.deepcopy(self.record());file=self.session_file();raw=file.read_bytes()
        self.model.summary_text=''
        with self.assertRaises(SafetyError):self.compact(identity)
        self.assertEqual(record,self.record());self.assertEqual(raw,file.read_bytes());self.assertFalse(self.session.registry.load('pi_compaction_receipt'))
        pending=self.session.state()['task_compaction']['pending'][0];budget=self.session.registry.budget();count=len(self.model.requests)
        self.session._operate('resolve_compaction',{'intent_id':pending['id'],'revision':pending['revision'],'choice':'abandon'})
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))

    def test_settled_summary_checkpoint_gap_reconciles_without_model_or_budget_replay(self):
        from repo_maintainer.task_session import TaskSessionLease
        run,second,identity=self.seed();old=copy.deepcopy(self.record());file=self.session_file();prefix=file.read_bytes()
        with patch.object(TaskSessionLease,'commit_compaction',side_effect=SafetyError('injected checkpoint gap')):
            with self.assertRaises(SafetyError):self.compact(identity)
        self.assertEqual(old,self.record());self.assertEqual(prefix,file.read_bytes()[:len(prefix)])
        count=len(self.model.requests);budget=self.session.registry.budget();self.assertEqual(0,budget['requests_reserved_unknown'])
        with self.assertRaisesRegex(SafetyError,'pending compaction'):self.resume(run)
        self.restart();pending=self.session.state()['task_compaction']['pending'][0]
        result=self.session._operate('resolve_compaction',{'intent_id':pending['id'],'revision':pending['revision'],'choice':'reconcile'})
        self.assertEqual('committed',result['status']);self.assertEqual(count,len(self.model.requests));self.assertEqual(budget,self.session.registry.budget())
        self.assertEqual(2,self.session.registry.load('pi_task')[identity]['revision'])
        self.assertEqual(1,len(self.session.registry.load('pi_compaction_receipt')))
        self.assertFalse(self.session.state()['task_compaction']['pending'])
        self.model.scenario='correct';self.resume(run);self.assertEqual(identity,self.record()['task_id'])

    def test_original_checkpoint_commit_with_intent_gap_is_metadata_only_reconcile(self):
        run,second,identity=self.seed();original_put=self.session.registry.put
        def fail_intent(kind,key,value,db=None):
            if kind=='pi_compaction_intent' and value.get('status')=='committed':raise SafetyError('injected final intent gap')
            return original_put(kind,key,value,db)
        with patch.object(self.session.registry,'put',side_effect=fail_intent):
            with self.assertRaises(SafetyError):self.compact(identity)
        record=copy.deepcopy(self.record());raw=self.session_file().read_bytes();count=len(self.model.requests);budget=self.session.registry.budget()
        pending=self.session.state()['task_compaction']['pending'][0]
        result=self.session._operate('resolve_compaction',{'intent_id':pending['id'],'revision':pending['revision'],'choice':'reconcile'})
        self.assertEqual('committed',result['status']);self.assertEqual(record,self.record());self.assertEqual(raw,self.session_file().read_bytes())
        self.assertEqual(count,len(self.model.requests));self.assertEqual(budget,self.session.registry.budget())

    def test_live_stop_preserves_old_checkpoint_and_unknown_consumption(self):
        import threading
        run,second,identity=self.seed();record=copy.deepcopy(self.record());file=self.session_file();prefix=file.read_bytes()
        self.model.gate=(threading.Event(),threading.Event())
        preview=self.session.compaction.detail(identity,fixture.ISSUE);request=uuid.uuid4().hex
        self.session.submit(request,'compact_task',{'task_id':identity,'revision':preview['revision']})
        try:
            self.assertTrue(self.model.gate[0].wait(20))
            control=self.session.state()['task_control'];self.assertTrue(control['can_stop'])
            self.session.submit(uuid.uuid4().hex,'stop_job',{'job_id':request,'revision':control['revision']})
        finally:self.model.gate[1].set()
        self.session.executor.submit(lambda:None).result(timeout=60)
        self.assertEqual('stopped',self.session.jobs[request]['status']);self.assertEqual(record,self.record())
        self.assertEqual(prefix,file.read_bytes()[:len(prefix)]);self.assertFalse(self.session.registry.load('pi_compaction_receipt'))
        self.assertEqual(4,self.session.registry.budget()['requests_used']);self.assertEqual(46,self.session.registry.budget()['requests_reserved_unknown'])

    def test_context_policy_loss_is_blocked_before_another_reservation(self):
        run,second,identity=self.seed();budget=self.session.registry.budget();count=len(self.model.requests)
        with self.session.registry.transaction() as db:db.execute('DELETE FROM objects WHERE kind=? AND id=?',('pi_context_policy',identity))
        with self.assertRaises(SafetyError):self.resume(run)
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))

    def test_native_read_capacity_blocks_summary_and_planning_before_new_reservation(self):
        run,second,identity=self.seed();budget=self.session.registry.budget();count=len(self.model.requests)
        record=copy.deepcopy(self.record());raw=self.session_file().read_bytes()
        with patch('repo_maintainer.task_session.MAX_SESSION_BYTES',len(raw)+1024):
            preview=self.session.compaction.detail(identity,fixture.ISSUE)
            self.assertFalse(preview['native_append_room']);self.assertFalse(preview['can_compact'])
            with self.assertRaises(SafetyError):self.session._operate('compact_task',{'task_id':identity,'revision':preview['revision']})
            with self.assertRaisesRegex(SafetyError,'Native history capacity'):self.resume(run)
        self.assertEqual(record,self.record());self.assertEqual(raw,self.session_file().read_bytes())
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))

    def test_tool_call_summary_never_changes_original_native_or_checkpoint(self):
        run,second,identity=self.seed();record=copy.deepcopy(self.record());raw=self.session_file().read_bytes()
        self.model.summary_mode='tool'
        with self.assertRaises(SafetyError):self.compact(identity)
        self.assertEqual(record,self.record());self.assertEqual(raw,self.session_file().read_bytes())
        self.assertFalse(self.session.registry.load('pi_compaction_receipt'))

    def test_source_drift_during_summary_keeps_old_checkpoint_prefix_and_unknown_allowance(self):
        run,second,identity=self.seed();record=copy.deepcopy(self.record());file=self.session_file();prefix=file.read_bytes()
        source=self.source/'calculator.py';original=source.read_bytes()
        def drift(body,count):
            if not body.get('tools'):source.write_bytes(original+b'# external edit during summary\n')
        self.model.on_request=drift
        try:
            with self.assertRaises(SafetyError):self.compact(identity)
        finally:source.write_bytes(original);self.model.on_request=None
        self.assertEqual(record,self.record());self.assertEqual(prefix,file.read_bytes()[:len(prefix)])
        self.assertFalse(self.session.registry.load('pi_compaction_receipt'))
        self.assertEqual(4,self.session.registry.budget()['requests_used'])
        self.assertEqual(46,self.session.registry.budget()['requests_reserved_unknown'])


class ConfigTests(unittest.TestCase):
    def test_context_schema_rejects_unknown_capacity_boolean_limits_and_no_output_room(self):
        self.assertEqual(CONFIG,validate_config(CONFIG))
        for patch in ({'enabled':1},{'trigger_tokens':True},{'context_window_tokens':0},{'summary_max_output_tokens':2**53},
                      {'trigger_tokens':1048000},{'keep_recent_tokens':700000},{'commands':['git']}):
            with self.assertRaises(SafetyError):validate_config({**CONFIG,**patch})

    def test_large_declared_summary_and_context_keep_the_original_room_requirement(self):
        config={**CONFIG,'context_window_tokens':4000000,'trigger_tokens':2000000,
                'keep_recent_tokens':300000,'summary_max_output_tokens':1500000}
        self.assertEqual(config,validate_config(config))
        for patch in ({'summary_max_output_tokens':2000000},{'keep_recent_tokens':2000000},
                      {'context_window_tokens':2**53},{'trigger_tokens':2**53}):
            with self.assertRaises(SafetyError):validate_config({**config,**patch})
