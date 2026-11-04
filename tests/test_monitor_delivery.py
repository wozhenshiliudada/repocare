"""Original monitor/env/SQLite, real owned bare Git, simulated PRs; no Internet."""
import copy,json,uuid
from pathlib import Path
from unittest.mock import patch
import unittest

import test_monitor_execution as original
from test_repository_workbench import FakeGitHub,FakeServer
from test_issue_publication import LocalBranchTransport,prepare_bare,bare_git
from test_task_planning import SyntheticModel
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.monitor_delivery import MonitorDelivery,MonitorPublication
from repo_maintainer.repository_workbench import RepositoryWorkbench,create_workbench_app,validate_request
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.delivery_inbox import projection


class MonitorRemote:
    """Only this test adapter reads the owned local bare repository."""
    def __init__(self,bare):
        self.bare=bare;self.rows=[];self.writes=0;self.mode='normal';self.before_post=None
        self.repo_id=123;self.default='main';self.reads=0
    def api(self,path):
        self.reads+=1
        assert path=='/repos/example/project'
        return {'id':self.repo_id,'full_name':'example/project','default_branch':self.default}
    def branch_head(self,url,branch):
        self.reads+=1
        assert url=='https://github.com/example/project'
        return bare_git(self.bare,'show-ref','--verify','--hash','refs/heads/'+branch).decode().strip()
    def pulls_for_head(self,slug,head):
        self.reads+=1;assert slug=='example/project'
        return copy.deepcopy([r for r in self.rows if r['head']['ref']==head])
    def create_draft(self,slug,payload):
        assert slug=='example/project';self.writes+=1
        if self.before_post:self.before_post()
        if self.mode=='no_write_timeout':raise TimeoutError('author PR outcome unknown')
        value={'id':901,'number':9,'html_url':'https://github.com/'+slug+'/pull/9',
            'state':'open','merged_at':None,**copy.deepcopy(payload)}
        for side in ('head','base'):
            branch=payload[side]
            value[side]={'ref':branch,'sha':self.branch_head('https://github.com/'+slug,branch),
                         'repo':{'id':self.repo_id,'full_name':slug}}
        self.rows.append(value)
        if self.mode=='write_timeout':raise TimeoutError('author PR written; response lost')
        return copy.deepcopy(value)


class MonitorDeliveryRequestTests(unittest.TestCase):
    def test_registering_monitor_delivery_does_not_replace_existing_action_or_expand_payload(self):
        selection={'monitor_id':'a'*16,'event_id':'b'*16,'run_id':'c'*32,'request_id':'d'*32}
        validate_request('local',{'path':'author-repo','request_id':'e'*32})
        validate_request('monitor_delivery_prepare_local',selection)
        validate_request('monitor_delivery_pr',{**selection,'revision':'challenge','approval_sha256':'f'*64})
        with self.assertRaises(SafetyError):validate_request('monitor_delivery_prepare_local',{**selection,'title':'unexpected'})
        with self.assertRaises(SafetyError):validate_request('monitor_delivery_merge',selection)


class MonitorDeliveryTests(unittest.TestCase):
    new_hub=original.MonitorExecutionTests.new_hub
    finish=original.MonitorExecutionTests.finish
    def worker(self,function,*args,**kwargs):
        return self.hub.executor.submit(function,*args,**kwargs).result(timeout=180)
    scope=original.MonitorExecutionTests.scope
    view=original.MonitorExecutionTests.view
    payload=original.MonitorExecutionTests.payload
    preview=original.MonitorExecutionTests.preview
    configure=original.MonitorExecutionTests.configure
    repair_preview=original.MonitorExecutionTests.repair_preview
    execute=original.MonitorExecutionTests.execute
    preserve_cleanup=original.MonitorExecutionTests.preserve_cleanup

    def setUp(self):
        original.MonitorExecutionTests.setUp(self)
        self.repo_gateway=RepositoryGateway(self.repo)
        self.repo_gateway.command('remote','add','origin','https://github.com/example/project.git')
        self.mid=self.configure(allow_local_code=True,mode='auto')
        self.worker(self.hub.monitor.poll,self.mid);self.event=self.view()['events'][0]
        result=self.execute(self.repair_preview(self.mid,self.event))
        self.assertEqual(result['status'],'candidate_ready',result)
        self.session=self.hub.monitor.servers[self.mid,'root-'+self.event['id']].session
        self.preserve_cleanup(self.session)
        self.event=self.view()['events'][0];self.rid=self.event['root_execution']['run_id']
        self.bare=self.root/'remote.git';prepare_bare(self.repo_gateway,self.bare)
        self.remote=MonitorRemote(self.bare);self.transport=LocalBranchTransport(self.bare)
        self.hub.monitor_delivery_remote=self.remote;self.hub.monitor_publication_transport=self.transport
        self.service=MonitorDelivery(self.hub.monitor,self.mid,self.event['id'],self.remote)
        self.publisher=MonitorPublication(self.service,self.transport)
        self.selection={'monitor_id':self.mid,'event_id':self.event['id'],'run_id':self.rid}
        self.budget=self.hub.monitor.registries[self.mid].budget()
        self.source_before=self.repo_gateway.snapshot()
        self.config_before=(self.repo_gateway.common_dir/'config').read_bytes()

    def action(self,name,**extra):
        return self.worker(self.service.operate,'monitor_delivery_'+name,{**self.selection,**extra},self.transport)
    def sealed(self):
        prepared=self.action('prepare_local')
        self.action('publish_local',revision=prepared['review_sha256'])
        return prepared
    def pushed(self):
        self.sealed();proposal=self.worker(self.publisher.prepare,self.rid)
        return self.worker(self.publisher.push,self.rid,proposal['challenge'],approve_sha256=proposal['approval_sha256'],actor='fixture')
    def preserved(self):
        self.assertEqual(self.repo_gateway.snapshot(),self.source_before)
        self.assertEqual((self.repo_gateway.common_dir/'config').read_bytes(),self.config_before)
        registry=self.hub.monitor.registries[self.mid]
        self.assertEqual(registry.budget(),self.budget)
        self.assertEqual(registry.load('issue_execution'),{});self.assertEqual(registry.load('issue_publication'),{})
        self.assertEqual(registry.load('issue_delivery'),{})

    def test_separate_exact_approvals_real_create_only_push_and_draft_do_not_create_issue_claims(self):
        self.sealed();proposal=self.worker(self.publisher.prepare,self.rid)
        self.assertEqual(self.remote.writes,0);self.assertEqual(self.transport.attempts,0)
        with self.assertRaises(SafetyError):
            self.worker(self.publisher.push,self.rid,proposal['challenge'],approve_sha256='f'*64,actor='fixture')
        pushed=self.worker(self.publisher.push,self.rid,proposal['challenge'],approve_sha256=proposal['approval_sha256'],actor='fixture')
        self.assertEqual(pushed['status'],'remote-ref-verified');self.assertEqual(self.transport.attempts,1)
        self.assertEqual(self.remote.writes,0,'Branch approval must never create a PR')
        draft=self.worker(self.service.prepare,self.rid)
        self.assertEqual(draft['core']['binding']['origin_kind'],'monitor')
        self.assertNotIn('Refs #',draft['core']['payload']['body']);self.assertNotIn('issue_number',draft['core']['binding'])
        def intent():self.assertEqual(self.service.registry.load('monitor_delivery')[self.rid]['status'],'create_intent')
        self.remote.before_post=intent
        result=self.worker(self.service.create,self.rid,draft['challenge'],approve_sha256=draft['approval_sha256'],actor='fixture')
        self.assertEqual(result['status'],'awaiting-review');self.assertEqual(self.remote.writes,1)
        self.assertTrue(result['receipt']['draft']);self.assertIsNone(result['receipt']['merged_at'])
        self.assertEqual(projection(self.hub)['items'][0]['delivery']['pr']['status'],'awaiting-review')
        with self.assertRaises(SafetyError):self.worker(self.service.create,self.rid,draft['challenge'],approve_sha256=draft['approval_sha256'],actor='fixture')
        self.preserved()

    def test_source_change_or_paused_monitor_rejects_old_push_before_transport(self):
        self.sealed();proposal=self.worker(self.publisher.prepare,self.rid)
        self.worker(self.hub.monitor.operate,'monitor_pause',{'monitor_id':self.mid})
        with self.assertRaises(SafetyError):self.worker(self.publisher.push,self.rid,proposal['challenge'],approve_sha256=proposal['approval_sha256'],actor='fixture')
        self.assertEqual(self.transport.attempts,0);self.assertEqual(self.remote.writes,0)
        self.preserved()

    def test_candidate_environment_or_registration_missing_never_falls_back_or_allocates(self):
        path=self.session.runs_dir/self.rid/'environment/environment.json';saved=path.with_suffix('.preserved')
        path.rename(saved)
        try:
            with self.assertRaises(OSError):self.action('prepare_local')
            self.assertFalse(path.exists())
        finally:saved.rename(path)
        self.assertEqual(self.transport.attempts,0);self.preserved()

    def test_push_timeout_after_write_reconciles_once_without_second_push(self):
        self.sealed();p=self.worker(self.publisher.prepare,self.rid);self.transport.mode='timeout_after_write'
        with self.assertRaises(TimeoutError):self.worker(self.publisher.push,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        self.assertEqual(self.worker(self.publisher.view,self.rid)['status'],'push_unknown')
        with self.assertRaises(SafetyError):self.worker(self.publisher.push,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        reopened=MonitorPublication(MonitorDelivery(self.hub.monitor,self.mid,self.event['id'],self.remote),self.transport)
        result=self.worker(reopened.reconcile,self.rid)
        self.assertEqual(result['status'],'remote-ref-verified');self.assertEqual(self.transport.attempts,1)
        self.preserved()

    def test_push_timeout_without_write_stays_unknown_and_never_retries(self):
        self.sealed();p=self.worker(self.publisher.prepare,self.rid);self.transport.mode='timeout_without_write'
        with self.assertRaises(TimeoutError):self.worker(self.publisher.push,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        with self.assertRaises(SafetyError):self.worker(self.publisher.reconcile,self.rid)
        self.assertEqual(self.worker(self.publisher.view,self.rid)['status'],'push_unknown')
        self.assertEqual(self.transport.attempts,1);self.preserved()

    def test_remote_branch_race_is_never_overwritten(self):
        self.sealed();p=self.worker(self.publisher.prepare,self.rid);b=p['core']['binding']
        def race():self.repo_gateway.command('-c','protocol.file.allow=always','push','--',str(self.bare),self.repo_gateway.head+':refs/heads/'+b['head'])
        self.transport.gate=race
        with self.assertRaises(SafetyError):self.worker(self.publisher.push,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        self.assertEqual(self.remote.branch_head('https://github.com/example/project',b['head']),self.repo_gateway.head)
        with self.assertRaises(SafetyError):self.worker(self.publisher.reconcile,self.rid)
        self.preserved()

    def test_pr_timeout_after_write_reconciles_without_another_post(self):
        self.pushed();p=self.worker(self.service.prepare,self.rid);self.remote.mode='write_timeout'
        with self.assertRaises(TimeoutError):self.worker(self.service.create,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        with self.assertRaises(SafetyError):self.worker(self.service.create,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        reopened=MonitorDelivery(self.hub.monitor,self.mid,self.event['id'],self.remote)
        result=self.worker(reopened.reconcile,self.rid)
        self.assertEqual(result['status'],'awaiting-review');self.assertEqual(self.remote.writes,1)
        self.preserved()

    def test_pr_timeout_without_write_stays_unknown_and_cannot_cross_event(self):
        self.pushed();p=self.worker(self.service.prepare,self.rid);self.remote.mode='no_write_timeout'
        with self.assertRaises(TimeoutError):self.worker(self.service.create,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        with self.assertRaises(SafetyError):self.worker(self.service.reconcile,self.rid)
        other=MonitorDelivery(self.hub.monitor,self.mid,'f'*16,self.remote);reads=self.remote.reads
        with self.assertRaises(SafetyError):self.worker(other.reconcile,self.rid)
        with self.assertRaises(SafetyError):self.worker(MonitorPublication(other,self.transport).view,self.rid)
        self.assertEqual(self.remote.reads,reads);self.assertEqual(self.remote.writes,1);self.preserved()

    def test_result_after_source_drift_is_recorded_review_required_not_repeated(self):
        self.pushed();p=self.worker(self.service.prepare,self.rid)
        self.remote.before_post=lambda:self.repo_gateway.command('commit','--allow-empty','-m','new owner revision')
        result=self.worker(self.service.create,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        self.assertEqual(result['status'],'remote-created-review-required');self.assertEqual(self.remote.writes,1)
        self.assertTrue(result['receipt']['url'].endswith('/pull/9'))

    def test_http_keeps_auth_origin_and_no_arbitrary_payload_or_merge_close(self):
        from starlette.testclient import TestClient
        with TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345') as client:
            headers={'Authorization':'Bearer '+self.hub.token};body={**self.selection,'request_id':uuid.uuid4().hex}
            url='/api/actions/monitor_delivery_prepare_local'
            self.assertEqual(client.post(url,json=body).status_code,401)
            self.assertEqual(client.post(url,headers={**headers,'Origin':'https://outside.invalid'},json=body).status_code,403)
            self.assertEqual(client.post(url,headers=headers,json={**body,'title':'Closes #6'}).status_code,409)
            response=client.post(url,headers=headers,json=body);self.assertEqual(response.status_code,202,response.text)
            self.worker(lambda:None);self.assertEqual(self.hub.jobs[body['request_id']]['status'],'finished',self.hub.jobs[body['request_id']])
            self.assertEqual(client.post('/api/actions/monitor_delivery_close',headers=headers,json=body).status_code,409)
        self.assertEqual(self.remote.writes,0);self.assertEqual(self.transport.attempts,0);self.preserved()


class MonitorDeliveryNativeTests(unittest.TestCase):
    # Reuse fixture helpers, not all mock test cases.
    finish=MonitorDeliveryTests.finish;worker=MonitorDeliveryTests.worker;scope=MonitorDeliveryTests.scope
    view=MonitorDeliveryTests.view;payload=MonitorDeliveryTests.payload;preview=MonitorDeliveryTests.preview
    configure=MonitorDeliveryTests.configure;repair_preview=MonitorDeliveryTests.repair_preview
    execute=MonitorDeliveryTests.execute;preserve_cleanup=MonitorDeliveryTests.preserve_cleanup
    action=MonitorDeliveryTests.action;sealed=MonitorDeliveryTests.sealed;preserved=MonitorDeliveryTests.preserved
    def new_hub(self):
        return RepositoryWorkbench(self.root/'hub',self.config,request_budget=8,allow_auto=True,
            github=FakeGitHub(),server_factory=FakeServer,
            provider_factory=lambda p:PiProvider(p.allowed_paths,self.model.url,readonly_paths=p.readonly_paths,max_requests=8))
    def setUp(self):
        self.model=SyntheticModel();self.addCleanup(self.model.close);MonitorDeliveryTests.setUp(self)
    def test_original_pi_native_is_verified_and_unchanged_through_branch_and_pr_delivery(self):
        self.sealed();p=self.worker(self.publisher.prepare,self.rid);native=p['core']['binding']['native']
        self.assertIsNotNone(native);self.assertEqual(native['revision'],1)
        row=self.hub.monitor.registries[self.mid].load('pi_task')[native['task_id']]
        checkpoint=self.hub.monitor.root/self.mid/'task-sessions'/native['task_id']/'sessions'/row['checkpoint']['file']
        original=checkpoint.read_bytes()
        report=self.session.runs_dir/self.rid/'run.json';report_bytes=report.read_bytes()
        checkpoint.write_bytes(b'{}\n')
        try:
            with self.assertRaises(SafetyError):
                self.worker(self.publisher.push,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
            self.assertEqual(self.transport.attempts,0)
        finally:checkpoint.write_bytes(original)
        self.worker(self.publisher.push,self.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        draft=self.worker(self.service.prepare,self.rid)
        result=self.worker(self.service.create,self.rid,draft['challenge'],approve_sha256=draft['approval_sha256'],actor='fixture')
        self.assertEqual(result['status'],'awaiting-review');self.assertEqual(checkpoint.read_bytes(),original)
        self.assertEqual(report.read_bytes(),report_bytes)
        self.assertEqual(len(self.model.requests),2);self.preserved()
