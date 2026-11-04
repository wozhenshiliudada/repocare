"""Failed terminal diagnostics must not confer authority or release consumption."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer.models import SafetyError
from repo_maintainer.pi_auxiliary import terminal_failure_evidence
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.ui_registry import DurableProvider, UIRegistry

SHA = 'a' * 64


def envelope(purpose='memory_reflection'):
    field = 'reflection_context_sha256' if purpose == 'memory_reflection' else 'compaction_context_sha256'
    return {'protocol_version': 1, 'status': 'failed', 'evidence': {
        'schema_version': 1, 'status': 'failed', 'failure_category': 'provider_error',
        'purpose': purpose, field: SHA, 'active_tools': ['propose_memory'] if purpose == 'memory_reflection' else [],
        'requests': None, 'requests_started': 1, 'count_scope': 'adapter_stream_invocations',
        'consumption': 'unknown', 'tool_calls': 0, 'events': [{'type': 'agent_end'}],
        'usage': [{'input': 20, 'output': 4}], 'events_complete': True,
        'events_dropped': 0, 'model_cost_verified': False}}


class AuxiliaryFailureTests(unittest.TestCase):
    def test_only_original_purpose_and_context_can_supply_terminal_diagnostics(self):
        for purpose in ('memory_reflection', 'task_compaction'):
            data = envelope(purpose)
            self.assertEqual(data['evidence'], terminal_failure_evidence(json.dumps(data).encode(), purpose, SHA, 2))
            self.assertIsNone(terminal_failure_evidence(json.dumps(data).encode(), purpose, 'b'*64, 2))
            other = 'task_compaction' if purpose == 'memory_reflection' else 'memory_reflection'
            self.assertIsNone(terminal_failure_evidence(json.dumps(data).encode(), other, SHA, 2))

    def test_forged_count_extra_private_fields_and_unfinished_progress_are_rejected(self):
        variants = []
        for mutate in (lambda d: d.update(protocol_version=True),
                       lambda d: d.update(candidate={'title': 'not executable'}),
                       lambda d: d['evidence'].update(requests_started=3),
                       lambda d: d['evidence'].update(events_complete=False),
                       lambda d: d['evidence'].update(error='PRIVATE_PROVIDER_TEXT'),
                       lambda d: d['evidence'].update(status='completed', consumption='reported', requests=1)):
            data = deepcopy(envelope()); mutate(data); variants.append(json.dumps(data).encode())
        for raw in (*variants, b'not json', b'x'*200001, None):
            with self.subTest(raw=type(raw).__name__):
                self.assertIsNone(terminal_failure_evidence(raw, 'memory_reflection', SHA, 2))

    def test_parent_retains_valid_terminal_failure_but_original_reservation_stays_unknown(self):
        class FailedProcess:
            returncode = 1
            def communicate(self, *args, **kwargs):
                return json.dumps(envelope()).encode(), b'Untrusted stderr never becomes evidence\n'
        with tempfile.TemporaryDirectory() as directory:
            registry = UIRegistry(Path(directory)/'state', {'provider': {'request_budget': 100}})
            try:
                provider = PiProvider(('calculator.py',), 'http://127.0.0.1:1/v1', max_requests=100)
                durable = DurableProvider(provider, registry)
                with patch('repo_maintainer.pi_auxiliary.shutil.which', return_value='test-only-node'), \
                     patch('repo_maintainer.pi_auxiliary.subprocess.Popen', return_value=FailedProcess()):
                    with self.assertRaises(SafetyError): durable.reflect({'context_sha256': SHA}, lambda: None)
                attempt = next(iter(registry.load('attempt').values()))
                evidence = attempt['evidence']
                self.assertEqual('unknown', attempt['status'])
                self.assertEqual('provider_error', evidence['failure_category'])
                self.assertTrue(evidence['events_complete']); self.assertEqual(1, evidence['requests_started'])
                self.assertIsNone(evidence['requests']); self.assertEqual('unknown', evidence['consumption'])
                self.assertEqual({'request_budget': 100, 'requests_used': 0,
                                 'requests_reserved_unknown': 100, 'requests_remaining': 0}, registry.budget())
                self.assertEqual(0, provider.requests_used)
                self.assertNotIn('Untrusted stderr', json.dumps(evidence))
            finally:
                registry.close()

    def test_untrusted_or_mismatched_terminal_output_is_not_exposed_by_parent(self):
        data = envelope(); data['private_response'] = 'PRIVATE_PROVIDER_TEXT'
        class FailedProcess:
            returncode = 1
            def communicate(self, *args, **kwargs): return json.dumps(data).encode(), b''
        provider = PiProvider(('calculator.py',), 'http://127.0.0.1:1/v1', max_requests=2)
        with patch('repo_maintainer.pi_auxiliary.shutil.which', return_value='test-only-node'), \
             patch('repo_maintainer.pi_auxiliary.subprocess.Popen', return_value=FailedProcess()):
            with self.assertRaises(SafetyError): provider.reflect({'context_sha256': SHA}, lambda: None)
        self.assertNotIn('PRIVATE_PROVIDER_TEXT', json.dumps(provider.evidence))
        self.assertEqual('unknown', provider.evidence['consumption'])
        self.assertNotIn('events_complete', provider.evidence)
