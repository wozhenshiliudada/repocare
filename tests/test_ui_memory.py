"""UI binding/restart tests; Pi transport is independently tested by the Node suite."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.cli import main
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, create_app, validate_payload
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider import MockProvider
from repo_maintainer.source_memory import build_memory, search_memory, CATALOG
from repo_maintainer.ui_memory import UIMemory
from repo_maintainer.workflow import cleanup_run
from starlette.testclient import TestClient
from source_protocol_fixture import source_protocol_only


class UIMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.repo = create_fixture(self.directory / "repository")
        self.gateway = RepositoryGateway(self.repo)
        self.before = self.gateway.snapshot()
        self.root = self.directory / "documents"
        self.root.mkdir()
        self.text = "状态：确认\naddition requires new approval; historical approvals never authorize execution."
        (self.root / "rule.md").write_text(self.text, encoding="utf-8")
        (self.root / "case.md").write_text("Historical addition case; old approval is reference only.", encoding="utf-8")
        self.entries = [dict(id=identity, topic=identity, path=identity+".md", start_line=1, end_line=end,
                             kind=kind, classification=classification)
                        for identity, end, kind, classification in [("rule", 2, "document", "confirmed"),
                                                                  ("case", 1, "case", "historical")]]
        self.save_catalog()
        self.index = self.directory / "private.memory.sqlite3"
        build_memory(self.root, self.repo, self.index)
        self.policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
        self.session = None
        self.addCleanup(self.finish)

    def save_catalog(self):
        (self.root / CATALOG).write_text(json.dumps({"schema_version": 1, "entries": self.entries}), encoding="utf-8")

    def provider(self, enabled=True):
        provider = PiProvider(self.policy.allowed_paths, "http://127.0.0.1:12345/v1", self.policy.readonly_paths, 4)
        if enabled:
            provider.memory_root, provider.memory_index = self.root, self.index
        return provider

    def open(self, enabled=True):
        if self.session:
            self.session.close()
        self.session = UISession(self.repo, self.policy, self.provider(enabled), self.directory / "runs", allow_auto=True)
        source_protocol_only(self.session)
        return self.session

    def finish(self):
        if self.session:
            self.session.close()
            for identity in self.session.runs:
                if (self.directory / "runs" / identity / "run.json").exists():
                    cleanup_run(self.directory / "runs" / identity)

    def generate(self, issue="addition bug"):
        self.open() if not self.session else None
        provider = self.session.provider.inner
        def fixture_plan(repository, current_issue, tests):
            provider.requests_used += 2
            context = search_memory(self.root, self.repo, self.index, current_issue)["context"]
            from repo_maintainer.ui_memory import citations
            provider.evidence = {"requests": 2, "memory_context_sha256": context["context_sha256"],
                                 "memory_citations": citations(context["hits"])}
            return MockProvider().plan(repository, current_issue, tests)
        with patch.object(provider, "plan", side_effect=fixture_plan):
            return self.session._operate("plan", {"issue": issue})

    def db_bytes(self):
        return (self.directory / "runs" / "ui-state" / "registry.sqlite3").read_bytes()

    def test_preview_only_metadata_and_classifications_no_model_or_excerpt(self):
        session = self.open()
        before = self.db_bytes()
        with patch.object(session.provider.inner, "plan", side_effect=AssertionError("model called")):
            result = session._operate("memory_preview", {"issue": "addition"})["memory_preview"]
        self.assertEqual("verified", result["status"])
        self.assertFalse(result["runtime_authorized"])
        self.assertEqual({"confirmed", "historical"}, {r["classification"] for r in result["citations"]})
        self.assertTrue(all("excerpt" not in row for row in result["citations"]))
        self.assertNotIn(self.text, json.dumps(result))
        self.assertEqual(before, self.db_bytes())
        self.assertEqual([], session.state()["planning_attempts"])
        self.assertEqual(self.before, self.gateway.snapshot())

    def test_real_binding_restarts_with_same_budget_plan_receipt_and_new_claim(self):
        self.generate()
        old = self.session.state()["plans"][0]
        used = self.session.state()["requests_used"]
        self.open()
        state = self.session.state()
        restored = state["plans"][0]
        self.assertEqual(2, used)
        self.assertEqual(used, state["requests_used"])
        self.assertFalse(restored["claimed"])
        self.assertNotEqual(old["revision"], restored["revision"])
        with self.assertRaises(SafetyError):
            self.session._operate("claim_plan", {"plan_id": restored["id"], "revision": old["revision"]})
        with self.assertRaisesRegex(SafetyError, "explicit claim"):
            self.session._operate("decision", {"plan_id": restored["id"], "fingerprint": restored["fingerprint"], "decision": "approve"})
        self.session._operate("claim_plan", {"plan_id": restored["id"], "revision": restored["revision"]})
        with patch("repo_maintainer.provider_config.load_provider_config", side_effect=AssertionError("credential read")):
            result = self.session._operate("decision", {"plan_id": restored["id"], "fingerprint": restored["fingerprint"], "decision": "approve"})
        run = self.session.state()["runs"][0]
        self.assertEqual("candidate_ready", result["workflow_status"])
        self.assertEqual(restored["planning_evidence"]["memory_citations"], run["planning_origin"]["provider_evidence"]["memory_citations"])
        self.assertEqual(2, self.session.state()["requests_used"])
        self.assertEqual(self.before, self.gateway.snapshot(exclude_branch=run["branch"]))

    def test_old_no_memory_journal_cannot_enable_memory_or_reset_budget(self):
        session = self.open(False)
        identity, _ = session.registry.reserve()
        session.registry.settle(identity, {}, success=False)
        session.close()
        before = self.db_bytes()
        with self.assertRaisesRegex(SafetyError, "changed"):
            self.open(True)
        self.assertEqual(before, self.db_bytes())
        self.open(False)
        self.assertEqual(4, self.session.state()["requests_reserved_unknown"])
        self.assertFalse(self.session.state()["source_memory"]["enabled"])

    def test_scope_changes_cannot_reuse_same_journal_and_db_bytes_unchanged(self):
        self.generate()
        self.session.close()
        original = self.db_bytes()
        copied = self.directory / "copy.memory.sqlite3"
        copied.write_bytes(self.index.read_bytes())
        for change in ("index_path", "root_path", "disable", "index_bytes"):
            with self.subTest(change=change):
                provider = self.provider(change != "disable")
                if change == "index_path": provider.memory_index = copied
                if change == "root_path": provider.memory_root = self.directory
                if change == "index_bytes":
                    content = self.index.read_bytes()
                    with contextlib.closing(sqlite3.connect(self.index)) as db:
                        db.execute("PRAGMA user_version=2")
                try:
                    with self.assertRaises(SafetyError):
                        UISession(self.repo, self.policy, provider, self.directory / "runs")
                    self.assertEqual(original, self.db_bytes())
                finally:
                    if change == "index_bytes": self.index.write_bytes(content)

    def test_stale_source_reopens_for_history_but_blocks_plan_auto_claim_and_decision(self):
        self.generate()
        (self.root / "rule.md").write_text(self.text+"\nchanged", encoding="utf-8")
        self.open()
        state = self.session.state()
        self.assertEqual("invalid", state["source_memory"]["status"])
        self.assertEqual([], state["source_memory"]["sources"])
        plan = state["plans"][0]
        for action, payload in [("plan", {"issue": "addition"}), ("auto", {"issue": "addition"}),
                                ("claim_plan", {"plan_id": plan["id"], "revision": plan["revision"]})]:
            with self.subTest(action=action), patch.object(self.session.provider.inner, "plan", side_effect=AssertionError("model")):
                with self.assertRaises(SafetyError): self.session._operate(action, payload)
        self.session.claimed_plans.add(plan["id"])
        with self.assertRaises(SafetyError):
            self.session._operate("decision", {"plan_id": plan["id"], "fingerprint": plan["fingerprint"], "decision": "approve"})
        final = self.session.state()
        self.assertEqual(2, final["requests_used"])
        self.assertEqual(1, len(final["planning_attempts"]))
        self.assertEqual([], final["runs"])
        self.assertEqual("awaiting_approval", final["plans"][0]["status"])
        self.assertEqual(self.before, self.gateway.snapshot())

    def test_new_stale_scope_cannot_create_zero_budget_journal(self):
        (self.root / "rule.md").unlink()
        with self.assertRaises((SafetyError, OSError)):
            self.open()
        self.assertFalse((self.directory / "runs" / "ui-state").exists())

    def test_mixed_status_preview_visible_and_injection_stops_before_reservation(self):
        (self.root / "proposal.md").write_text("状态：提议\naddition architecture alternative", encoding="utf-8")
        self.entries.append(dict(id="proposal", topic="rule", path="proposal.md", start_line=1, end_line=2,
                                 kind="document", classification="proposed"))
        self.save_catalog()
        self.index = self.directory / "mixed.memory.sqlite3"
        build_memory(self.root, self.repo, self.index)
        self.open()
        preview = self.session._operate("memory_preview", {"issue": "addition"})["memory_preview"]
        self.assertEqual("mixed_classifications", preview["status"])
        self.assertEqual(["rule"], preview["mixed_topics"])
        self.assertEqual(["rule"], self.session.state()["source_memory"]["mixed_topics"])
        with self.assertRaisesRegex(SafetyError, "Mixed"):
            self.session._operate("plan", {"issue": "addition"})
        self.assertEqual([], self.session.state()["planning_attempts"])
        self.assertEqual(4, self.session.state()["requests_remaining"])

    def test_changed_index_blocks_current_action_and_reopen_without_reset(self):
        self.open()
        original = self.db_bytes()
        with contextlib.closing(sqlite3.connect(self.index)) as db:
            db.execute("PRAGMA user_version=2")
        self.assertEqual("invalid", self.session.state()["source_memory"]["status"])
        with self.assertRaisesRegex(SafetyError, "index changed"):
            self.session._operate("plan", {"issue": "addition"})
        self.session.close()
        with self.assertRaisesRegex(SafetyError, "changed"): self.open()
        self.assertEqual(original, self.db_bytes())

    def test_failure_reservation_survives_restart_and_memory_preview_does_not_refund(self):
        self.open()
        with patch.object(self.session.provider.inner, "plan", side_effect=SafetyError("unknown transport failure")):
            with self.assertRaises(SafetyError): self.session._operate("plan", {"issue": "addition"})
        self.open()
        self.session._operate("memory_preview", {"issue": "addition"})
        state = self.session.state()
        self.assertEqual(4, state["requests_reserved_unknown"])
        self.assertEqual(0, state["requests_remaining"])
        with self.assertRaisesRegex(SafetyError, "budget exhausted"):
            self.session._operate("plan", {"issue": "addition"})
        self.assertEqual(1, len(self.session.state()["planning_attempts"]))

    def test_saved_receipt_mismatch_rejected_without_consumption_or_workspace(self):
        self.generate()
        plan = self.session.state()["plans"][0]
        self.session.plans[plan["id"]]["view"]["memory_binding"] = {"changed": True}
        with self.assertRaisesRegex(SafetyError, "receipt changed"):
            self.session._operate("decision", {"plan_id": plan["id"], "fingerprint": plan["fingerprint"], "decision": "approve"})
        self.assertEqual([], self.session.state()["runs"])
        self.assertEqual("awaiting_approval", self.session.state()["plans"][0]["status"])

    def test_missing_memory_receipt_is_not_saved_as_approvable_plan(self):
        self.open()
        provider = self.session.provider.inner
        def invalid(repository, issue, tests):
            provider.evidence = {"requests": 2}
            return MockProvider().plan(repository, issue, tests)
        with patch.object(provider, "plan", side_effect=invalid), self.assertRaisesRegex(SafetyError, "receipt"):
            self.session._operate("plan", {"issue": "addition"})
        self.assertEqual([], self.session.state()["plans"])
        self.assertEqual(2, self.session.state()["requests_used"])

    def test_http_preview_auth_exact_fields_and_untrusted_text_remains_data(self):
        self.open()
        with TestClient(create_app(self.session, 12345), base_url="http://127.0.0.1:12345") as client:
            payload = {"request_id": uuid.uuid4().hex, "issue": "addition"}
            self.assertEqual(401, client.post("/api/actions/memory_preview", json=payload).status_code)
            headers = {"Authorization": "Bearer "+self.session.token}
            self.assertEqual(409, client.post("/api/actions/memory_preview", json={**payload, "root": "private"}, headers=headers).status_code)
            self.assertEqual(404, client.get("/api/memory/sql", headers=headers).status_code)
        self.assertEqual([], self.session.state()["jobs"])
        with self.assertRaises(SafetyError): validate_payload("memory_preview", {"request_id": uuid.uuid4().hex, "issue": ""})

    def test_pair_and_mock_configuration_rejected_and_old_mock_scope_reopens(self):
        provider = self.provider();provider.memory_index = None
        with self.assertRaisesRegex(SafetyError, "together"): UIMemory(provider, self.repo)
        mock = MockProvider();mock.memory_root, mock.memory_index = self.root, self.index
        with self.assertRaisesRegex(SafetyError, "real Pi"): UIMemory(mock, self.repo)
        session = UISession(self.repo, self.policy, MockProvider(), self.directory / "mock-runs")
        session.close()
        session = UISession(self.repo, self.policy, MockProvider(), self.directory / "mock-runs")
        try:
            self.assertEqual("disabled", session.state()["source_memory"]["status"])
        finally: session.close()

    def test_cli_memory_pair_and_demo_rejected_before_fixture_or_provider_configuration(self):
        for args in (["serve", "--memory-root", str(self.root)],
                     ["serve", "--demo", str(self.directory / "demo"), "--memory-root", str(self.root), "--memory-index", str(self.index)]):
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1, main(args))
        self.assertFalse((self.directory / "demo").exists())

    def test_pi_rechecks_ui_index_pin_before_configuration_read_even_after_earlier_valid_check(self):
        self.open()
        provider = self.session.provider.inner
        self.session.memory.require_current("addition")
        (self.root / "case.md").write_text("Historical addition case: changed after UI precheck", encoding="utf-8")
        fresh = self.directory / "replacement.memory.sqlite3"
        build_memory(self.root, self.repo, fresh)
        self.index.write_bytes(fresh.read_bytes())  # Explicit test-only replacement of our fixture.
        provider.config_file = self.directory / ".env.unread.local"
        provider.mock_url = None
        with patch("repo_maintainer.provider_config.load_provider_config", side_effect=AssertionError("credential read")):
            with self.assertRaisesRegex(SafetyError, "index changed before"):
                provider.plan(self.gateway, "addition", self.policy.allowed_tests)
        self.assertEqual(0, provider.requests_used)
        self.assertEqual({}, provider.evidence)
        self.assertEqual(self.before, self.gateway.snapshot())

    def test_bound_memory_journal_backup_retains_binding_usage_and_unknown_budget_without_sources(self):
        from repo_maintainer.ui_backup import backup_ui_history, verify_ui_backup
        self.generate()
        identity, _ = self.session.registry.reserve()
        self.session.registry.settle(identity, {}, success=False)
        binding = self.session.memory.binding
        self.session.close()
        before = self.db_bytes()
        result = backup_ui_history(self.directory / "runs")
        verified = verify_ui_backup(self.directory / "runs", result["backup_id"])
        self.assertFalse(verified["runtime_authorized"])
        self.assertEqual(before, self.db_bytes())
        self.open()
        self.assertEqual(binding, self.session.memory.binding)
        self.assertEqual(2, self.session.state()["requests_used"])
        self.assertEqual(2, self.session.state()["requests_reserved_unknown"])


if __name__ == "__main__": unittest.main()
