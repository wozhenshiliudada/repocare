"""One original Issue/Pi continuation; loopback synthetic SSE, no external writes."""
import copy
import json
from pathlib import Path
import time
import unittest

from repo_maintainer.maintenance_correlation import CLAIMS
from repo_maintainer.models import SafetyError
from repo_maintainer.workbench_correlation import RECEIPTS
import test_workbench_correlation as original


class IssuePreviousControllerTests(unittest.TestCase):
    setUp=original.WorkbenchCorrelationTests.setUp
    start_hub=original.WorkbenchCorrelationTests.start_hub
    finish=original.WorkbenchCorrelationTests.finish
    wait=original.WorkbenchCorrelationTests.wait
    job=original.WorkbenchCorrelationTests.job
    execute=original.WorkbenchCorrelationTests.execute
    connect=original.WorkbenchCorrelationTests.connect
    prepare=original.WorkbenchCorrelationTests.prepare
    selector=original.WorkbenchCorrelationTests.selector
    preview=original.WorkbenchCorrelationTests.preview
    confirm=original.WorkbenchCorrelationTests.confirm
    native=original.WorkbenchCorrelationTests.native
    candidates=original.WorkbenchCorrelationTests.candidates

    def worker(self,operation):
        return self.hub.executor.submit(operation).result(timeout=180)

    def test_original_issue_review_retry_preserves_owner_native_and_rejects_receipt_changes(self):
        self.connect(mode='approval')
        left,right=self.prepare(7),self.prepare(8)
        self.confirm(self.preview(left,right))
        service=self.session.issue_workbench._service(left)
        self.assertTrue(callable(service.dispatch_fence));self.assertTrue(callable(service.dispatch_verify))
        begin=time.monotonic()
        parent=self.worker(lambda:service.start(left['id'],left['environment'],left['grant'],
            approval=lambda _:'reject',max_attempts=3))
        root_seconds=time.monotonic()-begin
        self.assertEqual(parent['status'],'failed_no_replay',parent)
        self.assertEqual(len(self.model.requests),2)
        budget=self.registry.budget();self.assertEqual(budget['requests_used'],2)
        claims=copy.deepcopy(self.registry.load(CLAIMS))
        report=service.runs/parent['run_id']/'run.json';old_report=report.read_bytes()
        task=parent['task_id'];revision=self.registry.load('pi_task')[task]['revision']
        reason='Keep the original regression scope and propose a fresh reviewed addition fix.'
        preview=self.worker(lambda:service.review_revision_preview(parent['id'],reason))
        begin=time.monotonic()
        child=self.worker(lambda:service.revise_review(parent['id'],preview['revision'],reason,
            left['environment'],left['grant'],approval=lambda _:'approve'))
        child_seconds=time.monotonic()-begin
        self.assertEqual(child['status'],'awaiting-review',child)
        self.assertEqual(child['previous_execution'],parent['id'])
        self.assertEqual(child['task_id'],task)
        self.assertEqual(self.registry.load('pi_task')[task]['revision'],revision+1)
        self.assertEqual(self.registry.load(CLAIMS),claims)
        self.assertEqual(report.read_bytes(),old_report)
        self.assertEqual(len(self.model.requests),4)
        self.assertEqual(self.registry.budget()['requests_used'],4)
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],0)
        receipt=self.registry.load(RECEIPTS)[child['id']]
        self.assertEqual(receipt['purpose'],'issue-repair-continuation-v1')
        self.assertEqual(child['correlation'],receipt)
        before=(self.native(),self.registry.budget(),len(self.model.requests))
        # These checks call the actual same controller's original dispatch hook.
        # A completed child cannot be admitted or given an altered purpose again.
        for changed in ({**receipt,'purpose':'validation-proposal-v1'},
                        {**receipt,'previous_execution':'9'*32}):
            self.worker(lambda changed=changed:self.registry.put(RECEIPTS,child['id'],changed))
            with self.assertRaises(SafetyError):self.worker(lambda:service.dispatch_verify(receipt))
        self.worker(lambda:self.registry.put(RECEIPTS,child['id'],receipt))
        with self.assertRaises(SafetyError):
            self.worker(lambda:service.revise_review(parent['id'],preview['revision'],reason,
                left['environment'],left['grant'],approval=lambda _:'approve'))
        self.assertEqual((self.native(),self.registry.budget(),len(self.model.requests)),before)
        evidence={'scope':'Original Issue controller and genuine Pi with loopback synthetic SSE; not HTTP/browser E2E',
            'root_seconds':root_seconds,'child_seconds':child_seconds,'requests':len(self.model.requests),
            'budget':self.registry.budget(),'parent_execution':parent['id'],'child_execution':child['id'],
            'parent_run':parent['run_id'],'child_run':child['run_id'],'native_task':task,
            'native_revision_before':revision,'native_revision_after':self.registry.load('pi_task')[task]['revision'],
            'owner_claim_unchanged':self.registry.load(CLAIMS)==claims,'parent_report_unchanged':report.read_bytes()==old_report,
            'purpose':receipt['purpose'],'child_status':child['status']}
        (Path(__file__).resolve().parents[1]/'.demo'/'r3-issue-previous-controller-evidence.json').write_text(
            json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':unittest.main()
