"""Evaluation must preserve original accounting and revalidate pinned facts."""
import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from memory_temporal_controls import FrozenMaintenance, LimitedDurableProvider, RequestWindow, profile
from repo_maintainer.models import SafetyError
from repo_maintainer.ui_registry import DurableProvider, UIRegistry


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.registry = UIRegistry(Path(self.temp.name) / 'state', {'provider': {'request_budget': 100}})
        self.addCleanup(self.registry.close)

    def provider(self, count=2, fail=False):
        class Inner:
            max_requests, requests_used, evidence = 100, 0, {}
            allowance = None
            def plan(inner, *args):
                inner.allowance = inner.max_requests - inner.requests_used
                if fail: raise RuntimeError('upstream result unknown')
                inner.requests_used += count
                inner.evidence = {'requests': count}
                return 'proposal'
            compact = plan
        return Inner()

    def test_delegate_limits_actual_process_allowance_but_keeps_original_receipts(self):
        inner = self.provider()
        window = RequestWindow(self.registry, 4)
        limited = LimitedDurableProvider(DurableProvider(inner, self.registry), window)
        self.assertEqual('proposal', limited.plan())
        self.assertEqual(4, inner.allowance)
        limited.plan()
        self.assertEqual(2, inner.allowance)
        before = self.registry.load('attempt')
        with self.assertRaises(SafetyError): limited.plan()
        self.assertEqual(before, self.registry.load('attempt'))
        self.assertEqual((100, 4, 0), tuple(self.registry.budget()[k] for k in
            ('request_budget', 'requests_used', 'requests_reserved_unknown')))
        self.assertEqual([4, 2], [v['reserved'] for v in before.values()])
        self.assertEqual(100, inner.max_requests)

    def test_unknown_upstream_keeps_bounded_original_reservation_and_stops_new_windows(self):
        limited = LimitedDurableProvider(DurableProvider(self.provider(fail=True), self.registry),
                                          RequestWindow(self.registry, 16))
        with self.assertRaises(RuntimeError): limited.plan()
        budget = self.registry.budget()
        self.assertEqual((0, 16, 84), (budget['requests_used'], budget['requests_reserved_unknown'], budget['requests_remaining']))
        with self.assertRaises(SafetyError): RequestWindow(self.registry, 16)
        self.assertEqual('unknown', next(iter(self.registry.load('attempt').values()))['status'])

    def test_compact_local_exhaustion_does_not_open_an_unknown_reservation(self):
        limited = LimitedDurableProvider(DurableProvider(self.provider(), self.registry), RequestWindow(self.registry, 1))
        with self.assertRaises(SafetyError): limited.compact()
        self.assertEqual({}, self.registry.load('attempt'))
        self.assertEqual(100, self.registry.budget()['requests_remaining'])

    def test_delegation_rejects_invalid_or_foreign_journal(self):
        for value in (True, 0, 101, -1):
            with self.subTest(value=value), self.assertRaises(SafetyError): RequestWindow(self.registry, value)
        window = RequestWindow(self.registry, 16)
        with self.assertRaises(SafetyError):
            LimitedDurableProvider(DurableProvider(self.provider(), SimpleNamespace()), window)


class FrozenTests(unittest.TestCase):
    def setUp(self):
        self.rows = {'old': {'source_ref': {'kind': 'run', 'id': 'old'}, 'body': {'result': 'failed'}}}
        self.memory = SimpleNamespace(search=lambda _: {'hits': list(self.rows.values()), 'runtime_authorized': False},
                                     resolve=lambda kind, identity: self.rows[identity])
        self.frozen = FrozenMaintenance(self.memory, 'same target')

    def test_later_group_rows_are_excluded_and_returned_data_is_not_trusted_as_mutable_cache(self):
        self.rows['new'] = {'source_ref': {'kind': 'run', 'id': 'new'}, 'body': {'result': 'passed'}}
        result = self.frozen.search('same target'); result['hits'].clear()
        self.assertEqual(['old'], [r['source_ref']['id'] for r in self.frozen.search('same target')['hits']])

    def test_original_evidence_mutation_is_rejected_on_each_access(self):
        self.frozen.search('same target')
        self.rows['old']['body']['result'] = 'changed'
        with self.assertRaises(SafetyError): self.frozen.search('same target')

    def test_query_change_or_packet_mutation_is_rejected(self):
        with self.assertRaises(SafetyError): self.frozen.search('different target')
        self.frozen.packet['hits'].clear()
        with self.assertRaises(SafetyError): self.frozen.search('same target')


class ProfileTests(unittest.TestCase):
    def test_foreign_root_is_rejected_before_optional_layers_are_detached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            session = SimpleNamespace(repo=root / 'foreign', task_planner='retain')
            with self.assertRaises(SafetyError), profile(session, 'finite_summary', root, None):
                self.fail('foreign profile entered')
            self.assertEqual('retain', session.task_planner)

    def test_optional_wiring_is_restored_even_after_a_candidate_exception(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'temporal-owner.json').write_text(json.dumps({'schema_version': 1, 'kind': 'authored-temporal-evaluation'}))
            objects = {k: object() for k in ('experience', 'knowledge', 'maintenance', 'compaction')}
            planner = SimpleNamespace(**objects)
            inner = SimpleNamespace(**{'_' + k + '_context': v for k, v in objects.items() if k != 'compaction'},
                                    revise=lambda *args: 'original')
            durable = SimpleNamespace(inner=inner, task_planner=planner)
            session = SimpleNamespace(repo=root / 'source', task_planner=planner, provider=durable,
                                      failure_feedback='feedback', feedback_cleanup='cleanup', **objects)
            frozen = SimpleNamespace(memory=objects['maintenance'])
            with patch('repo_maintainer.git.RepositoryGateway', return_value=SimpleNamespace(common_dir=root / 'source/.git')):
                with self.assertRaises(RuntimeError):
                    with profile(session, 'no_memory', root, frozen):
                        self.assertIsNone(session.task_planner); self.assertIsNone(inner._knowledge_context)
                        raise RuntimeError('candidate stopped')
            self.assertIs(planner, session.task_planner); self.assertIs(planner, durable.task_planner)
            self.assertIs(objects['knowledge'], inner._knowledge_context)
            self.assertIs(objects['maintenance'], session.maintenance)
            self.assertEqual('original', inner.revise())
