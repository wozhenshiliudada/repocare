import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
spec = importlib.util.spec_from_file_location('cl_evaluation', Path(__file__).resolve().parents[1] / 'scripts/swebench_cl_evaluation.py')
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)


class Registry:
    def __init__(self, entry): self.entries = {'prior': entry}
    def load(self, kind):
        assert kind == cl.KIND
        return self.entries


class CLConditionTests(unittest.TestCase):
    def setUp(self):
        self.entry = {'review': 'accepted', 'review_version': 2, 'payload_sha256': 'sha',
                      'payload': {'source_files': {'code.py': 'old'}, 'issue': 'prior issue'}}
        self.current = {'code.py': 'new'}
        self.validated = []
        def validate(identity, entry, applicable):
            self.validated.append((identity, applicable))
            return entry['payload']
        self.search = lambda issue: {'hits': [], 'retrieval': {'name': 'actual-search'}}
        experience = SimpleNamespace(search=self.search, validate=validate, files=lambda: self.current)
        self.actions = []
        self.session = SimpleNamespace(registry=Registry(self.entry), experience=experience,
            state=lambda: {'experience_memory': {'settings_revision': 'revision'}},
            _operate=lambda action, body: self.actions.append((action, body)),
            failure_feedback=object(), task_planner=object())

    def test_manual_selection_revalidates_current_policy_and_marks_changed_source(self):
        row = cl.selected_history(self.session, 'prior', self.entry)
        self.assertEqual(self.validated, [('prior', True)])
        self.assertEqual(row['hits'][0]['applicability'], 'requires_revalidation')
        self.assertFalse(row['runtime_authorized'])

    def test_mutated_historical_entry_fails_closed(self):
        pinned = copy.deepcopy(self.entry)
        self.session.registry.entries['prior']['payload']['issue'] = 'changed'
        with self.assertRaisesRegex(RuntimeError, 'Frozen history differs'):
            cl.selected_history(self.session, 'prior', pinned)

    def test_target_results_cannot_enter_accepted_pool(self):
        self.session.registry.entries['target'] = copy.deepcopy(self.entry)
        with self.assertRaisesRegex(RuntimeError, 'Historical pool changed'):
            with cl.condition(self.session, cl.ARMS[2], 'prior', self.entry): pass

    def test_baseline_keeps_normal_feedback_and_task_session(self):
        old = (self.session.failure_feedback, self.session.task_planner)
        with cl.condition(self.session, cl.ARMS[0], 'prior', self.entry):
            self.assertEqual(old, (self.session.failure_feedback, self.session.task_planner))
            self.assertIs(self.session.experience.search, self.search)
        self.assertFalse(self.actions[0][1]['enabled'])

    def test_manual_override_is_restored_even_after_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'test interruption'):
            with cl.condition(self.session, cl.ARMS[1], 'prior', self.entry):
                self.assertEqual(self.session.experience.search('target')['hits'][0]['id'], 'prior')
                raise RuntimeError('test interruption')
        self.assertIs(self.session.experience.search, self.search)

    def test_automatic_condition_really_uses_existing_search(self):
        with cl.condition(self.session, cl.ARMS[2], 'prior', self.entry):
            self.assertIs(self.session.experience.search, self.search)
            self.assertEqual(self.session.experience.search('target')['retrieval']['name'], 'actual-search')
        self.assertTrue(self.actions[0][1]['enabled'])


if __name__ == '__main__': unittest.main()
