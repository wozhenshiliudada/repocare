"""Original embedded HTTP/auth/queue tests; model helper replies are deterministic."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from starlette.applications import Starlette
from starlette.testclient import TestClient
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.embedded_workspace import EmbeddedWorkspace, WorkspaceRouter
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.job_control import OperationStopped
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider


def receipt(revision):
    return dict(schema_version=1,purpose='model_connectivity',status='completed',pi_version='1.0.4',
        requests=1,requests_started=1,consumption='reported',count_scope='adapter_stream_invocations',
        active_tools=[],tool_calls=0,response_received=True,preview_revision=revision,
        transport_retries=0,repair_verified=False,model_cost_verified=False,transport='live')


class ConnectivityHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=create_fixture(self.root/'repo')
        self.before=RepositoryGateway(self.repo).snapshot()
        self.config=self.root/'provider.env'
        self.config.write_text('PI_MAINTENANCE_API_KEY=synthetic-never-sent\n'
                               'PI_MAINTENANCE_BASE_URL=https://example.invalid/v1\nPI_MAINTENANCE_MODEL=explicit-test\n')
        self.inner=PiProvider(('calculator.py',),readonly_paths=('tests/test_calculator.py',),max_requests=6)
        self.inner.config_file=self.config
        self.session=UISession(self.repo,Policy(('calculator.py',),(DEMO_TEST,),
                               readonly_paths=('tests/test_calculator.py',)),self.inner,self.root/'runs')
        self.addCleanup(self.session.close)
        # Other delegated files can change concurrently. This fixes only their
        # unrelated fingerprint; the actual require_current gate still runs.
        self.fingerprint=patch.object(self.session.runtime,'_fingerprint',return_value=self.session.runtime.loaded)
        self.fingerprint.start();self.addCleanup(self.fingerprint.stop)
        self.workspace=EmbeddedWorkspace(self.session,12345)
        hub=SimpleNamespace(lock=threading.RLock(),workspaces={self.workspace.identity:self.workspace})
        self.client=TestClient(WorkspaceRouter(hub,Starlette()),base_url='http://127.0.0.1:12345')
        self.addCleanup(self.client.close)
        self.prefix=self.workspace.view()['api_prefix']
        self.headers={'Authorization':'Bearer '+self.session.token}

    def state(self):
        result=self.client.get(self.prefix+'state',headers=self.headers)
        self.assertEqual(200,result.status_code,result.text);return result.json()

    def submit(self,action,payload=None,identity=None):
        response=self.client.post(self.prefix+'actions/'+action,headers=self.headers,
                                 json={'request_id':identity or uuid.uuid4().hex,**(payload or {})})
        self.assertEqual(202,response.status_code,response.text);return response.json()['id']

    def wait(self,identity):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            job=next(j for j in self.state()['jobs'] if j['id']==identity)
            if job['status']!='running':return job
            time.sleep(.01)
        self.fail('Original queue did not finish')

    def preview(self):
        job=self.wait(self.submit('model_probe_preview'))
        self.assertEqual('finished',job['status'],job)
        return job['result']['model_probe_preview']

    def test_embedded_auth_exact_fields_and_get_never_dispatch(self):
        path=self.prefix+'actions/model_probe_preview'
        self.assertEqual(401,self.client.post(path,json={'request_id':uuid.uuid4().hex}).status_code)
        self.assertEqual(401,self.client.get(self.prefix+'state',headers={'Authorization':'Bearer hub-token'}).status_code)
        self.assertEqual(403,self.client.post(path,headers={**self.headers,'Origin':'https://evil.invalid'},
                                              json={'request_id':uuid.uuid4().hex}).status_code)
        self.assertEqual(409,self.client.post(path,headers=self.headers,
                   json={'request_id':uuid.uuid4().hex,'confirmed':True}).status_code)
        with patch('repo_maintainer.model_connectivity._invoke') as invoke:
            preview=self.preview()
            with patch('repo_maintainer.model_connectivity.load_provider_config',side_effect=AssertionError('GET read config')):
                self.assertEqual([],self.state()['model_connectivity']['checks'])
            invoke.assert_not_called()
        self.assertEqual('explicit-test',preview['model']);self.assertEqual(1,preview['request_count'])
        self.assertNotIn('synthetic-never-sent',json.dumps(preview))
        self.assertEqual({},self.session.registry.load('attempt'))

    def test_queued_preview_then_single_original_attempt_duplicate_never_resends(self):
        self.session.source_guard=lambda: None
        preview=self.preview();identity=uuid.uuid4().hex
        with patch('repo_maintainer.model_connectivity._invoke',return_value=receipt(preview['revision'])) as invoke:
            first=self.wait(self.submit('model_probe',{'revision':preview['revision']},identity))
            self.assertEqual('finished',first['status'],first)
            second=self.wait(self.submit('model_probe',{'revision':preview['revision']},identity))
            self.assertEqual(first,second);self.assertEqual(1,invoke.call_count)
        state=self.state()
        self.assertEqual(1,state['requests_used']);self.assertEqual(0,state['requests_reserved_unknown'])
        self.assertEqual('completed',state['model_connectivity']['checks'][0]['status'])
        self.assertFalse(state['model_connectivity']['checks'][0]['repair_verified'])
        self.assertEqual(1,len(self.session.registry.load('attempt')))
        self.assertEqual(self.before,RepositoryGateway(self.repo).snapshot())

    def test_probe_requires_original_finished_preview_and_stale_config_is_rejected(self):
        with patch('repo_maintainer.model_connectivity._invoke') as invoke:
            job=self.wait(self.submit('model_probe',{'revision':'a'*64}))
            self.assertEqual('failed',job['status']);self.assertIn('预览',job['error'])
            preview=self.preview()
            self.config.write_text(self.config.read_text().replace('explicit-test','changed-model'))
            job=self.wait(self.submit('model_probe',{'revision':preview['revision']}))
            self.assertEqual('failed',job['status']);invoke.assert_not_called()
        self.assertEqual(0,self.state()['requests_reserved_unknown'])

    def test_original_stop_control_cancels_probe_and_preserves_one_unknown(self):
        preview=self.preview();entered=threading.Event()
        def invoke(provider,config,revision,cancel):
            entered.set()
            if not cancel.wait(5):raise RuntimeError('Original cancel event missing')
            raise OperationStopped('synthetic cancellation')
        with patch('repo_maintainer.model_connectivity._invoke',side_effect=invoke) as transport:
            identity=self.submit('model_probe',{'revision':preview['revision']})
            self.assertTrue(entered.wait(5))
            control=self.state()['task_control'];self.assertTrue(control['can_stop'])
            self.submit('stop_job',{'job_id':identity,'revision':control['revision']})
            job=self.wait(identity)
            self.assertEqual('stopped',job['status']);self.assertEqual(1,transport.call_count)
            self.submit('model_probe',{'revision':preview['revision']},identity)
            self.assertEqual(1,transport.call_count)
        state=self.state();self.assertEqual(1,state['requests_reserved_unknown'])
        self.assertEqual(0,state['requests_used']);self.assertEqual('unknown',state['model_connectivity']['checks'][0]['status'])

    def test_monitor_source_and_runtime_gates_fail_before_original_reservation(self):
        with patch('repo_maintainer.model_connectivity._invoke') as invoke:
            self.session.monitor_owned=True
            self.assertFalse(self.state()['model_connectivity']['available'])
            job=self.wait(self.submit('model_probe_preview'));self.assertEqual('failed',job['status'])
            self.assertIn('原控制器',job['error'])
            self.session.monitor_owned=False
            self.session.source_guard=lambda: (_ for _ in ()).throw(SafetyError('original source changed'))
            job=self.wait(self.submit('model_probe_preview'));self.assertIn('source changed',job['error'])
            self.session.source_guard=None
            with patch.object(self.session.runtime,'require_current',side_effect=SafetyError('runtime changed')):
                job=self.wait(self.submit('model_probe_preview'));self.assertIn('runtime changed',job['error'])
            invoke.assert_not_called()
        self.assertEqual({},self.session.registry.load('attempt'))


if __name__=='__main__':unittest.main()
