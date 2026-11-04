"""Optional Pi claims never rebind a journal or turn source text into permission."""
import contextlib
from dataclasses import asdict
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import test_gui_decision_relations as gui_tests
from repo_maintainer.cli import main
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.pi_memory_relations import with_relations
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider import MockProvider
from repo_maintainer.source_memory import search_memory


class PiDecisionContextTests(unittest.TestCase):
    save_catalog = gui_tests.GUIRelationsTests.save_catalog
    configure = gui_tests.GUIRelationsTests.configure
    open = gui_tests.GUIRelationsTests.open
    finish = gui_tests.GUIRelationsTests.finish
    db_bytes = gui_tests.GUIRelationsTests.db_bytes

    def setUp(self):
        self.context_enabled = True
        gui_tests.GUIRelationsTests.setUp(self)

    def provider(self, enabled=True):
        value = gui_tests.GUIRelationsTests.provider(self, enabled)
        value.memory_relations_context = self.context_enabled
        return value

    def generate(self, issue="beta addition"):
        self.open(); provider = self.session.provider.inner
        def fake(repository, current_issue, tests):
            context = self.session.memory.require_current(current_issue)["context"]
            from repo_maintainer.ui_memory import citations
            provider.requests_used += 2
            provider.evidence = {"requests": 2, "memory_context_sha256": context["context_sha256"],
                                 "memory_citations": citations(context["hits"])}
            return MockProvider().plan(repository, current_issue, tests)
        with patch.object(provider, "plan", side_effect=fake):
            return self.session._operate("plan", {"issue": issue})

    def test_context_is_explicit_claim_only_direct_metadata_and_separate_receipt(self):
        self.open(); before = self.db_bytes()
        original = search_memory(self.root, self.repo, self.index, "beta")["context"]
        result = self.session.memory.verify("beta")["context"]
        self.assertEqual((3, 1), (result["schema_version"], result["source_schema_version"]))
        self.assertEqual(original["hits"], result["hits"])
        self.assertNotEqual(original["context_sha256"], result["context_sha256"])
        edge = result["decision_relations"]["relations"][0]
        self.assertEqual("old", edge["older"]["id"])
        self.assertNotIn("old", [r["id"] for r in result["hits"]])
        self.assertEqual(("confirmed", "confirmed"), (edge["newer"]["classification"], edge["older"]["classification"]))
        self.assertNotIn("excerpt", edge["older"])
        self.assertNotIn("path_sha256", result["decision_relations"])
        self.assertNotIn(str(self.manifest), json.dumps(result))
        self.assertEqual([], self.session.memory.verify("no_matching_zetazeta")["context"]["decision_relations"]["relations"])
        self.assertEqual(before, self.db_bytes())

    def test_no_optin_keeps_original_context_and_binding_exact(self):
        self.context_enabled = False; self.open()
        expected = search_memory(self.root, self.repo, self.index, "beta")["context"]
        self.assertEqual(expected, self.session.memory.verify("beta")["context"])
        self.assertNotIn("model_context", self.session.memory.binding["decision_relations"])

    def test_enable_disable_optin_rejected_without_releasing_used_budget(self):
        self.generate(); self.session.close(); before = self.db_bytes()
        self.context_enabled = False
        with self.assertRaisesRegex(SafetyError, "scope changed"): self.open()
        self.assertEqual(before, self.db_bytes())
        self.context_enabled = True; self.open()
        self.assertEqual(2, self.session.state()["requests_used"])
        self.session.close(); self.context_enabled = False
        runs = self.directory / "display-only"
        old = UISession(self.repo, self.policy, self.provider(), runs); old.close()
        old_bytes = (runs / "ui-state" / "registry.sqlite3").read_bytes()
        self.context_enabled = True
        with self.assertRaisesRegex(SafetyError, "scope changed"):
            UISession(self.repo, self.policy, self.provider(), runs)
        self.assertEqual(old_bytes, (runs / "ui-state" / "registry.sqlite3").read_bytes())

    def test_mixed_claim_preview_remains_readonly_and_blocks_before_reservation(self):
        self.configure(proposed=True); self.open()
        preview = self.session._operate("memory_preview", {"issue": "beta"})["memory_preview"]
        self.assertEqual(["replacement"], preview["mixed_topics"])
        self.assertEqual("proposed", preview["decision_relations"]["relations"][0]["newer"]["classification"])
        before = self.db_bytes()
        with self.assertRaisesRegex(SafetyError, "Mixed"):
            self.session._operate("plan", {"issue": "beta"})
        self.assertEqual(before, self.db_bytes())
        self.assertEqual([], self.session.state()["planning_attempts"])

    def test_saved_receipt_restart_requires_new_claim_and_approval(self):
        self.generate(); old = self.session.state()["plans"][0]
        self.assertTrue(old["memory_binding"]["decision_relations"]["model_context"])
        receipt = self.session.memory.require_current("beta addition")["context"]["context_sha256"]
        self.assertEqual(receipt, old["planning_evidence"]["memory_context_sha256"])
        self.open(); new = self.session.state()["plans"][0]
        self.assertFalse(new["claimed"])
        with self.assertRaisesRegex(SafetyError, "claim challenge changed"):
            self.session._operate("claim_plan", {"plan_id": new["id"], "revision": old["revision"]})
        self.session._operate("claim_plan", {"plan_id": new["id"], "revision": new["revision"]})
        self.assertEqual(2, self.session.state()["requests_used"])

    def test_relation_guard_precedes_config_on_revision(self):
        self.open(); repository = RepositoryGateway(self.repo)
        value = self.session.provider.inner; value.config_file = self.directory / "unused.env.local"
        self.manifest.write_bytes(self.manifest.read_bytes()+b" ")
        with patch("repo_maintainer.provider_config.load_provider_config", side_effect=AssertionError("credentials")):
            with self.assertRaisesRegex(SafetyError, "relations file changed"):
                value.revise(repository, "beta", self.policy.allowed_tests, {})

    def simulated_sdk(self, mode):
        self.open(); context = self.session.memory.require_current("beta addition")["context"]
        repository = RepositoryGateway(self.repo)
        plan = asdict(MockProvider().plan(repository, "beta addition", self.policy.allowed_tests))
        data = {"protocol_version": 1, "status": "completed", "plan": plan,
                "evidence": {"schema_version": 1, "status": "completed", "failure_category": None,
                    "requests": 2, "requests_started": 2, "count_scope": "adapter_stream_invocations",
                    "consumption": "reported", "model_cost_verified": False, "events_complete": True,
                    "events": [], "usage": [], "memory_context_sha256": context["context_sha256"]}}
        calls, original_popen = [], subprocess.Popen
        manifest = self.manifest.read_bytes()
        outer = self
        class Process:
            returncode = 0
            def __init__(self):
                self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
            def communicate(self, raw, timeout):
                request = json.loads(raw); calls.append(request)
                data["evidence"]["edit_protocol"] = "exact-replacements-v1"
                data["patches"] = [{"path": edit["path"], "replacements": [{
                    "oldText": next(f["content"] for f in request["files"] if f["path"] == edit["path"]),
                    "newText": edit["content"]}]} for edit in data["plan"]["edits"]]
                outer.assertEqual(context, request["source_memory"])
                if mode == "source_changed":
                    case = outer.root / "case.md"; case.write_bytes(case.read_bytes()+b"\nchanged unhit source")
                if mode == "manifest_changed": outer.manifest.write_bytes(manifest+b" ")
                if mode == "wrong_receipt":
                    data["evidence"]["memory_context_sha256"] = search_memory(outer.root, outer.repo, outer.index, "beta addition")["context"]["context_sha256"]
                return json.dumps(data).encode(), b""
        def factory(argv, *args, **kwargs):
            if len(argv)>1 and Path(argv[1]).name == "plan.mjs": return Process()
            return original_popen(argv, *args, **kwargs)
        with patch("repo_maintainer.pi_provider.subprocess.Popen", side_effect=factory):
            with self.assertRaises(SafetyError): self.session._operate("plan", {"issue": "beta addition"})
        self.assertEqual(1, len(calls))
        state = self.session.state()
        self.assertEqual([], state["plans"]); self.assertEqual([], state["runs"])
        self.assertEqual(4, state["requests_reserved_unknown"])
        self.assertEqual(0, state["requests_remaining"])
        return manifest

    def test_changed_manifest_after_sdk_cannot_return_plan_or_refund_allowance(self):
        original = self.simulated_sdk("manifest_changed")
        self.manifest.write_bytes(original); self.open()
        self.assertEqual(4, self.session.state()["requests_reserved_unknown"])

    def test_unhit_source_changed_after_sdk_cannot_return_plan(self):
        self.simulated_sdk("source_changed")

    def test_legacy_receipt_cannot_substitute_for_claim_envelope(self):
        self.simulated_sdk("wrong_receipt")

    def test_optin_without_ui_relation_binding_fails_before_credentials(self):
        value = PiProvider(("calculator.py",)); value.memory_relations_context = True
        value.config_file = self.directory / "unused.env.local"
        with patch("repo_maintainer.provider_config.load_provider_config", side_effect=AssertionError("credentials")):
            with self.assertRaisesRegex(SafetyError, "fixed UI memory binding"): value.plan(None, "issue", ())

    def test_projection_rejects_unverified_claims_and_context_fingerprints(self):
        self.open(); context = search_memory(self.root, self.repo, self.index, "beta")["context"]
        verified = self.session.memory.verify_relations()
        for wrong in ({**verified, "claims_only": False}, {**verified, "runtime_authorized": True},
                      {**verified, "manifest_sha256": "unknown"}):
            with self.assertRaises(SafetyError): with_relations(context, wrong)
        with self.assertRaises(SafetyError): with_relations({**context, "context_sha256": "a"*64}, verified)

    def test_cli_optin_requires_relations_before_constructing_provider(self):
        with contextlib.redirect_stderr(io.StringIO()), patch("repo_maintainer.cli.PiProvider", side_effect=AssertionError("provider")):
            self.assertEqual(1, main(["serve", "--memory-relations-context"]))


if __name__ == "__main__": unittest.main()
