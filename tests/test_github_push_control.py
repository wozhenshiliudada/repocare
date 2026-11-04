"""Original monitor integration over real local Git; no external GitHub/model."""
import copy
import hashlib
import hmac
import json
import contextlib
import io
import socket
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch

from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.github_connection import fetch_snapshot
from repo_maintainer.github_push import normalize_push, PushInputError
from repo_maintainer.github_push_control import GitHubPushControl, load_configuration
from repo_maintainer.repository_workbench import RepositoryWorkbench, inspect_repository,serve_workbench
from repo_maintainer.models import SafetyError
from test_maintenance_monitor import CountingProvider
from test_repository_workbench import FakeServer


class PushControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=RepositoryGateway(create_fixture(self.root/'source'))
        self.repo.command('config','user.name','Push fixture');self.repo.command('config','user.email','fixture@example.invalid')
        self.repo.command('remote','add','origin','https://github.com/example/private.git')
        self.remote_head=self.repo.head;self.api_calls=[]
        class Remote:
            pass
        self.remote=Remote()
        self.remote.api=self.metadata
        self.remote.branch_head=lambda *args:self.remote_head
        self.remote.workflow_runs=lambda *args:[]
        self.remote.snapshot=lambda url,branch,sha,dest:fetch_snapshot(self.repo.root,branch,sha,dest)
        config=self.root/'provider.local';config.write_text('synthetic only',encoding='utf-8')
        CountingProvider.calls=0
        self.hub=RepositoryWorkbench(self.root/'state',config,request_budget=6,github=self.remote,
            provider_factory=lambda _:CountingProvider(),server_factory=FakeServer)
        self.addCleanup(self.hub.close)
        # Deterministic fixture only: thread is joined, then original active state
        # remains observable. This does not implement a production resume path.
        self.hub.monitor.stop();self.hub.monitor.stopping.clear()
        source=inspect_repository(self.repo.root)
        scope={'path':str(self.repo.root),'revision':source['revision'],'allowed_paths':['calculator.py'],
            'readonly_paths':['tests/test_calculator.py'],'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':30,
            'confirmed':True,'branch':source['branch'],'github_url':'https://github.com/example/private',
            'interval_seconds':60}
        self.mid=self.worker(self.hub.monitor.start,scope)['monitor_id']
        self.target={'monitor_id':self.mid,'repository_id':123,'repository':'example/private','branch':source['branch']}
        self.secret=b'local-fixture-secret-not-real-'*2
        self.control=GitHubPushControl(self.hub,self.target,self.secret)
        self.hub.github_push=self.control

    def worker(self,function,*args,**kwargs):
        return self.hub.executor.submit(function,*args,**kwargs).result(timeout=30)

    def metadata(self,path):
        self.api_calls.append(path)
        self.assertEqual('/repos/example/private',path)
        return {'id':123,'full_name':'example/private','private':True}

    def packet(self,delivery='delivery-1',after=None,**extra):
        after=after or self.repo.head
        value={'repository':{'id':123,'full_name':'example/private','private':True},
            'ref':'refs/heads/'+self.target['branch'],'before':'a'*40,'after':after,
            'created':False,'deleted':False,'forced':False,'head_commit':{'id':after},**extra}
        body=json.dumps(value,ensure_ascii=False).encode()
        headers=[('Content-Type','application/json'),('Content-Length',str(len(body))),
            ('X-GitHub-Event','push'),('X-GitHub-Delivery',delivery),
            ('X-Hub-Signature-256','sha256='+hmac.new(self.secret,body,hashlib.sha256).hexdigest())]
        return headers,body

    def settled(self):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if not self.control.queued:
                return self.control.view()
            time.sleep(.01)
        self.fail('Original push worker did not finish')

    def test_real_original_poll_records_one_pending_approval_and_poll_redelivery_do_not_repeat(self):
        before=self.repo.snapshot()
        status,receipt=self.control.receive(*self.packet())
        self.assertEqual(202,status)
        state=self.settled();work=state['work'][0]
        self.assertEqual('event_linked',work['status'],state)
        event=self.hub.monitor.view()[0]['events'][0]
        self.assertEqual('awaiting_approval',event['status'])
        self.assertEqual(self.repo.head,event['sha'])
        self.assertEqual(1,CountingProvider.calls)
        budget=self.control.registry.budget()
        self.assertEqual((2,0),(budget['requests_used'],budget['requests_reserved_unknown']))
        self.assertEqual((202,receipt),self.control.receive(*self.packet()))
        self.control.receive(*self.packet('another-delivery'))
        self.settled();self.worker(self.hub.monitor.poll,self.mid)
        self.assertEqual(budget,self.control.registry.budget())
        self.assertEqual(1,CountingProvider.calls)
        self.assertEqual(1,len(self.control.inbox.view()['work']))
        self.assertEqual(2,len(self.control.inbox.view()['deliveries']))
        self.assertEqual(before,self.repo.snapshot())
        self.assertNotIn(self.secret.decode(),json.dumps(self.hub.state()))

    def test_bad_signature_and_force_or_delete_never_enter_original_poll(self):
        headers,body=self.packet();headers[-1]=(headers[-1][0],'sha256='+'0'*64)
        with self.assertRaises(PushInputError):self.control.receive(headers,body)
        self.control.receive(*self.packet('force',forced=True))
        self.control.receive(*self.packet('delete',after='0'*40,deleted=True,head_commit=None))
        state=self.settled()
        self.assertEqual([],state['work']);self.assertEqual([],self.api_calls)
        self.assertEqual({},self.control.registry.load('event'));self.assertEqual(0,CountingProvider.calls)

    def test_remote_head_changes_before_poll_never_runs_the_other_version(self):
        first=self.repo.head;second='b'*40
        with patch.object(self.hub.monitor,'current_head',side_effect=[first,second]):
            self.control.receive(*self.packet())
            state=self.settled()
        self.assertEqual('superseded',state['work'][0]['status'])
        self.assertEqual({},self.control.registry.load('event'))
        self.assertEqual(0,CountingProvider.calls)

    def test_older_notification_is_superseded_without_git_or_model(self):
        self.control.receive(*self.packet(after='c'*40))
        self.assertEqual('superseded',self.settled()['work'][0]['status'])
        self.assertEqual({},self.control.registry.load('event'));self.assertEqual(0,CountingProvider.calls)

    def test_pause_unknown_budget_and_identity_mismatch_are_blocked_without_model(self):
        self.worker(self.hub.monitor.operate,'monitor_pause',{'monitor_id':self.mid})
        self.control.receive(*self.packet())
        self.assertEqual('blocked',self.settled()['work'][0]['status'])
        self.assertEqual([],self.api_calls);self.assertEqual(0,CountingProvider.calls)

    def test_original_unknown_reservation_is_not_reset_or_dispatched(self):
        self.control.registry.reserve(1);before=self.control.registry.budget()
        self.control.receive(*self.packet())
        self.assertEqual('blocked',self.settled()['work'][0]['status'])
        self.assertEqual(before,self.control.registry.budget());self.assertEqual([],self.api_calls)

    def test_changed_private_repository_identity_does_not_execute(self):
        self.remote.api=lambda _: {'id':999,'private':True,'full_name':'example/private'}
        self.control.receive(*self.packet())
        self.assertEqual('failed',self.settled()['work'][0]['status'])
        self.assertEqual({},self.control.registry.load('event'));self.assertEqual(0,CountingProvider.calls)

    def test_changed_original_spec_cannot_borrow_the_registered_mapping(self):
        self.hub.monitor.records[self.mid]['spec']['timeout_seconds']=999
        self.control.receive(*self.packet())
        self.assertEqual('failed',self.settled()['work'][0]['status'])
        self.assertEqual({},self.control.registry.load('event'));self.assertEqual(0,CountingProvider.calls)

    def test_receipt_ack_is_durable_before_original_worker_can_run(self):
        entered,release=threading.Event(),threading.Event()
        def hold():entered.set();release.wait(10)
        self.hub.executor.submit(hold);self.assertTrue(entered.wait(2))
        try:
            started=time.monotonic();status,_=self.control.receive(*self.packet())
            self.assertEqual(202,status);self.assertLess(time.monotonic()-started,2)
            self.assertEqual('received',self.control.inbox.view()['work'][0]['status'])
            self.assertEqual([],self.api_calls)
        finally:release.set()
        self.assertEqual('event_linked',self.settled()['work'][0]['status'])

    def test_restart_reconciles_original_dispatch_intent_without_replay(self):
        headers,body=self.packet()
        receipt=self.control.inbox.accept(normalize_push(body,headers,self.target,self.secret))
        self.assertIsNotNone(self.control.inbox.begin(receipt['work_id']))
        before=self.control.registry.budget();self.control.close()
        self.control=GitHubPushControl(self.hub,self.target,self.secret);self.hub.github_push=self.control
        self.assertEqual('interrupted_no_replay',self.control.view()['work'][0]['status'])
        self.control.receive(headers,body);self.settled()
        self.assertEqual([],self.api_calls);self.assertEqual(before,self.control.registry.budget())

    def test_configuration_uses_only_explicit_bounded_file_and_no_secret_reflection(self):
        secret_path=self.root/'push.secret';secret_path.write_bytes(self.secret)
        config=self.root/'push.json';config.write_text(json.dumps({'schema_version':1,'target':self.target,
            'secret_file':str(secret_path),'port':0}),encoding='utf-8')
        self.assertEqual((self.target,self.secret,0),load_configuration(config))
        secret_path.write_bytes(b'too-short')
        with self.assertRaises(SafetyError) as caught:load_configuration(config)
        self.assertNotIn('too-short',str(caught.exception))
        config.write_text('{"schema_version":1,"schema_version":1}',encoding='utf-8')
        with self.assertRaises(SafetyError):load_configuration(config)

    def test_real_receive_only_http_reaches_original_healthy_check_and_safe_stop_closes_both(self):
        source=self.repo.root/'calculator.py'
        source.write_bytes(source.read_bytes().replace(b'return a - b',b'return a + b'))
        self.repo.command('add','calculator.py');self.repo.command('commit','-m','Healthy fixture version')
        self.remote_head=self.repo.head
        self.hub.close()  # Release the constructor-thread workbench FileLock.
        errors=[]
        def run_original_server():
            application=None
            try:
                application=RepositoryWorkbench(self.root/'state',self.root/'provider.local',request_budget=6,
                    github=self.remote,provider_factory=lambda _:CountingProvider(),server_factory=FakeServer,ui='react')
                self.hub=application
                self.control=GitHubPushControl(application,self.target,self.secret)
                application.github_push=self.control
                self.control.start(0)
                serve_workbench(application,0)
            except BaseException as error:errors.append(error)
            finally:
                if application is not None:application.close()
        thread=threading.Thread(target=run_original_server)
        private=io.StringIO()
        with contextlib.redirect_stdout(private):
            thread.start()
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline and not getattr(self.hub,'url',None) and not errors:time.sleep(.01)
                self.assertFalse(errors,errors)
                base=self.hub.url.split('/#',1)[0]
                receiver_port=self.control.receiver.port
                def call(path,body=None,authenticated=True):
                    headers={'Authorization':'Bearer '+self.hub.token} if authenticated else {}
                    raw=None if body is None else json.dumps(body).encode()
                    if raw is not None:headers['Content-Type']='application/json'
                    try:
                        with urlopen(Request(base+path,data=raw,headers=headers),timeout=10) as response:
                            return response.status,response.read()
                    except HTTPError as error:return error.code,error.read()
                # Uvicorn may not yet have entered accept after printing the URL.
                for _ in range(100):
                    try:
                        status,_=call('/api/service')
                        if status==200:break
                    except OSError:pass
                    time.sleep(.01)
                self.assertEqual(401,call('/api/state',authenticated=False)[0])
                headers,body=self.packet()
                with urlopen(Request(self.control.receiver.url,data=body,headers=dict(headers)),timeout=10) as response:
                    self.assertEqual(202,response.status)
                push=self.settled()
                self.assertEqual('healthy',push['work'][0]['monitor_status'],push)
                self.assertEqual(0,CountingProvider.calls)
                self.assertEqual({},self.control.registry.load('pi_task'))
                state=json.loads(call('/api/state')[1])
                self.assertEqual('event_linked',state['github_push']['work'][0]['status'])
                try:
                    urlopen(self.control.receiver.url.replace('/github/push','/api/state'),timeout=3)
                    self.fail('Receiver exposed management API')
                except HTTPError as error:self.assertEqual(404,error.code)
                # Pause by the original operation before requesting its safe stop.
                self.worker(self.hub.monitor.operate,'monitor_pause',{'monitor_id':self.mid})
                service=json.loads(call('/api/service')[1])
                status,_=call('/api/actions/stop_workbench',{'request_id':uuid.uuid4().hex,'revision':service['stop_revision']})
                self.assertEqual(202,status)
                thread.join(10)
                self.assertFalse(thread.is_alive(),'Original stop did not close workbench')
                self.assertFalse(errors,errors)
                self.assertTrue(self.hub.closed);self.assertTrue(self.control.closed)
                for port in [receiver_port,int(base.rsplit(':',1)[1])]:
                    with socket.socket() as probe:
                        probe.settimeout(.3);self.assertNotEqual(0,probe.connect_ex(('127.0.0.1',port)))
            finally:
                if thread.is_alive():
                    if self.hub.stop_callback:self.hub.stop_callback()
                    thread.join(10)
        self.assertNotIn(self.hub.token,json.dumps(push))


if __name__=='__main__':unittest.main()
