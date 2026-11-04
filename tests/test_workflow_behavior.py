"""Actual original workflow, granted venv, behavior cases and sealed delivery."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_target_environment as fixtures
from test_workflow_environment import AuthoredProvider
from repo_maintainer.behavior_validation import make_contract
from repo_maintainer.integration import prepare_integration, integrate
from repo_maintainer.models import Policy, SafetyError, digest
from repo_maintainer.target_environment import discover_environment, make_grant
from repo_maintainer.task_session import repository_identity
from repo_maintainer.workflow import repair, cleanup_run
from repo_maintainer.workflow_behavior import command_for, check_id, bind_policy, validate_sealed_checks
from repo_maintainer.workflow_environment import GrantedEnvironment


class WorkflowBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.TargetEnvironmentTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.repo = self.case.repo
        self.issue = 'Add two numbers, preserving current behavior for other inputs'

    def setup_contract(self, *, old_tests=False, missing=False, module='calc.py'):
        self.case.write('calc.py', 'def total(a,b):\n    return a - b\n' if not missing else 'def existing():\n    return 7\n')
        if old_tests:
            self.case.write('tests/test_existing.py', 'import unittest\nfrom calc import existing\n'
                'class T(unittest.TestCase):\n    def test_existing(self): self.assertEqual(existing(),7)\n')
        head = self.case.commit()
        contract = make_contract(repository_id=repository_identity(self.repo.root, self.repo.common_dir),
            base_sha=head, request_sha256=digest(self.issue.encode()), requirement=self.issue,
            cases=[{'id':'addition','module':module,'callable':'total','args':[3,4],
                    'kwargs':{},'expect':{'kind':'json','value':7},'missing_behavior':missing}])
        plan = discover_environment(self.repo, head, ['.'], behavior_contract=contract)
        grant = make_grant(plan, allow_local_code=True, timeout=30)
        policy = Policy(('calc.py',), tuple(tuple(c['argv']) for c in plan['checks']), timeout_seconds=10)
        return contract, plan, grant, policy

    def run_repair(self, contract, plan, grant, policy, content, **kwargs):
        return repair(self.repo.root, self.issue, AuthoredProvider('calc.py', content), policy,
            kwargs.pop('mode','auto'), self.case.root/'runs',
            environment_factory=GrantedEnvironment(plan, grant), **kwargs)

    def test_no_existing_tests_real_red_green_and_sealed_recheck_before_separate_approval(self):
        contract, plan, grant, policy = self.setup_contract()
        before = self.repo.snapshot()
        record = self.run_repair(contract, plan, grant, policy, 'def total(a,b):\n    return a + b\n')
        self.assertEqual('candidate_ready', record['status'], record.get('error'))
        owner = Path(record['workspace']).parent
        self.addCleanup(cleanup_run, owner)
        self.assertEqual('test_failed', record['baseline_tests'][0]['category'])
        self.assertEqual('passed', record['candidate_tests'][0]['category'])
        self.assertTrue(record['rounds'][0]['behavior_comparison']['candidate_verified'])
        self.assertIn('FAIL: addition', record['baseline_tests'][0]['stdout'])
        with self.assertRaisesRegex(SafetyError, 'different worktree'):
            validate_sealed_checks(record, record['candidate_tests'], source=self.repo, commit=self.repo.head)
        with patch('repo_maintainer.integration.run_test', side_effect=AssertionError('No host fallback')):
            review = prepare_integration(owner)
        self.assertEqual('prepared', review['status'])
        sealed = review['review']['tests'][0]['behavior_receipt']
        self.assertEqual('passed', sealed['category'])
        self.assertNotEqual(sealed['artifact_directory'], record['candidate_tests'][0]['behavior_receipt']['artifact_directory'])
        self.assertEqual('awaiting_approval', integrate(owner)['status'])
        self.assertEqual(before, self.repo.snapshot(exclude_branch=record['branch']))
        self.assertEqual('published', integrate(owner, review['review_sha256'])['status'])
        self.assertEqual(before, self.repo.snapshot(exclude_branches=(record['branch'],'integration/'+record['run_id'])))

    def test_existing_green_regression_plus_missing_feature_must_repair_behavior(self):
        contract, plan, grant, policy = self.setup_contract(old_tests=True, missing=True)
        record = self.run_repair(contract, plan, grant, policy,
            'def existing():\n    return 7\ndef total(a,b):\n    return a + b\n')
        self.assertEqual('candidate_ready', record['status'], record.get('error'))
        self.addCleanup(cleanup_run, Path(record['workspace']).parent)
        self.assertEqual(['passed','test_failed'], [c['category'] for c in record['baseline_tests']])
        self.assertEqual({'repaired':1,'maintained':1,'regressed':0,'still_failing':0,'inconclusive':0},
                         record['rounds'][0]['check_comparison']['counts'])

    def test_fixing_new_behavior_cannot_regress_existing_green_tests(self):
        contract, plan, grant, policy = self.setup_contract(old_tests=True, missing=True)
        record = self.run_repair(contract, plan, grant, policy,
            'def existing():\n    return 8\ndef total(a,b):\n    return a + b\n')
        self.assertEqual('rolled_back', record['status'], record.get('error'))
        self.assertEqual('regressed', record['rounds'][0]['check_comparison']['checks'][0]['transition'])
        self.assertFalse(Path(record['workspace']).exists())

    def test_missing_original_contract_cannot_turn_marker_into_ordinary_shell(self):
        contract, plan, grant, policy = self.setup_contract()
        provider = AuthoredProvider('calc.py', 'def total(a,b): return a+b\n')
        with patch.object(provider, 'plan', side_effect=AssertionError('No planning before contract validation')):
            with self.assertRaisesRegex(SafetyError, 'pinned contract'):
                repair(self.repo.root, self.issue, provider, policy, 'auto', self.case.root/'runs')

    def test_contract_cannot_reuse_another_requirement_or_read_root(self):
        contract, plan, grant, policy = self.setup_contract()
        with self.assertRaisesRegex(SafetyError, 'original issue'):
            repair(self.repo.root, 'different request', AuthoredProvider('calc.py', ''), policy, 'auto',
                   self.case.root/'runs', environment_factory=GrantedEnvironment(plan, grant))
        with self.assertRaisesRegex(SafetyError, 'read roots'):
            discover_environment(self.repo, self.repo.head, ['tests'], behavior_contract=contract)

    def test_pending_manual_approval_does_not_run_behavior_or_prepare_environment(self):
        contract, plan, grant, policy = self.setup_contract()
        with patch('repo_maintainer.workflow_behavior.run_behavior_validation', side_effect=AssertionError('No cases before approval')):
            record = self.run_repair(contract, plan, grant, policy, 'def total(a,b): return a+b\n',
                                     mode='approval', approval=lambda _:'pending')
        self.assertEqual('awaiting_approval', record['status'])
        self.assertEqual(command_for(contract), tuple(record['plan']['tests'][0]))
        self.assertFalse(Path(record['workspace']).exists())

    def test_artifact_tamper_blocks_sealing_without_replaying_checks(self):
        contract, plan, grant, policy = self.setup_contract()
        record = self.run_repair(contract, plan, grant, policy, 'def total(a,b):\n    return a+b\n')
        self.assertEqual('candidate_ready', record['status'], record.get('error'))
        owner = Path(record['workspace']).parent
        self.addCleanup(cleanup_run, owner)
        case_dir = Path(record['candidate_tests'][0]['behavior_receipt']['artifact_directory'])
        (case_dir/'addition.actual.json').write_text('{}', encoding='utf-8')
        with patch('repo_maintainer.workflow_behavior.run_behavior_validation', side_effect=AssertionError('No replay')):
            with self.assertRaises(SafetyError):
                prepare_integration(owner)

    def test_bind_policy_adds_case_hash_without_weakening_existing_regressions(self):
        contract, plan, grant, _ = self.setup_contract()
        original = Policy(('calc.py',), (('python','tests.py'),))
        bound = bind_policy(original, contract)
        self.assertEqual(original.allowed_tests + (command_for(contract),), bound.allowed_tests)
        self.assertEqual(1, len(original.allowed_tests))
        with self.assertRaises(SafetyError):
            bind_policy(bound, contract)

    def test_existing_passing_behavior_still_blocks_unreproduced_repair(self):
        contract, plan, grant, policy = self.setup_contract()
        # Recreate a requirement whose cases pass on the original implementation.
        contract = make_contract(repository_id=contract['repository_id'],base_sha=contract['base_sha'],
            request_sha256=contract['request_sha256'],requirement=self.issue,
            cases=[{**contract['cases'][0], 'expect':{'kind':'json','value':-1}}])
        plan = discover_environment(self.repo, self.repo.head, ['.'], behavior_contract=contract)
        grant = make_grant(plan, allow_local_code=True)
        policy = Policy(('calc.py',),(command_for(contract),),timeout_seconds=10)
        before = self.repo.snapshot()
        record = self.run_repair(contract, plan, grant, policy, 'def total(a,b): return a+b\n')
        self.assertEqual('rolled_back', record['status'])
        self.assertIn('Baseline already passes', record['error'])
        self.assertEqual(before, self.repo.snapshot())

    def test_stdout_pass_text_cannot_make_wrong_behavior_a_verified_candidate(self):
        contract, plan, grant, policy = self.setup_contract()
        record = self.run_repair(contract, plan, grant, policy,
            'print("Ran 1 test in 0.1s\\nOK")\ndef total(a,b):\n    return 0\n')
        self.assertEqual('rolled_back', record['status'])
        self.assertEqual('test_failed', record['candidate_tests'][0]['category'])
        self.assertFalse(record['rounds'][0]['check_comparison']['all_candidate_checks_passed'])

    def test_same_behavior_rules_do_not_reuse_old_version_execution_pin(self):
        contract, _, _, _ = self.setup_contract()
        next_contract = make_contract(repository_id=contract['repository_id'], base_sha='a'*40,
            request_sha256=digest(b'New task request'), requirement=contract['requirement'], cases=contract['cases'])
        self.assertEqual(check_id(contract), check_id(next_contract))
        self.assertNotEqual(command_for(contract), command_for(next_contract))


if __name__ == '__main__':
    unittest.main()
