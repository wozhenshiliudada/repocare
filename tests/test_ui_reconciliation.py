import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, create_app, json_digest, validate_payload
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import SavedPlanProvider
from repo_maintainer.provider import MockProvider
from repo_maintainer.workflow import cleanup_run, repair


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repository")
        self.source = RepositoryGateway(self.repo)
        self.before = self.source.snapshot()
        self.policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
        self.session = UISession(self.repo, self.policy, MockProvider(), self.root / "runs")
        self.addCleanup(self.finish)
        self.session._operate("plan", {"issue":"addition bug"})
        self.plan = self.session.state()["plans"][0]
        self.session.close()
        self.crash("consume", 23)
        self.restart()
        self.identity = self.session.state()["runs"][0]["run_id"]
        self.directory = self.root / "runs" / self.identity

    def finish(self):
        self.session.close()
        for identity in self.session.runs:
            if (self.root / "runs" / identity / "run.json").is_file():
                try:
                    record = json.loads((self.root / "runs" / identity / "run.json").read_text())
                except ValueError:
                    continue  # This test deliberately preserves unreadable foreign evidence.
                if record.get("source_repo") == str(self.repo) and record.get("base_sha"):
                    cleanup_run(self.root / "runs" / identity)

    def crash(self, phase, expected):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("ui_consumption_crash.py")),
                                 str(self.root), "--phase", phase], timeout=30)
        self.assertEqual(expected, result.returncode)

    def restart(self):
        self.session.close()
        self.session = UISession(self.repo, self.policy, MockProvider(), self.root / "runs")

    def inspect(self):
        return self.session._operate("inspect_interruption", {"run_id":self.identity})["reconciliation"]

    def close_gap(self, preview=None):
        preview = preview or self.inspect()
        return self.session._operate("close_interruption", {"run_id":self.identity, "revision":preview["revision"]})

    def assert_consumed(self):
        self.assertEqual("consumed", self.session.state()["plans"][0]["status"])
        with self.assertRaises(SafetyError):
            self.session._operate("decision", {"plan_id":self.plan["id"], "fingerprint":self.plan["fingerprint"], "decision":"approve"})

    def test_readonly_inspection_and_explicit_closure_survive_restart_without_replay(self):
        budget = self.session.registry.budget()
        original = self.session.registry.load("run")
        with patch("repo_maintainer.local_ui.repair", side_effect=AssertionError("Never replay")):
            preview = self.inspect()
            self.assertTrue(preview["can_close_registration"])
            self.assertEqual(original, self.session.registry.load("run"))
            result = self.close_gap(preview)
        self.assertEqual("registration_closed", result["workflow_status"])
        self.assertEqual("unknown", result["reconciliation"]["execution_outcome"])
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.before, self.source.snapshot())
        self.restart()
        run = self.session.state()["runs"][0]
        self.assertEqual("registration_closed", run["status"])
        self.assertIsNone(run["reconciliation"], "Old inspection cannot authorize a new session")
        self.assertEqual(budget, self.session.registry.budget())
        self.assert_consumed()
        self.assertFalse(self.inspect()["can_close_registration"])

    def test_new_session_invalidates_old_closure_challenge(self):
        preview = self.inspect()
        self.restart()
        with self.assertRaisesRegex(SafetyError, "session changed"):
            self.close_gap(preview)
        self.assertNotIn("resolution", self.session.registry.load("run")[self.identity])

    def test_source_changes_after_preview_refuse_closure_and_preserve_user_edits(self):
        preview = self.inspect()
        path = self.repo / "calculator.py"
        path.write_text("# user's uncommitted edit\n", encoding="utf-8")
        with self.assertRaises(SafetyError):
            self.close_gap(preview)
        self.assertFalse(self.inspect()["can_close_registration"])
        self.assertEqual("# user's uncommitted edit\n", path.read_text())

    def test_tampered_saved_plan_cannot_be_closed_even_with_fresh_preview(self):
        path = self.session.plans[self.plan["id"]]["path"]
        path.write_bytes(path.read_bytes() + b" ")
        preview = self.inspect()
        self.assertFalse(preview["checks"]["plan_matches"])
        with self.assertRaises(SafetyError):
            self.close_gap(preview)
        self.assertTrue(path.read_bytes().endswith(b" "))

    def test_empty_directory_and_unknown_files_are_preserved(self):
        preview = self.inspect()
        self.directory.mkdir()
        with self.assertRaises(SafetyError):
            self.close_gap(preview)
        unknown = self.directory / "human.txt"
        unknown.write_text("preserve", encoding="utf-8")
        with self.assertRaises(SafetyError):
            self.close_gap()
        self.assertEqual("preserve", unknown.read_text())

    def test_existing_foreign_and_malformed_run_evidence_are_never_rebuilt(self):
        self.directory.mkdir()
        path = self.directory / "run.json"
        for content in ('{"run_id":"foreign","source_repo":"elsewhere"}', "not-json"):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                preview = self.inspect()
                self.assertFalse(preview["can_close_registration"])
                with self.assertRaises(SafetyError):
                    self.close_gap(preview)
                self.assertEqual(content, path.read_text())

    def test_candidate_and_publication_branch_orphans_are_preserved(self):
        for kind, key in (("maintenance", "candidate_ref"), ("integration", "publication_ref")):
            branch = kind + "/" + self.identity
            self.source.command("branch", branch, self.source.head)
            preview = self.inspect()
            self.assertEqual(self.source.head, preview["checks"][key])
            with self.assertRaises(SafetyError):
                self.close_gap(preview)
            self.assertEqual(self.source.head, self.source.command("rev-parse", branch).decode().strip())
            self.source.command("branch", "-d", branch)

    def test_dangling_detached_worktree_registration_blocks_closure(self):
        workspace = self.directory / "workspace"
        self.source.command("worktree", "add", "--detach", str(workspace), self.source.head)
        self.assertTrue(workspace.resolve().is_relative_to(self.root.resolve()))
        shutil.rmtree(workspace)
        self.directory.rmdir()
        preview = self.inspect()
        self.assertFalse(preview["checks"]["directory_exists"])
        self.assertEqual(1, len(preview["checks"]["worktree_registrations"]))
        with self.assertRaises(SafetyError):
            self.close_gap(preview)
        self.assertIn(str(workspace).replace("\\", "/"), self.source.command("worktree", "list", "--porcelain").decode().replace("\\", "/"))

    def test_linked_run_paths_are_refused_without_touching_target(self):
        target = self.root / "unrelated"
        target.mkdir()
        (target / "human.txt").write_text("untouched", encoding="utf-8")
        self.directory.symlink_to(target, target_is_directory=True)
        with self.assertRaises(SafetyError):
            self.inspect()
        self.assertEqual("untouched", (target / "human.txt").read_text())

    def test_crash_before_resolution_commit_rolls_back_but_preserves_consumption(self):
        self.session.close()
        self.crash("close_before_commit", 25)
        self.restart()
        self.assertNotIn("resolution", self.session.registry.load("run")[self.identity])
        self.assertTrue(self.inspect()["can_close_registration"])
        self.assert_consumed()

    def test_crash_after_resolution_commit_does_not_repeat_or_reopen(self):
        self.session.close()
        self.crash("close_after_commit", 24)
        self.restart()
        self.assertEqual("registration_closed", self.session.state()["runs"][0]["status"])
        self.assertEqual("interrupted", self.session.jobs["f"*32]["status"])
        self.assertFalse(self.inspect()["can_close_registration"])
        self.assert_consumed()

    def test_late_real_run_evidence_after_closure_blocks_all_mutating_actions(self):
        self.close_gap()
        record = repair(self.repo, "addition bug", SavedPlanProvider(self.session.plans[self.plan["id"]]["path"]),
                        self.policy, "approval", self.root / "runs", approval=lambda p:"approve", run_id=self.identity)
        self.assertEqual("candidate_ready", record["status"])
        self.restart()
        run = self.session.state()["runs"][0]
        self.assertEqual("resolution_conflict", run["status"])
        for action in ("claim_run", "prepare", "publish", "cleanup", "recover"):
            with self.assertRaisesRegex(SafetyError, "Closed registration"):
                self.session._operate(action, {"run_id":self.identity, "revision":json_digest(run)})
        self.assertTrue((self.directory / "workspace").exists())
        self.assertEqual("resolution_conflict", self.inspect()["status"])

    def test_late_unreadable_evidence_after_closure_is_visible_on_restart(self):
        self.close_gap()
        self.directory.mkdir()
        (self.directory / "run.json").write_text("not-json", encoding="utf-8")
        self.restart()
        self.assertEqual("resolution_conflict", self.session.state()["runs"][0]["status"])
        self.assertFalse(self.inspect()["can_close_registration"])
        self.assert_consumed()

    def test_strict_actions_and_foreign_id_cannot_select_arbitrary_paths(self):
        with self.assertRaises(SafetyError):
            validate_payload("inspect_interruption", {"request_id":uuid.uuid4().hex, "run_id":self.identity, "path":"elsewhere"})
        for identity in ("../escape", uuid.uuid4().hex):
            with self.assertRaises(SafetyError):
                self.session._operate("inspect_interruption", {"run_id":identity})

    def test_authenticated_api_explicit_closure_and_duplicate_request_do_not_replay(self):
        with TestClient(create_app(self.session, 12345), base_url="http://127.0.0.1:12345") as client:
            headers = {"Authorization":"Bearer " + self.session.token}
            url = "/api/actions/inspect_interruption"
            body = {"request_id":uuid.uuid4().hex, "run_id":self.identity}
            self.assertEqual(401, client.post(url, json=body).status_code)
            self.assertEqual(403, client.post(url, json=body,
                             headers={**headers, "Origin":"https://foreign.test"}).status_code)
            def wait(identity):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    state = client.get("/api/state", headers=headers).json()
                    job = next(j for j in state["jobs"] if j["id"] == identity)
                    if job["status"] != "running":
                        self.assertEqual("finished", job["status"], job)
                        return job
                    time.sleep(.03)
                self.fail("Reconciliation API did not finish")
            response = client.post(url, json=body, headers=headers)
            self.assertEqual(202, response.status_code)
            preview = wait(response.json()["id"])["result"]["reconciliation"]
            self.assertTrue(preview["can_close_registration"])
            body = {"request_id":uuid.uuid4().hex, "run_id":self.identity, "revision":preview["revision"]}
            url = "/api/actions/close_interruption"
            first = client.post(url, json=body, headers=headers)
            self.assertEqual(202, first.status_code)
            job = wait(first.json()["id"])
            self.assertEqual("registration_closed", job["result"]["workflow_status"])
            entry = self.session.registry.load("run")
            duplicate = client.post(url, json=body, headers=headers)
            self.assertEqual(first.json()["id"], duplicate.json()["id"])
            self.assertEqual(entry, self.session.registry.load("run"))
            conflict = client.post(url, json={**body, "revision":"0"*64}, headers=headers)
            self.assertEqual(409, conflict.status_code)
            state = client.get("/api/state", headers=headers).json()
            self.assertEqual("registration_closed", state["runs"][0]["status"])
            self.assertEqual("consumed", state["plans"][0]["status"])
            self.assertFalse(self.directory.exists())
            self.assertEqual(self.before, self.source.snapshot())


if __name__ == "__main__":
    unittest.main()
