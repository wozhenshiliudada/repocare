import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from starlette.testclient import TestClient
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.job_control import JobControl, OperationStopped
from repo_maintainer.local_ui import UISession, create_app
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_live import communicate_live
from repo_maintainer.provider import MockProvider
from repo_maintainer.runner import run_test, stop_tree
from repo_maintainer.ui_registry import UIRegistry, provider_binding
from repo_maintainer.workflow import repair

def evidence():
    return {'schema_version':1,'status':'running','failure_category':None,
        'requests':None,'requests_started':1,'count_scope':'adapter_stream_invocations',
        'consumption':'unknown','tool_calls':0,'events':[{'type':'agent_start'}],
        'usage':[],'events_complete':False,'model_cost_verified':False}

class GateProvider(MockProvider):
    max_requests = 4
    requests_used = 0
    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.ignore_stop = False
        self._progress_callback = self._cancel_event = None
    def plan(self, *args):
        self._progress_callback(evidence())
        while not self.release.wait(.01):
            if self._cancel_event.is_set() and not self.ignore_stop:
                raise OperationStopped('Synthetic planning cancelled')
        self.evidence = {'requests':1}
        return super().plan(*args)

class LiveProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / 'repo')
        self.gateway = RepositoryGateway(self.repo)
        self.before = self.gateway.snapshot()
        self.policy = Policy(('calculator.py',), (DEMO_TEST,))

    def test_progress_is_durable_before_completion_without_refunding(self):
        binding = {'repository':'fixture','policy':{},'provider':provider_binding(GateProvider())}
        registry = UIRegistry(self.root / 'registry', binding)
        identity, allowance = registry.reserve()
        registry.progress(identity, evidence())
        self.assertEqual(4, registry.budget()['requests_reserved_unknown'])
        registry.close()
        registry = UIRegistry(self.root / 'registry', binding)
        self.addCleanup(registry.close)
        saved = registry.load('attempt')[identity]
        self.assertEqual('unknown', saved['status'])
        self.assertEqual(1, saved['progress']['requests_started'])
        self.assertEqual(4, registry.budget()['requests_reserved_unknown'])

    def test_progress_rejects_raw_text_reversed_counts_and_late_frames(self):
        binding = {'repository':'fixture','policy':{},'provider':provider_binding(GateProvider())}
        registry = UIRegistry(self.root / 'registry', binding)
        self.addCleanup(registry.close)
        identity, _ = registry.reserve()
        for frame in ({**evidence(),'raw_text':'credential-shaped-private-text'},
                      {**evidence(),'events':[{'type':'agent_start','text':'private'}]}):
            with self.assertRaises(SafetyError): registry.progress(identity, frame)
        registry.progress(identity, evidence())
        with self.assertRaises(SafetyError): registry.progress(identity, {**evidence(),'requests_started':0})
        registry.settle(identity, {}, success=False)
        with self.assertRaises(SafetyError): registry.progress(identity, evidence())
        self.assertNotIn('credential-shaped', json.dumps(registry.load('attempt')))

    def start_ui(self):
        provider = GateProvider()
        session = UISession(self.repo, self.policy, provider, self.root / 'runs')
        self.addCleanup(session.close)
        self.addCleanup(provider.release.set)
        client = TestClient(create_app(session, 12345), base_url='http://127.0.0.1:12345')
        self.addCleanup(client.close)
        headers = {'Authorization':'Bearer '+session.token}
        identity = uuid.uuid4().hex
        reply = client.post('/api/actions/plan', headers=headers,
            json={'request_id':identity,'issue':'addition bug'})
        self.assertEqual(202, reply.status_code, reply.text)
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            value=client.get('/api/state',headers=headers).json()
            if value['jobs'][0].get('progress',{}).get('requests_started')==1:
                return provider,session,client,headers,value
            time.sleep(.02)
        self.fail('No live progress before completion')

    def test_stop_authentication_target_and_idempotency_keep_unknown_budget(self):
        provider,session,client,headers,state = self.start_ui()
        target=state['task_control']
        body={'request_id':uuid.uuid4().hex,'job_id':target['job_id'],'revision':target['revision']}
        self.assertEqual(401,client.post('/api/actions/stop_job',json=body).status_code)
        self.assertEqual(409,client.post('/api/actions/stop_job',headers=headers,json={**body,'revision':'0'*64}).status_code)
        self.assertEqual(409,client.post('/api/actions/stop_job',headers=headers,json={**body,'job_id':uuid.uuid4().hex}).status_code)
        self.assertEqual(409,client.post('/api/actions/stop_job',headers=headers,json={**body,'request_id':target['job_id']}).status_code)
        reply=client.post('/api/actions/stop_job',headers=headers,json=body)
        self.assertEqual(202,reply.status_code,reply.text)
        self.assertEqual(reply.json(),client.post('/api/actions/stop_job',headers=headers,json=body).json())
        self.assertEqual(409,client.post('/api/actions/plan',headers=headers,
            json={'request_id':body['request_id'],'issue':'addition bug'}).status_code)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            state=client.get('/api/state',headers=headers).json()
            if not state['active']: break
            time.sleep(.02)
        self.assertEqual('stopped',state['jobs'][0]['status'])
        self.assertFalse(state['plans'])
        self.assertEqual(4,state['requests_reserved_unknown'])
        self.assertFalse(state['task_control']['can_stop'])
        self.assertEqual(self.before,self.gateway.snapshot())
        self.assertEqual(409,client.post('/api/actions/stop_job',headers=headers,
            json={**body,'request_id':uuid.uuid4().hex}).status_code)
        session.close()
        reopened=UISession(self.repo,self.policy,GateProvider(),self.root/'runs')
        self.addCleanup(reopened.close)
        self.assertEqual('stopped',reopened.state()['jobs'][0]['status'])
        self.assertEqual(4,reopened.state()['requests_reserved_unknown'])
        self.assertEqual(1,reopened.budget_registry.load('attempt').popitem()[1]['progress']['requests_started'])

    def test_late_completed_plan_cannot_become_approvable_after_stop(self):
        provider,session,client,headers,state=self.start_ui()
        # Return a valid late plan despite cancellation; final publication must still reject it.
        target=state['task_control']
        provider.ignore_stop=True
        reply=client.post('/api/actions/stop_job',headers=headers,
            json={'request_id':uuid.uuid4().hex,'job_id':target['job_id'],'revision':target['revision']})
        provider.release.set()
        self.assertEqual(202,reply.status_code)
        deadline=time.monotonic()+10
        while session.active and time.monotonic()<deadline: time.sleep(.02)
        self.assertFalse(session.state()['plans'])
        self.assertEqual('stopped',session.state()['jobs'][0]['status'])

    def test_stop_during_candidate_test_waits_then_removes_only_own_candidate(self):
        control=JobControl(lambda value:None)
        calls=[]
        def testing(*args,**kwargs):
            result=run_test(*args,**kwargs)
            calls.append(result)
            if len(calls)==2: control.cancel.set()
            return result
        with patch('repo_maintainer.workflow.run_test',side_effect=testing):
            result=repair(self.repo,'addition',MockProvider(),self.policy,'auto',self.root/'runs',
                operation_control=control.phase)
        self.assertEqual(2,len(calls))
        self.assertEqual(0,calls[-1]['returncode'])
        self.assertTrue(result['operator_stop'])
        self.assertEqual('rolled_back',result['status'])
        self.assertFalse(Path(result['workspace']).exists())
        self.assertEqual(self.before,self.gateway.snapshot())

    def test_pipe_progress_precedes_exit_and_cancel_does_not_kill_foreign_process(self):
        frame='PI_EVIDENCE_V1 '+json.dumps(evidence())+'\n'
        code='import sys,time; sys.stdin.buffer.read(); sys.stderr.write('+repr(frame)+'); sys.stderr.flush(); time.sleep(30)'
        kwargs={'stdin':subprocess.PIPE,'stdout':subprocess.PIPE,'stderr':subprocess.PIPE,
            'start_new_session':os.name!='nt','creationflags':subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0}
        process=subprocess.Popen([sys.executable,'-c',code],**kwargs)
        foreign=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
        cancel=threading.Event()
        seen=[]
        def progress(value):
            self.assertIsNone(process.poll())
            seen.append(value)
            cancel.set()
        try:
            with self.assertRaises(OperationStopped):
                communicate_live(process,b'{}',4,progress,cancel,5)
            self.assertEqual(1,len(seen))
            self.assertIsNotNone(process.poll())
            self.assertIsNone(foreign.poll())
        finally:
            stop_tree(process);stop_tree(foreign)
            for child in (process,foreign):
                child.wait(timeout=10)
                for pipe in (child.stdin,child.stdout,child.stderr):
                    if not pipe.closed: pipe.close()

    def test_real_pi_live_progress_then_stop_keeps_source_and_consumption(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from repo_maintainer.pi_provider import PiProvider
        release=threading.Event()
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)))
                calls.append(1)
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                if len(calls)==1:
                    frame={'id':'synthetic','object':'chat.completion.chunk','created':1,'model':'mock-maintenance',
                        'choices':[{'index':0,'delta':{'role':'assistant','tool_calls':[{'index':0,'id':'read',
                          'type':'function','function':{'name':'read_source','arguments':'{"path":"calculator.py"}'}}]},'finish_reason':None}]}
                    tail={**frame,'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls'}]}
                    self.wfile.write(('data: '+json.dumps(frame)+'\n\ndata: '+json.dumps(tail)+'\n\ndata: [DONE]\n\n').encode());self.wfile.flush()
                else:
                    release.wait(20)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        provider=PiProvider(('calculator.py',),mock_url='http://127.0.0.1:'+str(server.server_port)+'/v1',max_requests=4)
        session=UISession(self.repo,self.policy,provider,self.root/'real-pi')
        try:
            session.submit(uuid.uuid4().hex,'plan',{'issue':'addition bug'})
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                state=session.state();progress=state['jobs'][0].get('progress',{})
                if progress.get('requests_started')==2 and any(event.get('path')=='calculator.py' for event in progress.get('events',[])): break
                if not state['active']:self.fail(str(state['jobs']))
                time.sleep(.04)
            self.assertEqual(2,progress.get('requests_started'))
            self.assertTrue(any(event.get('path')=='calculator.py' for event in progress['events']))
            target=state['task_control']
            session.submit(uuid.uuid4().hex,'stop_job',{'job_id':target['job_id'],'revision':target['revision']})
            deadline=time.monotonic()+15
            while session.active and time.monotonic()<deadline:time.sleep(.04)
            self.assertIsNone(session.active)
            self.assertEqual('stopped',session.state()['jobs'][0]['status'])
            self.assertEqual(4,session.state()['requests_reserved_unknown'])
            self.assertEqual(2,len(calls))
            self.assertEqual(self.before,self.gateway.snapshot())
            attempt=list(session.registry.load('attempt').values())[0]
            self.assertEqual('unknown',attempt['status'])
            self.assertEqual('parent_interrupted',attempt['evidence']['failure_category'])
            self.assertNotIn('return a - b',json.dumps(attempt))
        finally:
            release.set();session.close();server.shutdown();server.server_close()
