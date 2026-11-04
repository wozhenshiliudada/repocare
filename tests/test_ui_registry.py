import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.models import SafetyError
from repo_maintainer.ui_registry import DurableProvider, UIRegistry, provider_binding
from repo_maintainer.pi_evidence import CountedPlanningFailure


def failed_count():
    return {'schema_version':1,'status':'failed','failure_category':'no_plan','requests':1,'requests_started':1,
            'consumption':'reported','terminal_count':{'schema_version':1,'completed_streams':1},
            'count_scope':'adapter_stream_invocations','model_cost_verified':False,'events_complete':True,
            'events':[],'usage':[],'pi_version':'1.0.4','upstream_commit':'7c10bd4337495ee613f2224843ecdf349b80d1df',
            'source_snapshot_sha256':'a'*64,'edit_protocol':'exact-replacements-v1'}


class CountingProvider:
    max_requests = 6
    requests_used = 0
    evidence = {}

    def plan(self):
        self.requests_used += 2
        self.evidence = {"requests": 2, "events": [{"type": "agent_end"}]}
        return "plan"


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binding = {"repository": "fixture", "policy": {}, "provider": provider_binding(CountingProvider())}
        self.registry = UIRegistry(self.root, self.binding)
        self.addCleanup(lambda: self.registry.close())

    def restart(self):
        self.registry.close()
        self.registry = UIRegistry(self.root, self.binding)

    def test_usage_separates_live_and_historical_holds_without_changing_accounting(self):
        old, _ = self.registry.reserve(maximum=2)
        self.registry.settle(old, {'requests': None}, success=False)
        old_record = self.registry.load('attempt')[old]
        live, _ = self.registry.reserve(maximum=3)

        def amounts():
            usage = self.registry.budget(include_usage=True)
            base = self.registry.budget()
            self.assertEqual(base, {k: usage[k] for k in base})
            self.assertEqual(usage['requests_reserved_unknown'],
                             usage['requests_reserved_active'] + usage['requests_ended_unknown'])
            return tuple(usage[k] for k in ('requests_used', 'requests_reserved_active',
                                            'requests_ended_unknown', 'requests_remaining'))

        self.assertEqual((0, 3, 2, 1), amounts())
        self.registry.settle(live, {'requests': 1}, success=True)
        self.assertEqual((1, 0, 2, 3), amounts())
        self.registry.reserve(maximum=1)
        self.restart()
        self.assertEqual((1, 0, 3, 2), amounts())
        self.assertEqual(old_record, self.registry.load('attempt')[old])

    def test_bounded_reservation_is_atomic_clamped_and_does_not_touch_legacy_unknown(self):
        for invalid in (0,-1,True,1.5,'2',100000):
            with self.subTest(invalid=invalid),self.assertRaises(SafetyError):self.registry.reserve(maximum=invalid)
        self.assertEqual({},self.registry.load('attempt'))
        old,_=self.registry.reserve(maximum=2)
        self.registry.settle(old,{'requests':None},success=False)
        original=self.registry.load('attempt')[old]
        identity,allowance=self.registry.reserve(maximum=6)
        self.assertEqual(4,allowance)
        receipt=CountedPlanningFailure(failed_count(),allowance)
        self.registry.settle(identity,receipt.receipt,success=False,failure_receipt=receipt)
        self.assertEqual(original,self.registry.load('attempt')[old])
        self.assertEqual((1,2,3),tuple(self.registry.budget()[k] for k in
            ('requests_used','requests_reserved_unknown','requests_remaining')))
        with self.assertRaises(SafetyError):
            self.registry.settle(identity,receipt.receipt,success=False,failure_receipt=receipt)
        self.restart()
        self.assertEqual(original,self.registry.load('attempt')[old])
        preview=self.registry.preview_extension(8,'fixture')
        self.assertEqual(2,preview['requests_reserved_unknown']);self.assertEqual(1,preview['requests_used'])

    def test_failure_dictionary_alone_or_a_mismatched_receipt_cannot_release_a_reservation(self):
        provider=CountingProvider()
        def fail():
            provider.evidence=failed_count()
            raise RuntimeError('an arbitrary failure with a claimed count')
        provider.plan=fail
        with self.assertRaises(RuntimeError):DurableProvider(provider,self.registry).plan()
        self.assertEqual(6,self.registry.budget()['requests_reserved_unknown'])
        attempt=next(iter(self.registry.load('attempt').values()))
        self.assertEqual('unknown',attempt['evidence']['consumption'])
        self.assertIsNone(attempt['evidence']['requests']);self.assertNotIn('terminal_count',attempt['evidence'])

    def test_only_matching_terminal_exception_can_settle_a_failed_attempt(self):
        identity,allowance=self.registry.reserve(maximum=2)
        receipt=CountedPlanningFailure(failed_count(),3)
        self.registry.settle(identity,failed_count(),success=False,failure_receipt=receipt)
        self.assertEqual(2,self.registry.budget()['requests_reserved_unknown'])
        self.assertEqual('unknown',self.registry.load('attempt')[identity]['status'])

    def test_success_usage_is_persisted_and_shared_across_restarts(self):
        for used in (2, 4, 6):
            provider = DurableProvider(CountingProvider(), self.registry)
            self.assertEqual("plan", provider.plan())
            self.assertEqual(used, provider.requests_used)
            self.restart()
        with self.assertRaisesRegex(SafetyError, "exhausted"):
            DurableProvider(CountingProvider(), self.registry).plan()
        self.assertEqual(3, len(self.registry.load("attempt")))
        self.assertEqual(0, self.registry.budget()["requests_reserved_unknown"])

    def test_unknown_failed_consumption_is_reserved_and_never_reset_on_restart(self):
        provider = CountingProvider()
        def unknown():
            raise TimeoutError("No trustworthy final Pi usage")
        provider.plan = unknown
        with self.assertRaises(TimeoutError):
            DurableProvider(provider, self.registry).plan()
        self.restart()
        budget = self.registry.budget()
        self.assertEqual(0, budget["requests_used"])
        self.assertEqual(6, budget["requests_reserved_unknown"])
        self.assertEqual(0, budget["requests_remaining"])
        self.assertEqual("unknown", next(iter(self.registry.load("attempt").values()))["status"])
        with self.assertRaises(SafetyError):
            DurableProvider(CountingProvider(), self.registry).plan()

    def test_interrupted_intent_preserves_reservation_and_job_identity(self):
        identity, allowance = self.registry.reserve()
        self.registry.put("job", "b"*32, {"status": "running", "signature": "original", "action": "plan"})
        self.restart()
        self.assertEqual(6, allowance)
        self.assertEqual("unknown", self.registry.load("attempt")[identity]["status"])
        job = self.registry.load("job")["b"*32]
        self.assertEqual("interrupted", job["status"])
        self.assertEqual("original", job["signature"])
        self.assertEqual(6, self.registry.budget()["requests_reserved_unknown"])

    def test_invalid_success_usage_retains_reservation_and_failure_evidence(self):
        provider = CountingProvider()
        def invalid():
            provider.evidence = {"requests": True}
            return "invalid"
        provider.plan = invalid
        with self.assertRaises(SafetyError):
            DurableProvider(provider, self.registry).plan()
        self.restart()
        self.assertEqual(6, self.registry.budget()["requests_reserved_unknown"])
        self.assertEqual("unknown", next(iter(self.registry.load("attempt").values()))["status"])

    def test_existing_journal_cannot_change_budget_provider_or_repository(self):
        self.registry.close()
        for binding in ({**self.binding, "repository": "foreign"},
                        {**self.binding, "provider": {**self.binding["provider"], "request_budget": 18}}):
            with self.assertRaisesRegex(SafetyError, "changed"):
                UIRegistry(self.root, binding)
        self.restart()
        self.assertEqual(6, self.registry.budget()["request_budget"])

    def test_unknown_schema_and_corrupt_database_are_preserved_and_refused(self):
        self.registry.close()
        with closing(sqlite3.connect(self.registry.path)) as db:
            db.execute("PRAGMA user_version=42")
        original = self.registry.path.read_bytes()
        with self.assertRaisesRegex(SafetyError, "schema"):
            UIRegistry(self.root, self.binding)
        self.assertEqual(original, self.registry.path.read_bytes())
        self.registry.path.write_bytes(b"corrupt database evidence")
        with self.assertRaises(sqlite3.DatabaseError):
            UIRegistry(self.root, self.binding)
        self.assertEqual(b"corrupt database evidence", self.registry.path.read_bytes())

    def test_failed_transaction_does_not_publish_partial_intent(self):
        with self.assertRaises(RuntimeError):
            with self.registry.transaction() as db:
                self.registry.put("plan", "c"*32, {"status":"consumed"}, db)
                raise RuntimeError("crash before transaction commit")
        self.restart()
        self.assertEqual({}, self.registry.load("plan"))

    def test_missing_journal_and_legacy_ui_history_cannot_silently_reset_budget(self):
        DurableProvider(CountingProvider(), self.registry).plan()
        self.registry.close()
        self.registry.path.unlink()
        with self.assertRaisesRegex(SafetyError, "refuse to reset"):
            UIRegistry(self.root, self.binding)
        self.assertFalse(self.registry.path.exists())
        legacy = self.root / "legacy"
        (legacy / "ui-sessions" / ("d"*32)).mkdir(parents=True)
        with self.assertRaisesRegex(SafetyError, "legacy"):
            UIRegistry(legacy, self.binding)


if __name__ == "__main__":
    unittest.main()
