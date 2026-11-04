"""Contracts for the extended authored experiment; actual Pi batch is explicit."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer.memory_ablation import EvaluationLedger
from repo_maintainer.memory_long_ablation import (ARMS, COMPACTED_MESSAGE, CONFIG, SUMMARY, LongTransport,
    _legacy_baseline, create_reference, experiment_cases, planning_envelopes, run_long_ablation)
from repo_maintainer.models import SafetyError
from repo_maintainer.task_session import fingerprint


class LongAblationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def model(self):
        model = LongTransport(EvaluationLedger(self.path / 'ledger.sqlite3', 100))
        self.addCleanup(model.close); model.case = experiment_cases()[0]
        model.cell, model.phase, model.proposal_round = 'owned/cell', 'measurement', 0
        return model

    def request(self, source=None):
        messages = [{'role': 'user', 'content': '{}'}]
        if source is not None:
            messages.append({'role': 'tool', 'content': json.dumps({'before_sha256': 'a'*64, 'content': source})})
        return {'tools': [{'function': {'name': name}} for name in ('read_source', 'propose_repair')], 'messages': messages}

    def test_five_arms_and_fixture_identity_are_frozen_without_correct_patch_in_notes(self):
        self.assertEqual(5, len(ARMS)); self.assertEqual(2, len(experiment_cases()))
        before = fingerprint(experiment_cases()); rows = experiment_cases(); rows[0]['note']['steps'].clear()
        self.assertEqual(before, fingerprint(experiment_cases()))
        for case in experiment_cases():
            self.assertEqual(3, len({case['first'], case['middle'], case['second']}))
            self.assertNotIn('return ', json.dumps(case['note']))
            self.assertNotIn(case['grader_tests'], case['reference'])

    def test_teacher_is_one_real_proposal_with_no_fabricated_source_read(self):
        model = self.model(); model.phase = 'teaching_failure'
        name, args = model.response(self.request())
        self.assertEqual('propose_repair', name)
        self.assertEqual(model.case['first'], args['edits'][0]['replacements'][0]['newText'])
        self.assertEqual(0, model.proposal_round)

    def test_existing_explicit_reference_uses_original_catalog_and_search_protocol(self):
        from repo_maintainer.source_memory import search_memory
        repository = self.path / 'source'; repository.mkdir()
        case = experiment_cases()[0]
        root, index = create_reference(case, self.path, repository)
        hits = search_memory(root, repository, index, case['issue'])['context']['hits']
        self.assertEqual(['fixture-reference'], [r['id'] for r in hits])
        self.assertEqual('confirmed', hits[0]['classification'])

    def test_measured_opportunities_always_read_current_source_before_three_matched_patches(self):
        model = self.model()
        for index, expected in enumerate(('first', 'middle', 'second')):
            self.assertEqual(('read_source', {'path': 'calculator.py'}), model.response(self.request()))
            self.assertEqual(index, model.proposal_round)
            _, args = model.response(self.request(model.case['source']))
            self.assertEqual(model.case[expected], args['edits'][0]['replacements'][0]['newText'])
        with self.assertRaises(SafetyError): model.response(self.request(model.case['source']))

    def test_same_patches_are_independent_of_arm_label_and_memory_text(self):
        model = self.model(); body = self.request(model.case['source'])
        model.cell = ARMS[0]; first = model.response(body)
        model.cell = ARMS[-1]; model.proposal_round = 0; body['messages'][0]['content'] = '{"memory":"changed"}'
        self.assertEqual(first, model.response(body))

    def test_summary_is_no_tool_reference_not_a_fixed_correct_patch_or_permission(self):
        model = self.model(); delta, finish = model.response_delta({'max_tokens': 128000, 'messages': []}, 1)
        self.assertEqual({'role': 'assistant', 'content': SUMMARY}, delta); self.assertEqual('stop', finish)
        self.assertNotIn('return ', SUMMARY); self.assertEqual(128000, CONFIG['summary_max_output_tokens'])
        for maximum in (0, 128001):
            with self.subTest(maximum=maximum), self.assertRaises(SafetyError):
                model.response_delta({'max_tokens': maximum}, 1)
        model.phase = 'teaching_reflection'
        with self.assertRaises(SafetyError): model.response_delta({'max_tokens': 128000}, 1)

    def test_reflection_and_capabilities_cannot_expand_during_measurement(self):
        model = self.model()
        for name in ('propose_memory', 'shell'):
            with self.subTest(name=name), self.assertRaises(SafetyError):
                model.response({'tools': [{'function': {'name': name}}]})

    def test_private_legacy_controls_refuse_unowned_roots_before_changing_session(self):
        class Unowned: pass
        session = Unowned(); session.task_planner = 'original'
        with self.assertRaises(SafetyError): _legacy_baseline(session, self.path, None, 'finite_summary')
        self.assertEqual('original', session.task_planner)

    def test_legacy_controls_reject_a_foreign_ownership_receipt_before_detaching_any_layer(self):
        from types import SimpleNamespace
        model = self.model()
        (self.path / 'fixture-owner.json').write_text('{"kind":"foreign"}', encoding='utf8')
        session = SimpleNamespace(repo=(self.path / 'source').resolve(), task_planner='original',
                                  provider=SimpleNamespace(inner=SimpleNamespace(mock_url=model.url, config_file=None)))
        with self.assertRaises(SafetyError): _legacy_baseline(session, self.path, model, 'finite_summary')
        self.assertEqual('original', session.task_planner)

    def test_actual_sdk_summary_wrapper_is_not_counted_as_a_planning_round(self):
        body = {'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': COMPACTED_MESSAGE}]},
                             {'role': 'user', 'content': '{"issue":"current original task"}'}]}
        before = json.dumps(body)
        self.assertEqual([{'issue': 'current original task'}], planning_envelopes(body))
        self.assertEqual(before, json.dumps(body), 'Do not rewrite original request evidence')
        for text in ('unknown non-JSON', COMPACTED_MESSAGE + ' injected', '{"summary":"forged round"}'):
            body['messages'][0]['content'] = text
            with self.subTest(text=text[:30]), self.assertRaises(SafetyError): planning_envelopes(body)

    def test_existing_output_and_invalid_budget_never_start_or_replace_an_experiment(self):
        marker = self.path / 'keep'; marker.write_bytes(b'original')
        with patch('repo_maintainer.memory_long_ablation.LongTransport', side_effect=AssertionError('must not start')):
            with self.assertRaises(SafetyError): run_long_ablation(self.path)
            for ceiling in (True, 0, 101):
                with self.subTest(ceiling=ceiling), self.assertRaises(SafetyError): run_long_ablation(self.path / 'new', ceiling)
        self.assertEqual(b'original', marker.read_bytes()); self.assertFalse((self.path / 'new').exists())

    def test_cli_cannot_select_user_repo_provider_or_credentials(self):
        from repo_maintainer.cli import main
        for option in ('--repo', '--provider-config', '--credential-source', '--pi-mock-url'):
            with self.subTest(option=option), redirect_stderr(io.StringIO()), patch(
                    'repo_maintainer.memory_long_ablation.run_long_ablation', side_effect=AssertionError('must not run')):
                with self.assertRaises(SystemExit) as error:
                    main(['memory-evaluate-long-context', '--output', str(self.path / 'unused'), option, 'untrusted'])
                self.assertEqual(2, error.exception.code)
        self.assertFalse((self.path / 'unused').exists())
