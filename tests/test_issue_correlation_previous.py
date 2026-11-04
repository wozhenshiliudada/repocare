"""Original SQLite/claim contracts only; no Pi, Native allocation or model calls."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.issue_workbench import IssueWorkbench
from repo_maintainer.maintenance_correlation import CLAIMS
from repo_maintainer.models import SafetyError, Plan
from repo_maintainer.ui_registry import UIRegistry
from repo_maintainer.workbench_correlation import WorkbenchCorrelation, RECEIPTS, AUTHORITY

PURPOSE='issue-repair-continuation-v1'


class IssuePreviousReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.registry=UIRegistry(self.root/'state',{'provider':{'request_budget':12}})
        self.addCleanup(self.registry.close)
        hub=SimpleNamespace(monitor=SimpleNamespace(registries={'original':self.registry}),sessions={})
        self.service=WorkbenchCorrelation(hub);self.w=SimpleNamespace(registry=self.registry)
        self.origin={'kind':'issue','registry_path':str(self.registry.path),'investigation_id':'a'*64}
        self.basis={'issue_token':'b'*64,'config_sha256':'c'*64}
        comparison={'repository_id':42,'repository':'owner/repo','branch':'main','head_sha':'a'*40,
            'checks_sha256':'b'*64,'environment_sha256':'c'*64,'fault_sha256':'d'*64,'authority_sha256':'e'*64}
        authority={'schema_version':1,'origin':self.origin,'basis':self.basis,'comparison':comparison}
        oid=fingerprint(self.origin)
        self.context={'origin':self.origin,'basis':self.basis,'max_attempts':3,
            'issue_runs':self.registry.root.parent,'repository':SimpleNamespace(root=self.root/'repo')}
        self.coordinator=self.service._coordinator(self.registry)
        observation=self.coordinator.observe_source(self.registry,{**comparison,'source_type':'issue',
            'source_id':oid,'source_version_sha256':fingerprint(authority)})['observation_id']
        core={'origins':{oid:{'origin':self.origin,'authority':authority}}}
        self.pair={'id':fingerprint(core),'core':core,'observations':{oid:observation}}
        # The fixture supplies an already-confirmed exact product route. The
        # durable owner/authority/reference checks remain the real implementation.
        self.service._route=lambda registry,origin:(self.pair,self.registry)
        self.service._issue=lambda w,identity:self.context
        self.verify_calls=0
        self.parent=self.row('1'*32,'2'*32,None)
        self.parent.update(status='interrupted',task_id='3'*32)
        self.registry.put('issue_execution',self.parent['id'],self.parent)
        root=self.service.claim_issue(self.w,self.origin['investigation_id'],self.parent,self.verify,3)
        self.assertTrue(root['dispatch_allowed'],root)
        self.parent['correlation']=root
        self.registry.put('issue_execution',self.parent['id'],self.parent)
        self.registry.put('pi_task','3'*32,{'task_id':'3'*32,'revision':1})
        self.registry.put('pi_task_link','run:'+self.parent['run_id'],{'task_id':'3'*32})
        self.report(self.parent)
        self.child=self.row('4'*32,'5'*32,self.parent['id']);self.save_child()
        self.verify_calls=0
        self.budget=self.registry.budget();self.native=self.registry.load('pi_task')
        self.claims=copy.deepcopy(self.registry.load(CLAIMS))

    def row(self,identity,run,previous):
        return {'id':identity,'revision':0,'status':'claimed','investigation_id':self.origin['investigation_id'],
            'issue_key':'k'*64,'issue_number':7,'issue_token':self.basis['issue_token'],'repository_id':42,
            'rules_sha256':'r'*64,'spec_sha256':'s'*64,'policy_binding':{'binding_sha256':'p'*64},
            'environment_grant_sha256':'g'*64,'source_before':{'head':'a'*40},'run_id':run,
            'owner_process':{'pid':1,'created':1},'pause_requested':False,'created_at':'fixture',
            'previous_execution':previous}

    def report(self,row):
        directory=self.context['issue_runs']/row['run_id'];directory.mkdir(parents=True,exist_ok=True)
        value={'run_id':row['run_id'],'source_repo':str(self.context['repository'].root),'status':'rolled_back',
            'finished_at':'fixture','source_unchanged':True,'issue_execution':{
                'execution_id':row['id'],'issue_number':row['issue_number'],'issue_token':row['issue_token'],
                'policy_binding_sha256':row['policy_binding']['binding_sha256'],
                'task_id':row.get('task_id'),'rules_sha256':row['rules_sha256']}}
        (directory/'run.json').write_text(json.dumps(value),encoding='utf-8')

    def save_child(self):
        self.registry.put('issue_execution',self.child['id'],self.child)
        self.registry.put('issue_execution_head',self.child['issue_key'],{'execution_id':self.child['id']})

    def verify(self):self.verify_calls+=1

    def acquire(self):
        return self.service.claim_issue(self.w,self.origin['investigation_id'],self.child,self.verify,3)

    def running(self,receipt):
        self.child.update(correlation=receipt,status='running',task_id=self.parent.get('task_id') or '6'*32,revision=1)
        self.save_child()
        self.registry.put('pi_task_link','run:'+self.child['run_id'],{'task_id':self.child['task_id']})
        self.service.verify(self.registry,receipt,lambda:self.context)

    def unchanged(self):
        self.assertEqual(self.registry.budget(),self.budget)
        self.assertEqual(self.registry.load('pi_task'),self.native)
        self.assertEqual(self.registry.load(CLAIMS),self.claims)

    def test_legacy_root_gives_typed_exact_receipt_and_rechecks_original_controller(self):
        receipt=self.acquire()
        self.assertEqual(receipt.get('purpose'),PURPOSE)
        self.assertEqual(receipt['previous_execution'],self.parent['id'])
        self.assertGreater(self.verify_calls,0)
        self.running(receipt);self.unchanged()

    def test_reuse_calls_original_verify_even_when_claim_already_exists(self):
        def stopped():raise SafetyError('original controller paused')
        with self.assertRaisesRegex(SafetyError,'paused'):
            self.service.claim_issue(self.w,self.origin['investigation_id'],self.child,stopped,3)
        self.assertNotIn(self.child['id'],self.registry.load(RECEIPTS));self.unchanged()

    def test_readonly_purpose_and_wrong_canonical_execution_are_rejected(self):
        original=copy.deepcopy(self.parent['correlation'])
        for changes in ({'purpose':'validation-proposal-v1'},{'purpose':'repair-continuation-v1'},
                        {'execution_id':'9'*32}):
            with self.subTest(changes=changes):
                self.registry.put(RECEIPTS,self.parent['id'],{**original,**changes})
                with self.assertRaises(SafetyError):self.acquire()
        self.unchanged()

    def test_missing_predecessor_receipt_never_falls_back_to_a_new_claim(self):
        with self.registry.transaction() as db:
            db.execute('DELETE FROM objects WHERE kind=? AND id=?',(RECEIPTS,self.parent['id']))
        with self.assertRaises(SafetyError):self.acquire()
        self.unchanged()

    def test_wrong_parent_origin_and_nonowner_are_rejected(self):
        parent=copy.deepcopy(self.parent);parent['investigation_id']='z'*64
        self.registry.put('issue_execution',parent['id'],parent)
        with self.assertRaises(SafetyError):self.acquire()
        self.registry.put('issue_execution',self.parent['id'],self.parent)
        receipt={**self.parent['correlation'],'dispatch_allowed':False}
        self.registry.put(RECEIPTS,self.parent['id'],receipt)
        with self.assertRaises(SafetyError):self.acquire()
        self.unchanged()

    def test_unknown_original_owner_blocks_even_if_budget_has_no_held_requests(self):
        receipt=self.parent['correlation'];claim=self.registry.load(CLAIMS)[receipt['claim_id']]
        coordinator=self.service._coordinator(self.registry)
        coordinator.mark_outcome(receipt['claim_id'],receipt['owner_reference'],'unknown',revision=claim['revision'])
        self.claims=copy.deepcopy(self.registry.load(CLAIMS))
        with self.assertRaises(SafetyError):self.acquire()
        self.unchanged()

    def test_model_guard_rejects_changed_persistent_receipt_and_new_row(self):
        receipt=self.acquire();self.running(receipt)
        self.registry.put(RECEIPTS,self.child['id'],{**receipt,'previous_execution':'9'*32})
        with self.assertRaises(SafetyError):self.service.verify(self.registry,receipt,lambda:self.context)
        self.registry.put(RECEIPTS,self.child['id'],receipt)
        self.child['policy_binding']={'binding_sha256':'changed'};self.save_child()
        with self.assertRaises(SafetyError):self.service.verify(self.registry,receipt,lambda:self.context)
        self.unchanged()

    def test_model_guard_rejects_changed_parent_report_or_native_link(self):
        receipt=self.acquire();self.running(receipt)
        path=self.context['issue_runs']/self.parent['run_id']/'run.json'
        original=path.read_bytes();path.write_bytes(original+b' ')
        with self.assertRaises(SafetyError):self.service.verify(self.registry,receipt,lambda:self.context)
        path.write_bytes(original)
        self.registry.put('pi_task_link','run:'+self.child['run_id'],{'task_id':'9'*32})
        with self.assertRaises(SafetyError):self.service.verify(self.registry,receipt,lambda:self.context)
        self.unchanged()

    def test_legacy_copied_same_origin_chain_remains_compatible(self):
        root=copy.deepcopy(self.parent)
        legacy=copy.deepcopy(self.child);legacy.update(status='paused',task_id=root['task_id'])
        legacy['correlation']={**root['correlation'],'execution_id':legacy['id'],'previous_execution':root['id']}
        self.registry.put('issue_execution',legacy['id'],legacy)
        self.registry.put(RECEIPTS,legacy['id'],legacy['correlation'])
        self.registry.put('pi_task_link','run:'+legacy['run_id'],{'task_id':legacy['task_id']});self.report(legacy)
        self.parent=legacy;self.child=self.row('7'*32,'8'*32,legacy['id']);self.save_child()
        receipt=self.acquire();self.assertEqual(receipt.get('purpose'),PURPOSE)
        self.running(receipt);self.unchanged()

    def test_pause_before_first_native_keeps_explicit_absence_compatible(self):
        path=self.context['issue_runs']/self.parent['run_id']/'run.json';path.unlink()
        self.parent.pop('task_id');self.registry.put('issue_execution',self.parent['id'],self.parent)
        with self.registry.transaction() as db:
            db.execute('DELETE FROM objects WHERE kind=? AND id=?',('pi_task_link','run:'+self.parent['run_id']))
        receipt=self.acquire();self.assertEqual(receipt.get('purpose'),PURPOSE)
        self.running(receipt);self.unchanged()

    def legacy_running(self):
        # Exact pre-f03 shallow-copy expression and the actual original root
        # receipt produced by claim_issue, not a fixture-only invented schema.
        receipt={**self.parent['correlation'],'execution_id':self.child['id'],
                 'previous_execution':self.parent['id']}
        self.registry.put(RECEIPTS,self.child['id'],receipt)
        self.child.update(correlation=receipt,status='running',task_id=self.parent['task_id'],revision=1)
        self.save_child()
        self.registry.put('pi_task_link','run:'+self.child['run_id'],{'task_id':self.child['task_id']})
        return receipt

    def test_exact_legacy_running_receipt_survives_original_pending_approval_without_upgrade(self):
        receipt=self.legacy_running()
        execution=object.__new__(IssueExecution);execution.registry=self.registry
        execution.controls={self.child['id']:object()}
        workbench=object.__new__(IssueWorkbench)
        workbench.session=SimpleNamespace(closed=False,token='synthetic-approval-session')
        workbench.registry=self.registry;workbench.services={'original':execution}
        workbench.waiters={};workbench.lock=threading.RLock();workbench.watch_context=None
        plan=Plan('existing repair','a'*40,'existing plan','low',(),(),'fixture')
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(workbench._approval,{'id':self.origin['investigation_id']},plan)
            pending=None
            try:
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    pending=next(iter(self.registry.load('issue_approval').values()),None)
                    if pending:break
                    time.sleep(.01)
                self.assertIsNotNone(pending)
                self.assertEqual(pending['status'],'pending')
                # Original _approval persists pending separately; execution
                # remains running, not a new invented awaiting_approval row.
                self.assertEqual(execution.view(self.child['id'])['status'],'running')
                self.service.verify(self.registry,receipt,lambda:self.context)
                self.assertEqual(self.registry.load(RECEIPTS)[self.child['id']],receipt)
                self.assertNotIn('purpose',receipt);self.unchanged()
            finally:
                workbench.session.closed=True
                if pending:
                    self.registry.put('issue_approval',pending['id'],{**pending,'status':'rejected'})
                    workbench.waiters[pending['id']].set()
                future.result(timeout=3)
        self.service.verify(self.registry,receipt,lambda:self.context)
        # A persisted old approval without its original live waiter still has
        # no authority, even though the exact legacy repair receipt is readable.
        self.registry.put('issue_approval',pending['id'],pending)
        with self.assertRaisesRegex(SafetyError,'无活跃执行者'):
            workbench.immediate('issue_decide',{'approval_id':pending['id'],
                'revision':workbench.challenge(pending),'decision':'approve'})
        self.assertEqual(self.registry.load('issue_approval')[pending['id']],pending)
        self.unchanged()

    def test_legacy_live_receipt_still_refuses_pause_readonly_and_changed_native(self):
        receipt=self.legacy_running()
        self.child['pause_requested']=True;self.save_child()
        with self.assertRaises(SafetyError):self.service.verify(self.registry,receipt,lambda:self.context)
        self.child['pause_requested']=False;self.save_child()
        readonly={**receipt,'purpose':'validation-proposal-v1'}
        self.registry.put(RECEIPTS,self.child['id'],readonly)
        with self.assertRaises(SafetyError):self.service.verify(self.registry,readonly,lambda:self.context)
        self.registry.put(RECEIPTS,self.child['id'],receipt)
        self.registry.put('pi_task_link','run:'+self.child['run_id'],{'task_id':'9'*32})
        with self.assertRaises(SafetyError):self.service.verify(self.registry,receipt,lambda:self.context)
        self.unchanged()

    def test_stripping_typed_purpose_does_not_make_a_legacy_receipt(self):
        receipt=self.acquire();self.running(receipt)
        stripped={k:v for k,v in receipt.items() if k!='purpose'}
        self.registry.put(RECEIPTS,self.child['id'],stripped)
        self.child['correlation']=stripped;self.save_child()
        with self.assertRaises(SafetyError):self.service.verify(self.registry,stripped,lambda:self.context)
        self.unchanged()

    def test_legacy_live_receipt_does_not_replay_an_unknown_original_owner(self):
        receipt=self.legacy_running();claim=self.registry.load(CLAIMS)[receipt['claim_id']]
        self.coordinator.mark_outcome(receipt['claim_id'],receipt['owner_reference'],'unknown',revision=claim['revision'])
        self.claims=copy.deepcopy(self.registry.load(CLAIMS))
        with self.assertRaises(SafetyError):self.service.verify(self.registry,receipt,lambda:self.context)
        self.unchanged()

    def test_legacy_stopped_execution_becomes_fresh_typed_child_without_rewriting_legacy(self):
        old=self.legacy_running();legacy=copy.deepcopy(self.child)
        legacy.update(status='paused',revision=2)
        self.registry.put('issue_execution',legacy['id'],legacy);self.report(legacy)
        self.parent=legacy;self.child=self.row('7'*32,'8'*32,legacy['id']);self.save_child()
        receipt=self.acquire();self.assertEqual(receipt['purpose'],PURPOSE)
        self.assertEqual(self.registry.load(RECEIPTS)[legacy['id']],old)
        self.running(receipt);self.unchanged()

    def test_adapter_walk_is_bounded_by_existing_rows_not_a_second_hardcoded_capacity(self):
        # Direct historical registry contract: this does not bypass/change the
        # original IssueExecution.start journal capacity check in production.
        parent=self.parent
        for index in range(101):
            row=self.row(f'{1000+index:032x}',f'{2000+index:032x}',parent['id'])
            row.update(status='paused',task_id=self.parent['task_id'])
            row['correlation']={**parent['correlation'],'execution_id':row['id'],'previous_execution':parent['id']}
            self.registry.put('issue_execution',row['id'],row)
            self.registry.put(RECEIPTS,row['id'],row['correlation'])
            parent=row
        self.parent=parent;self.report(parent)
        self.registry.put('pi_task_link','run:'+parent['run_id'],{'task_id':parent['task_id']})
        self.child=self.row('7'*32,'8'*32,parent['id']);self.save_child()
        receipt=self.acquire();self.running(receipt);self.unchanged()

    def test_legacy_cycle_is_rejected_without_new_claim_or_budget(self):
        receipt=self.legacy_running()
        self.child.update(status='paused',previous_execution=self.child['id'])
        receipt={**receipt,'previous_execution':self.child['id']}
        self.child['correlation']=receipt
        self.registry.put('issue_execution',self.child['id'],self.child)
        self.registry.put(RECEIPTS,self.child['id'],receipt);self.report(self.child)
        self.parent=self.child;self.child=self.row('7'*32,'8'*32,self.parent['id']);self.save_child()
        with self.assertRaises(SafetyError):self.acquire()
        self.unchanged()


if __name__=='__main__':unittest.main()
