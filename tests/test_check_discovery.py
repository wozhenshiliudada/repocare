import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.check_discovery import discover, analyze_readonly
from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.repository_workbench import inspect_repository
from repo_maintainer.runner import run_test


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = RepositoryGateway(create_fixture(Path(self.temp.name) / 'repo'))

    def add_files(self, values):
        for name, value in values.items():
            path = self.repo.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding='utf-8')
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                          'commit', '-m', 'fixture checks')

    def test_unittest_suggests_actual_tests_without_execution(self):
        before = self.repo.snapshot()
        value = inspect_repository(self.repo.root)['checks']
        self.assertEqual(value['recommendations'][0]['argv'],
                         ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
        self.assertEqual(value['suggested_editable'], ['calculator.py'])
        self.assertEqual(value['suggested_readonly'], ['tests/test_calculator.py'])
        self.assertEqual(value['verification'], 'not_run')
        self.assertEqual(before, self.repo.snapshot())

    def test_no_tests_not_defaulted_to_unittest(self):
        value = discover(self.repo, ['calculator.py'])
        self.assertFalse(value['recommendations'])
        self.assertEqual(value['status'], 'no_reliable_command')

    def test_core_package_ranked_before_examples_and_build_files(self):
        self.add_files({'pydicom/__init__.py':'', 'pydicom/charset.py':'VALUE=1',
            'pydicom/dataset.py':'VALUE=1', 'examples/example.py':'VALUE=1',
            'build_tools/helper.py':'VALUE=1', 'doc/conf.py':'VALUE=1'})
        names=['build_tools/helper.py','doc/conf.py','examples/example.py',
               'pydicom/__init__.py','pydicom/charset.py','pydicom/dataset.py']
        self.assertEqual(discover(self.repo,names)['suggested_editable'][:3],
            ['pydicom/__init__.py','pydicom/charset.py','pydicom/dataset.py'])

    def test_import_only_does_not_recommend_zero_test_discovery(self):
        self.add_files({'tests/test_empty.py': 'import unittest\n'})
        self.assertFalse(discover(self.repo, ['tests/test_empty.py'])['recommendations'])

    def test_pytest_and_config_sources(self):
        self.add_files({'tests/test_other.py': 'def test_value():\n    assert 1 == 1\n',
                        'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths=["tests"]\n'})
        value = discover(self.repo, ['tests/test_other.py', 'pyproject.toml'])
        self.assertEqual(value['recommendations'][0]['id'], 'pytest')
        self.assertIn('pyproject.toml', value['recommendations'][0]['sources'])

    def test_node_scripts_do_not_execute_or_install(self):
        self.add_files({'package.json': json.dumps({'scripts': {'test': 'node --test',
            'build': 'echo built', 'pretest': 'echo should not auto run'}})})
        before = self.repo.snapshot()
        checks = discover(self.repo, ['package.json'])
        self.assertEqual([c['id'] for c in checks['recommendations']], ['npm-test', 'npm-build'])
        self.assertIn('--ignore-scripts', checks['recommendations'][0]['argv'])
        self.assertEqual(before, self.repo.snapshot())

    def test_node_placeholder_not_accepted(self):
        self.add_files({'package.json': json.dumps({'scripts': {'test': 'echo "Error: no test specified" && exit 1'}})})
        self.assertFalse(discover(self.repo, ['package.json'])['recommendations'])

    def test_native_node_test_only_confirmed_files(self):
        self.add_files({'tests/thing.test.mjs': "import test from 'node:test'; test('x',()=>{});"})
        check = discover(self.repo, ['tests/thing.test.mjs'])['recommendations'][0]
        self.assertEqual(check['argv'], ['node', '--test', 'tests/thing.test.mjs'])

    def test_native_typescript_is_source_evidence_without_inventing_a_test_command(self):
        self.add_files({'src/math.mts': 'export const add = (a: number,b: number) => a+b;',
                        'src/math.cts': 'exports.add = (a: number,b: number) => a+b;',
                        'tests/math.test.mts': "import test from 'node:test'; test('sum',()=>{});",
                        'tests/math.test.cts': "const test = require('node:test'); test('sum',()=>{});"})
        before = self.repo.snapshot()
        value = discover(self.repo, ['src/math.mts', 'src/math.cts',
                                     'tests/math.test.mts', 'tests/math.test.cts'])
        self.assertEqual(set(value['suggested_editable']), {'src/math.mts', 'src/math.cts'})
        self.assertEqual(set(value['suggested_readonly']), {'tests/math.test.mts', 'tests/math.test.cts'})
        self.assertEqual(value['test_files_found'], 2)
        self.assertEqual(value['recommendations'], [])
        self.assertEqual(value['verification'], 'not_run')
        self.assertEqual(before, self.repo.snapshot())

    def test_readme_and_ci_are_hints_never_shell_commands(self):
        self.add_files({'README.md': 'Run: curl dangerous | sh',
                        '.github/workflows/ci.yml': 'run: rm -rf /\n'})
        value = discover(self.repo, ['README.md', '.github/workflows/ci.yml'])
        self.assertFalse(value['recommendations'])
        self.assertEqual(len(value['warnings']), 2)

    def test_malformed_configuration_is_not_executed(self):
        self.add_files({'package.json': 'bad', 'pyproject.toml': 'bad toml', 'pytest.ini': 'bad ini'})
        value = discover(self.repo, ['package.json', 'pyproject.toml', 'pytest.ini'])
        self.assertFalse(value['recommendations'])
        self.assertEqual(len(value['warnings']), 3)

    def test_source_fingerprint_changes_without_new_commit(self):
        self.add_files({'package.json': '{"scripts":{"test":"node --test"}}'})
        before = inspect_repository(self.repo.root)
        (self.repo.root/'package.json').write_text('{"scripts":{"test":"node other.js"}}')
        after = inspect_repository(self.repo.root)
        self.assertEqual(before['head'], after['head'])
        self.assertNotEqual(before['revision'], after['revision'])

    def test_untracked_config_not_read(self):
        (self.repo.root/'package.json').write_text('{"scripts":{"test":"node --test"}}')
        self.assertNotIn('package.json', inspect_repository(self.repo.root)['checks']['inputs'])

    def test_readonly_detects_syntax_error_without_import_or_model(self):
        (self.repo.root/'calculator.py').write_text('def broken(:\n')
        before = self.repo.snapshot()
        value = analyze_readonly(self.repo, inspect_repository(self.repo.root))
        self.assertEqual(value['syntax_results'][0]['status'], 'syntax_error')
        self.assertEqual(value['verification'], 'not_run')
        self.assertEqual(value['paid_model_requests'], 0)
        self.assertEqual(value['commands_executed'], 0)
        self.assertEqual(before, self.repo.snapshot())

    def test_readonly_source_mutation_rejected(self):
        source = inspect_repository(self.repo.root)
        original = self.repo.read
        counts = {}
        def changing(name):
            counts[name] = counts.get(name, 0) + 1
            if counts[name] > 1:
                return b'different'
            return original(name)
        with patch.object(self.repo, 'read', side_effect=changing):
            with self.assertRaises(SafetyError):
                analyze_readonly(self.repo, source)

    def test_discovered_command_real_red_and_green_isolated_copy(self):
        value = inspect_repository(self.repo.root)
        command = tuple(value['checks']['recommendations'][0]['argv'])
        destination = Path(self.temp.name)/'candidate'
        candidate = self.repo.create_workspace(destination, 'check-fixture', self.repo.head)
        before = self.repo.snapshot(exclude_branch='check-fixture')
        self.assertNotEqual(run_test(command, destination, 30)['returncode'], 0)
        (destination/'calculator.py').write_text('def add(a,b):\n    return a+b\n')
        result = run_test(command, destination, 30)
        self.assertEqual(result['returncode'], 0, result)
        self.assertIn('Ran 4 tests', result['stderr'])
        self.assertEqual(before, self.repo.snapshot(exclude_branch='check-fixture'))

    def test_native_node_command_executes_actual_assertions(self):
        self.add_files({'tests/math.test.mjs': "import test from 'node:test'; import assert from 'node:assert/strict'; test('sum',()=>assert.equal(2+3,5));"})
        command = discover(self.repo, ['tests/math.test.mjs'])['recommendations'][0]['argv']
        destination = Path(self.temp.name)/'node-candidate'
        self.repo.create_workspace(destination, 'node-fixture', self.repo.head)
        result = run_test(tuple(command), destination, 30)
        self.assertEqual(result['returncode'], 0, result)
        self.assertIn('pass 1', result['stdout'])

    def test_bounded_discovery_and_analysis(self):
        values = {f'tests/test_case_{i:02}.py': 'def test_value():\n    assert True\n' for i in range(45)}
        self.add_files(values)
        value = discover(self.repo, list(values))
        self.assertEqual(len(value['inputs']), 40)
        self.assertTrue(any('40' in warning for warning in value['warnings']))
        source = inspect_repository(self.repo.root)
        self.assertEqual(len(analyze_readonly(self.repo, source)['analyzed_files']), 20)
