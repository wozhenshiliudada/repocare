"""Original Pi, original monitor account, real isolated tests; loopback model only."""
import copy
from datetime import datetime,timezone
import json
import threading
import time
import unittest
import uuid
from starlette.testclient import TestClient
import test_monitor_continuation as fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.repository_workbench import create_workbench_app


class MonitorModelAuthorizationTests(unittest.TestCase):
    setUp=fixture.MonitorContinuationTests.setUp
    new_hub=fixture.MonitorContinuationTests.new_hub
    finish=fixture.MonitorContinuationTests.finish
    worker=fixture.MonitorContinuationTests.worker
    scope=fixture.MonitorContinuationTests.scope
    view=fixture.MonitorContinuationTests.view
    payload=fixture.MonitorContinuationTests.payload
    preview=fixture.MonitorContinuationTests.preview
    configure=fixture.MonitorContinuationTests.configure
    session=fixture.MonitorContinuationTests.session
    preserve_cleanup=fixture.MonitorContinuationTests.preserve_cleanup

    def grant_preview(self,mid):
        expires=datetime.fromtimestamp(time.time()+3600,timezone.utc).isoformat(timespec='seconds')
        return self.worker(self.hub.monitor.model_authorization.preview,
            {'monitor_id':mid,'expires_at':expires,'max_attempts':2})

    def grant(self,mid):
        value=self.grant_preview(mid)
        return self.worker(self.hub.monitor.model_authorization.configure,{**value,'confirmed':True})['authorization']

    def commit(self):
        repo=RepositoryGateway(self.repo)
        repo.command('commit','--allow-empty','-m','new author version')
        return repo.head

    def poll(self,mid):
        self.worker(self.hub.monitor.poll,mid)
        for (owner,_),server in self.hub.monitor.servers.items():
            if owner==mid:self.preserve_cleanup(server.session)

    def test_two_future_commits_auto_repair_new_tasks_share_original_budget_and_never_replay(self):
        mid=self.configure(allow_local_code=True,mode='auto');grant=self.grant(mid)
        self.poll(mid)  # Current commit is excluded, even before its first inspection.
        self.assertEqual(len(self.model.requests),0)
        for used in (2,4):
            self.commit();self.poll(mid)
            event=self.view()['events'][-1]
            self.assertEqual(event['root_execution']['status'],'candidate_ready',event)
            self.assertEqual(event['model_observation']['id'],grant['id'])
            self.assertEqual(event['model_dispatch']['status'],'finished')
            self.assertEqual(self.view()['requests_used'],used)
            self.poll(mid);self.assertEqual(len(self.model.requests),used)
        registry=self.hub.monitor.registries[mid]
        self.assertEqual(len(registry.load('pi_task')),2)
        self.assertEqual(registry.budget()['request_budget'],8)
        self.assertEqual(registry.budget()['requests_reserved_unknown'],0)
        self.assertTrue(all(s.session.registry.budget()['requests_used']==0 for s in self.hub.monitor.servers.values()))
        self.assertIn('return a - b',(self.repo/'calculator.py').read_text())

    def test_manual_mode_only_proposes_and_revocation_does_not_approve_or_destroy_saved_plan(self):
        mid=self.configure(allow_local_code=True);grant=self.grant(mid)
        self.assertEqual(grant['core']['max_attempts'],1)
        self.commit();self.poll(mid)
        event=self.view()['events'][-1];session=self.session(mid,event)
        self.assertEqual(event['root_execution']['status'],'awaiting_approval')
        self.assertEqual(session.runs,{})
        self.hub.submit(uuid.uuid4().hex,'monitor_model_revoke',{'monitor_id':mid,'grant_id':grant['id']})
        plan=next(iter(session.plans.values()))['view']
        result=session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.preserve_cleanup(session)
        self.assertEqual(result['workflow_status'],'candidate_ready')
        self.assertEqual(len(self.model.requests),2)

    def test_old_failure_and_current_commit_cannot_be_retroactively_admitted(self):
        mid=self.configure(allow_local_code=True);self.poll(mid)
        self.grant(mid);self.poll(mid)
        self.assertNotIn('model_observation',self.view()['events'][0])
        self.assertNotIn('root_execution',self.view()['events'][0])
        self.assertEqual(len(self.model.requests),0)

    def test_no_consent_tamper_changed_config_unknown_budget_and_stale_server_preview(self):
        mid=self.configure(allow_local_code=True);value=self.grant_preview(mid)
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.model_authorization.configure,{**value,'confirmed':False})
        bad=copy.deepcopy(value);bad['preview']['request_ceiling']=100
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.model_authorization.configure,{**bad,'confirmed':True})
        original=self.config.read_bytes();self.config.write_bytes(original+b'change')
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.model_authorization.configure,{**value,'confirmed':True})
        self.config.write_bytes(original)
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.model_authorization.configure,{**value,'confirmed':True})
        registry=self.hub.monitor.registries[mid];self.worker(registry.reserve)
        with self.assertRaises(SafetyError):self.grant_preview(mid)
        self.assertEqual(registry.budget()['requests_reserved_unknown'],8)
        self.assertEqual(len(self.model.requests),0)

    def test_expiry_rule_change_and_config_change_block_future_model_calls(self):
        mid=self.configure(allow_local_code=True);grant=self.grant(mid)
        authorization=self.hub.monitor.model_authorization
        authorization.clock=lambda:grant['core']['expires_epoch']+1
        self.assertEqual(authorization.view(mid)['effective_status'],'expired')
        self.commit();self.poll(mid);self.assertEqual(len(self.model.requests),0)
        authorization.clock=time.time
        self.configure(allow_local_code=True,check_families=['unittest','pytest'])
        self.assertEqual(authorization.view(mid)['effective_status'],'binding_changed')
        self.grant(mid)
        self.config.write_text('changed author config')
        self.assertEqual(authorization.view(mid)['effective_status'],'binding_changed')
        self.commit();self.poll(mid);self.assertEqual(len(self.model.requests),0)

    def test_pause_resume_restart_and_reconfirmation_never_rearm_old_events(self):
        mid=self.configure(allow_local_code=True);grant=self.grant(mid)
        request=uuid.uuid4().hex;payload={'monitor_id':mid,'grant_id':grant['id']}
        result=self.hub.submit(request,'monitor_model_pause',payload)
        self.assertEqual(self.hub.submit(request,'monitor_model_pause',payload),result)
        with self.assertRaises(SafetyError):self.hub.submit(request,'monitor_model_revoke',payload)
        self.worker(self.hub.monitor.operate,'monitor_resume',{'monitor_id':mid})
        self.assertEqual(self.hub.monitor.model_authorization.view(mid)['status'],'paused')
        self.commit();self.poll(mid);self.assertEqual(len(self.model.requests),0)
        self.grant(mid)
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        self.assertEqual(self.hub.monitor.model_authorization.view(mid)['reason'],'service_restart')
        self.commit();self.poll(mid);self.assertEqual(len(self.model.requests),0)
        new=self.grant(mid)
        with self.assertRaises(SafetyError):self.hub.submit(uuid.uuid4().hex,'monitor_model_revoke',payload)
        self.poll(mid);self.assertEqual(len(self.model.requests),0)
        self.assertNotEqual(new['id'],grant['id'])

    def running_control(self,deadline=False):
        mid=self.configure(allow_local_code=True,mode='auto');grant=self.grant(mid);self.commit()
        entered,released=threading.Event(),threading.Event();self.model.gate=(entered,released)
        self.hub.monitor.next_due[mid]=0
        request=uuid.uuid4().hex
        self.hub.submit(request,'monitor_check',{'monitor_id':mid})
        try:
            self.assertTrue(entered.wait(60),'No loopback model request started')
            if deadline:
                self.hub.monitor.model_authorization.clock=lambda:grant['core']['expires_epoch']+1
                limit=time.monotonic()+8
                while time.monotonic()<limit:
                    session=next(iter(self.hub.monitor.servers.values())).session
                    if session.registry.load('stop'):break
                    time.sleep(.05)
                self.assertTrue(session.registry.load('stop'))
            else:
                with TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345') as client:
                    data={'monitor_id':mid,'grant_id':grant['id'],'request_id':uuid.uuid4().hex}
                    route='/api/actions/monitor_model_revoke'
                    self.assertEqual(client.post(route,json=data).status_code,401)
                    headers={'Authorization':'Bearer '+self.hub.token}
                    self.assertEqual(client.post(route,json=data,headers={**headers,'Origin':'https://outside.invalid'}).status_code,403)
                    self.assertEqual(client.post(route,json=data,headers=headers).status_code,202)
                self.assertEqual(self.hub.monitor.model_authorization.view(mid)['status'],'revoked')
        finally:
            # Keep the first response in flight until the owned controller has
            # acknowledged cancellation; releasing it first races another turn.
            try:self.worker(lambda:None)
            finally:released.set()
        self.assertEqual(len(self.model.requests),1)
        registry=self.hub.monitor.registries[mid];budget=registry.budget()
        self.assertGreaterEqual(budget['requests_used']+budget['requests_reserved_unknown'],1)
        self.assertEqual(budget['request_budget'],8)
        self.assertEqual(self.view()['events'][-1]['root_execution']['status'],'failed_no_replay')
        self.assertEqual(len(registry.load('event')),1)
        self.poll(mid);self.assertEqual(len(self.model.requests),1)

    def test_revoke_during_model_call_is_immediate_authenticated_and_preserves_consumption(self):
        self.running_control()

    def test_deadline_stops_owned_model_job_with_original_receipt(self):
        self.running_control(deadline=True)


if __name__=='__main__':unittest.main()
