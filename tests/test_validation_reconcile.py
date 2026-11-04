"""Recovery uses real SDK Native bytes and the original ledger, with no model transport."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_validation_native as native_fixture
from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.store import now
from repo_maintainer.task_session import fingerprint
from repo_maintainer.ui_registry import UIRegistry,provider_binding
from repo_maintainer.validation_proposal import source_packet,ValidationProposal,ValidationPlanner,PURPOSE
from repo_maintainer.validation_reconcile import reconcile


class ValidationReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='validation-reconcile-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=RepositoryGateway(create_fixture(self.root/'source'))
        self.provider=PiProvider((),'http://127.0.0.1:1/v1',('calculator.py',),max_requests=6)
        self.registry=UIRegistry(self.root/'original-ledger',{
            'repository':str(self.repo.root),'provider':provider_binding(self.provider)})
        self.addCleanup(self.registry.close)
        self.issue='Return signed addition from calculator.add'
        self.source=source_packet(self.repo,self.issue,['.'],['calculator.py'],{'original_rule':'confirmed'})
        self.guard_calls=0
        def guard():self.guard_calls+=1
        self.guard=guard
        self.service=ValidationProposal(self.registry,self.repo,self.provider,self.source,guard,'new-session')

    def stage(self,*,clarification=False,committed=True):
        self.planner=ValidationPlanner(self.registry,self.repo,self.provider,self.source,self.guard)
        self.task_id=self.planner.begin(self.issue);scope,context=self.planner.current_scope(self.issue)
        directory=self.planner.store.directory(self.task_id)
        (directory/'workspace').mkdir();(directory/'sessions').mkdir()
        task={'mode':'create','context':context,'binding':{'schema_version':1,'task_id':self.task_id,'revision':0,
            'cwd':str(directory/'workspace'),'sessions_dir':str(directory/'sessions')}}
        value=native_fixture.proposal();value['requirement']=self.issue
        if clarification:value.update(clarification=True,cases=[],limitations='Specify the desired return value')
        # This fixture only appends synthetic messages via real SessionManager. It opens no model endpoint.
        result=native_fixture.ValidationNativeTests.node(self,task,proposal=value)
        self.file=Path(result['session_file'])
        import json
        entries=[json.loads(line) for line in result['raw'].splitlines()]
        receipt=next(e['message']['details'] for e in entries if e.get('message',{}).get('role')=='toolResult')
        self.attempt_id,_=self.registry.reserve(1)
        evidence={'schema_version':1,'purpose':PURPOSE,'status':'completed','failure_category':None,
            'count_scope':'adapter_stream_invocations','model_cost_verified':False,'requests':1,'requests_started':1,
            'consumption':'reported','events_complete':True,'events':[],'usage':[],
            'active_tools':['propose_validation','read_source'],'source_snapshot_sha256':fingerprint(self.source['files']),
            'validation_context_sha256':fingerprint(self.source),'validation_proposal_sha256':receipt['proposal_sha256'],
            'attempt_id':self.attempt_id,'task_id':self.task_id}
        self.registry.settle(self.attempt_id,evidence,success=True)
        if committed:
            with self.planner.store.lease(self.task_id,scope,0) as lease:
                lease.commit(self.file.name,result['leaf_id'],fingerprint(self.source['files']),self.attempt_id)
        self.row={'id':self.service.identity,'status':'interrupted_no_replay','source':self.source,
                  'task_id':self.task_id,'operation_id':'old-operation','preview_revision':'a'*64,
                  'started_at':now(),'finished_at':now(),'runtime_authorized':False,
                  'error_type':'InjectedResultWriteFailure','evidence':copy.deepcopy(evidence)}
        self.registry.put('validation_proposal',self.service.identity,self.row)
        self.revision=self.service.revision(self.row)

    def untouched(self):
        return (self.registry.budget(),self.registry.load('attempt'),self.registry.load('pi_task'),
                self.registry.load('pi_checkpoint_attempt'),self.file.read_bytes())

    def test_committed_settled_native_recovers_only_reviewable_result_and_preserves_prior_failure(self):
        self.stage();before=self.untouched()
        with (patch.object(self.provider,'propose_validation',side_effect=AssertionError('No model calls')),
             patch.object(self.registry,'reserve',side_effect=AssertionError('No reservation')),
             patch.object(self.registry,'settle',side_effect=AssertionError('No settlement'))):
            result=reconcile(self.service,self.revision)
        self.assertEqual('awaiting_validation_review',result['status']);self.assertFalse(result['runtime_authorized'])
        self.assertNotIn('review',result);self.assertEqual(before,self.untouched())
        self.assertEqual(self.row,self.registry.load('validation_reconcile')[result['reconciled']['id']]['previous_row'])
        self.assertEqual(self.attempt_id,result['reconciled']['attempt_id'])
        self.assertEqual(result['contract'],self.service.review(self.service.revision(result),'accept')['contract'])
        self.assertEqual(result['contract'],self.service.accepted())

    def test_clarification_recovers_without_contract_or_approval(self):
        self.stage(clarification=True);before=self.untouched();result=reconcile(self.service,self.revision)
        self.assertEqual('clarification_needed',result['status']);self.assertIsNone(result['contract'])
        self.assertIsNone(self.service.accepted());self.assertEqual(before,self.untouched())

    def test_uncommitted_initial_native_is_blocked_without_inferred_result(self):
        self.stage(committed=False);before=self.untouched()
        with self.assertRaisesRegex(SafetyError,'uncommitted'):reconcile(self.service,self.revision)
        self.assertEqual(before,self.untouched());self.assertEqual(self.row,self.registry.load('validation_proposal')[self.service.identity])

    def test_original_unknown_attempt_is_blocked_without_refund(self):
        self.stage();attempt=self.registry.load('attempt')[self.attempt_id]
        attempt['status']='unknown';self.registry.put('attempt',self.attempt_id,attempt);before=self.untouched()
        with self.assertRaisesRegex(SafetyError,'settled original'):reconcile(self.service,self.revision)
        self.assertEqual(before,self.untouched());self.assertEqual({},self.registry.load('validation_reconcile'))

    def test_context_source_and_proposal_hash_mismatches_are_blocked(self):
        self.stage();original=self.registry.load('attempt')[self.attempt_id]
        for field in ('validation_context_sha256','source_snapshot_sha256','validation_proposal_sha256'):
            altered=copy.deepcopy(original);altered['evidence'][field]='f'*64
            self.registry.put('attempt',self.attempt_id,altered);before=self.untouched()
            with self.subTest(field=field),self.assertRaises(SafetyError):reconcile(self.service,self.revision)
            self.assertEqual(before,self.untouched());self.assertEqual(self.row,self.registry.load('validation_proposal')[self.service.identity])
        self.assertEqual({},self.registry.load('validation_reconcile'))

    def test_reopened_session_rejects_old_challenge_and_duplicate_reconciliation(self):
        self.stage();self.service.session_token='reopened-session';before=self.untouched()
        with self.assertRaisesRegex(SafetyError,'review changed'):reconcile(self.service,self.revision)
        self.assertEqual(before,self.untouched())
        recovered=reconcile(self.service,self.service.revision(self.row))
        with self.assertRaises(SafetyError):reconcile(self.service,self.service.revision(recovered))
        self.assertEqual(1,len(self.registry.load('validation_reconcile')))

    def test_stale_issue_source_or_changed_native_cannot_be_reconciled(self):
        self.stage();before=self.registry.load('validation_proposal')[self.service.identity]
        self.file.write_bytes(self.file.read_bytes().replace(b'Return signed addition',b'Wrong signed addition'))
        with self.assertRaises(SafetyError):reconcile(self.service,self.revision)
        self.assertEqual(before,self.registry.load('validation_proposal')[self.service.identity])
        self.assertEqual({},self.registry.load('validation_reconcile'))

    def test_cross_issue_or_original_attempt_binding_cannot_be_substituted(self):
        self.stage();original=copy.deepcopy(self.row)
        self.row['source']={**self.source,'issue':'another Issue'}
        self.registry.put('validation_proposal',self.service.identity,self.row)
        with self.assertRaises(SafetyError):reconcile(self.service,self.service.revision(self.row))
        self.registry.put('validation_proposal',self.service.identity,original)
        self.registry.put('pi_checkpoint_attempt',self.attempt_id,{'task_id':'f'*32,'revision':1})
        with self.assertRaisesRegex(SafetyError,'original attempt'):reconcile(self.service,self.revision)
        self.assertEqual({},self.registry.load('validation_reconcile'))


if __name__=='__main__':unittest.main()
