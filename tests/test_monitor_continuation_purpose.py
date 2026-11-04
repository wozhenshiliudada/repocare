"""Small fail-closed checks for the typed continuation receipt projection."""
import copy
from types import SimpleNamespace
import unittest

from repo_maintainer.models import SafetyError
from repo_maintainer.workbench_correlation import WorkbenchCorrelation, RECEIPTS


class ContinuationPurposeTests(unittest.TestCase):
    def setUp(self):
        self.event={'id':'event'}
        self.root={'execution_id':'monitor:event','dispatch_allowed':True}
        self.row={'id':'continuation','event_id':'event','kind':'continuation','status':'started',
            'parent_run_id':'run','parent_record_sha256':'a'*64,'inspection_sha256':'b'*64,
            'original_intent_sha256':'c'*64,'binding':{'scope':'original'},'task':{'task_id':'native'}}
        self.data={'monitor_continuation_head':{'event':{'id':'continuation'}},
            'monitor_continuation':{'continuation':self.row},RECEIPTS:{}}
        registry=SimpleNamespace(load=lambda kind:self.data.get(kind,{}))
        self.service=WorkbenchCorrelation(SimpleNamespace(monitor=SimpleNamespace(registries={'monitor':registry})))
        self.receipt=self.service._continuation_receipt(self.root,self.row)
        self.row['correlation']=copy.deepcopy(self.receipt)
        self.data[RECEIPTS]['continuation']=copy.deepcopy(self.receipt)

    def verify(self):
        self.service._verify_monitor_continuation('monitor',self.event,self.root)

    def test_exact_receipt_remains_readonly_and_bound_to_original_task(self):
        before=copy.deepcopy(self.data);self.verify();self.assertEqual(self.data,before)

    def test_changed_or_stripped_purpose_never_becomes_a_repair_dispatch(self):
        for field,value in [('purpose','validation-proposal-v1'),('purpose',None),('parent_run_id','other')]:
            with self.subTest(field=field,value=value):
                self.row['correlation']=copy.deepcopy(self.receipt)
                if value is None:self.row['correlation'].pop(field)
                else:self.row['correlation'][field]=value
                with self.assertRaises(SafetyError):self.verify()

    def test_relabeling_original_intent_cannot_bypass_purpose_verification(self):
        for kind in ('validation-proposal-v1','readonly','unknown'):
            with self.subTest(kind=kind):
                self.row['kind']=kind
                with self.assertRaises(SafetyError):self.verify()

    def test_a_reconciled_result_is_readonly_and_cannot_be_started(self):
        self.row['kind']='reconciled_result'
        with self.assertRaises(SafetyError):self.verify()
        self.row['status']='candidate_ready'
        with self.assertRaises(SafetyError):self.verify()  # Relabeling still carries its dispatch receipt.
        self.row={k:self.row[k] for k in ('id','kind','event_id','binding','status','task','original_intent_sha256')}
        self.row.update(plan_id=None,run_id='run',created_at='2026-10-29')
        self.data['monitor_continuation']['continuation']=self.row
        self.data[RECEIPTS].clear();self.verify()

    def test_changed_binding_or_missing_durable_receipt_stays_blocked(self):
        self.row['binding']={'scope':'changed'}
        with self.assertRaises(SafetyError):self.verify()
        self.row['binding']={'scope':'original'}
        self.data[RECEIPTS].clear()
        with self.assertRaises(SafetyError):self.verify()


if __name__=='__main__':unittest.main()
