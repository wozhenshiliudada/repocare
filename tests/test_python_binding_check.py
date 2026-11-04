"""Optional checker protocol and real pinned Pyright; no model calls."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import python_binding_check as gate


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'main.py').write_bytes(b'value = 1\n')

    def test_exact_sources_and_check_subset(self):
        self.assertEqual({'main.py': b'value = 1\n'}, gate.sources(self.root, ['main.py'], ['main.py']))

    def test_noncanonical_or_foreign_paths_rejected(self):
        for name in ('../main.py', '/main.py', 'C:/main.py', 'x\\main.py', 'x/../main.py', './main.py',
                     'main*.py', 'main.py/', 'pyrightconfig.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                gate.sources(self.root, [name], [name])

    def test_empty_duplicate_missing_or_overlarge_scope_rejected(self):
        for names, checks in (([], []), (['main.py'] * 2, ['main.py']), (['main.py'], []),
                              (['main.py', 'MAIN.py'], ['main.py']),
                              (['main.py'], ['other.py']), (['main.py'], ['main.py'] * 2),
                              ([f'{i}.py' for i in range(51)], ['0.py'])):
            with self.subTest(names=names, checks=checks), self.assertRaises(ValueError):
                gate.sources(self.root, names, checks)

    def test_target_cannot_supply_checker_executable(self):
        node = self.root / 'node.exe'
        node.write_bytes(b'not an executable')
        with self.assertRaisesRegex(ValueError, 'outside checked root'):
            gate.evaluate(self.root, ['main.py'], ['main.py'], str(node), '3.11', 'Linux')

    def test_oversized_source_rejected(self):
        (self.root / 'main.py').write_bytes(b'#' * 100001)
        with self.assertRaisesRegex(ValueError, '100000'):
            gate.sources(self.root, ['main.py'], ['main.py'])

    def test_source_comments_cannot_override_checker(self):
        for content in ('# pyright: strict\n', '# PYRIGHT : ignore\n'):
            (self.root / 'main.py').write_text(content)
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, 'Inline'):
                gate.sources(self.root, ['main.py'], ['main.py'])

    def test_ordinary_strings_are_not_suppression_comments(self):
        (self.root / 'main.py').write_text('message = "# pyright: ignore"\n')
        self.assertIn('main.py', gate.sources(self.root, ['main.py'], ['main.py']))

    def test_static_environment_rejects_unknown_versions_and_is_explicit(self):
        with self.assertRaises(ValueError):
            gate.configuration(self.root, ['main.py'], '2.7', 'Linux')
        with self.assertRaises(ValueError):
            gate.configuration(self.root, ['main.py'], '3.11', 'guess')
        config = gate.configuration(self.root, ['main.py'], '3.11', 'Linux')
        self.assertEqual(['source/main.py'], config['include'])
        self.assertTrue((self.root / 'environments/empty/lib/site-packages').is_dir())
        self.assertTrue(all(config[rule] == 'error' for rule in gate.RULES))

    def output(self):
        return {'version': gate.VERSION, 'summary': {'filesAnalyzed': 1, 'errorCount': 1,
                'warningCount': 0, 'informationCount': 0}, 'generalDiagnostics': [
            {'file': str(self.root / 'main.py'), 'severity': 'error', 'rule': 'reportCallIssue',
             'message': 'bool is not callable', 'range': {'start': {'line': 0, 'character': 0}}}]}

    def test_real_diagnostic_projection(self):
        rows, counts = gate.verified_output(self.output(), 1, self.root, ['main.py'])
        self.assertEqual(1, rows[0]['line'])
        self.assertEqual('main.py', rows[0]['path'])
        self.assertEqual(1, counts['error'])

    def test_incomplete_or_contradictory_checker_results_rejected(self):
        for change, code in (({'version': 'unknown'}, 1), ({'generalDiagnostics': []}, 1), ({}, 0), ({}, 2),
                             ({'summary': {'filesAnalyzed': 0}}, 1)):
            value = self.output()
            value.update(change)
            with self.subTest(change=change, code=code), self.assertRaises(ValueError):
                gate.verified_output(value, code, self.root, ['main.py'])

    def test_foreign_file_and_bad_location_rejected(self):
        for change in ({'file': str(self.root.parent / 'foreign.py')},
                       {'file': str(self.root / 'other.py')},
                       {'range': {'start': {'line': -1, 'character': 0}}}):
            value = self.output()
            value['generalDiagnostics'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                gate.verified_output(value, 1, self.root, ['main.py'])


@unittest.skipUnless(os.environ.get('REPOCARE_PYRIGHT_INTEGRATION') == '1',
                     'Optional pinned Pyright integration is explicitly enabled')
class RealCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.node = os.environ['REPOCARE_TEST_NODE']

    def run_source(self, content, context=None):
        (self.root / 'main.py').write_text(content, encoding='utf-8')
        names = ['main.py']
        for name, body in (context or {}).items():
            (self.root / name).write_text(body, encoding='utf-8')
            names.append(name)
        return gate.evaluate(self.root, names, ['main.py'], self.node, '3.11', 'Linux')

    def test_boolean_call_is_static_error(self):
        result, code = self.run_source('def f(value: int):\n    evaluate = False\n    return evaluate(value)\n')
        self.assertEqual(1, code)
        self.assertIn('reportCallIssue', result['failure_excerpt'])

    def test_type_ignore_comment_cannot_hide_an_error(self):
        result, code = self.run_source('value = missing_name  # type: ignore\n')
        self.assertEqual(1, code, result)
        self.assertIn('reportUndefinedVariable', result['failure_excerpt'])

    def test_known_class_missing_attribute_is_static_error(self):
        result, code = self.run_source('class Number:\n    real: int = 1\n\ndef f(a: Number):\n    return a.imaginary_part\n')
        self.assertEqual(1, code)
        self.assertIn('reportAttributeAccessIssue', result['failure_excerpt'])

    def test_undefined_name_is_static_error(self):
        result, code = self.run_source('def f():\n    return MissingAdd(1, 2)\n')
        self.assertEqual(1, code)
        self.assertIn('reportUndefinedVariable', result['failure_excerpt'])

    def test_callable_shadowing_and_declared_stub_are_valid(self):
        result, code = self.run_source('from shapes import Number\n'
            'def f(a: Number):\n    evaluate = lambda x: x\n    return evaluate(a.real)\n',
            {'shapes.pyi': 'class Number:\n    real: int\n'})
        self.assertEqual(0, code, result)
        self.assertEqual(2, len(result['sources']))
        self.assertFalse(result['semantic_correctness_proven'])

    def test_unknown_and_any_are_recorded_as_blind_spots(self):
        for parameter in ('a', 'a: Any'):
            result, code = self.run_source('from typing import Any\n'
                f'def f({parameter}):\n    return a.imaginary_part\n')
            self.assertEqual(0, code, result)
            self.assertTrue(result['unknown_or_any_may_hide_errors'])
            self.assertFalse(result['semantic_correctness_proven'])

    def test_target_code_and_local_configuration_are_not_executed_or_loaded(self):
        marker = self.root / 'executed'
        (self.root / 'pyrightconfig.json').write_text('{"typeCheckingMode":"off","exclude":["**"]}')
        result, code = self.run_source(f'from pathlib import Path\nPath({str(marker)!r}).write_text("BAD")\n'
                                      'def f():\n    return missing_name\n')
        self.assertEqual(1, code, result)
        self.assertFalse(marker.exists())

    def test_secrets_and_injected_node_options_are_not_inherited(self):
        with patch.dict(os.environ, {'NODE_OPTIONS': '--require=nonexistent-should-not-load',
                                      'PI_MAINTENANCE_API_KEY': 'synthetic-only'}):
            result, code = self.run_source('value: int = 1\n')
        self.assertEqual(0, code, result)
        self.assertNotIn('synthetic-only', json.dumps(result))

    def test_concurrent_change_and_checker_timeout_are_not_green(self):
        original_run = gate.subprocess.run
        def mutate(*args, **kwargs):
            result = original_run(*args, **kwargs)
            (self.root / 'main.py').write_text('changed = 1\n')
            return result
        with patch.object(gate.subprocess, 'run', side_effect=mutate), self.assertRaisesRegex(ValueError, 'changed'):
            self.run_source('value = 1\n')
        with patch.object(gate.subprocess, 'run', side_effect=subprocess.TimeoutExpired('node', 60)), \
                self.assertRaises(subprocess.TimeoutExpired):
            self.run_source('value = 1\n')

    def test_many_diagnostics_keep_valid_json_inside_runner_limit(self):
        result, code = self.run_source('\n'.join(f'value_{i} = undefined_name_{i}_' + 'x' * 120 for i in range(80)))
        self.assertEqual(1, code, result)
        self.assertTrue(result['diagnostics_truncated'])
        self.assertEqual(80, result['counts']['error'])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()), 16000)

    def controller(self, bad):
        from repo_maintainer.demo import create_fixture, DEMO_TEST
        from repo_maintainer.git import RepositoryGateway
        from repo_maintainer.models import Edit, Policy
        from repo_maintainer.provider import MockProvider
        from repo_maintainer.workflow import repair, cleanup_run
        repo = create_fixture(self.root / 'repo')
        before = RepositoryGateway(repo).snapshot()
        command = ('{python}', str(Path(gate.__file__).resolve()), '--source', 'calculator.py',
                   '--check', 'calculator.py', '--node', self.node,
                   '--python-version', '3.11', '--python-platform', 'Linux')
        class Candidate(MockProvider):
            def plan(self, repository, issue, tests):
                plan = super().plan(repository, issue, tests)
                if not bad:
                    return plan
                edit = plan.edits[0]
                # Normal fixture tests never call this function: only static checking catches it.
                return replace(plan, edits=(Edit(edit.path, edit.before_sha256, edit.content +
                    '\ndef unused(value: int):\n    evaluate = False\n    return evaluate(value)\n'),))
        result = repair(repo, 'Fixture addition bug', Candidate(),
                        Policy(('calculator.py',), (DEMO_TEST, command), 90), 'approval',
                        self.root / 'runs', lambda plan: 'approve')
        if Path(result['workspace']).exists():
            self.addCleanup(cleanup_run, Path(result['workspace']).parent)
        self.assertTrue(result['source_unchanged'], result)
        self.assertEqual(before, RepositoryGateway(repo).snapshot(
            exclude_branch=result['branch'] if result['status'] == 'candidate_ready' else ''))
        self.assertEqual([1, 0], [row['returncode'] for row in result['baseline_tests']])
        if os.environ.get('REPOCARE_BINDING_EVIDENCE'):
            destination = Path(os.environ['REPOCARE_BINDING_EVIDENCE'])
            destination.mkdir(parents=True, exist_ok=True)
            with (destination / ('workflow-bad.json' if bad else 'workflow-good.json')).open('x', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=True, indent=2)
        return result

    def test_original_approval_workflow_rolls_back_static_regression(self):
        result = self.controller(True)
        self.assertEqual('rolled_back', result['status'], result)
        self.assertEqual([0, 1], [row['returncode'] for row in result['candidate_tests']])
        self.assertFalse(Path(result['workspace']).exists())
        self.assertIn('reportCallIssue', result['candidate_tests'][1]['stdout'])
        from repo_maintainer.pi_revision import test_focus
        focus = test_focus(result['candidate_tests'], result['baseline_tests'])
        self.assertIn('reportCallIssue', json.dumps(focus))

    def test_original_approval_workflow_preserves_good_candidate(self):
        result = self.controller(False)
        self.assertEqual('candidate_ready', result['status'], result)
        self.assertEqual([0, 0], [row['returncode'] for row in result['candidate_tests']])


if __name__ == '__main__':
    unittest.main()
