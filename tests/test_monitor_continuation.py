"""Original root monitor/repair/Native/ledger; loopback model, never external fees."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient
import test_monitor_execution as original
import test_maintenance_monitor as fixture
from test_repository_workbench import FakeGitHub,FakeServer
from test_task_planning import SyntheticModel
from repo_maintainer.dashboard import read_json
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench,create_workbench_app
from repo_maintainer.monitor_history import history,recovery_preview
from repo_maintainer.workflow import cleanup_run


class MonitorContinuationTests(unittest.TestCase):
    finish=fixture.MaintenanceTests.finish
    worker=original.MonitorExecutionTests.worker
    scope=original.MonitorExecutionTests.scope
    view=original.MonitorExecutionTests.view
    payload=original.MonitorExecutionTests.payload
    preview=original.MonitorExecutionTests.preview
    configure=original.MonitorExecutionTests.configure
    prepare=original.MonitorExecutionTests.prepare
    repair_preview=original.MonitorExecutionTests.repair_preview
    execute=original.MonitorExecutionTests.execute
    session=original.MonitorExecutionTests.session
    preserve_cleanup=original.MonitorExecutionTests.preserve_cleanup

    def setUp(self):
        self.model=SyntheticModel();self.addCleanup(self.model.close)
        fixture.MaintenanceTests.setUp(self)

    def new_hub(self):
        return RepositoryWorkbench(self.root/'hub',self.config,request_budget=8,allow_auto=True,
            github=FakeGitHub(),server_factory=FakeServer,
            provider_factory=lambda p:PiProvider(p.allowed_paths,self.model.url,readonly_paths=p.readonly_paths,max_requests=8))

    def parent(self,decision='reject'):
        mid,event=self.prepare()
        self.execute(self.repair_preview(mid,event))
        session=self.session(mid,event)
        plan=next(iter(session.plans.values()))['view']
        result=session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':decision})
        self.preserve_cleanup(session)
        return mid,event,result['run_id']

    def inspect(self,mid,event,run_id=None,mode='approval'):
        return self.worker(self.hub.monitor.continuation.inspect,{'monitor_id':mid,'event_id':event['id'],'run_id':run_id,'mode':mode})

    def continue_task(self,value):
        return self.worker(self.hub.monitor.continuation.start,{**value,'confirmed':True})

    def test_same_native_new_plan_new_approval_and_original_failure_preserved(self):
        mid,event,rid=self.parent()
        registry=self.hub.monitor.registries[mid];old=copy.deepcopy(registry.load('event')[event['id']])
        session=self.session(mid,event);directory=session.runs_dir/rid
        before=(directory/'run.json').read_bytes()
        task=next(iter(registry.load('pi_task').values()))
        original_file=task['checkpoint']['file'];original_task_id=task['task_id']
        from repo_maintainer.task_session import TaskSessionStore
        native_store=TaskSessionStore(registry,session.repo,RepositoryGateway(session.repo).common_dir)
        native_file=native_store.directory(original_task_id)/'sessions'/original_file
        native_prefix=native_file.read_bytes()
        value=self.inspect(mid,event)
        self.assertTrue(value['preview']['can_continue'],value)
        self.assertEqual(value['preview']['task']['task_id'],original_task_id)
        self.assertEqual(len(self.model.requests),2)
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        with self.assertRaises(SafetyError):self.continue_task(value)  # Old server receipt is not approval.
        result=self.continue_task(self.inspect(mid,event))
        self.assertEqual(result['status'],'awaiting_approval')
        session=self.session(mid,event)
        self.assertEqual(len(session.runs),1)  # New proposal has not modified source/candidate.
        self.assertEqual((directory/'run.json').read_bytes(),before)
        self.assertEqual(registry.path,self.hub.monitor.registries[mid].path)
        registry=self.hub.monitor.registries[mid]
        self.assertEqual(old,registry.load('event')[event['id']])
        self.assertEqual(registry.budget()['requests_used'],4)
        self.assertEqual(session.registry.budget()['requests_used'],0)
        tasks=registry.load('pi_task');self.assertEqual(len(tasks),1)
        self.assertEqual(tasks[original_task_id]['revision'],2)
        self.assertEqual(tasks[original_task_id]['checkpoint']['file'],original_file)
        self.assertTrue(native_file.read_bytes().startswith(native_prefix))
        plan=session.plans[result['plan_id']]['view']
        outcome=session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.preserve_cleanup(session)
        self.assertEqual(outcome['workflow_status'],'candidate_ready')
        self.assertEqual(RepositoryGateway(self.repo).command('status','--porcelain'),b'')
        self.assertEqual(len(self.model.requests),4)
        self.assertTrue(any('维护者确认继续同一问题' in json.dumps(m,ensure_ascii=False) for m in self.model.requests[-1]['messages']))
        self.assertEqual(len(history(self.hub.monitor,mid,event['id'])['continuations']),1)
        self.assertEqual(self.view()['events'][0]['display_status'],'candidate_ready')

    def test_no_consent_stale_source_unknown_budget_and_native_tamper_cannot_start(self):
        mid,event,rid=self.parent();registry=self.hub.monitor.registries[mid]
        value=self.inspect(mid,event)
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.continuation.start,{**value,'confirmed':False})
        with self.assertRaises(SafetyError):self.inspect(mid,event,rid,'auto')
        source=self.hub.monitor.root/mid/'events'/event['id']/'source'
        record=registry.load('pi_task');task=next(iter(record.values()))
        file=registry.root.parent/'task-sessions'/task['task_id']/'sessions'/task['checkpoint']['file']
        # Discover through the existing verified store, never construct a replacement Native.
        from repo_maintainer.task_session import TaskSessionStore
        store=TaskSessionStore(registry,source,RepositoryGateway(source).common_dir)
        file=store.directory(task['task_id'])/'sessions'/task['checkpoint']['file']
        saved=file.read_bytes();file.write_bytes(saved+b'{')
        with self.assertRaises(SafetyError):self.inspect(mid,event)
        file.write_bytes(saved)
        snapshot=source/'calculator.py';original_bytes=snapshot.read_bytes()
        snapshot.write_bytes(original_bytes+b'\n# author change\n')
        with self.assertRaises(SafetyError):self.continue_task(value)
        snapshot.write_bytes(original_bytes)
        self.worker(registry.reserve)
        with self.assertRaises(SafetyError):self.inspect(mid,event)
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual(registry.load('monitor_continuation'),{})
        RepositoryGateway(self.repo).command('commit','--allow-empty','-m','new source version')
        with self.assertRaisesRegex(SafetyError,'新提交'):self.inspect(mid,event)

    def test_duplicate_and_direct_session_requests_do_not_create_second_successor(self):
        mid,event,rid=self.parent();value=self.inspect(mid,event)
        session=self.session(mid,event)
        with self.assertRaises(SafetyError):session._operate('resume_run',{'run_id':rid,'revision':'0'*64,'mode':'approval'})
        with self.assertRaises(SafetyError):session._operate('plan',{'issue':'another unconfirmed paid task'})
        self.continue_task(value)
        with self.assertRaises(SafetyError):self.continue_task(value)
        self.assertEqual(len(self.model.requests),4)
        self.assertEqual(len(self.hub.monitor.registries[mid].load('monitor_continuation')),1)

    def test_completed_plan_lost_monitor_ack_is_linked_without_model_or_new_native(self):
        mid,event=self.prepare();self.execute(self.repair_preview(mid,event))
        registry=self.hub.monitor.registries[mid]
        saved=registry.load('event')[event['id']]
        saved['root_execution']['status']='started';saved['root_execution']['plan_id']=None
        saved['plan_id']=None
        self.worker(self.hub.monitor._event,mid,saved)
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        value=self.inspect(mid,event)
        self.assertTrue(value['preview']['can_reconcile'],value)
        self.worker(self.hub.monitor.continuation.recover,{**value,'confirmed':True})
        current=self.hub.monitor.registries[mid]
        self.assertEqual(current.load('event')[event['id']]['root_execution']['status'],'interrupted_no_replay')
        self.assertEqual(current.budget()['requests_used'],2)
        self.assertTrue(self.worker(recovery_preview,self.hub.monitor,{'monitor_id':mid,'event_id':event['id']})['can_open_original'])
        self.worker(self.hub.monitor.operate,'monitor_open',{'monitor_id':mid,'event_id':event['id']})
        self.assertEqual(self.session(mid,event).claimed_plans,set())
        self.assertEqual(len(self.model.requests),2)
        # A lost monitor acknowledgement must not lose the original Native origin
        # when a reconciled proposal is subsequently rejected and continued.
        session=self.session(mid,event)
        item=next(iter(session.plans.values()))
        plan=item['view']
        session._operate('claim_plan',{'plan_id':plan['id'],'revision':session._plan_revision(item)})
        session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'reject'})
        self.assertTrue(self.inspect(mid,event)['preview']['can_continue'])
        self.assertEqual(len(self.model.requests),2)

    def test_http_requires_auth_origin_and_explicit_recovery_consent(self):
        mid,event,rid=self.parent()
        with TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345') as client:
            data={'monitor_id':mid,'event_id':event['id'],'run_id':rid,'mode':'approval','request_id':uuid.uuid4().hex}
            route='/api/actions/monitor_continuation_inspect'
            self.assertEqual(client.post(route,json=data).status_code,401)
            headers={'Authorization':'Bearer '+self.hub.token}
            self.assertEqual(client.post(route,json=data,headers={**headers,'Origin':'https://outside.invalid'}).status_code,403)
            self.assertEqual(client.post(route,json=data,headers=headers).status_code,202)
            self.worker(lambda:None)
            value=self.hub.jobs[data['request_id']]['result']
            self.assertTrue(value['preview']['can_continue'])
            self.assertEqual(len(self.model.requests),2)

    def crash(self,phase):
        mid,event=self.prepare()
        if phase!='planning':self.execute(self.repair_preview(mid,event))
        self.hub.close()
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('monitor_continuation_crash.py')),
            str(self.root),self.model.url,phase],capture_output=True,timeout=120,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,94,result.stderr.decode(errors='replace'))
        self.hub=self.new_hub();self.hub.monitor.stop()
        return mid,event

    def test_real_planning_process_exit_preserves_unknown_and_never_replays(self):
        mid,event=self.crash('planning')
        registry=self.hub.monitor.registries[mid]
        before=registry.budget();self.assertEqual(before['requests_reserved_unknown'],8)
        self.assertEqual(registry.load('event')[event['id']]['root_execution']['status'],'interrupted_no_replay')
        with self.assertRaisesRegex(SafetyError,'未知消费'):self.inspect(mid,event)
        self.assertEqual(len(self.model.requests),0)
        self.assertEqual(registry.budget(),before)
        self.assertEqual(registry.load('monitor_continuation'),{})

    def test_real_successor_planning_exit_preserves_durable_intent_and_remaining_unknown(self):
        mid,event,rid=self.parent()
        self.hub.close()
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('monitor_continuation_crash.py')),
            str(self.root),self.model.url,'continuation_planning'],capture_output=True,timeout=120,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,94,result.stderr.decode(errors='replace'))
        self.hub=self.new_hub();self.hub.monitor.stop()
        registry=self.hub.monitor.registries[mid]
        before=registry.budget()
        self.assertEqual(before['requests_used'],2)
        self.assertEqual(before['requests_reserved_unknown'],6)
        row=next(iter(registry.load('monitor_continuation').values()))
        self.assertEqual(row['status'],'interrupted_no_replay')
        self.assertEqual(row['parent_run_id'],rid)
        with self.assertRaisesRegex(SafetyError,'未知消费'):self.inspect(mid,event)
        self.assertEqual(registry.budget(),before)
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual(len(registry.load('pi_task')),1)

    def test_real_exit_after_edit_requires_abort_and_fresh_consent(self):
        mid,event=self.crash('candidate_testing')
        value=self.inspect(mid,event)
        self.assertTrue(value['preview']['can_abort'],value)
        rid=value['preview']['run_id']
        with self.assertRaises(SafetyError):self.continue_task(value)
        self.worker(self.hub.monitor.continuation.recover,{**value,'confirmed':True})
        ready=self.inspect(mid,event)
        self.assertTrue(ready['preview']['can_continue'],ready)
        result=self.continue_task(ready)
        self.assertEqual(result['status'],'awaiting_approval')
        self.assertEqual(len(self.model.requests),4)
        report=read_json(self.hub.monitor.root/mid/'r'/event['id']/rid/'run.json')[0]
        self.assertEqual(report['status'],'repair_aborted');self.assertTrue(report['diff'])

    def test_real_exit_after_green_test_does_not_promote_success_and_live_process_blocks_abort(self):
        mid,event=self.crash('verifying_completion')
        value=self.inspect(mid,event)
        self.assertTrue(value['preview']['can_abort'],value)
        rid=value['preview']['run_id'];directory=self.hub.monitor.root/mid/'r'/event['id']/rid
        report=read_json(directory/'run.json')[0]
        self.assertFalse(report.get('finished_at'))
        self.assertEqual(report['candidate_tests'][0]['returncode'],0)
        from repo_maintainer.recovery import process_identity
        from repo_maintainer.repair_recovery import save_execution
        execution=read_json(directory/'repair-execution.json')[0]
        original_execution=copy.deepcopy(execution)
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            execution['inflight']={'kind':'author_live_test','status':'running','identity':process_identity(process.pid),'exited_before_probe':False}
            save_execution(directory,execution)
            blocked=self.inspect(mid,event)
            self.assertFalse(blocked['preview']['can_abort'])
            with self.assertRaises(SafetyError):self.worker(self.hub.monitor.continuation.recover,{**value,'confirmed':True})
            self.assertTrue((directory/'workspace').exists())
        finally:
            process.terminate();process.wait(timeout=10)  # Only the exact helper created in this test.
            save_execution(directory,original_execution)
        self.worker(self.hub.monitor.continuation.recover,{**self.inspect(mid,event),'confirmed':True})
        self.assertTrue(self.inspect(mid,event)['preview']['can_continue'])
        self.assertEqual(len(self.model.requests),2)

    def test_known_auto_result_lost_ack_is_linked_without_new_calls_or_claim(self):
        mid,event=self.prepare('auto')
        self.execute(self.repair_preview(mid,event))
        session=self.session(mid,event);self.preserve_cleanup(session)
        registry=self.hub.monitor.registries[mid]
        saved=registry.load('event')[event['id']];saved['root_execution']['status']='started'
        self.worker(self.hub.monitor._event,mid,saved)
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        value=self.inspect(mid,event)
        self.assertTrue(value['preview']['can_reconcile'],value)
        result=self.worker(self.hub.monitor.continuation.recover,{**value,'confirmed':True})
        self.assertEqual(result['status'],'candidate_ready')
        self.worker(self.hub.monitor.operate,'monitor_open',{'monitor_id':mid,'event_id':event['id']})
        self.assertEqual(self.session(mid,event).claimed_runs,set())
        self.assertEqual(len(self.model.requests),2)

    def test_real_failed_test_summary_reaches_same_native_new_turn(self):
        self.model.scenario='revise'
        mid,event,rid=self.parent('approve')
        session=self.session(mid,event)
        record=read_json(session.runs_dir/rid/'run.json')[0]
        self.assertEqual(record['status'],'rolled_back')
        self.assertNotEqual(record['candidate_tests'][0]['returncode'],0)
        self.model.scenario='correct'
        value=self.inspect(mid,event)
        self.assertIn('FAILED',value['preview']['diagnostic'])
        self.continue_task(value)
        self.assertIn('FAILED',json.dumps(self.model.requests[-1]['messages']))
        self.assertEqual(len(self.hub.monitor.registries[mid].load('pi_task')),1)
        self.assertEqual(len(self.model.requests),4)

    def test_original_auto_can_continue_failed_rounds_with_only_original_remaining_budget(self):
        self.model.scenario='feedback-revise'
        mid,event=self.prepare('auto')
        self.execute(self.worker(self.hub.monitor.execution.preview,
            {'monitor_id':mid,'event_id':event['id'],'max_attempts':2}))
        registry=self.hub.monitor.registries[mid]
        session=self.session(mid,event)
        report=read_json(session.runs_dir/next(reversed(session.runs))/'run.json')[0]
        self.assertEqual(registry.budget()['requests_used'],4,
            {k:report.get(k) for k in ('status','error','max_attempts')})
        self.assertEqual(len(report['rounds']),2)
        self.assertEqual(registry.budget()['requests_remaining'],4)
        self.model.scenario='correct'
        value=self.inspect(mid,event,mode='auto')
        self.assertTrue(value['preview']['can_continue'],value)
        self.assertEqual(value['preview']['max_attempts'],2)
        result=self.continue_task(value)
        self.preserve_cleanup(self.session(mid,event))
        self.assertEqual(result['status'],'candidate_ready')
        self.assertEqual(registry.budget()['requests_used'],6)
        self.assertEqual(registry.budget()['requests_remaining'],2)
        self.assertEqual(len(registry.load('pi_task')),1)
        self.assertEqual(len(self.model.requests),6)


if __name__=='__main__':unittest.main()
