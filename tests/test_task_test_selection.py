"""Task evidence selects real tests; no user-provided paths/commands or model calls."""
import copy
import json
import unittest
from unittest.mock import patch

import test_target_environment as fixtures
from repo_maintainer.source_discovery import CommittedTree
from repo_maintainer.models import SafetyError
from repo_maintainer.target_environment import discover_environment, make_grant
from repo_maintainer.repository_diagnosis import build_report


class TaskTestSelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TargetEnvironmentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo

    def project(self, count=45):
        self.fixture.write('calc.py', 'def total(n): return n + 1\n')
        self.fixture.write('pyproject.toml', '[project]\nname="task-test"\nversion="0.1"\n')
        for i in range(count):
            self.fixture.write(f'tests/test_{i:02}.py', 'import unittest\nfrom calc import total\n'
                'class T(unittest.TestCase):\n    def test_total(self): self.assertEqual(total(3),6)\n')
        self.head = self.fixture.commit()
        tree = CommittedTree(self.repo, self.head, ['.'])
        tree.read('tests/test_44.py')
        self.report = {'schema_version': 1, 'runtime_authorized': False,
            'git': {'head_sha': self.head}, 'target_sha': self.head,
            'file_candidates': [{**tree.pin('tests/test_44.py'), 'role': 'test',
                                 'score': 10, 'reasons': ['literal_token_match']}],
            'discovery': {'target_sha':self.head, 'verified_locations': []}}

    def focused(self, report=None):
        return discover_environment(self.repo, self.head, ['.'],
                                    investigation=report or self.report)

    def test_empty_catalog_behavior_uses_same_full_plan_without_dropping_existing_tests(self):
        from repo_maintainer.behavior_validation import make_contract
        from repo_maintainer.task_session import repository_identity
        self.fixture.write('calc.py', 'def total(n): return n + 1\n')
        self.head = self.fixture.commit()
        contract = make_contract(repository_id=repository_identity(self.repo.root, self.repo.common_dir),
            base_sha=self.head, request_sha256='a'*64, requirement='Double the number', cases=[
                {'id':'double', 'module':'calc.py','callable':'total','args':[3],'kwargs':{},
                 'expect':{'kind':'json','value':6},'missing_behavior':False}])
        report = build_report(self.repo, head_sha=self.head, read_roots=['.'], search=True, query='total')
        whole = discover_environment(self.repo, self.head, ['.'], behavior_contract=contract)
        focused = discover_environment(self.repo, self.head, ['.'], investigation=report, behavior_contract=contract)
        self.assertEqual(focused, whole)
        self.assertNotIn('selection', focused)
        self.assertEqual(len(focused['checks']), 1)
        self.fixture.write('tests/test_calc.py', 'import unittest\nfrom calc import total\n'
            'class T(unittest.TestCase):\n    def test_total(self): self.assertEqual(total(3),6)\n')
        self.head = self.fixture.commit()
        contract = make_contract(repository_id=repository_identity(self.repo.root, self.repo.common_dir),
            base_sha=self.head, request_sha256='a'*64, requirement='Double the number', cases=contract['cases'])
        report = build_report(self.repo, head_sha=self.head, read_roots=['.'], search=True, query='total')
        focused = discover_environment(self.repo, self.head, ['.'], investigation=report, behavior_contract=contract)
        self.assertEqual(focused['selection']['catalog_test_count'], 1)
        self.assertEqual(focused['selection']['selected_tests'], ['tests/test_calc.py'])
        self.assertEqual(len(focused['checks']), 2)

    def test_large_project_uses_pinned_task_tests_without_widening_caps_or_running_them(self):
        self.project()
        before = self.repo.snapshot()
        with self.assertRaisesRegex(SafetyError, 'capacity'):
            discover_environment(self.repo, self.head, ['.'])
        plan = self.focused()
        self.assertEqual(plan['status'], 'proposed')
        self.assertEqual(plan['selection']['selected_tests'], ['tests/test_44.py'])
        self.assertEqual(plan['selection']['unselected_test_count'], 44)
        self.assertFalse(plan['selection']['complete_repository_review'])
        self.assertEqual(plan['checks'][0]['argv'], ['{python}', '-m', 'unittest', 'discover',
                         '--start-directory=tests', '--pattern=test_44.py', '-v'])
        self.assertEqual({p['path'] for p in plan['inputs']}, {'pyproject.toml', 'tests/test_44.py'})
        self.assertFalse(plan['runtime_authorized'])
        self.assertEqual(before, self.repo.snapshot())

    def test_focused_check_actually_fails_then_passes_in_same_offline_venv(self):
        self.project()
        plan = self.focused()
        env = self.fixture.owned(plan, make_grant(plan, allow_local_code=True, allow_install=True)).prepare()
        self.assertEqual(env.run_check(plan['checks'][0]['argv'])['category'], 'test_failed')
        (env.workspace.root/'calc.py').write_text('def total(n): return n * 2\n', encoding='utf-8')
        self.assertEqual(env.run_check(plan['checks'][0]['argv'])['category'], 'passed')
        self.assertEqual(self.repo.head, self.head)

    def test_fake_hash_head_role_or_report_command_cannot_substitute_execution(self):
        self.project()
        for field, value in [('sha256', '0'*64), ('commit_sha', '0'*40), ('role', 'implementation')]:
            report = copy.deepcopy(self.report)
            report['file_candidates'][0][field] = value
            with self.subTest(field=field), self.assertRaises(SafetyError):
                self.focused(report)
        report = copy.deepcopy(self.report)
        report['test_candidates'] = [{'argv': ['python', '-c', 'raise RuntimeError("injection")']}]
        self.assertNotIn('injection', json.dumps(self.focused(report)['checks']))

    def test_all_reported_test_locations_are_required_instead_of_dropping_failures(self):
        self.project()
        tree = CommittedTree(self.repo, self.head, ['.'])
        for i in range(4):
            name = f'tests/test_{i:02}.py'
            tree.read(name)
            self.report['discovery']['verified_locations'].append({**tree.pin(name), 'status': 'verified_location'})
        with self.assertRaisesRegex(SafetyError, 'required test'):
            self.focused()

    def test_test_names_with_brackets_cannot_expand_the_pattern(self):
        self.project()
        self.fixture.write('tests/test_[ab].py', 'import unittest\nclass T(unittest.TestCase):\n'
            '    def test_ok(self): self.assertTrue(True)\n')
        self.head = self.fixture.commit()
        tree = CommittedTree(self.repo, self.head, ['.'])
        tree.read('tests/test_[ab].py')
        self.report.update(target_sha=self.head, git={'head_sha':self.head},
            discovery={'target_sha':self.head,'verified_locations':[]},
            file_candidates=[{**tree.pin('tests/test_[ab].py'), 'role':'test', 'score':1}])
        with self.assertRaisesRegex(SafetyError, 'module filename'):
            self.focused()

    def test_real_w2_report_locations_survive_candidate_ranking_and_bind_target(self):
        self.project()
        report = build_report(self.repo, head_sha=self.head, read_roots=['.'], search=True,
            checks=[{'command':['reported-check'], 'returncode':1,
                     'stderr':'File "tests/test_44.py", line 4\nAssertionError: wrong total'}])
        plan = self.focused(report)
        self.assertIn('tests/test_44.py', plan['selection']['required_test_locations'])
        bad = copy.deepcopy(report)
        bad['discovery']['verified_locations'][0]['status'] = 'line_out_of_range'
        with self.assertRaisesRegex(SafetyError, 'Unverified required'):
            self.focused(bad)

    def test_ranked_subset_is_explicit_and_required_location_outranks_optional_candidates(self):
        self.project()
        tree = CommittedTree(self.repo, self.head, ['.'])
        rows = []
        for i in range(5):
            path = f'tests/test_{i:02}.py'
            tree.read(path)
            rows.append({**tree.pin(path), 'role':'test', 'score':5-i})
        self.report['file_candidates'] = rows
        self.report['discovery']['verified_locations'] = [{**rows[-1], 'status':'verified_location'}]
        plan = self.focused()
        self.assertEqual(plan['selection']['selected_tests'], ['tests/test_04.py','tests/test_00.py','tests/test_01.py'])
        self.assertEqual(plan['selection']['unselected_candidates'], ['tests/test_02.py','tests/test_03.py'])
        self.assertEqual(len(plan['checks']), 3)
        for check in plan['checks']:
            self.assertIn('--pattern=' + check['sources'][0].split('/')[-1], check['argv'])

    def test_selected_zero_and_skipped_tests_are_not_green(self):
        self.project()
        self.fixture.write('tests/test_44.py', 'import unittest\n@unittest.skip("fixture")\n'
            'class T(unittest.TestCase):\n    def test_skip(self): pass\n')
        self.head = self.fixture.commit()
        tree = CommittedTree(self.repo, self.head, ['.'])
        tree.read('tests/test_44.py')
        self.report.update(git={'head_sha':self.head}, discovery={'target_sha':self.head},
            file_candidates=[{**tree.pin('tests/test_44.py'), 'role':'test'}])
        plan = self.focused()
        env = self.fixture.owned(plan, make_grant(plan, allow_local_code=True, allow_install=True)).prepare()
        self.assertEqual(env.run_check(plan['checks'][0]['argv'])['category'], 'zero_effective_tests')

    def test_node_native_task_paths_run_instead_of_readme_or_npm_free_text(self):
        self.fixture.write('calc.cjs', 'exports.total=n=>n+1;\n')
        self.fixture.write('tests/test_calc.cjs', "const test=require('node:test'); const a=require('node:assert/strict');"
            "const {total}=require('../calc.cjs'); test('total',()=>a.equal(total(3),6));\n")
        self.fixture.write('package.json', '{"name":"task-node","version":"1.0.0","scripts":{"test":"arbitrary-cmd"}}')
        self.head = self.fixture.commit()
        report = build_report(self.repo, head_sha=self.head, read_roots=['.'], search=True, query='total')
        plan = self.focused(report)
        self.assertEqual(plan['checks'][0]['argv'], ['node','--test','./tests/test_calc.cjs'])
        env = self.fixture.owned(plan, make_grant(plan, allow_local_code=True)).prepare()
        self.assertEqual(env.run_check(plan['checks'][0]['argv'])['category'], 'test_failed')
        (env.workspace.root/'calc.cjs').write_text('exports.total=n=>n*2;\n')
        self.assertEqual(env.run_check(plan['checks'][0]['argv'])['category'], 'passed')

    def test_matching_test_import_reaches_implementation_before_unrelated_tests_use_read_budget(self):
        self.project(count=105)
        self.fixture.write('src/zz_component.py', 'def CriticalRepairSymbol(n): return n+1\n')
        self.fixture.write('tests/test_000_focus.py', 'import unittest\nfrom src.zz_component import CriticalRepairSymbol\n'
            'class T(unittest.TestCase):\n    def test_result(self): self.assertEqual(CriticalRepairSymbol(3),6)\n')
        self.fixture.commit()
        self.fixture.write('README.md', 'A later documentation-only commit.\n')
        head=self.fixture.commit()
        # Keep the regression's original exhausted 100-file budget even when
        # the product default is expanded; do not weaken this priority check.
        with patch('repo_maintainer.source_discovery.CommittedTree',
                   side_effect=lambda *args, **kwargs: CommittedTree(*args, file_limit=100, **kwargs)):
            report=build_report(self.repo,head_sha=head,read_roots=['.'],search=True,query='CriticalRepairSymbol')
        paths=[p['path'] for p in report['file_candidates']]
        self.assertIn('src/zz_component.py',paths)
        self.assertLessEqual(report['discovery']['coverage']['files_read'],100)
        self.assertTrue(report['discovery']['coverage']['read_budget_exhausted'])
        self.assertFalse(report['discovery']['coverage']['complete_repository_review'])


if __name__ == '__main__':
    unittest.main()
