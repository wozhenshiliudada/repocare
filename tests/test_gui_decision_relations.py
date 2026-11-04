"""GUI relation pins never migrate approvals, consumption or model context."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_ui_memory as memory_tests
from repo_maintainer.cli import main
from repo_maintainer.decision_relations import register_relations
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, create_app
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.source_memory import build_memory, search_memory
from repo_maintainer.ui_backup import encoded
from starlette.testclient import TestClient


class GUIRelationsTests(unittest.TestCase):
    save_catalog = memory_tests.UIMemoryTests.save_catalog
    open = memory_tests.UIMemoryTests.open
    finish = memory_tests.UIMemoryTests.finish
    generate = memory_tests.UIMemoryTests.generate
    db_bytes = memory_tests.UIMemoryTests.db_bytes

    def setUp(self):
        memory_tests.UIMemoryTests.setUp(self)
        self.with_relations = True
        self.manifest = self.directory / "rules.memory-relations.json"
        self.configure()

    def provider(self, enabled=True):
        provider = memory_tests.UIMemoryTests.provider(self, enabled)
        provider.memory_relations = self.manifest if self.with_relations else None
        return provider

    def configure(self, proposed=False):
        for name, status, text in (("old", "confirmed", "Legacy alpha."),
                                  ("new", "proposed" if proposed else "confirmed", "Current beta.")):
            data = ("状态：" + ("提议" if status == "proposed" else "确认") + "\n" + text + "\n")
            if name == "new": data += "\nSupersedes: new -> old\n\n"
            (self.root / (name + ".md")).write_bytes(data.encode())
            self.entries = [r for r in self.entries if r["id"] != name]
            self.entries.append({"id": name, "topic": "replacement", "path": name + ".md",
                "start_line": 1, "end_line": len(data.splitlines()), "kind": "document", "classification": status})
        self.save_catalog()
        self.index = self.directory / ("proposed.memory.sqlite3" if proposed else "relations.memory.sqlite3")
        build_memory(self.root, self.repo, self.index)
        spec = self.directory / "spec.private.json"
        spec.write_bytes(encoded({"schema_version": 1, "relations": [{"id": "replacement", "newer_id": "new",
            "older_id": "old", "newer_sha256": digest((self.root / "new.md").read_bytes()),
            "older_sha256": digest((self.root / "old.md").read_bytes()), "statement_line": 4,
            "statement_sha256": digest(b"Supersedes: new -> old")}]}))
        self.manifest = self.directory / ("proposed.memory-relations.json" if proposed else "rules.memory-relations.json")
        register_relations(spec, self.root, self.repo, self.index, self.manifest)

    def test_fixed_path_hash_and_content_pin_preview_zero_budget_and_no_absolute_relation_path(self):
        self.open(); before = self.db_bytes()
        view = self.session.state()["source_memory"]
        value = self.session._operate("memory_preview", {"issue": "beta"})["memory_preview"]
        self.assertEqual(1, len(value["decision_relations"]["relations"]))
        self.assertEqual(digest(str(self.manifest.resolve()).encode()), view["binding"]["decision_relations"]["path_sha256"])
        self.assertEqual(digest(self.manifest.read_bytes()), view["decision_relations"]["manifest_sha256"])
        self.assertNotIn(str(self.manifest), json.dumps(self.session.state()))
        self.assertNotIn("excerpt", json.dumps(value["decision_relations"]))
        self.assertEqual([], self.session.state()["planning_attempts"])
        self.assertEqual(before, self.db_bytes())

    def test_mixed_classification_stays_previewable_and_blocks_model_reservation(self):
        self.configure(proposed=True); self.open()
        value = self.session._operate("memory_preview", {"issue": "beta"})["memory_preview"]
        edge = value["decision_relations"]["relations"][0]
        self.assertEqual(("proposed", "confirmed"), (edge["newer"]["classification"], edge["older"]["classification"]))
        self.assertEqual(["replacement"], value["mixed_topics"])
        before = self.db_bytes()
        with self.assertRaisesRegex(SafetyError, "Mixed"): self.session._operate("plan", {"issue": "beta"})
        self.assertEqual(before, self.db_bytes())
        self.assertEqual([], self.session.state()["planning_attempts"])

    def test_changed_relation_blocks_plan_auto_claim_and_approve_without_consumption(self):
        self.generate(); plan = self.session.state()["plans"][0]
        self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
        state = self.session.state(); self.assertEqual("invalid", state["source_memory"]["status"])
        before = self.db_bytes()
        actions = [("plan", {"issue": "addition"}), ("auto", {"issue": "addition"}),
            ("claim_plan", {"plan_id": plan["id"], "revision": plan["revision"]}),
            ("decision", {"plan_id": plan["id"], "fingerprint": plan["fingerprint"], "decision": "approve"})]
        for action, payload in actions:
            with self.subTest(action=action), self.assertRaisesRegex(SafetyError, "relations file changed"):
                self.session._operate(action, payload)
        self.assertEqual(before, self.db_bytes())
        state = self.session.state(); self.assertEqual("awaiting_approval", state["plans"][0]["status"])
        self.assertEqual(2, state["requests_used"]); self.assertEqual([], state["runs"])

    def test_same_bytes_other_path_disable_or_enable_cannot_rebind_journal(self):
        self.generate(); self.session.close(); before = self.db_bytes()
        copy = self.directory / "copy.memory-relations.json"; copy.write_bytes(self.manifest.read_bytes())
        for path in (None, copy):
            with self.subTest(path=path):
                provider = self.provider(); provider.memory_relations = path
                with self.assertRaisesRegex(SafetyError, "scope changed"):
                    UISession(self.repo, self.policy, provider, self.directory / "runs")
                self.assertEqual(before, self.db_bytes())
        self.open(); self.assertEqual(2, self.session.state()["requests_used"])
        self.session.close(); self.with_relations = False
        runs = self.directory / "old-no-relations"
        old = UISession(self.repo, self.policy, self.provider(), runs); old.close()
        old_db = (runs / "ui-state" / "registry.sqlite3").read_bytes(); self.with_relations = True
        with self.assertRaisesRegex(SafetyError, "scope changed"):
            UISession(self.repo, self.policy, self.provider(), runs)
        self.assertEqual(old_db, (runs / "ui-state" / "registry.sqlite3").read_bytes())

    def test_modified_manifest_prevents_restart_without_reset(self):
        self.generate(); self.session.close(); before = self.db_bytes()
        original = self.manifest.read_bytes(); self.manifest.write_bytes(original + b" ")
        with self.assertRaisesRegex(SafetyError, "scope changed"): self.open()
        self.assertEqual(before, self.db_bytes()); self.manifest.write_bytes(original)
        self.open(); self.assertEqual(2, self.session.state()["requests_used"])

    def test_stale_sources_same_manifest_reopen_history_and_refuse_new_journal(self):
        self.generate(); original = (self.root / "old.md").read_bytes()
        (self.root / "old.md").write_bytes(original + b"\nChanged outside range\n")
        self.open(); state = self.session.state()
        self.assertEqual("invalid", state["source_memory"]["status"])
        self.assertEqual(2, state["requests_used"]); self.assertFalse(state["plans"][0]["claimed"])
        with self.assertRaises(SafetyError):
            UISession(self.repo, self.policy, self.provider(), self.directory / "new-runs")
        self.assertFalse((self.directory / "new-runs" / "ui-state").exists())
        (self.root / "old.md").write_bytes(original)

    def test_unknown_reservation_survives_restart_and_readonly_relation_preview(self):
        self.open()
        with patch.object(self.session.provider.inner, "plan", side_effect=SafetyError("unknown transport")):
            with self.assertRaises(SafetyError): self.session._operate("plan", {"issue": "addition"})
        self.open(); before = self.db_bytes()
        self.session._operate("memory_preview", {"issue": "beta"})
        self.assertEqual(4, self.session.state()["requests_reserved_unknown"])
        self.assertEqual(before, self.db_bytes())

    def test_provider_relation_guard_precedes_config_and_transport_on_revision(self):
        self.open(); provider = self.session.provider.inner
        repository = RepositoryGateway(self.repo)
        provider.config_file = self.directory / "unused-config.private.json"
        self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
        with patch("repo_maintainer.provider_config.load_provider_config", side_effect=AssertionError("credentials")), \
             patch("repo_maintainer.pi_provider.subprocess.Popen", side_effect=AssertionError("transport")):
            with self.assertRaisesRegex(SafetyError, "relations file changed"):
                provider.revise(repository, "addition", self.policy.allowed_tests, {})

    def test_changed_during_model_planning_not_saved_and_used_budget_preserved(self):
        self.open(); provider = self.session.provider.inner
        from repo_maintainer.provider import MockProvider
        from repo_maintainer.ui_memory import citations
        def fake(repository, issue, tests):
            provider.requests_used += 2
            context = search_memory(self.root, self.repo, self.index, issue)["context"]
            provider.evidence = {"requests": 2, "memory_context_sha256": context["context_sha256"],
                                 "memory_citations": citations(context["hits"])}
            plan = MockProvider().plan(repository, issue, tests)
            self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
            return plan
        with patch.object(provider, "plan", side_effect=fake), self.assertRaisesRegex(SafetyError, "relations file changed"):
            self.session._operate("plan", {"issue": "addition"})
        state = self.session.state(); self.assertEqual([], state["plans"])
        self.assertEqual(2, state["requests_used"]); self.assertEqual([], state["runs"])

    def test_saved_plan_retains_relation_pin_and_requires_fresh_claim_after_restart(self):
        self.generate("beta addition")
        old = self.session.state()["plans"][0]
        self.assertEqual(1, len(old["decision_relations"]["relations"]))
        context = search_memory(self.root, self.repo, self.index, "beta addition")["context"]
        self.assertNotIn("decision_relations", context)
        self.open(); current = self.session.state()["plans"][0]
        self.assertFalse(current["claimed"]); self.assertNotEqual(old["revision"], current["revision"])
        with self.assertRaises(SafetyError): self.session._operate("claim_plan", {"plan_id": current["id"], "revision": old["revision"]})
        self.session._operate("claim_plan", {"plan_id": current["id"], "revision": current["revision"]})
        self.assertEqual(old["decision_relations"], self.session.state()["plans"][0]["decision_relations"])
        self.assertEqual(2, self.session.state()["requests_used"])

    def test_no_relation_configuration_preserves_original_schema_binding(self):
        self.with_relations = False; self.open()
        self.assertNotIn("decision_relations", self.session.memory.binding)
        self.assertNotIn("decision_relations", self.session.state()["source_memory"])
        self.assertIsNone(self.session.provider.inner._memory_relations_guard)

    def test_http_preview_rejects_arbitrary_relation_path_and_does_not_create_model_attempt(self):
        self.open()
        with TestClient(create_app(self.session, 12345), base_url="http://127.0.0.1:12345") as client:
            import uuid
            headers = {"Authorization": "Bearer " + self.session.token}
            response = client.post("/api/actions/memory_preview", headers=headers,
                json={"request_id": uuid.uuid4().hex, "issue": "beta", "relations": "other"})
            self.assertEqual(409, response.status_code)
        self.assertEqual([], self.session.state()["planning_attempts"])

    def test_cli_relation_without_memory_or_demo_rejected_before_provider_or_fixture(self):
        cases = [["serve", "--memory-relations", str(self.manifest)],
                 ["serve", "--demo", str(self.directory / "unused-demo"), "--memory-root", str(self.root),
                  "--memory-index", str(self.index), "--memory-relations", str(self.manifest)]]
        for args in cases:
            with contextlib.redirect_stderr(io.StringIO()), patch("repo_maintainer.cli.PiProvider", side_effect=AssertionError("provider")):
                self.assertEqual(1, main(args))
        self.assertFalse((self.directory / "unused-demo").exists())


if __name__ == "__main__": unittest.main()
