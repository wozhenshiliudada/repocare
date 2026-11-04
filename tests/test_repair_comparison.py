"""Read-only projection against real Git pins; no checks, models or installs."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from repo_maintainer.behavior_validation import make_contract
from repo_maintainer.git import RepositoryGateway, git_env
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.models import Policy, SafetyError, digest
from repo_maintainer.repair_comparison import repair_comparison
from repo_maintainer.target_environment import discover_environment, make_grant
from repo_maintainer.task_session import repository_identity


class RepairComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        source = cls.root / 'source'
        subprocess.run(['git', 'init', '-b', 'main', str(source)], check=True,
                       capture_output=True, env=git_env())
        (source / 'tests').mkdir()
        (source / 'calc.py').write_text('def total(n):\n    return n+1\n', encoding='utf-8')
        (source / 'tests/test_calc.py').write_text(
            'import unittest\nfrom calc import total\nclass Check(unittest.TestCase):\n'
            '    def test_total(self): self.assertEqual(total(3),6)\n', encoding='utf-8')
        (source / 'pyproject.toml').write_text(
            '[project]\nname="comparison-fixture"\nversion="0.1"\nrequires-python=">=3.11"\n',
            encoding='utf-8')
        cls.repo = RepositoryGateway(source)
        cls.repo.command('add', '.')
        cls.repo.command('-c', 'user.name=Comparison Fixture', '-c', 'user.email=fixture@example.invalid',
                         'commit', '-m', 'offline comparison fixture')
        cls.head = cls.repo.head
        clone = cls.root / 'clone'
        subprocess.run(['git', 'clone', '--no-hardlinks', str(source), str(clone)], check=True,
                       capture_output=True, env=git_env())
        cls.clone = RepositoryGateway(clone)
        cls.plan = discover_environment(cls.repo, cls.head, ['.'])
        cls.grant = make_grant(cls.plan, allow_local_code=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.policy = Policy(('calc.py',), tuple(tuple(c['argv']) for c in self.plan['checks']),
                             5, ('tests/test_calc.py', 'pyproject.toml'))
        self.args = {'repository_id': 42, 'slug': 'Owner/Repo', 'branch': 'main', 'head_sha': self.head,
                     'policy': self.policy, 'environment': deepcopy(self.plan), 'grant': deepcopy(self.grant),
                     'read_roots': ['.'], 'write_roots': ['calc.py'], 'mode': 'auto',
                     'model_identity': {'provider_config_sha256': 'a'*64, 'credential_source': 'explicit-file',
                                        'provider': {'model': 'fixture-model', 'runtime': 'pi-sdk-v1'}},
                     'max_attempts': 2, 'fault_group': 'b'*64}

    def project(self, repo=None, **changes):
        return repair_comparison(repo or self.repo, **{**self.args, **changes})

    @staticmethod
    def rehash(plan, grant):
        plan['plan_sha256'] = fingerprint({k:v for k,v in plan.items() if k != 'plan_sha256'})
        grant['plan_sha256'] = plan['plan_sha256']
        grant['grant_sha256'] = fingerprint({k:v for k,v in grant.items() if k != 'grant_sha256'})

    def test_clone_identity_removed_without_effects(self):
        plan = discover_environment(self.clone, self.head, ['.'])
        grant = make_grant(plan, allow_local_code=True)
        self.assertNotEqual(plan['repository_id'], self.plan['repository_id'])
        before = self.repo.snapshot()
        with (patch('repo_maintainer.runner.run_test', side_effect=AssertionError('No checks')),
             patch('repo_maintainer.pi_provider.PiProvider.plan', side_effect=AssertionError('No model')),
             patch('repo_maintainer.target_environment.TargetEnvironment.prepare', side_effect=AssertionError('No install'))):
            result = self.project()
            self.assertEqual(result, self.project(self.clone, environment=plan, grant=grant))
        self.assertEqual(self.repo.snapshot(), before)
        self.assertEqual(set(result), {'repository_id', 'repository', 'branch', 'head_sha',
                         'checks_sha256', 'environment_sha256', 'fault_sha256', 'authority_sha256'})
        self.assertEqual(result['repository'], 'owner/repo')

    def test_numeric_github_id_not_local_hash_or_bool(self):
        for value in (True, False, '42', digest(b'local-common-dir'), 0, -1, 2**53):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                self.project(repository_id=value)

    def test_exact_fault_group_branch_and_head(self):
        original = self.project()
        self.assertNotEqual(original['fault_sha256'], self.project(fault_group='c'*64)['fault_sha256'])
        self.assertNotEqual(original, self.project(branch='other'))
        for changes in ({'fault_group': 'Issue says same failure'}, {'head_sha': '0'*40},
                        {'slug': 'https://github.com/owner/repo'}):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                self.project(**changes)

    def test_policy_roots_mode_and_attempts_preserve_authority(self):
        original = self.project()['authority_sha256']
        for changes in ({'mode': 'approval'}, {'max_attempts': 1}, {'write_roots': ['.']},
                        {'policy': Policy(self.policy.allowed_paths, self.policy.allowed_tests, 6,
                                          self.policy.readonly_paths)}):
            with self.subTest(changes=changes):
                self.assertNotEqual(original, self.project(**changes)['authority_sha256'])
        for changes in ({'write_roots': ['tests']}, {'max_attempts': True}, {'mode': 'remote'}):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                self.project(**changes)

    def test_full_checks_and_discover_vs_focused_are_not_inferred_equivalent(self):
        original = self.project()
        plan, grant = deepcopy(self.plan), deepcopy(self.grant)
        plan['checks'][0]['argv'].append('--failfast')
        self.rehash(plan, grant)
        policy = Policy(self.policy.allowed_paths, tuple(tuple(c['argv']) for c in plan['checks']),
                        5, self.policy.readonly_paths)
        changed = self.project(environment=plan, grant=grant, policy=policy)
        self.assertNotEqual(original['checks_sha256'], changed['checks_sha256'])
        with self.assertRaises(SafetyError):
            self.project(environment=plan, grant=grant)
        plan, grant = deepcopy(self.plan), deepcopy(self.grant)
        plan['selection'] = {'kind':'task-tests-v1', 'investigation_sha256':'1'*64,
                             'selected_tests':['tests/test_calc.py'], 'required_test_locations':[],
                             'unselected_candidates':[], 'complete_repository_review':False}
        self.rehash(plan, grant)
        focused = self.project(environment=plan, grant=grant)
        self.assertNotEqual(original['checks_sha256'], focused['checks_sha256'])
        plan['selection']['investigation_sha256'] = '2'*64
        self.rehash(plan, grant)
        self.assertEqual(focused, self.project(environment=plan, grant=grant))

    def test_original_plan_grant_and_input_pins_are_verified(self):
        for mutate in ('plan_hash', 'grant_hash', 'input_pin', 'decision_pin', 'foreign_source'):
            plan, grant = deepcopy(self.plan), deepcopy(self.grant)
            if mutate == 'plan_hash': plan['runtime'] = 'node'
            elif mutate == 'grant_hash': grant['allow_network'] = True
            elif mutate == 'foreign_source':
                plan['repository_id'] = '0'*64
                self.rehash(plan, grant)
            else:
                plan['inputs' if mutate == 'input_pin' else 'decision_inputs'][0]['sha256'] = '0'*64
                self.rehash(plan, grant)
            with self.subTest(mutate=mutate), self.assertRaises(SafetyError):
                self.project(environment=plan, grant=grant)

    def test_source_drift_rejected_without_importing_target(self):
        path = self.repo.root / 'calc.py'
        original = path.read_bytes()
        try:
            path.write_bytes(b'raise AssertionError("must never import")\n')
            with self.assertRaises(SafetyError): self.project()
        finally:
            path.write_bytes(original)

    def test_dependency_runtime_grant_and_timeout_differences_retained(self):
        original = self.project()
        for key, value in (('requirements', ['fixture-dep==0.2']), ('python_constraint', '>=3.12')):
            plan, grant = deepcopy(self.plan), deepcopy(self.grant)
            plan[key] = value
            grant['allow_install'] = True
            self.rehash(plan, grant)
            with self.subTest(key=key):
                self.assertNotEqual(original['environment_sha256'],
                                    self.project(environment=plan, grant=grant)['environment_sha256'])
        for key, value in (('allow_network', True), ('timeout_seconds', 31),
                           ('index_url', 'https://example.invalid/simple')):
            grant = deepcopy(self.grant); grant[key] = value
            self.rehash(deepcopy(self.plan), grant)
            with self.subTest(key=key):
                self.assertNotEqual(original['environment_sha256'], self.project(grant=grant)['environment_sha256'])
        grant = deepcopy(self.grant); grant['python'] = str(self.root / 'missing-runtime')
        self.rehash(deepcopy(self.plan), grant)
        with self.assertRaises(SafetyError): self.project(grant=grant)

    def test_model_config_credential_and_runtime_identity_differences_retained(self):
        original = self.project()['authority_sha256']
        for key, value in (('provider_config_sha256', 'd'*64), ('credential_source', 'environment'),
                           ('provider', {'model':'another', 'runtime':'pi-sdk-v2'})):
            identity = deepcopy(self.args['model_identity']); identity[key] = value
            with self.subTest(key=key):
                self.assertNotEqual(original, self.project(model_identity=identity)['authority_sha256'])
        for identity in ({'credential_source':None}, {'provider_config_sha256':'a'*64},
                         {**self.args['model_identity'], 'request_budget':50}):
            with self.subTest(identity=identity), self.assertRaises(SafetyError):
                self.project(model_identity=identity)

    def test_runtime_same_path_byte_replacement_changes_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / 'authored-runtime.bin'
            runtime.write_bytes(b'never executed runtime v1')
            grant = deepcopy(self.grant); grant['python'] = str(runtime)
            self.rehash(deepcopy(self.plan), grant)
            first = self.project(grant=grant)
            runtime.write_bytes(b'never executed runtime v2')
            second = self.project(grant=grant)
            self.assertNotEqual(first['environment_sha256'], second['environment_sha256'])
            self.assertNotEqual(first['authority_sha256'], second['authority_sha256'])

    def test_wheelhouse_location_removed_but_actual_wheel_pins_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'first'; first.mkdir()
            second = Path(directory) / 'second'; second.mkdir()
            for root in (first, second):
                (root / 'fixture.whl').write_bytes(b'authored opaque wheel; never installed')
            grant_a = make_grant(self.plan, allow_local_code=True, allow_install=True, wheelhouse=first)
            grant_b = make_grant(self.plan, allow_local_code=True, allow_install=True, wheelhouse=second)
            self.assertEqual(self.project(grant=grant_a), self.project(grant=grant_b))
            (second / 'fixture.whl').write_bytes(b'changed opaque wheel')
            with self.assertRaises(SafetyError): self.project(grant=grant_b)

    def test_behavior_clone_wrappers_removed_full_request_and_cases_remain(self):
        cases = [{'id':'total_case', 'module':'calc.py', 'callable':'total', 'args':[3], 'kwargs':{},
                  'expect':{'kind':'json', 'value':6}, 'missing_behavior':False}]
        def configured(repo, request='e'*64, requirement='Double input', case_rows=None):
            contract = make_contract(repository_id=repository_identity(repo.root, repo.common_dir),
                base_sha=self.head, request_sha256=request, requirement=requirement, cases=case_rows or cases)
            plan = discover_environment(repo, self.head, ['.'], behavior_contract=contract)
            grant = make_grant(plan, allow_local_code=True)
            policy = Policy(self.policy.allowed_paths, tuple(tuple(c['argv']) for c in plan['checks']),
                            5, self.policy.readonly_paths)
            return self.project(repo, environment=plan, grant=grant, policy=policy)
        original = configured(self.repo)
        self.assertEqual(original, configured(self.clone))
        changed_cases = deepcopy(cases); changed_cases[0]['expect']['value'] = 7
        for changes in ({'request':'f'*64}, {'requirement':'Triple input'}, {'case_rows':changed_cases}):
            with self.subTest(changes=changes):
                self.assertNotEqual(original['checks_sha256'], configured(self.repo, **changes)['checks_sha256'])


if __name__ == '__main__':
    unittest.main()
