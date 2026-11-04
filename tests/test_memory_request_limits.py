"""Independent Python/Node memory receipts share the configured request bound."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from memory_catalog_fixture import fixture
import test_task_planning as planning
from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.local_ui import UISession
from repo_maintainer.memory_items import build_load
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.runtime_limits import MAX_REQUESTS


ROOT = Path(__file__).resolve().parents[1]
NODE_RECEIPT = """
import {buildMemoryLoad, verifiedMemoryLoad} from './runtime/pi/memory-load.mjs';
let raw = ''; for await (const part of process.stdin) raw += part;
const values = JSON.parse(raw);
const results = values.map(x => {
  const request = {issue:x.issue, base_sha:x.base, files:x.files,
    request_budget:x.allowance, controller_binding:x.frame,
    task_session:{binding:{task_id:x.task_id}}};
  try {
    const load = buildMemoryLoad(x.frame, request, x.blocks);
    return {accepted:true, load:verifiedMemoryLoad(load, request, x.blocks)};
  } catch (error) { return {accepted:false}; }
});
process.stdout.write(JSON.stringify(results));
"""


class MemoryRequestLimitTests(unittest.TestCase):
    def node_receipts(self, values):
        process = subprocess.run(
            [shutil.which('node'), '--input-type=module', '-e', NODE_RECEIPT],
            input=json.dumps(values).encode(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=ROOT, timeout=20)
        self.assertEqual(0, process.returncode, process.stderr.decode(errors='replace'))
        return json.loads(process.stdout)

    def test_legacy_and_larger_allowances_produce_identical_independent_receipts(self):
        allowances = sorted({value for value in (1, 6, 50, 100, 101, MAX_REQUESTS)
                             if value <= MAX_REQUESTS})
        values = [{**fixture(), 'allowance': allowance} for allowance in allowances]
        actual = self.node_receipts(values)
        for value, node in zip(values, actual):
            with self.subTest(allowance=value['allowance']):
                expected = build_load(**value)
                self.assertTrue(node['accepted'])
                self.assertEqual(expected, node['load'])
                self.assertEqual({'request_allowance': value['allowance'],
                                  'extra_model_requests': 0}, expected['budget'])
                self.assertFalse(expected['runtime_authorized'])

    def test_both_protocols_reject_invalid_or_over_configured_allowances(self):
        values = [{**fixture(), 'allowance': allowance} for allowance in
                  (0, -1, MAX_REQUESTS + 1, True, False, None, '500', 1.5)]
        actual = self.node_receipts(values)
        for value, node in zip(values, actual):
            with self.subTest(allowance=value['allowance']):
                with self.assertRaises(SafetyError):
                    build_load(**value)
                self.assertFalse(node['accepted'])


class MemoryBudgetNativeTests(unittest.TestCase):
    setUp = planning.TaskPlanningTests.setUp
    restart = planning.TaskPlanningTests.restart
    plan = planning.TaskPlanningTests.plan
    decision = planning.TaskPlanningTests.decision
    record = planning.TaskPlanningTests.record
    session_file = planning.TaskPlanningTests.session_file
    resume = planning.TaskPlanningTests.resume

    def open(self, auto=False, attempts=1):
        self.inner = PiProvider(('calculator.py',), self.model.url,
                                readonly_paths=('tests/test_calculator.py',),
                                max_requests=MAX_REQUESTS)
        self.session = UISession(self.source, Policy(('calculator.py',), (DEMO_TEST,)),
                                 self.inner, self.root/'state',
                                 allow_auto=auto, max_attempts=attempts)

    def test_large_budget_reopens_same_native_and_ledger_with_fresh_approval(self):
        source = (self.source/'calculator.py').read_bytes()
        first = self.plan()
        record = self.record()
        session_file = self.session_file(record)
        original_native = session_file.read_bytes()
        original_attempts = self.session.registry.load('attempt')
        first_load = self.session.plans[first['plan_id']]['view']['planning_evidence']['memory_load']
        self.assertEqual(MAX_REQUESTS, first_load['budget']['request_allowance'])
        self.assertEqual(2, self.session.registry.budget()['requests_used'])
        rejected = self.decision(first)
        budget = self.session.registry.budget()
        self.restart()
        self.assertEqual(budget, self.session.registry.budget())
        self.assertEqual(original_attempts, self.session.registry.load('attempt'))
        self.assertEqual(original_native, session_file.read_bytes())
        self.assertEqual(2, len(self.model.requests), 'Restart must not replay a model request')

        second = self.resume(rejected)
        current = self.record()
        second_load = self.session.plans[second['plan_id']]['view']['planning_evidence']['memory_load']
        self.assertEqual(MAX_REQUESTS-2, second_load['budget']['request_allowance'])
        self.assertEqual(record['task_id'], current['task_id'])
        self.assertEqual(record['checkpoint']['session_id'], current['checkpoint']['session_id'])
        self.assertEqual(session_file, self.session_file(current))
        self.assertTrue(session_file.read_bytes().startswith(original_native))
        self.assertNotEqual(first['plan_id'], second['plan_id'])
        budget = self.session.registry.budget()
        self.assertEqual((MAX_REQUESTS, 4, 0, MAX_REQUESTS-4), tuple(budget[k] for k in
            ('request_budget', 'requests_used', 'requests_reserved_unknown', 'requests_remaining')))
        for identity, receipt in original_attempts.items():
            self.assertEqual(receipt, self.session.registry.load('attempt')[identity])
        approved = self.decision(second, 'approve')
        self.assertEqual('candidate_ready', approved['workflow_status'])
        self.assertEqual(4, len(self.model.requests), 'Saved-plan approval must not call the model')
        self.assertEqual(budget, self.session.registry.budget())
        self.assertEqual(source, (self.source/'calculator.py').read_bytes())

    def test_large_budget_denied_read_receipt_survives_checkpoint_and_restart(self):
        self.model.scenario = 'denied-read'
        first = self.plan()
        record = self.record()
        file = self.session_file(record)
        native = file.read_bytes()
        budget = self.session.registry.budget()
        self.assertEqual((MAX_REQUESTS, 3, 0, MAX_REQUESTS-3), tuple(budget[k] for k in
            ('request_budget', 'requests_used', 'requests_reserved_unknown', 'requests_remaining')))
        self.assertIn(b'Path not authorized for read', native)
        self.restart()
        self.assertEqual(native, file.read_bytes())
        self.assertEqual(budget, self.session.registry.budget())
        self.assertEqual(3, len(self.model.requests))
        item = self.session.state()['plans'][0]
        self.session._operate('claim_plan', {'plan_id': item['id'], 'revision': item['revision']})
        approved = self.decision(first, 'approve')
        self.assertEqual('candidate_ready', approved['workflow_status'])
        self.assertEqual(budget, self.session.registry.budget())
        self.assertEqual(3, len(self.model.requests))
