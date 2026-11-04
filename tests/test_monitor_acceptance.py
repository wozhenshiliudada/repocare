"""Real merge/squash graphs and executed fixture tests; GitHub evidence simulated."""
import copy,json,re,unittest,uuid
from urllib.parse import unquote
from unittest.mock import patch
import test_monitor_delivery as fixtures
from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.monitor_acceptance import MonitorAcceptance,association
from repo_maintainer.models import SafetyError
from repo_maintainer.runner import run_test
from repo_maintainer.workflow import cleanup_run
from repo_maintainer.repository_workbench import create_workbench_app
from starlette.testclient import TestClient

BASE='/repos/example/project'


class AcceptanceRemote:
    def __init__(self,case):
        self.case=case;self.pr=copy.deepcopy(case.remote.rows[0]);self.calls=[];self.default='main'
        self.source=RepositoryGateway(case.session.repo)
        self.commit=self.pr['head']['sha'];self.after_read=None;self.actual_checks=[]
        self.merge()
    def merge(self,squash=False,ahead=True):
        source=self.source;tree=source.command('rev-parse',self.commit+'^{tree}').decode().strip()
        def commit(parents,message):
            return source.command('-c','user.name=Acceptance Test','-c','user.email=test@example.invalid',
                'commit-tree',tree,*parents,input_data=(message+'\n').encode()).decode().strip()
        merged=commit(['-p',source.head]+([] if squash else ['-p',self.commit]),'actual squash' if squash else 'actual merge')
        self.head=commit(['-p',merged],'default advanced') if ahead else merged
        self.pr.update(state='closed',merged=True,draft=False,merge_commit_sha=merged,merged_at='2026-10-26T01:00:00Z')
        self.pr['base']['sha']=self.head
        directory=self.case.root/('accepted-'+uuid.uuid4().hex[:8])
        source.command('worktree','add','--detach',str(directory),self.head)
        self.case.addCleanup(source.command,'worktree','remove','--force',str(directory))
        result=run_test(DEMO_TEST,directory,30);self.actual_checks.append(result)
        assert result['returncode']==0 and re.search(r'Ran 4 tests',result['stdout']+result['stderr']),result
        self.runs=[{'id':501,'workflow_id':1001,'run_number':8,'run_attempt':1,'event':'push',
            'head_sha':self.head,'head_branch':'main','repository':{'id':123},'status':'completed',
            'conclusion':'success','updated_at':'2026-10-26T01:01:00Z'}]
        self.jobs=[{'id':601,'run_id':501,'head_sha':self.head,'name':'actual fixture tests',
            'status':'completed','conclusion':'success','completed_at':'2026-10-26T01:01:00Z'}]
    def read(self,path):
        self.calls.append(path)
        if self.after_read:self.after_read(path)
        if path==BASE:return {'id':123,'full_name':'example/project','default_branch':self.default}
        if path==BASE+'/branches/main':return {'name':'main','commit':{'sha':self.head}}
        if path==BASE+'/pulls/9':return copy.deepcopy(self.pr)
        if path.startswith(BASE+'/pulls?'):return [copy.deepcopy(self.pr)]
        if path.startswith(BASE+'/actions/runs?'):
            page=int(path[-1]);return {'total_count':len(self.runs),'workflow_runs':copy.deepcopy(self.runs[(page-1)*30:page*30])}
        if '/attempts/' in path:return {'total_count':len(self.jobs),'jobs':copy.deepcopy(self.jobs)}
        if path.startswith(BASE+'/compare/'):
            ancestor,head=path.split('/compare/')[1].split('?')[0].split('...')
            common=self.source.command('merge-base',ancestor,head,check=False).decode().strip()
            status='identical' if ancestor==head else 'ahead' if common==ancestor else 'diverged'
            return {'url':'https://api.github.com'+path.split('?')[0],'base_commit':{'sha':ancestor},
                'merge_base_commit':{'sha':common},'behind_by':int(status=='diverged'),
                'ahead_by':0 if status=='identical' else 1,'status':status}
        raise AssertionError('Unexpected read '+path)


class MonitorAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.MonitorDeliveryTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.case.pushed();p=self.case.worker(self.case.service.prepare,self.case.rid)
        if 'unknown' in self._testMethodName:self.case.remote.mode='write_timeout'
        try:self.case.worker(self.case.service.create,self.case.rid,p['challenge'],approve_sha256=p['approval_sha256'],actor='fixture')
        except TimeoutError:pass
        self.remote=AcceptanceRemote(self.case);self.service=MonitorAcceptance(self.case.service,self.remote)
        self.case.hub.monitor_acceptance_remote=self.remote
    def prepare(self):return self.case.worker(self.service.prepare,self.case.rid)
    def verify(self,p=None):
        p=p or self.prepare()
        return self.case.worker(self.service.verify,p['id'],p['challenge'],p['approval_sha256'])
    def preserved(self):
        self.case.preserved();self.assertEqual(self.case.remote.writes,1);self.assertEqual(self.case.transport.attempts,1)
        self.assertEqual(self.case.hub.monitor.registries[self.case.mid].load('issue_closure'),{})
    def test_merged_default_version_all_exact_jobs_and_separate_approval(self):
        p=self.prepare();self.assertEqual(p['status'],'prepared')
        with self.assertRaises(SafetyError):self.case.worker(self.service.verify,p['id'],p['challenge'],'0'*64)
        result=self.verify(p);self.assertEqual(result['status'],'delivery-verified',result)
        self.assertEqual(result['proof']['default_head_sha'],self.remote.head)
        self.assertEqual(result['proof']['workflows'][0]['run_attempt'],1)
        self.assertFalse(result['proof']['local_tests_executed']);self.assertEqual(result['proof']['remote_writes'],0)
        with self.assertRaises(SafetyError):self.verify(p)
        self.preserved()
    def test_squash_and_advanced_default_do_not_require_candidate_ancestry(self):
        self.remote.merge(squash=True)
        self.assertNotEqual(self.remote.source.command('merge-base',self.remote.commit,self.remote.head).decode().strip(),self.remote.commit)
        self.assertEqual(self.verify()['status'],'delivery-verified');self.preserved()
    def test_historical_acceptance_survives_candidate_cleanup_and_local_user_work(self):
        cleanup_run(self.case.session.runs_dir/self.case.rid)
        path=self.case.repo/'calculator.py';old=path.read_bytes();path.write_bytes(old+b'# owner local work\n')
        try:
            self.assertEqual(self.verify()['status'],'delivery-verified')
            self.assertEqual(path.read_bytes(),old+b'# owner local work\n')
        finally:path.write_bytes(old)
        report=self.case.session.runs_dir/self.case.rid/'run.json';raw=report.read_bytes()
        changed=json.loads(raw);changed['candidate_tests']=[];report.write_text(json.dumps(changed),encoding='utf-8')
        try:
            with self.assertRaises(SafetyError):self.prepare()
        finally:report.write_bytes(raw)
        self.preserved()
    def test_unknown_pr_intent_uses_monitor_marker_without_replaying_creation(self):
        self.assertEqual(self.verify()['status'],'delivery-verified')
        self.assertEqual(self.case.service.view(self.case.rid)['status'],'creation_unknown');self.preserved()
    def test_unmerged_foreign_head_nondefault_or_missing_workflows_cannot_prepare(self):
        saved=copy.deepcopy(self.remote.pr)
        for key,value in [('merged',False),('draft',True),('state','open')]:
            self.remote.pr[key]=value
            with self.assertRaises(SafetyError):self.prepare()
            self.remote.pr=copy.deepcopy(saved)
        self.remote.default='other'
        with self.assertRaises(SafetyError):self.prepare()
        self.remote.default='main';self.remote.runs=[]
        with self.assertRaises(SafetyError):self.prepare()
        self.preserved()
    def test_failed_pending_skipped_and_new_attempt_are_not_hidden_by_old_green(self):
        for status,conclusion in [('completed','failure'),('in_progress',None)]:
            self.remote.runs[0].update(status=status,conclusion=conclusion)
            self.assertEqual(self.verify()['status'],'not-verified')
        self.remote.runs[0].update(status='completed',conclusion='success')
        self.remote.jobs[0]['conclusion']='skipped';self.assertEqual(self.verify()['status'],'not-verified')
        self.remote.jobs[0]['conclusion']='success';p=self.prepare();self.remote.runs[0]['run_attempt']=2
        result=self.verify(p);self.assertEqual(result['status'],'not-verified')
        self.assertIn('变化',result['error']);self.preserved()
    def test_default_or_ci_changes_during_observation_are_not_verified(self):
        p=self.prepare()
        def change(path):
            if '/attempts/' in path:self.remote.runs[0]['run_attempt']=2
        self.remote.after_read=change
        self.assertEqual(self.verify(p)['status'],'not-verified');self.preserved()
    def test_cross_event_and_tampered_original_report_cannot_be_accepted(self):
        from repo_maintainer.monitor_delivery import MonitorDelivery
        other=MonitorAcceptance(MonitorDelivery(self.case.hub.monitor,self.case.mid,'f'*16,self.case.remote),self.remote)
        p=self.prepare();reads=len(self.remote.calls)
        with self.assertRaises(SafetyError):other.view(p['id'])
        self.assertEqual(len(self.remote.calls),reads)
        path=self.case.session.runs_dir/self.case.rid/'run.json';raw=path.read_bytes();path.write_bytes(raw+b'\n')
        try:self.assertEqual(self.verify(p)['status'],'not-verified')
        finally:path.write_bytes(raw)
        self.preserved()
    def test_original_ci_workflow_must_be_present_not_replaced_by_unrelated_green(self):
        original=association(self.case.service,self.case.rid);original['original_ci_workflow_id']=999
        with patch('repo_maintainer.monitor_acceptance.association',return_value=original):
            with self.assertRaises(SafetyError):self.prepare()
        self.preserved()
    def test_http_auth_origin_fixed_fields_and_no_merge_or_close(self):
        hub=self.case.hub
        with TestClient(create_workbench_app(hub,12345),base_url='http://127.0.0.1:12345') as client:
            body={**self.case.selection,'request_id':uuid.uuid4().hex};url='/api/actions/monitor_delivery_acceptance_preview'
            h={'Authorization':'Bearer '+hub.token}
            self.assertEqual(client.post(url,json=body).status_code,401)
            self.assertEqual(client.post(url,headers={**h,'Origin':'https://outside.invalid'},json=body).status_code,403)
            self.assertEqual(client.post(url,headers=h,json={**body,'workflow_ids':[999]}).status_code,409)
            response=client.post(url,headers=h,json=body);self.assertEqual(response.status_code,202,response.text)
            self.case.worker(lambda:None);self.assertEqual(hub.jobs[body['request_id']]['status'],'finished',hub.jobs[body['request_id']])
            self.assertEqual(client.post('/api/actions/monitor_delivery_acceptance_merge',headers=h,json=body).status_code,409)
        self.preserved()
