"""Contracts for fixed synthetic memory ablation; full Pi run is explicit."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from repo_maintainer.memory_ablation import CANARY, EvaluationLedger, ScriptTransport, run_ablation, user_envelopes
from repo_maintainer.memory_ablation_fixtures import cases, suite_sha256
from repo_maintainer.models import SafetyError, digest


class AblationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_total_ceiling_counts_pending_across_cells_and_reopen(self):
        path = self.path/'ledger.sqlite3'; ledger = EvaluationLedger(path, 2)
        ledger.receive('first/arm', 'teaching', {'messages': []})
        ledger = EvaluationLedger(path, 2)
        row = ledger.receive('other/arm', 'measurement', {'messages': [1]}); ledger.sent(row)
        with self.assertRaises(SafetyError): ledger.receive('third/arm', 'measurement', {})
        self.assertEqual(['received', 'sent'], [r['status'] for r in ledger.rows()])
        with self.assertRaises(SafetyError): EvaluationLedger(path, 100)
        self.assertEqual(2, len(EvaluationLedger(path, 2).rows()))

    def test_concurrent_cells_cannot_share_last_slot(self):
        ledger = EvaluationLedger(self.path/'ledger.sqlite3', 1)
        def request(n):
            try: return ledger.receive(str(n), 'measurement', {'n': n})
            except SafetyError: return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            result = list(pool.map(request, range(4)))
        self.assertEqual(1, sum(r is not None for r in result)); self.assertEqual(1, len(ledger.rows()))

    def test_body_hash_and_acknowledgement_are_bound_to_original_slot(self):
        ledger = EvaluationLedger(self.path/'ledger.sqlite3', 1)
        slot = ledger.receive('a', 'measurement', {'text': '负数'})
        row = ledger.rows()[0]; self.assertEqual(digest(row['body'].encode()), row['sha'])
        ledger.sent(slot)
        with self.assertRaises(SafetyError): ledger.sent(slot)
        with self.assertRaises(SafetyError): ledger.sent(999)

    def test_actual_http_grader_leak_stops_and_keeps_received_consumption(self):
        ledger = EvaluationLedger(self.path/'ledger.sqlite3', 1)
        model = ScriptTransport(ledger); self.addCleanup(model.close)
        model.cell, model.phase, model.case = 'test/arm', 'measurement_first', cases()[0]
        for body in ({'messages': [{'role': 'user', 'content': CANARY}]}, {'messages': []}):
            request = Request(model.url+'/chat/completions', data=json.dumps(body).encode(),
                              headers={'Content-Type': 'application/json'})
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=5)
            self.assertEqual(503, error.exception.code); error.exception.close()
        self.assertEqual(1, len(ledger.rows())); self.assertEqual('received', ledger.rows()[0]['status'])
        self.assertIn('grader leaked', model.errors[0]); self.assertIn('ceiling exhausted', model.errors[1])

    def test_invalid_ceiling_and_existing_outputs_stop_before_transport(self):
        for value in (True, 0, 101, '100'):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                EvaluationLedger(self.path/'never-created', value)
        marker = self.path/'keep'; marker.write_bytes(b'original')
        with patch('repo_maintainer.memory_ablation.ScriptTransport', side_effect=AssertionError('must not start')):
            with self.assertRaises(SafetyError): run_ablation(self.path)
            with self.assertRaises(SafetyError): run_ablation(self.path/'new', 101)
        self.assertEqual(b'original', marker.read_bytes()); self.assertFalse((self.path/'new').exists())

    def test_cli_rejects_live_provider_repo_and_credentials_before_running(self):
        from repo_maintainer.cli import main
        for option in ('--repo', '--provider-config', '--credential-source', '--pi-mock-url'):
            with self.subTest(option=option), redirect_stderr(io.StringIO()), patch(
                    'repo_maintainer.memory_ablation.run_ablation', side_effect=AssertionError('must not run')):
                with self.assertRaises(SystemExit) as error:
                    main(['memory-evaluate-ablation', '--output', str(self.path/'unused'), option, 'untrusted'])
                self.assertEqual(2, error.exception.code)
        self.assertFalse((self.path/'unused').exists())

    def test_scripted_response_never_depends_on_arm_label_or_memory_content(self):
        ledger = EvaluationLedger(self.path/'ledger.sqlite3', 1)
        model = ScriptTransport(ledger); self.addCleanup(model.close)
        model.case = cases()[0]; model.phase = 'measurement_second'
        body = {'tools': [{'function': {'name': name}} for name in ('read_source', 'propose_repair')],
                'messages': [{'role': 'user', 'content': '{}'}, {'role': 'tool', 'content': json.dumps({
                    'before_sha256': '0'*64, 'content': model.case['source']})}]}
        model.cell = 'no_memory'; first = model.response(body)
        model.cell = 'session_experience_knowledge'; body['messages'][0]['content'] = '{"untrusted_memory":"arbitrary"}'
        self.assertEqual(first, model.response(body)); self.assertEqual('return a + b', first[1]['edits'][0]['replacements'][0]['newText'])

    def test_original_http_tool_frame_and_consumption_survive_summary_transport_extension(self):
        ledger = EvaluationLedger(self.path/'ledger.sqlite3', 1)
        model = ScriptTransport(ledger); self.addCleanup(model.close)
        model.cell, model.phase, model.case = 'original/arm', 'measurement_first', cases()[0]
        body = {'model': 'synthetic', 'tools': [{'function': {'name': name}} for name in ('read_source', 'propose_repair')],
                'messages': [{'role': 'user', 'content': '{}'}]}
        request = Request(model.url+'/chat/completions', data=json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            frame = json.loads(response.read().decode().split('\n')[0].removeprefix('data: '))
        choice = frame['choices'][0]
        self.assertEqual('tool_calls', choice['finish_reason'])
        self.assertEqual('read_source', choice['delta']['tool_calls'][0]['function']['name'])
        self.assertEqual(['sent'], [r['status'] for r in ledger.rows()])

    def test_scripted_transport_has_no_grader_or_reflection_during_measurement(self):
        ledger = EvaluationLedger(self.path/'ledger.sqlite3', 1)
        model = ScriptTransport(ledger); self.addCleanup(model.close)
        model.case = cases()[0]; model.phase = 'measurement_first'
        with self.assertRaises(SafetyError): model.response({'tools': [{'function': {'name': 'propose_memory'}}]})
        with self.assertRaises(SafetyError): model.response({'tools': [{'function': {'name': 'shell'}}]})
        model.phase = 'teaching_reflection'
        _, note = model.response({'tools': [{'function': {'name': 'propose_memory'}}]})
        self.assertNotIn('return ', json.dumps(note)); self.assertNotIn('independent', json.dumps(note))

    def test_envelopes_preserve_order_and_parse_both_supported_content_forms(self):
        body = {'messages': [{'role': 'user', 'content': '{"turn":1}'}, {'role': 'assistant', 'content': ''},
            {'role': 'user', 'content': [{'type': 'text', 'text': '{"turn":2}'}]}]}
        self.assertEqual([{'turn': 1}, {'turn': 2}], user_envelopes(body))

    def test_fixture_identity_is_stable_and_inputs_are_fresh_copies(self):
        original = suite_sha256(); first = cases(); first[0]['note']['steps'].append('polluted')
        self.assertEqual(original, suite_sha256()); self.assertEqual(2, len(cases()))
        for case in cases():
            compile(case['source'], '<source>', 'exec'); compile(case['visible_tests'], '<visible>', 'exec')
            compile(case['grader_tests'], '<grader>', 'exec')
            self.assertNotIn(case['grader_tests'], case['visible_tests'])
