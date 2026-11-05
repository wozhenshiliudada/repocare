"""Coverage and failure propagation contracts for the Windows CI partitions."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/ci_unittest.py'
spec = importlib.util.spec_from_file_location('ci_unittest', SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class PartitionTests(unittest.TestCase):
    def cases(self):
        cases = []
        for index in range(30):
            cls = type('Fixture', (unittest.TestCase,), {
                '__module__': f'fixture_module_{index}', 'test_a': lambda self: None,
                'test_b': lambda self: None,
            })
            cases.extend((cls('test_a'), cls('test_b')))
        return cases

    def test_nested_discovery_preserves_all_occurrences_and_module_fixtures(self):
        cases = self.cases()
        cases.append(cases[0])
        nested = unittest.TestSuite([unittest.TestSuite(cases[:20]), unittest.TestSuite(cases[20:])])
        parts = ci.partition(ci.flatten(nested), 4)
        self.assertEqual(Counter(t.id() for p in parts for t in p), Counter(t.id() for t in cases))
        owners = {}
        for index, part in enumerate(parts):
            for case in part:
                module = type(case).__module__
                self.assertEqual(owners.setdefault(module, index), index)

    def test_order_changes_and_new_modules_do_not_move_existing_modules(self):
        cases = self.cases()
        before = {case.id(): i for i, part in enumerate(ci.partition(cases, 4)) for case in part}
        after = {case.id(): i for i, part in enumerate(ci.partition(reversed(cases), 4)) for case in part}
        self.assertEqual(before, after)
        new_class = type('Later', (unittest.TestCase,), {
            '__module__': 'a_new_module', 'test_new': lambda self: None,
        })
        expanded = {case.id(): i for i, part in enumerate(ci.partition([new_class('test_new'), *cases], 4))
                    for case in part}
        self.assertEqual(before, {key: expanded[key] for key in before})
        self.assertEqual(ci.partition(cases, 1), [cases])

    def test_invalid_shard_counts_are_rejected(self):
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                ci.partition(self.cases(), value)


class CommandTests(unittest.TestCase):
    def invoke(self, source, *extra):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'test_partition_fixture.py').write_text(source, encoding='utf-8')
            report = root / 'report.json'
            command = [sys.executable, '-B', str(SCRIPT), '--start-dir', str(root),
                       '--shard', '1', '--shards', '1', '--report', str(report), *extra]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            value = json.loads(report.read_text(encoding='utf-8')) if report.exists() else None
            return result, value

    def test_actual_failure_and_skip_are_reported_without_losing_other_cases(self):
        result, report = self.invoke('''import unittest
class Example(unittest.TestCase):
    def test_a(self): self.fail('expected fixture failure')
    @unittest.skip('fixture-only skip')
    def test_b(self): pass
    def test_c(self): pass
''')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(report['completed'])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['tests_run'], 3)
        self.assertEqual(len(report['failures']), 1)
        self.assertEqual(len(report['skipped']), 1)
        self.assertEqual(len(report['durations']), 3)

    def test_module_import_error_is_a_failure_instead_of_empty_green(self):
        result, report = self.invoke("raise RuntimeError('expected import failure')\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(report['errors']), 1)
        self.assertEqual(report['status'], 'failed')

    def test_plan_only_does_not_run_tests_or_claim_completion(self):
        result, report = self.invoke('''import unittest
class Example(unittest.TestCase):
    def test_a(self): self.fail('must not run in planning mode')
''', '--plan-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['status'], 'planned')
        self.assertFalse(report['completed'])
        self.assertNotIn('tests_run', report)

    def test_empty_discovery_and_invalid_selection_do_not_pass(self):
        for extra in ((), ('--shard', '0'), ('--shard', '2')):
            result, report = self.invoke('', *extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(report)
