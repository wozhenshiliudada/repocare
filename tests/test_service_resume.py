import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, create_app
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.service_state import saved_budget
from repo_maintainer.runtime_limits import MAX_REQUESTS
from repo_maintainer.ui_registry import UIRegistry
from repo_maintainer.workflow import cleanup_run


class BudgetExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.binding={'repository':'fixture','policy':{},'provider':{'request_budget':50}}
        self.registry=UIRegistry(self.root,self.binding);self.addCleanup(lambda:self.registry.close())
        first,_=self.registry.reserve();self.registry.settle(first,{'requests':16},success=True)
        second,_=self.registry.reserve();self.registry.settle(second,{},success=False)

    def test_extension_preserves_unknown_and_used_and_reopens_with_saved_ceiling(self):
        before=self.registry.load('attempt')
        preview=self.registry.preview_extension(100,'session')
        self.assertEqual(50,preview['new_remaining']);self.assertEqual(34,preview['requests_reserved_unknown'])
        self.registry.extend_budget(100,preview['revision'],'session',uuid.uuid4().hex)
        self.assertEqual(before,self.registry.load('attempt'));self.assertEqual(100,saved_budget(self.root))
        self.assertEqual({'request_budget':100,'requests_used':16,'requests_reserved_unknown':34,'requests_remaining':50},self.registry.budget())
        self.registry.close()
        with self.assertRaises(SafetyError):UIRegistry(self.root,self.binding)
        new={**self.binding,'provider':{'request_budget':100}}
        self.registry=UIRegistry(self.root,new)
        self.assertEqual(34,self.registry.budget()['requests_reserved_unknown'])

    def test_stale_session_or_target_cannot_expand_and_no_unused_reservation_is_refunded(self):
        preview=self.registry.preview_extension(100,'old-session');before=self.registry.budget()
        with self.assertRaises(SafetyError):self.registry.extend_budget(100,preview['revision'],'new-session',uuid.uuid4().hex)
        for total in (50,49,MAX_REQUESTS+1,True):
            with self.assertRaises(SafetyError):self.registry.preview_extension(total,'session')
        self.assertEqual(before,self.registry.budget())

    def test_corrupt_receipts_or_active_operations_block_extension_without_mutation(self):
        with self.registry.transaction() as db:db.execute('UPDATE budget SET used=15 WHERE id=1')
        with self.assertRaisesRegex(SafetyError,'disagree'):self.registry.preview_extension(100,'session')
        with self.registry.transaction() as db:db.execute('UPDATE budget SET used=16 WHERE id=1')
        self.registry.put('job','a'*32,{'status':'running'})
        with self.assertRaisesRegex(SafetyError,'operation'):self.registry.preview_extension(100,'session')

    def test_expansion_commit_is_atomic_and_replay_cannot_add_another_hundred(self):
        preview=self.registry.preview_extension(100,'session')
        with patch.object(self.registry,'put',side_effect=RuntimeError('injected audit-write failure')):
            with self.assertRaises(RuntimeError):self.registry.extend_budget(100,preview['revision'],'session',uuid.uuid4().hex)
        self.assertEqual(50,self.registry.budget()['request_budget']);self.assertEqual(50,saved_budget(self.root))
        self.registry.extend_budget(100,preview['revision'],'session',uuid.uuid4().hex)
        with self.assertRaises(SafetyError):self.registry.extend_budget(100,preview['revision'],'session',uuid.uuid4().hex)
        self.assertEqual(1,len(self.registry.load('budget_change')))


class SyntheticProvider(MockProvider):
    max_requests=50
    requests_used=0
    def __init__(self):super().__init__('broken');self.calls=0
    def plan(self,*args):
        self.calls+=1;self.evidence={'requests':2}
        return super().plan(*args)


class ServiceResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.repo=create_fixture(self.root/'repository');self.policy=Policy(('calculator.py',),(DEMO_TEST,))
        self.provider=SyntheticProvider();self.session=UISession(self.repo,self.policy,self.provider,self.root/'runs',allow_auto=True)
        self.client=TestClient(create_app(self.session,12345),base_url='http://127.0.0.1:12345')
        self.headers={'Authorization':'Bearer '+self.session.token,'Origin':'http://127.0.0.1:12345'}
        self.addCleanup(self.finish)

    def finish(self):
        self.session.close()
        for identity in reversed(list(self.session.runs)):
            cleanup_run(self.root/'runs'/identity)
        self.client.close()

    def state(self):return self.client.get('/api/state',headers=self.headers).json()
    def action(self,action,payload,identity=None):
        identity=identity or uuid.uuid4().hex
        response=self.client.post('/api/actions/'+action,json={'request_id':identity,**payload},headers=self.headers)
        self.assertEqual(202,response.status_code,response.text)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            job=next(job for job in self.state()['jobs'] if job['id']==identity)
            if job['status']!='running':return job
            time.sleep(.02)
        self.fail('operation did not finish')

    def failed_run(self):
        result=self.action('auto',{'issue':'addition'})
        self.assertEqual('rolled_back',result['result']['workflow_status'])
        return next(run for run in self.state()['runs'] if run['run_id']==result['result']['run_id'])

    def test_continue_uses_same_budget_and_new_run_without_replaying_parent(self):
        before=RepositoryGateway(self.repo).snapshot();parent=self.failed_run();calls=self.provider.calls
        self.provider.scenario='fix'
        payload={'run_id':parent['run_id'],'revision':parent['continuation']['revision'],'mode':'auto'}
        request=uuid.uuid4().hex
        result=self.action('resume_run',payload,request)
        self.assertEqual('candidate_ready',result['result']['workflow_status'])
        duplicate=self.action('resume_run',payload,request);self.assertEqual(result['result'],duplicate['result'])
        self.assertEqual(calls+1,self.provider.calls);self.assertEqual(4,self.state()['requests_used'])
        child=next(run for run in self.state()['runs'] if run['run_id']==result['result']['run_id'])
        self.assertEqual(parent['run_id'],child['continued_from_run'])
        self.assertTrue(child['source_unchanged'])
        cleanup_run(self.root/'runs'/child['run_id']);self.assertEqual(before,RepositoryGateway(self.repo).snapshot())

    def test_changed_source_or_new_session_revision_cannot_continue(self):
        parent=self.failed_run();calls=self.provider.calls
        result=self.action('resume_run',{'run_id':parent['run_id'],'revision':'0'*64,'mode':'auto'})
        self.assertEqual('failed',result['status']);self.assertEqual(calls,self.provider.calls)
        path=self.repo/'calculator.py';original=path.read_bytes();path.write_bytes(original+b'# concurrent\n')
        result=self.action('resume_run',{'run_id':parent['run_id'],'revision':parent['continuation']['revision'],'mode':'auto'})
        self.assertEqual('failed',result['status']);self.assertEqual(calls,self.provider.calls);path.write_bytes(original)

    def test_version_change_blocks_models_before_reservation_and_compact_service_endpoint_is_protected(self):
        self.assertEqual(401,self.client.get('/api/service').status_code)
        view=self.client.get('/api/service',headers=self.headers).json()
        self.assertEqual(1,view['runtime']['revision_protocol']);self.assertNotIn('runs',view)
        before=self.session.registry.budget()
        with patch.object(self.session.runtime,'_fingerprint',return_value='f'*64):
            job=self.action('plan',{'issue':'addition'})
        self.assertEqual('failed',job['status']);self.assertEqual(0,self.provider.calls)
        self.assertEqual(before,self.session.registry.budget())

    def test_extension_http_is_idempotent_and_invalidates_old_claims(self):
        first,_=self.session.registry.reserve();self.session.registry.settle(first,{'requests':16},success=True)
        second,_=self.session.registry.reserve();self.session.registry.settle(second,{},success=False)
        preview=self.action('budget_preview',{'new_total':100})['result']['budget_preview']
        payload={'new_total':100,'revision':preview['revision']};request=uuid.uuid4().hex
        self.action('extend_budget',payload,request);self.action('extend_budget',payload,request)
        self.assertEqual(100,self.provider.max_requests);self.assertEqual(50,self.state()['requests_remaining'])
        self.assertEqual(34,self.state()['requests_reserved_unknown']);self.assertEqual(1,len(self.state()['budget_changes']))


if __name__=='__main__':unittest.main()
