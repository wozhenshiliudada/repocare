import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from starlette.testclient import TestClient
from repo_maintainer.demo import create_fixture,DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.provider import MockProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench,create_workbench_app,inspect_repository

class BudgetMockProvider(MockProvider):
    max_requests=6
    requests_used=0
    def plan(self,*args):
        plan=super().plan(*args)
        self.evidence={'requests':1}  # Synthetic accounting only; no network/model.
        return plan

class UnifiedWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.repos=[create_fixture(self.root/name) for name in ('alpha','beta')]
        self.config=self.root/'.env.local';self.config.write_text('not read')
        self.start()
        self.addCleanup(lambda:self.hub.close());self.addCleanup(lambda:self.client.close())
    def start(self,ui='legacy'):
        self.hub=RepositoryWorkbench(self.root/'state',self.config,request_budget=6,
                                    provider_factory=lambda policy:BudgetMockProvider(),ui=ui)
        self.hub.url='http://127.0.0.1:12345/#session='+self.hub.token
        self.client=TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345')
        self.headers={'Authorization':'Bearer '+self.hub.token}
    def wait(self,response,headers,prefix):
        self.assertEqual(202,response.status_code,response.text)
        identity=response.json()['id'];deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            state=self.client.get(prefix+'state',headers=headers).json()
            job=next(item for item in state['jobs'] if item['id']==identity)
            if job['status']!='running':return job
            time.sleep(.02)
        self.fail('Owned task did not complete')
    def connect(self,repo):
        preview=inspect_repository(repo)
        body={'request_id':uuid.uuid4().hex,'path':str(repo),'revision':preview['revision'],
              'allowed_paths':['calculator.py'],'readonly_paths':['tests/test_calculator.py'],
              'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':30,'allow_auto':False,'confirmed':True}
        job=self.wait(self.client.post('/api/actions/connect',headers=self.headers,json=body),self.headers,'/api/')
        self.assertEqual('finished',job['status'],job)
        record=next(r for r in self.client.get('/api/state',headers=self.headers).json()['workspaces'] if r['repository']==str(repo))
        return record,{'Authorization':'Bearer '+record['token']}
    def operate(self,record,headers,action,body):
        return self.wait(self.client.post(record['api_prefix']+'actions/'+action,headers=headers,
            json={'request_id':uuid.uuid4().hex,**body}),headers,record['api_prefix'])
    def state(self,record,headers):return self.client.get(record['api_prefix']+'state',headers=headers).json()

    def test_default_sessions_use_one_listener_without_background_child_server(self):
        with patch('repo_maintainer.repository_workbench.SessionServer',side_effect=AssertionError('No child listener allowed')):
            record,headers=self.connect(self.repos[0])
        self.assertEqual(str(self.repos[0]),self.state(record,headers)['repository'])
        self.assertEqual('http://127.0.0.1:12345/#workspace='+record['id'],self.hub.sessions[str(self.repos[0])].url)
        self.assertFalse(self.state(record,headers)['plans'])
        self.assertEqual(200,self.client.get('/maintenance.html').status_code)
        self.assertEqual(200,self.client.get('/maintenance.js').status_code)
        self.assertEqual(200,self.client.get('/maintenance.css').status_code)
        script=self.client.get('/session-access.js')
        self.assertEqual(200,script.status_code)
        self.assertNotIn(record['token'],script.text)
        self.assertNotIn(self.hub.token,script.text)

    def test_routes_keep_own_token_origin_host_headers_and_unknown_identity_guards(self):
        a,ha=self.connect(self.repos[0]);b,hb=self.connect(self.repos[1])
        path=a['api_prefix']+'state'
        for headers in ({},self.headers,hb):self.assertEqual(401,self.client.get(path,headers=headers).status_code)
        self.assertEqual(401,self.client.get('/api/state',headers=ha).status_code)
        for headers in ({**ha,'Origin':'https://foreign.invalid'},{**ha,'Host':'foreign.invalid'},
                        {**ha,'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(403,self.client.get(path,headers=headers).status_code)
        self.assertEqual(403,self.client.get(path,headers=[('Authorization',ha['Authorization']),('Authorization','other')]).status_code)
        response=self.client.get(path,headers=ha)
        self.assertIn("frame-ancestors 'none'",response.headers['content-security-policy'])
        self.assertIn(self.client.get('/api/workspaces/'+uuid.uuid4().hex+'/state',headers=ha).status_code,(401,404))
        self.assertEqual(404,self.client.get(a['api_prefix']+'../state',headers=self.headers).status_code)

    def test_approval_and_candidate_workflow_remain_bound_to_selected_repository(self):
        before=[RepositoryGateway(repo).snapshot() for repo in self.repos]
        a,ha=self.connect(self.repos[0]);b,hb=self.connect(self.repos[1])
        job=self.operate(a,ha,'plan',{'issue':'addition bug'})
        self.assertEqual('finished',job['status'],job)
        plan=self.state(a,ha)['plans'][0]
        wrong=self.operate(b,hb,'decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.assertEqual('failed',wrong['status'])
        self.assertFalse(self.state(b,hb)['plans'])
        self.assertEqual(before,[RepositoryGateway(repo).snapshot() for repo in self.repos])
        approved=self.operate(a,ha,'decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.assertEqual('candidate_ready',approved['result']['workflow_status'],approved)
        self.assertFalse(self.state(b,hb)['runs'])
        self.assertTrue(self.state(a,ha)['runs'][0]['source_unchanged'])
        run=self.state(a,ha)['runs'][0]
        self.operate(a,ha,'cleanup',{'run_id':run['run_id'],'revision':run['revision']})
        self.assertEqual(before,[RepositoryGateway(repo).snapshot() for repo in self.repos])

    def test_restart_preserves_held_budget_and_replaces_session_routes_and_capabilities(self):
        a,ha=self.connect(self.repos[0]);workspace=self.hub.workspaces[a['id']]
        # Explicit accounting fixture: reserve on this journal, then reopen identical scope.
        workspace.session.registry.reserve()
        old=workspace.session.registry.budget()
        self.client.close();self.hub.close();self.start(ui='react')
        newer,headers=self.connect(self.repos[0])
        self.assertNotEqual(a['id'],newer['id']);self.assertNotEqual(ha,headers)
        self.assertEqual(old,{key:self.state(newer,headers)[key] for key in old})
        self.assertIn(self.client.get(a['api_prefix']+'state',headers=ha).status_code,(401,404))

    def test_idle_shutdown_refuses_stale_challenge_and_active_workspace(self):
        record,_=self.connect(self.repos[0]);session=self.hub.workspaces[record['id']].session
        stopped=[];self.hub.stop_callback=lambda:stopped.append(True)
        self.assertEqual('failed',self.wait(self.client.post('/api/actions/stop_workbench',headers=self.headers,
            json={'request_id':uuid.uuid4().hex,'revision':'0'*64}),self.headers,'/api/')['status'])
        status=self.client.get('/api/service',headers=self.headers).json()
        session.active=uuid.uuid4().hex
        job=self.wait(self.client.post('/api/actions/stop_workbench',headers=self.headers,
            json={'request_id':uuid.uuid4().hex,'revision':status['stop_revision']}),self.headers,'/api/')
        self.assertEqual('failed',job['status']);self.assertFalse(stopped)
        session.active=None
        job=self.wait(self.client.post('/api/actions/stop_workbench',headers=self.headers,
            json={'request_id':uuid.uuid4().hex,'revision':status['stop_revision']}),self.headers,'/api/')
        self.assertEqual('finished',job['status']);self.assertEqual([True],stopped)

    def test_monitor_pending_plan_uses_same_listener_and_original_shared_budget(self):
        repo=self.repos[0];preview=inspect_repository(repo)
        body={'request_id':uuid.uuid4().hex,'path':str(repo),'revision':preview['revision'],
              'allowed_paths':['calculator.py'],'readonly_paths':['tests/test_calculator.py'],
              'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':30,'confirmed':True,
              'branch':'main','github_url':'','interval_seconds':3600}
        job=self.wait(self.client.post('/api/actions/monitor_start',headers=self.headers,json=body),self.headers,'/api/')
        self.assertEqual('finished',job['status'],job)
        identity=job['result']['monitor_id'];deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            state=self.client.get('/api/state',headers=self.headers).json()
            monitor=next(item for item in state['monitors'] if item['id']==identity)
            events=monitor['events']
            if events and events[0]['status']=='awaiting_approval':break
            time.sleep(.05)
        self.assertEqual('awaiting_approval',events[0]['status'],monitor)
        record=state['workspaces'][0];workspace=self.hub.workspaces[record['id']]
        self.assertIs(self.hub.monitor.registries[identity],workspace.session.budget_registry)
        headers={'Authorization':'Bearer '+record['token']}
        self.assertEqual(1,len(self.state(record,headers)['plans']))
        self.assertEqual(1,self.state(record,headers)['requests_used'])
        opened=self.wait(self.client.post('/api/actions/monitor_open',headers=self.headers,json={
            'request_id':uuid.uuid4().hex,'monitor_id':identity,'event_id':events[0]['id']}),self.headers,'/api/')
        self.assertEqual(workspace.url,opened['result']['launch_url'])
