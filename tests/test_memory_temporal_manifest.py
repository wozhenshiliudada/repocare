"""Confirmations bind code/cases; no preparatory credential or model access."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory_temporal_evaluation as evaluation
from repo_maintainer.models import SafetyError
from repo_maintainer.runtime_limits import MAX_OUTPUT_TOKENS, PLANNING_TIMEOUT_SECONDS


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'new'
        self.config = evaluation.PROJECT / '.env.mimo.local'

    def prepared(self):
        # Neither credentials nor the application provider is loaded here.
        with patch.object(Path, 'is_file', return_value=True), \
             patch('repo_maintainer.provider_config.load_provider_config', side_effect=AssertionError('no credential read')), \
             patch.object(evaluation, 'code_hashes', return_value={'fixed': 'a'*64}), \
             patch.object(evaluation, 'open_session', side_effect=AssertionError('no model/session')):
            return evaluation.prepare(self.root, self.config)

    def test_prepare_has_no_model_or_credential_read_and_allocates_one_original_ceiling(self):
        receipt = self.prepared()
        self.assertEqual(0, receipt['paid_requests']); self.assertFalse(receipt['credentials_read'])
        manifest = json.loads((self.root / 'manifest.json').read_bytes())
        self.assertEqual(100, manifest['history_ceiling'] + len(manifest['arms']) * manifest['per_arm_ceiling'])
        self.assertEqual((MAX_OUTPUT_TOKENS, PLANNING_TIMEOUT_SECONDS),
                         (manifest['max_output_tokens'], manifest['planning_timeout_seconds']))
        self.assertEqual(5, len(manifest['arms']))

    def test_smaller_new_fixture_budget_is_frozen_without_changing_product_limits(self):
        with patch.object(evaluation, 'code_hashes', return_value={'fixed': 'a'*64}):
            manifest = evaluation.specification(self.config, 80)
        self.assertEqual((80,20,12), tuple(manifest[k] for k in
                         ('original_repository_request_ceiling','history_ceiling','per_arm_ceiling')))
        self.assertEqual(80,manifest['history_ceiling']+len(manifest['arms'])*manifest['per_arm_ceiling'])
        self.assertEqual((MAX_OUTPUT_TOKENS, PLANNING_TIMEOUT_SECONDS),
                         (manifest['max_output_tokens'],manifest['planning_timeout_seconds']))
        for ceiling in (True,0,79,81,101):
            with self.subTest(ceiling=ceiling),self.assertRaises(SafetyError):evaluation.specification(self.config,ceiling)

    def test_runtime_receives_the_new_fixture_ceiling_not_an_existing_journals_budget(self):
        with patch.object(evaluation,'PiProvider') as provider,patch.object(evaluation,'UISession') as session:
            evaluation.open_session(self.root, {'provider_config':str(self.config),'original_repository_request_ceiling':80})
        self.assertEqual(80,provider.call_args.kwargs['max_requests'])
        self.assertEqual(self.root/'state',session.call_args.args[3])

    def test_code_or_manifest_change_invalidates_confirmation(self):
        receipt = self.prepared()
        with patch.object(evaluation, 'code_hashes', return_value={'fixed': 'a'*64}):
            evaluation.checked_manifest(self.root, receipt['manifest_sha256'])
            with self.assertRaises(SafetyError): evaluation.checked_manifest(self.root, None)
        with patch.object(evaluation, 'code_hashes', return_value={'changed': 'b'*64}):
            with self.assertRaises(SafetyError): evaluation.checked_manifest(self.root, receipt['manifest_sha256'])
        path = self.root / 'manifest.json'
        value = json.loads(path.read_bytes()); value['per_arm_ceiling'] = 100
        path.write_text(json.dumps(value), encoding='utf8')
        with self.assertRaises(SafetyError): evaluation.checked_manifest(self.root, receipt['manifest_sha256'])

    def test_foreign_config_and_existing_output_are_rejected_without_overwrite(self):
        with patch.object(Path, 'is_file', return_value=True):
            with self.assertRaises(SafetyError): evaluation.prepare(self.root, Path(self.temp.name) / '.env.mimo.local')
        self.prepared(); before = (self.root / 'manifest.json').read_bytes()
        with self.assertRaises(SafetyError): evaluation.prepare(self.root, self.config)
        self.assertEqual(before, (self.root / 'manifest.json').read_bytes())

    def test_measure_requires_review_before_opening_a_provider(self):
        self.root.mkdir()
        (self.root / 'history-ready.json').write_text('{"knowledge_review_sha256":"reviewed-original"}', encoding='utf8')
        with patch.object(evaluation, 'open_session', side_effect=AssertionError('must not open')):
            with self.assertRaises(SafetyError): evaluation.measure(self.root, {}, 'other')
            (self.root / 'measurement-intent.json').write_text('{}', encoding='utf8')
            with self.assertRaises(SafetyError): evaluation.measure(self.root, {}, 'reviewed-original')

    def test_synthetic_preflight_retains_existing_evidence_before_starting_transport(self):
        import check_memory_temporal as check
        self.root.mkdir(); (self.root / 'keep').write_bytes(b'previous evidence')
        with patch.object(check, 'TemporalTransport', side_effect=AssertionError('must not start')):
            with self.assertRaises(SafetyError): check.preflight(self.root)
        self.assertEqual(b'previous evidence', (self.root / 'keep').read_bytes())

    def test_synthetic_three_trials_cannot_return_to_the_original_target_as_a_noop(self):
        from types import SimpleNamespace
        from check_memory_temporal import TemporalTransport
        model = SimpleNamespace(phase='measurement', proposal_round=0)
        original = evaluation.TARGET_SOURCE
        before = original
        patches = []
        for _ in range(3):
            body = {'tools': [{'function': {'name': name}} for name in ('read_source', 'propose_repair')],
                    'messages': [{'role': 'user'}, {'role': 'tool',
                    'content': json.dumps({'before_sha256': 'a'*64, 'content': before})}]}
            _, candidate = TemporalTransport.response(model, body)
            replacement = candidate['edits'][0]['replacements'][0]['newText']
            patches.append(replacement)
            before = 'def add(a, b):\n    ' + replacement + '\n'
            self.assertNotEqual(original, before, 'Original controller must be able to test each distinct candidate')
        self.assertEqual(3, len(set(patches)))
