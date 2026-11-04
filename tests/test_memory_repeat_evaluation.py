"""First-candidate metrics and integrity contracts; no paid model or candidate imports."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory_repeat_evaluation as evaluation
from memory_repeat_cases import CASES, KIND, PRESERVATION, feature_tests, fixture
from memory_repeat_judge import names, run_probe, snapshot, verified
from repo_maintainer.models import digest

def receipt(case='nested_group', bad=False, errors=False):
    groups = {}
    for group, text in (('FEATURE', feature_tests(case)), ('PRESERVATION', PRESERVATION)):
        expected = names(text)
        failed = expected if errors else ['test_unique_declarations'] if bad and group == 'PRESERVATION' else []
        groups[group] = {'success': [n for n in expected if n not in failed], 'failure': failed,
                         'errors': failed if errors else [], 'output': 'synthetic contract receipt'}
    return {'kind': KIND, 'case': case, 'source_sha256': digest(fixture(case, 'bad' if bad else 'good').encode()),
            'tests_status': groups, 'repeat_error': bad and not errors, 'model_requests': 0,
            'official_score': False, 'passed': not bad and not errors}

class ReceiptTests(unittest.TestCase):
    def test_counts_identity_and_actual_exit_must_agree(self):
        value = receipt(); self.assertEqual(value, verified(value, 0, 'nested_group', value['source_sha256']))
        for altered, code in (({**value, 'model_requests': True}, 0), ({**value, 'official_score': True}, 0),
                              ({**value, 'source_sha256': 'x'}, 0), (value, 1), (value, False)):
            with self.subTest(value=altered, code=code), self.assertRaises(ValueError):
                verified(altered, code, 'nested_group', value['source_sha256'])

    def test_missing_duplicate_and_forged_error_cases_are_rejected(self):
        for change in (lambda v: v['tests_status']['FEATURE']['success'].pop(),
                       lambda v: v['tests_status']['PRESERVATION']['failure'].append('test_number'),
                       lambda v: v['tests_status']['PRESERVATION']['errors'].append('not_a_test')):
            value = receipt(); change(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                verified(value, 0, 'nested_group', value['source_sha256'])

    def test_import_failure_is_not_observed_avoidance_or_repetition(self):
        value = receipt(errors=True)
        verified(value, 1, 'nested_group', value['source_sha256'])
        record = self.record([value]); score = evaluation.score_run(record)
        self.assertIsNone(score['first_repeat_error']); self.assertFalse(score['final_all_checks_passed'])

    def record(self, values):
        return {'source_unchanged': True, 'rounds': [{'plan': {'edits': [{'content': fixture('nested_group', 'bad' if value['repeat_error'] else 'good')}]},
            'tests': [{'timed_out': False, 'returncode': 0 if value['passed'] else 1, 'stdout': json.dumps(value)}],
            'provider_evidence': {'memory_citations': []}} for value in values]}

    def test_final_success_cannot_overwrite_first_candidate_failure(self):
        score = evaluation.score_run(self.record([receipt(bad=True), receipt()]))
        self.assertTrue(score['first_repeat_error']); self.assertFalse(score['first_all_checks_passed'])
        self.assertTrue(score['final_all_checks_passed']); self.assertEqual(2, score['rounds'])

    def test_missing_candidate_not_scored_as_avoiding_error(self):
        score = evaluation.score_run({'source_unchanged': True, 'rounds': []})
        self.assertFalse(score['first_candidate_available']); self.assertIsNone(score['first_repeat_error'])
        self.assertFalse(score['final_all_checks_passed'])

    def test_timeout_changed_source_or_candidate_hash_mismatch_are_inconclusive(self):
        for change in (lambda r: r.update(source_unchanged=False),
                       lambda r: r['rounds'][0]['tests'][0].update(timed_out=True),
                       lambda r: r['rounds'][0]['plan']['edits'][0].update(content='different candidate')):
            record = self.record([receipt()]); change(record)
            with self.subTest(record=record), self.assertRaises((RuntimeError, ValueError)):
                evaluation.score_run(record)

    def test_foreign_image_stops_before_any_docker_import_or_write(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {'docker': None}):
            with self.assertRaises(ValueError):
                run_probe(b'invalid', 'nested_group', {'digest': 'foreign', 'image_id': 'foreign'}, Path(temp))
            self.assertFalse(list(Path(temp).iterdir()))

class ScopeTests(unittest.TestCase):
    def test_candidate_wrong_base_readonly_and_untracked_scope_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); source = root / 'cells/owned/source'; source.mkdir(parents=True)
            (source / 'printer.py').write_bytes(fixture('nested_group').encode())
            (source / 'test_feature.py').write_bytes(feature_tests('nested_group').encode())
            evaluation.git(source, 'init', '-b', 'fixture'); evaluation.git(source, 'add', '.')
            evaluation.git(source, 'commit', '-m', 'owned contract fixture')
            base = evaluation.git(source, 'rev-parse', 'HEAD').decode().strip()
            manifest = {'kind': KIND, 'root': str(root), 'cells': [{'case': 'nested_group', 'source': str(source), 'base_sha': base}]}
            (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            snapshot(root, 'nested_group', source)
            (source / 'outside.py').write_text('unexpected', encoding='utf-8')
            with self.assertRaises(ValueError): snapshot(root, 'nested_group', source)
            (source / 'outside.py').unlink()
            (source / 'test_feature.py').write_text('changed', encoding='utf-8')
            with self.assertRaises(ValueError): snapshot(root, 'nested_group', source)
            (source / 'test_feature.py').write_bytes(feature_tests('nested_group').encode())
            manifest['cells'][0]['base_sha'] = '0' * 40
            (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaises(ValueError): snapshot(root, 'nested_group', source)

    def test_original_mimo_failure_is_projected_without_importing_old_permissions(self):
        notes, proof = evaluation.historical_notes()
        text = notes[evaluation.RELATED]
        self.assertIn('Round 1: failed', text); self.assertIn('Round 2: passed', text)
        self.assertIn('test_python_basic', text)
        self.assertFalse(proof[evaluation.RELATED]['native_episode_imported'])
        self.assertLess(len(text.encode()), 6000)

    def test_paid_stage_requires_new_authorization_before_session(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(evaluation, 'checked', return_value={'cells': []}), \
             patch.object(evaluation, 'open_session', side_effect=AssertionError('no model')):
            root = Path(temp)
            with self.assertRaises(FileNotFoundError): evaluation.run(root, 'fixed')
            (root / 'paid-authorization.json').write_text('{"approved":false}', encoding='utf-8')
            with self.assertRaises(RuntimeError): evaluation.run(root, 'fixed')

    def test_old_intent_cannot_be_replayed_or_old_state_reset(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(evaluation, 'checked', return_value={'cells': []}), \
             patch.object(evaluation, 'open_session', side_effect=AssertionError('no model')):
            root = Path(temp)
            (root / 'paid-authorization.json').write_text(json.dumps({'approved': True, 'manifest_sha256': 'fixed', 'request_ceiling': 225}), encoding='utf-8')
            (root / 'run-intent.json').write_bytes(b'original intent')
            with self.assertRaises(RuntimeError): evaluation.run(root, 'fixed')
            self.assertEqual(b'original intent', (root / 'run-intent.json').read_bytes())

    def test_existing_cell_state_blocks_before_config_read_and_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(evaluation, 'checked', return_value={'cells': [{'id': 'one'}]}), \
             patch.object(evaluation, 'open_session', side_effect=AssertionError('no model')):
            root = Path(temp); state = root / 'cells/one/state'; state.mkdir(parents=True)
            (state / 'original-journal.bin').write_bytes(b'preserve existing unknown consumption')
            (root / 'paid-authorization.json').write_text(json.dumps({'approved': True, 'manifest_sha256': 'fixed', 'request_ceiling': 225}), encoding='utf-8')
            with self.assertRaises(RuntimeError): evaluation.run(root, 'fixed')
            self.assertFalse((root / 'run-intent.json').exists())
            self.assertEqual(b'preserve existing unknown consumption', (state / 'original-journal.bin').read_bytes())

    def test_bad_confirmation_or_code_drift_rejected_before_model_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); value = {'kind': KIND, 'root': str(root), 'code': {'original': 'a' * 64}}
            (root / 'manifest.json').write_text(json.dumps(value), encoding='utf-8')
            with patch.object(evaluation, 'code_hashes', return_value={'changed': 'b' * 64}):
                with self.assertRaises(RuntimeError): evaluation.checked(root, evaluation.fingerprint(value))
                with self.assertRaises(RuntimeError): evaluation.checked(root, None)

if __name__ == '__main__':
    unittest.main()
