"""Offline original-ledger regressions; only the Pi process boundary is synthetic.

These exercise controller/registry reopening, not Native checkpoint recovery.
No Node process, HTTP server, credential file or original user journal is used.
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_catalog_fixture import fixture
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.memory_catalog import MemoryCatalog
from repo_maintainer.models import Policy, SafetyError, digest
from repo_maintainer.pi_evidence import CountedPlanningFailure
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.runtime_limits import MAX_REQUESTS
from repo_maintainer.ui_registry import DurableProvider, UIRegistry, provider_binding
from test_pi_evidence import counted_evidence, evidence


class SyntheticPiProcess:
    """A protocol peer, with no subprocess or model transport behind it."""
    stdin = stdout = stderr = None

    def __init__(self, calls, mode, count):
        self.calls, self.mode, self.count = calls, mode, count
        self.returncode = 0 if mode == 'completed' else 1

    def communicate(self, raw, **kwargs):
        request = json.loads(raw)
        self.calls.append(request)
        load = request['memory_load']
        if self.mode == 'counted':
            receipt = counted_evidence()
        else:
            receipt = evidence('completed' if self.mode == 'completed' else 'failed')
        receipt.update(edit_protocol='exact-replacements-v1', transport='loopback-mock',
                       memory_load_sha256=load['context_sha256'], memory_task_type=load['task_type'],
                       maintenance_context_sha256=request['repository_maintenance']['context_sha256'],
                       maintenance_selection_sha256=request['repository_maintenance']['selection_sha256'],
                       source_snapshot_sha256=digest(json.dumps(request['files'], ensure_ascii=False,
                                                               separators=(',', ':')).encode()))
        if self.mode in {'completed', 'counted'}:
            receipt.update(requests=self.count, requests_started=self.count)
        if self.mode == 'counted':
            receipt['terminal_count']['completed_streams'] = self.count
        packet = {'protocol_version': 1, 'status': receipt['status'], 'evidence': receipt}
        if self.mode == 'completed':
            source = request['files'][0]
            old, new = 'a - b', 'a + b'
            packet.update(plan={'issue': request['issue'], 'base_sha': request['base_sha'],
                'diagnosis': 'Synthetic ledger compatibility check', 'risk': 'temporary fixture only',
                'edits': [{'path': source['path'], 'before_sha256': source['before_sha256'],
                           'content': source['content'].replace(old, new)}],
                'tests': request['tests'], 'provider': 'synthetic-process'},
                patches=[{'path': source['path'], 'replacements': [{'oldText': old, 'newText': new}]}])
        return json.dumps(packet).encode(), b''


class MemoryBudgetCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='memory-budget-compat-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = RepositoryGateway(create_fixture(self.root/'repository'))
        self.source_before = self.repo.snapshot()
        self.policy = Policy(('calculator.py',), (DEMO_TEST,))
        self.calls = []
        self.registry = None
        self.addCleanup(lambda: self.registry.close() if self.registry else None)

    def open(self, ceiling, name='journal'):
        if self.registry is not None:
            self.registry.close()
        self.inner = PiProvider(('calculator.py',), 'http://127.0.0.1:1/unused', max_requests=ceiling)
        self.binding = {'repository': str(self.repo.root), 'policy': json.loads(json.dumps(asdict(self.policy))),
                        'provider': provider_binding(self.inner)}
        self.registry = UIRegistry(self.root/name, self.binding)
        session = SimpleNamespace(repo=self.repo.root, policy=self.policy, registry=self.registry,
            budget_registry=self.registry, source_guard=None,
            memory=SimpleNamespace(binding=None), structure=SimpleNamespace(binding=None))
        self.inner._memory_catalog = MemoryCatalog(session)
        maintenance = copy.deepcopy(fixture()['blocks']['maintenance'])
        maintenance['selection_sha256'] = 'f'*64
        self.inner._maintenance_context = lambda *args: maintenance
        self.provider = DurableProvider(self.inner, self.registry)

    def seed_usage_and_unknown(self):
        paid, _ = self.registry.reserve(maximum=7)
        self.registry.settle(paid, {'requests': 7}, success=True)
        unknown, _ = self.registry.reserve(maximum=3)
        self.registry.settle(unknown, {'requests': None}, success=False)
        return self.registry.load('attempt')

    def invoke(self, mode='completed', count=2):
        process = SyntheticPiProcess(self.calls, mode, count)
        # Replace this module's environment binding, without reading or changing
        # the real process environment or affecting Git/other concurrent tests.
        environment = SimpleNamespace(name=os.name, environ={})
        transport = SimpleNamespace(Popen=lambda *args, **kwargs: process, PIPE=subprocess.PIPE,
            CREATE_NEW_PROCESS_GROUP=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
            TimeoutExpired=subprocess.TimeoutExpired)
        with patch('repo_maintainer.pi_provider.os', environment), \
                patch('repo_maintainer.pi_provider.runtime_script', return_value=Path('synthetic-plan.mjs')), \
                patch('repo_maintainer.pi_provider.shutil.which', return_value='synthetic-node'), \
                patch('repo_maintainer.pi_provider.subprocess', transport):
            return self.provider.plan(self.repo, 'Preserve signed addition.', self.policy.allowed_tests)

    def assert_original_attempts(self, originals):
        current = self.registry.load('attempt')
        self.assertEqual(originals, {identity: current[identity] for identity in originals})

    def test_legacy_50_and_100_ledgers_keep_ceiling_used_unknown_and_reject_default_upgrade(self):
        for ceiling in (50, 100):
            with self.subTest(ceiling=ceiling):
                name = 'legacy-'+str(ceiling)
                self.open(ceiling, name)
                originals = self.seed_usage_and_unknown()
                before = self.registry.budget()
                path = self.registry.path
                self.registry.close()
                original_bytes = path.read_bytes()
                changed = {**self.binding, 'provider': {**self.binding['provider'], 'request_budget': MAX_REQUESTS}}
                with self.assertRaisesRegex(SafetyError, 'changed'):
                    UIRegistry(self.root/name, changed)
                self.assertEqual(original_bytes, path.read_bytes())
                self.open(ceiling, name)
                self.assertEqual(before, self.registry.budget())
                calls = len(self.calls)
                self.invoke()
                self.assertEqual(calls+1, len(self.calls))
                self.assertEqual(ceiling-10, self.calls[-1]['request_budget'])
                self.assertEqual(ceiling-10, self.calls[-1]['memory_load']['budget']['request_allowance'])
                self.assert_original_attempts(originals)
                after = self.registry.budget()
                self.assertEqual((ceiling, 9, 3, ceiling-12), tuple(after.values()))
                self.open(ceiling, name)
                self.assertEqual(after, self.registry.budget())
                self.assertEqual(calls+1, len(self.calls))
                self.assert_original_attempts(originals)
        self.assertEqual(self.source_before, self.repo.snapshot())

    def test_large_new_and_reopened_controller_pass_actual_remaining_allowance_and_settle_once(self):
        self.open(MAX_REQUESTS)
        for expected_used in (2, 4):
            before = self.registry.budget()
            self.invoke()
            request = self.calls[-1]
            self.assertEqual(MAX_REQUESTS-before['requests_used'], request['request_budget'])
            self.assertEqual(request['request_budget'], request['memory_load']['budget']['request_allowance'])
            self.assertGreater(request['request_budget'], 100)
            attempt = self.registry.load('attempt')[request['controller_binding']['invocation_id']]
            self.assertEqual('finished', attempt['status'])
            self.assertEqual(request['request_budget'], attempt['reserved'])
            self.assertEqual(request['memory_load'], attempt['evidence']['memory_load'])
            self.assertEqual(expected_used, self.registry.budget()['requests_used'])
            self.assertEqual(0, self.registry.budget()['requests_reserved_unknown'])
            after = self.registry.budget()
            attempts = self.registry.load('attempt')
            calls = len(self.calls)
            self.open(MAX_REQUESTS)
            self.assertEqual(after, self.registry.budget())
            self.assertEqual(attempts, self.registry.load('attempt'))
            self.assertEqual(calls, len(self.calls))
        self.assertEqual(2, len(self.calls))
        self.assertEqual(self.source_before, self.repo.snapshot())

    def test_known_zero_and_counted_failure_release_only_their_reservation_preserving_old_unknown(self):
        self.open(MAX_REQUESTS)
        originals = self.seed_usage_and_unknown()
        before = self.registry.budget()
        self.invoke(count=0)
        self.assertEqual(before, self.registry.budget())
        self.assertEqual(MAX_REQUESTS-10, self.calls[-1]['request_budget'])
        zero = list(self.registry.load('attempt').values())[-1]
        self.assertEqual(('finished', 0), (zero['status'], zero['evidence']['requests']))
        self.assert_original_attempts(originals)
        with self.assertRaises(CountedPlanningFailure):
            self.invoke(mode='counted', count=1)
        self.assertEqual((MAX_REQUESTS, 8, 3, MAX_REQUESTS-11), tuple(self.registry.budget().values()))
        counted = list(self.registry.load('attempt').values())[-1]
        self.assertEqual(('finished', 'failed', 1),
                         (counted['status'], counted['outcome'], counted['evidence']['requests']))
        self.assert_original_attempts(originals)
        attempts = self.registry.load('attempt')
        self.open(MAX_REQUESTS)
        self.assertEqual(attempts, self.registry.load('attempt'))
        self.assertEqual((MAX_REQUESTS, 8, 3, MAX_REQUESTS-11), tuple(self.registry.budget().values()))
        self.assertEqual(2, len(self.calls))

    def test_unknown_failure_holds_remaining_allowance_after_restart_and_cannot_replay(self):
        self.open(MAX_REQUESTS)
        originals = self.seed_usage_and_unknown()
        with self.assertRaisesRegex(SafetyError, 'Pi planning failed'):
            self.invoke(mode='unknown')
        self.assertEqual(1, len(self.calls))
        self.assertEqual(MAX_REQUESTS-10, self.calls[-1]['request_budget'])
        self.assertEqual((MAX_REQUESTS, 7, MAX_REQUESTS-7, 0), tuple(self.registry.budget().values()))
        self.assert_original_attempts(originals)
        attempts = self.registry.load('attempt')
        newest = list(attempts.values())[-1]
        self.assertEqual('unknown', newest['status'])
        self.assertIsNone(newest['evidence']['requests'])
        self.open(MAX_REQUESTS)
        self.assertEqual(attempts, self.registry.load('attempt'))
        self.assertEqual((MAX_REQUESTS, 7, MAX_REQUESTS-7, 0), tuple(self.registry.budget().values()))
        with self.assertRaisesRegex(SafetyError, 'exhausted'):
            self.invoke()
        self.assertEqual(attempts, self.registry.load('attempt'))
        self.assertEqual(1, len(self.calls))
        self.assertEqual(self.source_before, self.repo.snapshot())


if __name__ == '__main__':
    unittest.main()
