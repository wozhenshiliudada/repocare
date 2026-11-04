import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('cl_prepare', Path(__file__).resolve().parents[1] / 'scripts/prepare_swebench_cl.py')
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)


def fixture(identifier, date):
    return {'metadata': {'instance_id': identifier, 'repo': 'example/repo', 'base_commit': 'a'*40,
                         'created_at': date},
            'task': {'problem_statement': 'Repair the documented behavior', 'hints_text': 'HINT_CANARY'},
            'evaluation': {'patch': 'GOLD_CANARY', 'test_patch': 'TEST_CANARY'},
            'continual_learning': {'dependencies': ['DEPENDENCY_CANARY'], 'modified_files': ['PATH_CANARY']}}


class CLPreparationTests(unittest.TestCase):
    def setUp(self):
        self.history = fixture('example__repo-1', '2020-01-01T00:00:00+00:00')
        self.target = fixture('example__repo-2', '2020-02-01T00:00:00+00:00')

    def test_gold_and_nested_metadata_are_not_agent_input(self):
        self.target['task']['new_future_field'] = 'NESTED_CANARY'
        blob = json.dumps(cl.agent_task(self.target))
        self.assertEqual(set(json.loads(blob)), {'instance_id', 'repo', 'base_commit', 'problem_statement'})
        self.assertNotIn('CANARY', blob)

    def test_time_cutoff_uses_dates_not_sequence_positions(self):
        self.history['metadata']['created_at'] = '2020-03-01T00:00:00+00:00'
        self.history['continual_learning']['sequence_position'] = 1
        self.target['continual_learning']['sequence_position'] = 2
        with self.assertRaisesRegex(ValueError, 'strictly earlier'):
            cl.validate_pair(self.history, self.target)

    def test_other_repository_and_same_task_rejected(self):
        self.target['metadata']['repo'] = 'other/repo'
        with self.assertRaises(ValueError): cl.validate_pair(self.history, self.target)
        self.target = self.history
        with self.assertRaises(ValueError): cl.validate_pair(self.history, self.target)

    def test_long_issue_is_rejected_instead_of_silently_truncated(self):
        self.target['task']['problem_statement'] = 'x'*2001
        with self.assertRaisesRegex(ValueError, 'do not truncate'):
            cl.validate_pair(self.history, self.target)

    def test_duplicate_task_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            cl.tasks({'sequences': [{'repo': 'example/repo', 'tasks': [self.history, self.history]}]})

    def test_cross_source_mismatch_and_existing_output_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = {'sequences': [{'repo': 'example/repo', 'tasks': [self.history, self.target]}]}
            official = [cl.agent_task(row) | row['evaluation'] | {'FAIL_TO_PASS': ['new_test'], 'PASS_TO_PASS': ['old_test']}
                        for row in (self.history, self.target)]
            (root/'cl.json').write_text(json.dumps(source))
            (root/'official.json').write_text(json.dumps(official))
            arguments = (root/'cl.json', root/'official.json', root/'out', 'example__repo-1', 'example__repo-2')
            result = cl.prepare(*arguments)
            self.assertEqual(result['paid_requests'], 0)
            self.assertNotIn('CANARY', (root/'out/agent/tasks.json').read_text())
            with self.assertRaisesRegex(ValueError, 'exists'): cl.prepare(*arguments)
            official[1]['patch'] = 'CHANGED'
            (root/'official.json').write_text(json.dumps(official))
            with self.assertRaisesRegex(ValueError, 'judge patch differs'):
                cl.prepare(*arguments[:2], root/'other', *arguments[3:])
            self.assertFalse((root/'other').exists())


if __name__ == '__main__': unittest.main()
