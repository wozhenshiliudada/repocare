"""Real process deaths, owned worktrees, live test identity and explicit recovery."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import psutil
from starlette.testclient import TestClient
from repo_maintainer.cli import main
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, create_app
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.recovery import operation_lock
from repo_maintainer.repair_recovery import inspect_repair, recover_repair, save_execution
from repo_maintainer.workflow import cleanup_run, repair


class RepairRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repository")
        self.source = RepositoryGateway(self.repo)
        self.before = self.source.snapshot()
        self.policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))

    def crash(self, phase="candidate_checkpoint", code=94, **options):
        argv = [sys.executable, str(Path(__file__).with_name("repair_crash.py")), str(self.root), "--phase",phase]
        for name, value in options.items():
            if value:
                argv.append("--" + name.replace("_", "-"))
        result = subprocess.run(argv, capture_output=True, timeout=30)
        self.assertEqual(code, result.returncode, result.stderr.decode(errors="replace"))
        self.directory = next(p.parent for p in (self.root / "runs").glob("*/run.json"))
        self.workspace = self.directory / "workspace"
        self.identity = self.directory.name
        return json.loads((self.directory / "run.json").read_text())

    def abort(self):
        preview = inspect_repair(self.directory)
        self.assertTrue(preview["can_abort"], preview)
        return recover_repair(self.directory, preview["revision"], "test-operator")

    def test_real_exit_after_candidate_checkpoint_aborts_without_replay_or_source_changes(self):
        self.crash()
        original = (self.directory / "run.json").read_bytes()
        with patch("repo_maintainer.workflow.run_test", side_effect=AssertionError("Do not replay")):
            preview = inspect_repair(self.directory)
            self.assertTrue(preview["can_abort"])
            self.assertEqual(original, (self.directory / "run.json").read_bytes())
            result = recover_repair(self.directory, preview["revision"])
        self.assertEqual("repair_aborted", result["status"])
        self.assertTrue(result["source_unchanged"])
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.before, self.source.snapshot())
        self.assertEqual("aborted", inspect_repair(self.directory)["status"])
        self.assertFalse(inspect_repair(self.directory)["can_abort"])
        execution = json.loads((self.directory / "repair-execution.json").read_text())
        self.assertEqual(1, len(execution["abort_authorizations"]))
        self.assertEqual("unknown", execution["abort_authorization"]["inspection"]["execution_outcome"])
        self.assertIn("Repair process evidence", (self.directory / "report.md").read_text())

    def test_real_exit_after_workspace_ready_before_any_test_is_recoverable(self):
        self.crash("workspace_ready")
        self.assertEqual([], json.loads((self.directory / "repair-execution.json").read_text())["process_facts"])
        self.abort()
        self.assertEqual(self.before, self.source.snapshot())

    def test_real_exit_after_baseline_result_preserves_facts_but_never_promotes_success(self):
        self.crash("baseline_result")
        evidence = json.loads((self.directory / "repair-execution.json").read_text())
        self.assertEqual(1, evidence["process_facts"][0]["returncode"])
        result = self.abort()
        self.assertEqual("repair_aborted", result["status"])

    def test_real_exit_after_green_candidate_result_still_requires_explicit_abort(self):
        record = self.crash("candidate_result")
        self.assertFalse(record.get("finished_at"))
        self.assertEqual(0, json.loads((self.directory / "repair-execution.json").read_text())["process_facts"][-1]["returncode"])
        with self.assertRaises(SafetyError):
            cleanup_run(self.directory)
        self.assertTrue(self.workspace.exists())
        self.abort()

    def test_live_recorded_test_refuses_abort_then_exit_allows_fresh_inspection(self):
        self.crash("baseline_pid", live_test=True)
        preview = inspect_repair(self.directory)
        self.assertIn("process_still_alive", preview["reasons"])
        self.assertFalse(preview["can_abort"])
        with self.assertRaises(SafetyError):
            recover_repair(self.directory, preview["revision"])
        pid = preview["checks"]["inflight"]["identity"]["pid"]
        # Wait only for our exact fixture process; never kill or poll an external Agent.
        try:
            psutil.Process(pid).wait(timeout=8)
        except psutil.NoSuchProcess:
            pass
        self.abort()

    def test_unknown_test_pid_window_refuses_all_cleanup(self):
        self.crash("test_starting")
        preview = inspect_repair(self.directory)
        self.assertIn("process_pid_launch_gap", preview["reasons"])
        with self.assertRaises(SafetyError):
            recover_repair(self.directory, preview["revision"])
        with self.assertRaises(SafetyError):
            cleanup_run(self.directory)
        self.assertTrue(self.workspace.exists())

    def test_unfinished_edit_window_preserves_candidate(self):
        self.crash("editing")
        preview = inspect_repair(self.directory)
        self.assertIn("unfinished_external_or_edit_window", preview["reasons"])
        self.assertFalse(preview["can_abort"])
        self.assertTrue(self.workspace.exists())

    def test_source_user_edit_invalidates_preview_without_stashing_or_reset(self):
        self.crash()
        preview = inspect_repair(self.directory)
        path = self.repo / "calculator.py"
        path.write_text("# user's source edit\n")
        with self.assertRaises(SafetyError):
            recover_repair(self.directory, preview["revision"])
        self.assertIn("source_snapshot_changed", inspect_repair(self.directory)["reasons"])
        self.assertEqual("# user's source edit\n", path.read_text())
        self.assertTrue(self.workspace.exists())

    def test_tracked_untracked_ignored_and_empty_directory_changes_are_preserved(self):
        self.crash()
        cases = [(self.workspace / "calculator.py", b"# human edit\n"),
                 (self.workspace / "human.txt", b"manual"), (self.workspace / "__pycache__" / "ignored.pyc", b"ignored")]
        for path, content in cases:
            old = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            preview = inspect_repair(self.directory)
            self.assertFalse(preview["can_abort"], str(path))
            with self.assertRaises(SafetyError):
                recover_repair(self.directory, preview["revision"])
            self.assertEqual(content, path.read_bytes())
            if old is None:
                path.unlink()
                if path.parent.name == "__pycache__":
                    path.parent.rmdir()
            else:
                path.write_bytes(old)
        empty = self.workspace / "human-empty"
        empty.mkdir()
        self.assertFalse(inspect_repair(self.directory)["can_abort"])
        self.assertTrue(empty.is_dir())

    def test_changed_candidate_index_refuses_abort(self):
        self.crash()
        candidate = RepositoryGateway(self.workspace)
        candidate.command("add", "calculator.py")
        self.assertFalse(inspect_repair(self.directory)["can_abort"])
        self.assertTrue(self.workspace.exists())

    def test_advanced_candidate_ref_and_locked_registration_are_preserved(self):
        self.crash()
        self.source.command("worktree","lock", "--reason","human hold", str(self.workspace))
        self.assertFalse(inspect_repair(self.directory)["can_abort"])
        self.source.command("worktree","unlock",str(self.workspace))
        candidate = RepositoryGateway(self.workspace)
        candidate.command("add","calculator.py")
        candidate.command("-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-m","human commit")
        preview = inspect_repair(self.directory)
        self.assertIn("candidate_ref_changed",preview["reasons"])
        self.assertFalse(preview["can_abort"])
        self.assertTrue(self.workspace.exists())

    def test_worktree_launch_window_with_no_checkpoint_requires_manual_inspection(self):
        self.crash("workspace_add_intent")
        preview = inspect_repair(self.directory)
        self.assertIn("unfinished_external_or_edit_window",preview["reasons"])
        self.assertFalse(preview["can_abort"])
        self.assertEqual(self.before,self.source.snapshot())

    def test_foreign_worktree_on_same_candidate_branch_is_never_removed(self):
        self.crash()
        other = self.root / "human-workspace"
        branch = "maintenance/"+self.identity
        self.source.command("worktree","add","--force",str(other),branch)
        preview = inspect_repair(self.directory)
        self.assertIn("candidate_registered_at_other_path",preview["reasons"])
        with self.assertRaises(SafetyError):
            recover_repair(self.directory,preview["revision"])
        self.assertTrue(other.exists())
        self.assertTrue(self.workspace.exists())

    def test_candidate_ref_race_at_delete_uses_expected_old_sha_and_preserves_new_ref(self):
        self.crash()
        tree = self.source.command("rev-parse","HEAD^{tree}").decode().strip()
        commit = self.source.command("-c","user.name=Fixture","-c","user.email=fixture@example.invalid",
                                     "commit-tree",tree,"-p",self.source.head,input_data=b"human race\n").decode().strip()
        original = RepositoryGateway.command
        branch = "refs/heads/maintenance/"+self.identity
        def changed(repo,*args,**kw):
            if args[:2] == ("update-ref","-d"):
                original(repo,"update-ref",branch,commit)
            return original(repo,*args,**kw)
        with patch.object(RepositoryGateway,"command",changed):
            with self.assertRaises(SafetyError):
                self.abort()
        self.assertEqual(commit,self.source.command("rev-parse",branch).decode().strip())
        self.assertFalse(self.workspace.exists())
        self.assertFalse(inspect_repair(self.directory)["can_abort"])

    def test_unknown_abort_git_launch_gap_keeps_remaining_branch(self):
        self.crash()
        self.crash("before_branch_delete",95)
        preview = inspect_repair(self.directory)
        self.assertIn("process_pid_launch_gap",preview["reasons"])
        self.assertFalse(preview["can_abort"])
        self.assertTrue(preview["checks"]["branch"])

    def test_tampered_journal_and_legacy_missing_evidence_are_preserved(self):
        self.crash()
        path = self.directory / "repair-execution.json"
        data = json.loads(path.read_text())
        data["owner"]["branch"] = "main"
        path.write_text(json.dumps(data))
        with self.assertRaises(SafetyError):
            inspect_repair(self.directory)
        path.unlink()
        with self.assertRaises(OSError):
            inspect_repair(self.directory)
        with self.assertRaises(SafetyError):
            cleanup_run(self.directory)
        self.assertTrue(self.workspace.exists())

    def test_real_exit_after_removal_resumes_with_new_approval_and_keeps_old_authorization(self):
        self.crash()
        old = inspect_repair(self.directory)
        self.crash("after_remove", 95)
        self.assertFalse(self.workspace.exists())
        with self.assertRaises(SafetyError):
            recover_repair(self.directory, old["revision"])
        self.abort()
        data = json.loads((self.directory / "repair-execution.json").read_text())
        self.assertEqual(2, len(data["abort_authorizations"]))
        self.assertEqual(self.before, self.source.snapshot())

    def test_real_exit_after_branch_delete_can_finish_report_without_git_replay(self):
        self.crash()
        self.crash("after_branch_delete", 95)
        self.assertEqual(self.before, self.source.snapshot())
        original = RepositoryGateway.command
        def no_mutation(repo, *args, **kw):
            self.assertNotIn(args[:2], (("worktree","remove"), ("update-ref","-d")))
            return original(repo,*args,**kw)
        with patch.object(RepositoryGateway,"command",no_mutation):
            self.abort()
        self.assertEqual(self.before, self.source.snapshot())

    def test_explicit_cli_inspection_and_approval_and_live_operation_lock(self):
        self.crash()
        with operation_lock(self.directory):
            with self.assertRaisesRegex(SafetyError, "Another operation"):
                inspect_repair(self.directory)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, main(["inspect-repair", "--run-dir",str(self.directory)]))
        preview = json.loads(output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, main(["recover-repair","--run-dir",str(self.directory),"--approve-abort","0"*64]))
            self.assertEqual(0, main(["recover-repair","--run-dir",str(self.directory),"--approve-abort",preview["revision"]]))
        self.assertEqual(self.before, self.source.snapshot())

    def test_ui_restart_new_challenge_budget_and_authenticated_explicit_abort(self):
        session = UISession(self.repo,self.policy,MockProvider(),self.root / "runs")
        session._operate("plan", {"issue":"addition bug"})
        session.close()
        self.crash(ui=True)
        session = UISession(self.repo,self.policy,MockProvider(),self.root / "runs")
        budget = session.registry.budget()
        old = session._operate("inspect_repair", {"run_id":self.identity})["repair_inspection"]
        session.close()
        session = UISession(self.repo,self.policy,MockProvider(),self.root / "runs")
        self.addCleanup(session.close)
        with self.assertRaisesRegex(SafetyError,"session changed"):
            session._operate("recover_repair", {"run_id":self.identity,"revision":old["session_revision"]})
        with TestClient(create_app(session,12345),base_url="http://127.0.0.1:12345") as client:
            headers = {"Authorization":"Bearer " + session.token}
            def action(name, body):
                request_id = uuid.uuid4().hex
                data = {**body, "request_id":request_id}
                response = client.post("/api/actions/"+name,json=data,headers=headers)
                self.assertEqual(202,response.status_code,response.text)
                deadline = time.monotonic()+30
                while time.monotonic()<deadline:
                    state = client.get("/api/state",headers=headers).json()
                    job = next(j for j in state["jobs"] if j["id"]==request_id)
                    if job["status"] != "running":
                        self.assertEqual("finished",job["status"],job)
                        return job["result"], state, data
                    time.sleep(.03)
                self.fail("Recovery API deadline")
            self.assertEqual(401,client.post("/api/actions/inspect_repair",json={"run_id":self.identity,"request_id":uuid.uuid4().hex}).status_code)
            preview = action("inspect_repair",{"run_id":self.identity})[0]["repair_inspection"]
            result,state,data = action("recover_repair",{"run_id":self.identity,"revision":preview["session_revision"]})
            self.assertEqual("repair_aborted",result["workflow_status"])
            self.assertEqual("consumed",state["plans"][0]["status"])
            self.assertEqual("repair_aborted",state["runs"][0]["status"])
            self.assertEqual(202,client.post("/api/actions/recover_repair",json=data,headers=headers).status_code)
            self.assertEqual(budget,session.registry.budget())
            self.assertEqual(self.before,self.source.snapshot())


if __name__ == "__main__":
    unittest.main()
