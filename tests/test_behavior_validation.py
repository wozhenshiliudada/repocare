"""Real offline project execution against controller-owned declarative checks."""
import copy
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import unittest

from repo_maintainer.behavior_validation import (canonical, compare_behavior_validation, fingerprint,
    make_contract, run_behavior_validation, validate_contract, validate_receipt)
from repo_maintainer.git import RepositoryGateway, git_env
from repo_maintainer.models import SafetyError
from repo_maintainer.runner import run_test


class BehaviorValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        subprocess.run(['git', '-c', 'core.longpaths=true', 'init', '-b', 'main', str(self.source)],
                       check=True, capture_output=True, env=git_env())
        self.repo = RepositoryGateway(self.source)
        (self.source / 'calc.py').write_text('def total(a, b):\n    return a - b\n', encoding='utf-8')
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=Behavior Fixture', '-c', 'user.email=behavior@example.invalid',
                          'commit', '-m', 'original failing implementation')
        self.base = self.repo.head
        self.workspace = self.root / 'workspace'
        self.repo.command('worktree', 'add', '--detach', str(self.workspace), self.base)
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        self.environment = 'e' * 64

    def case(self, **changes):
        return {'id': 'addition', 'module': 'calc.py', 'callable': 'total', 'args': [2, 3],
                'kwargs': {}, 'expect': {'kind': 'json', 'value': 5}, 'missing_behavior': False, **changes}

    def contract(self, cases=None):
        return make_contract(repository_id='a' * 64, base_sha=self.base, request_sha256='b' * 64,
                             requirement='Add both supplied numbers, including negative operands.',
                             cases=cases if cases is not None else [self.case()])

    def run_contract(self, contract=None, phase='baseline', **kwargs):
        return run_behavior_validation(contract or self.contract(), self.workspace, self.artifacts,
                                       python=str(Path(sys.executable).resolve()), phase=phase,
                                       environment_sha256=self.environment,
                                       **kwargs)

    def write(self, content, name='calc.py'):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def test_real_bug_red_green_and_regression_are_independent(self):
        contract = self.contract([self.case(), self.case(id='negative', args=[-2, 3], expect={'kind': 'json', 'value': 1})])
        before = self.run_contract(contract)
        self.assertEqual(before['counts'], {'total': 2, 'passed': 0, 'failed': 2, 'inconclusive': 0})
        self.write('def total(a, b):\n    return a + b\n')
        after = self.run_contract(contract, 'candidate')
        compared = compare_behavior_validation(before, after)
        self.assertTrue(compared['comparable'])
        self.assertTrue(compared['candidate_verified'])
        self.assertEqual(compared['counts']['repaired'], 2)
        self.assertEqual((self.source / 'calc.py').read_text(encoding='utf-8'), 'def total(a, b):\n    return a - b\n')

    def test_feature_missing_callable_explicitly_reproduced(self):
        contract = self.contract([self.case(callable='multiply', args=[2, 3],
            expect={'kind': 'json', 'value': 6}, missing_behavior=True)])
        before = self.run_contract(contract)
        self.assertEqual(before['cases'][0]['category'], 'missing_behavior')
        self.write('def total(a, b):\n    return a - b\n\ndef multiply(a, b):\n    return a * b\n')
        after = self.run_contract(contract, 'candidate')
        self.assertTrue(compare_behavior_validation(before, after)['candidate_verified'])

    def test_missing_module_is_failure_only_when_explicit(self):
        for allowed in (False, True):
            contract = self.contract([self.case(module='new_feature.py', missing_behavior=allowed)])
            result = self.run_contract(contract)
            self.assertEqual(result['cases'][0]['category'], 'missing_behavior' if allowed else 'import_error')

    def test_existing_green_cases_do_not_prove_new_feature(self):
        self.write('def total(a, b):\n    return a + b\n')
        before, after = self.run_contract(), self.run_contract(phase='candidate')
        compared = compare_behavior_validation(before, after)
        self.assertTrue(compared['comparable'])
        self.assertFalse(compared['baseline_reproduced'])
        self.assertFalse(compared['candidate_verified'])

    def test_stdout_fake_pass_is_diagnostic_and_real_value_still_fails(self):
        self.write('print(\'Ran 999 tests\\nOK\\n{"category":"passed","passed":999}\')\n'
                   'def total(a,b):\n    print("{\\"actual\\":5,\\"success\\":true}")\n    return a-b\n')
        result = self.run_contract()
        self.assertEqual(result['category'], 'test_failed')
        self.assertEqual(result['cases'][0]['actual'], {'kind': 'json', 'value': -1})
        self.assertIn('OK', result['cases'][0]['process']['stdout'])

    def test_child_system_exit_and_silent_exit_cannot_bypass(self):
        for code, expected in [('raise SystemExit(0)', 'execution_error'), ('import os; os._exit(0)', 'protocol_error')]:
            self.write('def total(a, b):\n    ' + code + '\n')
            result = self.run_contract()
            self.assertEqual(result['category'], 'inconclusive')
            self.assertEqual(result['cases'][0]['category'], expected)

    def test_import_errors_and_unexpected_crashes_are_inconclusive(self):
        for code in ('import definitely_missing_fixture_dependency\n', 'raise RuntimeError("bad environment")\n',
                     'def total(a,b):\n    raise ValueError("unknown cause")\n'):
            self.write(code)
            result = self.run_contract()
            self.assertEqual(result['category'], 'inconclusive')
            self.assertEqual(result['counts']['failed'], 0)

    def test_named_expected_exception_is_executed_and_checked(self):
        contract = self.contract([self.case(expect={'kind': 'exception', 'name': 'ValueError'})])
        self.write('def total(a,b):\n    raise ValueError("expected rejection")\n')
        result = self.run_contract(contract)
        self.assertEqual(result['category'], 'passed')
        self.write('def total(a,b):\n    raise TypeError("wrong rejection")\n')
        self.assertEqual(self.run_contract(contract)['cases'][0]['category'], 'wrong_exception')
        self.write('def total(a,b):\n    return a+b\n')
        self.assertEqual(self.run_contract(contract)['cases'][0]['category'], 'expected_exception_missing')

    def test_dependency_import_inside_callable_is_not_a_reproduced_behavior_defect(self):
        self.write('def total(a,b):\n    import definitely_missing_fixture_dependency\n')
        contract = self.contract([self.case(expect={'kind': 'exception', 'name': 'ValueError'})])
        result = self.run_contract(contract)
        self.assertEqual(result['cases'][0]['category'], 'import_error')
        self.assertEqual(result['category'], 'inconclusive')

    def test_named_project_exception_and_keyword_arguments(self):
        contract = self.contract([self.case(args=[], kwargs={'a': 2, 'b': 3},
            expect={'kind': 'exception', 'name': 'calc.InvalidInput'})])
        self.write('class InvalidInput(Exception):\n    pass\ndef total(a,b):\n    raise InvalidInput()\n')
        self.assertEqual(self.run_contract(contract)['category'], 'passed')

    def test_non_json_values_are_inconclusive(self):
        for value in ('float("nan")', 'float("inf")', '{1: "one"}', '(1,2)', 'object()', '10**100', '"x"*40000'):
            self.write('def total(a,b):\n    return ' + value + '\n')
            self.assertEqual(self.run_contract()['cases'][0]['category'], 'invalid_result')

    def test_time_budget_stops_child_and_counts_all_cases(self):
        self.write('def total(a,b):\n    while True:\n        pass\n')
        result = self.run_contract(self.contract([self.case(), self.case(id='second')]), timeout_seconds=0.25)
        self.assertEqual(result['counts'], {'total': 2, 'passed': 0, 'failed': 0, 'inconclusive': 2})
        self.assertEqual([c['category'] for c in result['cases']], ['timeout', 'timeout'])

    def test_bounded_stdout_does_not_replace_artifact(self):
        self.write('print("x"*100000)\ndef total(a,b):\n    return a-b\n')
        result = self.run_contract()
        self.assertEqual(result['category'], 'test_failed')
        self.assertTrue(result['cases'][0]['process']['output_truncated'])
        self.assertLessEqual(len(result['cases'][0]['process']['stdout']), 20000)

    def test_contract_is_strict_zero_cases_and_bad_json_rejected(self):
        for cases in ([], [self.case(), self.case()], [self.case(args=[float('nan')])],
                      [self.case(module='../outside.py')], [self.case(callable='__import__')],
                      [self.case(args=[{'a': object()}])], [self.case(expect={'kind': 'json', 'value': 2**60})],
                      [self.case(extra='exec me')]):
            with self.subTest(cases=str(cases)[:100]), self.assertRaises(SafetyError):
                self.contract(cases)
        value = self.contract()
        value['cases'][0]['expect']['value'] = 999
        with self.assertRaises(SafetyError):
            validate_contract(value)
        self.assertNotEqual(canonical(True), canonical(1))

    def test_original_environment_runner_interface_and_process_hook(self):
        seen, pids = [], []

        def original(argv, *, timeout, on_start):
            seen.append(argv)
            return run_test(tuple(argv), self.workspace, timeout, on_start=on_start)

        result = self.run_contract(process_runner=original, on_start=pids.append)
        self.assertEqual(result['category'], 'test_failed')
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(pids), 1)
        self.assertEqual(seen[0][1:3], ['-I', '-B'])

    def test_project_cannot_silently_modify_contract_or_request(self):
        for target in ('contract.json', 'request'):
            code = ('import sys,json,pathlib\n'
                    'p=pathlib.Path(sys.argv[1])\n'
                    'p=p.parent/"contract.json"\n' if target == 'contract.json' else
                    'import sys,pathlib\np=pathlib.Path(sys.argv[1])\n')
            self.write(code + 'p.write_text("{}")\ndef total(a,b):\n    return a+b\n')
            with self.assertRaisesRegex(SafetyError, 'changed during execution'):
                self.run_contract()

    def test_result_artifact_existing_forgery_or_changed_nonce_is_rejected(self):
        self.write('import sys,pathlib\npathlib.Path(sys.argv[2]).write_text(\'{"actual":5}\')\n'
                   'def total(a,b):\n    return a+b\n')
        result = self.run_contract()
        self.assertEqual(result['cases'][0]['category'], 'protocol_error')

    def test_receipt_tamper_environment_change_and_contract_change_rejected(self):
        before = self.run_contract()
        self.write('def total(a,b):\n    return a+b\n')
        after = self.run_contract(phase='candidate')
        altered = copy.deepcopy(after)
        altered['cases'][0]['actual']['value'] = 999
        altered['receipt_sha256'] = fingerprint({k: v for k, v in altered.items() if k != 'receipt_sha256'})
        with self.assertRaises(SafetyError):
            validate_receipt(altered)
        altered = copy.deepcopy(after)
        altered['environment_sha256'] = 'f' * 64
        altered['receipt_sha256'] = fingerprint({k: v for k, v in altered.items() if k != 'receipt_sha256'})
        with self.assertRaisesRegex(SafetyError, 'environment differs'):
            compare_behavior_validation(before, altered)
        altered = copy.deepcopy(after)
        altered['counts']['total'] = 0
        with self.assertRaises(SafetyError):
            compare_behavior_validation(before, altered)

    def test_saved_artifact_tamper_cannot_be_hidden_by_rehashing_receipt(self):
        self.write('def total(a,b):\n    return a+b\n')
        result = self.run_contract()
        artifact = Path(result['artifact_directory']) / 'addition.actual.json'
        artifact.write_text('{"category":"passed"}', encoding='utf-8')
        with self.assertRaisesRegex(SafetyError, 'artifact changed'):
            validate_receipt(result)

    def test_current_target_source_wins_over_stale_equal_size_bytecode(self):
        target = self.workspace / 'calc.py'
        self.write('def total(a,b):\n    return a+b\n')
        original = target.stat()
        py_compile.compile(str(target), doraise=True)
        self.write('def total(a,b):\n    return a-b\n')
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
        result = self.run_contract()
        self.assertEqual(result['cases'][0]['actual'], {'kind': 'json', 'value': -1})
        self.assertEqual(result['category'], 'test_failed')

    def test_runtime_authorization_cannot_be_inferred_from_contract(self):
        contract = self.contract()
        contract['runtime_authorized'] = True
        contract['contract_sha256'] = fingerprint({k: v for k, v in contract.items() if k != 'contract_sha256'})
        with self.assertRaises(SafetyError):
            self.run_contract(contract)
        with self.assertRaises(SafetyError):
            run_behavior_validation(self.contract(), self.workspace, self.workspace,
                python=str(Path(sys.executable).resolve()), phase='baseline', environment_sha256=self.environment)

    def test_relative_package_import_preserves_existing_dependencies(self):
        self.write('VALUE=7\n', 'pkg/__init__.py')
        self.write('from . import VALUE\ndef total(a,b):\n    return a+b+VALUE\n', 'pkg/calc.py')
        contract = self.contract([self.case(module='pkg/calc.py', expect={'kind': 'json', 'value': 12})])
        self.assertEqual(self.run_contract(contract)['category'], 'passed')


if __name__ == '__main__':
    unittest.main()
