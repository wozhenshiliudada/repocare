"""Offline preparation contracts, not production authorization/repair acceptance."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from opc_w0_support import (audit_contract_examples, audit_selection, check_outcome,
                            event_key, patch_statistics, public_prompt, select_live)


class W0PreparationTests(unittest.TestCase):
    def setUp(self):
        self.examples = json.loads((ROOT / 'benchmarks/opc_w0/protocol_examples.json').read_text(encoding='utf-8'))

    def test_live_prompt_does_not_leak_judge_or_hint_fields(self):
        row = {'problem_statement': 'official unchanged issue', 'patch': 'gold',
               'test_patch': 'hidden', 'FAIL_TO_PASS': ['secret_test'],
               'hints_text': 'solution', 'root_paths': ['secret.py']}
        self.assertEqual(public_prompt(row), {'problem_statement': row['problem_statement']})

    def test_pro_prompt_uses_instruction_only_and_preserves_bytes_as_text(self):
        text = 'official\n instructions  \n'
        self.assertEqual(public_prompt({'instruction': text, 'solution': 'secret'}, 'pro'), {'instruction': text})
        with self.assertRaises(ValueError): public_prompt({'problem_statement': ''})
        with self.assertRaises(ValueError): public_prompt({'instruction': text}, 'unknown')

    def test_patch_stratification_never_exports_root_paths(self):
        value = patch_statistics('diff --git a/src/a.py b/src/a.py\n-x\n+y\n'
                                 'diff --git a/tests/test_a.py b/tests/test_a.py\n-z\n+w\n')
        self.assertEqual(value, {'implementation_files': 1, 'changed_lines': 4})

    def test_selection_is_order_independent_and_repository_distinct(self):
        patch = 'diff --git a/src/a.py b/src/a.py\ndiff --git a/src/b.py b/src/b.py\n' + '+x\n' * 20
        rows = [{'instance_id': n, 'repo': repo, 'patch': patch, 'problem_statement': 'x'*120,
                 'FAIL_TO_PASS': ['x'], 'PASS_TO_PASS': ['y']} for n, repo in
                [('one','a/r'),('two','a/r'),('three','b/r'),('four','c/r')]]
        selected = select_live(rows, 2)
        self.assertEqual(selected, select_live(list(reversed(rows)), 2))
        self.assertEqual(len({r['repo'] for r in selected}), 2)
        self.assertTrue(all(r['repo'] != 'a/r' for r in select_live(rows, 2, ['a/r'])))
        with self.assertRaises(ValueError): select_live(rows, 4)

    def test_same_event_poll_does_not_change_key(self):
        event = self.examples['event']
        self.assertEqual(event_key(event), event_key({**event, 'received_at':'later', 'ui_status':'new'}))

    def test_new_attempt_issue_revision_or_commit_gets_distinct_key(self):
        event = self.examples['event']
        for field, value in [('source_version','attempt-2'), ('target_sha','a'*40),
                             ('repository_id','another'), ('kind','issue')]:
            with self.subTest(field=field):
                self.assertNotEqual(event_key(event), event_key({**event, field:value}))
        with self.assertRaises(ValueError): event_key({**event, 'target_sha':'main'})

    def test_draft_contract_cannot_claim_approval_or_new_budget(self):
        self.assertFalse(audit_contract_examples(self.examples)['runtime_authorized'])
        for field, value in [('approval_receipt','old-approval'), ('budget_ref','fresh-ledger'),
                             ('policy_version',2), ('editable_paths',[])]:
            changed = copy.deepcopy(self.examples)
            changed['execution_scope'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): audit_contract_examples(changed)

    def test_diagnosis_is_not_authority_and_remains_bound_to_baseline(self):
        for field, value in [('authorization_effect','grant'), ('target_sha','a'*40)]:
            changed = copy.deepcopy(self.examples)
            changed['diagnosis'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): audit_contract_examples(changed)

    def test_empty_or_unknown_test_run_never_becomes_green(self):
        self.assertEqual(check_outcome(0,False,0),'inconclusive_no_tests')
        self.assertEqual(check_outcome(5,False,0),'inconclusive_no_tests')
        self.assertEqual(check_outcome(0,True,6),'inconclusive_timeout')
        self.assertEqual(check_outcome(1,False,None),'unknown_environment_or_parser')
        self.assertEqual(check_outcome(0,False,6),'passed_selected_checks')
        self.assertEqual(check_outcome(1,False,6),'failed_selected_checks')

    def test_frozen_selection_roles_and_pending_environment_are_explicit(self):
        selection = json.loads((ROOT / 'benchmarks/opc_w0/selection.json').read_text(encoding='utf-8'))
        self.assertEqual(audit_selection(selection)['model_calls'],0)
        for mutation in ['paid','duplicate','environment']:
            changed = copy.deepcopy(selection)
            if mutation == 'paid': changed['paid_authorized'] = True
            elif mutation == 'duplicate': changed['tasks'][1]['instance_id'] = changed['tasks'][0]['instance_id']
            else: changed['tasks'][0]['environment_verified'] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): audit_selection(changed)


if __name__ == '__main__':
    unittest.main()
