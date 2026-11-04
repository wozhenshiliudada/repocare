from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.cli import main
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import Edit, Policy, SafetyError, canonical_path
from repo_maintainer.pi_provider import SavedPlanProvider
from repo_maintainer.provider import MockProvider
from repo_maintainer.runner import run_test
from repo_maintainer.store import redact
from repo_maintainer.workflow import cleanup_run, plan_repair, repair


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repo")
        self.gateway = RepositoryGateway(self.repo)
        self.before = self.gateway.snapshot()
        self.policy = Policy(("calculator.py",), (DEMO_TEST,))
        self.runs = self.root / "runs"

    def execute(self, scenario="fix", mode="auto", approval=None, policy=None, provider=None):
        result = repair(self.repo, "Addition bug", provider or MockProvider(scenario),
                        policy or self.policy, mode, self.runs, approval)
        if Path(result["workspace"]).exists():
            self.addCleanup(cleanup_run, Path(result["workspace"]).parent)
        return result

    def unchanged(self, record):
        self.assertTrue(record["source_unchanged"], record)
        self.assertEqual(self.before, self.gateway.snapshot(
            exclude_branch=record["branch"] if record["status"] == "candidate_ready" else ""))

    def test_red_to_green_and_reviewable_candidate(self):
        result = self.execute()
        self.assertEqual("candidate_ready", result["status"], result.get("error"))
        self.assertNotEqual(0, result["baseline_tests"][0]["returncode"])
        self.assertEqual(0, result["candidate_tests"][0]["returncode"])
        self.assertIn("+    return a + b", result["diff"])
        self.assertEqual("disabled", result["integration"])
        self.unchanged(result)
        saved = json.loads((Path(result["workspace"]).parent / "run.json").read_text())
        self.assertEqual(result["status"], saved["status"])
        cleaned = cleanup_run(Path(result["workspace"]).parent)
        self.assertEqual("discarded", cleaned["status"])
        self.assertEqual(self.before, self.gateway.snapshot())

    def test_bad_patch_rolls_back_with_evidence(self):
        result = self.execute("broken")
        self.assertEqual("rolled_back", result["status"], result)
        self.assertNotEqual(0, result["candidate_tests"][0]["returncode"])
        self.assertFalse(Path(result["workspace"]).exists())
        self.assertTrue((Path(result["workspace"]).parent / "candidate.diff").exists())
        self.assertEqual("candidate_removed", result["recovery"]["result"])
        self.unchanged(result)

    def test_source_baseline_is_durable_before_a_provider_returns_any_plan(self):
        owner=self
        class NoPlanProvider:
            def plan(self, repository, issue, tests):
                records=list(owner.runs.glob('*/run.json'))
                owner.assertEqual(len(records),1)
                saved=json.loads(records[0].read_bytes())
                owner.assertEqual(saved['base_sha'],owner.before['head'])
                owner.assertEqual(saved['source_before'],owner.before)
                owner.assertNotIn('plan',saved)
                raise SafetyError('controlled no-plan failure')
        result=self.execute(provider=NoPlanProvider())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['base_sha'],self.before['head'])
        self.assertNotIn('plan',result)
        self.assertEqual([],result['rounds'])
        self.assertEqual([],result['candidate_tests'])
        self.assertFalse(Path(result['workspace']).exists())
        self.unchanged(result)

    def test_negative_input_regression_is_blocked(self):
        result = self.execute("regression")
        self.assertEqual("rolled_back", result["status"])
        self.assertIn("test_negative_regression", result["candidate_tests"][0]["stderr"])
        self.unchanged(result)

    def test_rejection_never_creates_workspace_or_runs_tests(self):
        result = self.execute(mode="approval", approval=lambda plan: "reject")
        self.assertEqual("rejected", result["status"])
        self.assertEqual([], result["baseline_tests"])
        self.assertFalse(Path(result["workspace"]).exists())
        self.unchanged(result)

    def test_missing_approval_waits_without_writes(self):
        result = self.execute(mode="approval")
        self.assertEqual("awaiting_approval", result["status"])
        self.assertFalse(Path(result["workspace"]).exists())
        self.unchanged(result)

    def test_approval_precedes_even_baseline_tests(self):
        def approve(plan):
            self.assertEqual(self.before, self.gateway.snapshot())
            self.assertEqual(1, self.gateway.command("worktree", "list", "--porcelain")
                             .decode().count("worktree "))
            self.assertEqual("calculator.py", plan.edits[0].path)
            return "approve"
        result = self.execute(mode="approval", approval=approve)
        self.assertEqual("candidate_ready", result["status"])
        self.assertEqual(result["plan_sha256"], result["authorization"]["plan_sha256"])
        self.unchanged(result)

    def test_out_of_scope_path_is_denied_before_workspace(self):
        result = self.execute(policy=Policy(("other.py",), (DEMO_TEST,)))
        self.assertEqual("failed", result["status"])
        self.assertFalse(Path(result["workspace"]).exists())
        self.unchanged(result)

    def test_unapproved_command_is_denied(self):
        class RogueProvider(MockProvider):
            def plan(self, repository, issue, tests):
                return replace(super().plan(repository, issue, tests), tests=(("unapproved",),))
        result = self.execute(provider=RogueProvider())
        self.assertEqual("failed", result["status"])
        self.assertIn("not explicitly authorized", result["error"])
        self.unchanged(result)

    def test_dirty_source_preserved_including_staged_untracked(self):
        (self.repo / "calculator.py").write_text("user work\n")
        self.gateway.command("add", "calculator.py")
        (self.repo / "notes.txt").write_text("user untracked\n")
        before = self.gateway.snapshot()
        result = self.execute()
        self.assertEqual("failed", result["status"])
        self.assertEqual(before, self.gateway.snapshot())
        self.assertFalse(Path(result["workspace"]).exists())
        self.assertTrue(result["source_unchanged"])

    def test_source_changed_during_approval_requires_replan(self):
        def approve(plan):
            (self.repo / "notes.txt").write_text("concurrent user edit")
            return "approve"
        result = self.execute(mode="approval", approval=approve)
        self.assertEqual("source_changed", result["status"])
        self.assertFalse(Path(result["workspace"]).exists())
        self.assertEqual("concurrent user edit", (self.repo / "notes.txt").read_text())

    def test_candidate_timeout_kills_process_and_rolls_back(self):
        # Baseline fails immediately; the patched candidate sleeps past its timeout.
        command = ("{python}", "-c", "import calculator,time,sys; "
                   "time.sleep(10) if calculator.add(2,3)==5 else sys.exit(1)")
        result = self.execute(policy=Policy(("calculator.py",), (command,), .3))
        self.assertEqual("rolled_back", result["status"], result)
        self.assertTrue(result["candidate_tests"][0]["timed_out"])
        self.assertFalse(Path(result["workspace"]).exists())
        self.unchanged(result)

    def test_baseline_timeout_cleans_before_edit(self):
        command = ("{python}", "-c", "import time; time.sleep(10)")
        result = self.execute(policy=Policy(("calculator.py",), (command,), .3))
        self.assertEqual("rolled_back", result["status"])
        self.assertTrue(result["baseline_tests"][0]["timed_out"])
        self.assertEqual([], result["candidate_tests"])
        self.assertEqual("", result["diff"])
        self.unchanged(result)

    def test_hard_process_exit_with_unknown_test_launch_gap_preserves_candidate(self):
        # Production cannot infer whether launch happened from this unjournaled-PID window.
        src = str(Path(__file__).resolve().parents[1] / "src")
        code = ("import sys,os; from pathlib import Path; from unittest.mock import patch; "
                "sys.path.insert(0,sys.argv[1]); "
                "from repo_maintainer.workflow import repair; "
                "from repo_maintainer.provider import MockProvider; "
                "from repo_maintainer.models import Policy; "
                "from repo_maintainer.demo import DEMO_TEST; "
                "p=patch('repo_maintainer.workflow.run_test',side_effect=lambda *a,**kw:os._exit(99)); "
                "p.start(); repair(Path(sys.argv[2]),'issue',MockProvider(),"
                "Policy(('calculator.py',),(DEMO_TEST,)),'auto',Path(sys.argv[3]))")
        completed = subprocess.run([sys.executable, "-c", code, src, str(self.repo), str(self.runs)],
                                   capture_output=True, timeout=15)
        self.assertEqual(99, completed.returncode, completed.stderr)
        run_dir = next(self.runs.iterdir())
        saved = json.loads((run_dir / "run.json").read_text())
        self.assertEqual("baseline_testing", saved["status"])
        self.assertTrue((run_dir / "workspace").exists())
        with self.assertRaisesRegex(SafetyError, "explicit recover-repair"):
            cleanup_run(run_dir)
        from repo_maintainer.repair_recovery import inspect_repair
        preview = inspect_repair(run_dir)
        self.assertFalse(preview["can_abort"])
        self.assertIn("process_pid_launch_gap", preview["reasons"])
        self.assertTrue((run_dir / "workspace").exists())
        self.assertEqual(self.before, self.gateway.snapshot(exclude_branch=saved["branch"]))

    def test_candidate_test_side_effects_are_not_accepted(self):
        command = ("{python}", "-c", "import calculator,sys; from pathlib import Path; "
                   "Path('extra.txt').write_text('side effect') if calculator.add(2,3)==5 "
                   "else sys.exit(1)")
        result = self.execute(policy=Policy(("calculator.py",), (command,)))
        self.assertEqual("rolled_back", result["status"])
        self.assertIn("outside the approved patch", result["error"])
        self.unchanged(result)

    def test_symlink_to_external_file_is_not_editable(self):
        external = self.root / "outside.py"
        external.write_text("private data")
        link = self.repo / "link.py"
        try:
            link.symlink_to(external)
        except OSError:
            self.skipTest("OS does not grant symlink creation rights")
        with self.assertRaises(SafetyError):
            self.gateway.safe_file("link.py")
        self.assertEqual("private data", external.read_text())

    def test_cleanup_record_tampering_is_rejected(self):
        result = self.execute()
        run_dir = Path(result["workspace"]).parent
        path = run_dir / "run.json"
        text = path.read_text()
        tampered = json.loads(text)
        tampered["workspace"] = str(self.repo)
        path.write_text(json.dumps(tampered))
        try:
            with self.assertRaises(SafetyError):
                cleanup_run(run_dir)
            self.unchanged(result)
        finally:
            path.write_text(text)

    def test_interruption_retains_evidence_and_cleans_candidate(self):
        with patch("repo_maintainer.workflow.run_test", side_effect=KeyboardInterrupt):
            result = self.execute()
        self.assertEqual("rolled_back", result["status"])
        self.assertIn("interrupted", result["error"])
        self.unchanged(result)

    def test_no_reproduced_failure_does_not_claim_repair(self):
        command = ("{python}", "-c", "pass")
        result = self.execute(policy=Policy(("calculator.py",), (command,)))
        self.assertEqual("rolled_back", result["status"])
        self.assertIn("Baseline already passes", result["error"])
        self.assertEqual([], result["candidate_tests"])
        self.unchanged(result)

    def test_baseline_test_file_writes_are_rejected(self):
        command = ("{python}", "-c", "from pathlib import Path; import sys; "
                   "Path('extra.txt').write_text('side effect'); sys.exit(1)")
        result = self.execute(policy=Policy(("calculator.py",), (command,)))
        self.assertEqual("rolled_back", result["status"])
        self.assertIn("Baseline tests modified", result["error"])
        self.unchanged(result)

    def test_stale_edit_precondition_does_not_write(self):
        class StaleProvider(MockProvider):
            def plan(self, repository, issue, tests):
                plan = super().plan(repository, issue, tests)
                return replace(plan, edits=(replace(plan.edits[0], before_sha256="0" * 64),))
        result = self.execute(provider=StaleProvider())
        self.assertEqual("rolled_back", result["status"])
        self.assertEqual("", result["diff"])
        self.unchanged(result)

    def test_report_path_in_source_is_refused(self):
        with self.assertRaises(SafetyError):
            repair(self.repo, "issue", MockProvider(), self.policy, "auto", self.repo / "reports")
        self.assertEqual(self.before, self.gateway.snapshot())

    def test_advanced_candidate_is_preserved_on_cleanup(self):
        result = self.execute()
        candidate = RepositoryGateway(Path(result["workspace"]))
        candidate.command("add", "calculator.py")
        candidate.command("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                          "commit", "-m", "user kept candidate")
        with self.assertRaises(SafetyError):
            cleanup_run(Path(result["workspace"]).parent)
        self.assertTrue(Path(result["workspace"]).exists())
        # Test teardown may remove its own temporary workspace, without changing main.
        self._cleanups.pop()  # remove guarded cleanup: expected to refuse advanced candidate
        self.gateway.command("worktree", "remove", "--force", result["workspace"])
        self.gateway.command("branch", "-D", result["branch"])
        self.assertEqual(self.before, self.gateway.snapshot())

    def test_cleanup_idempotent(self):
        result = self.execute("broken")
        first = cleanup_run(Path(result["workspace"]).parent)
        second = cleanup_run(Path(result["workspace"]).parent)
        self.assertEqual("discarded", first["status"])
        self.assertEqual("discarded", second["status"])
        self.assertEqual(self.before, self.gateway.snapshot())

    def test_cli_plan_then_approval_fingerprint(self):
        policy_path = self.root / "policy.json"
        policy_path.write_text(json.dumps({"allowed_paths": ["calculator.py"],
                                          "allowed_tests": [list(DEMO_TEST)]}))
        plan = plan_repair(self.repo, "issue", MockProvider(), self.policy)
        args = ["repair", "--repo", str(self.repo), "--issue", "issue", "--policy", str(policy_path),
                "--runs-dir", str(self.runs), "--approve-plan", plan.fingerprint, "--provider", "mock"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(args))
        run_dir = next(self.runs.iterdir())
        self.addCleanup(cleanup_run, run_dir)
        self.assertEqual("candidate_ready", json.loads((run_dir / "run.json").read_text())["status"])

    def test_stale_cli_approval_does_not_create_workspace(self):
        policy_path = self.root / "policy.json"
        policy_path.write_text(json.dumps({"allowed_paths": ["calculator.py"],
                                          "allowed_tests": [list(DEMO_TEST)]}))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(1, main(["repair", "--repo", str(self.repo), "--issue", "issue",
                                     "--policy", str(policy_path), "--runs-dir", str(self.runs),
                                     "--approve-plan", "0" * 64, "--provider", "mock"]))
        self.assertEqual(self.before, self.gateway.snapshot())

    def test_saved_plan_resumes_exact_reviewed_contents(self):
        from dataclasses import asdict
        plan = plan_repair(self.repo, "Addition bug", MockProvider(), self.policy)
        path = self.root / "reviewed-plan.json"
        path.write_text(json.dumps({"plan": asdict(plan), "plan_sha256": plan.fingerprint}))
        result = self.execute(mode="approval", approval=lambda p: "approve", provider=SavedPlanProvider(path))
        self.assertEqual("candidate_ready", result["status"])
        self.assertEqual(plan.fingerprint, result["plan_sha256"])
        self.unchanged(result)

    def test_saved_plan_tampering_cannot_cross_approval_gate(self):
        from dataclasses import asdict
        plan = plan_repair(self.repo, "Addition bug", MockProvider(), self.policy)
        data = {"plan": asdict(plan), "plan_sha256": plan.fingerprint}
        data["plan"]["edits"][0]["content"] = "tampered content"
        path = self.root / "reviewed-plan.json"
        path.write_text(json.dumps(data))
        result = self.execute(mode="approval", approval=lambda p: "approve", provider=SavedPlanProvider(path))
        self.assertEqual("failed", result["status"])
        self.assertIn("fingerprint", result["error"])
        self.assertFalse(Path(result["workspace"]).exists())
        self.unchanged(result)


class PrimitiveSafetyTests(unittest.TestCase):
    def test_path_traversal_and_git_metadata_rejected(self):
        for path in ("../escape.py", "/tmp/a", "C:/a", "a\\b", ".git/config", "a/../b",
                     "CON.py", "a./b", "a//b"):
            with self.subTest(path=path), self.assertRaises(SafetyError):
                canonical_path(path)

    def test_redaction(self):
        with patch.dict(os.environ, {"MY_API_KEY": "example-very-secret"}):
            result = redact("example-very-secret Bearer token123 ghp_abcdefghijklmnop")
        self.assertNotIn("example-very-secret", result)
        self.assertNotIn("token123", result)
        self.assertNotIn("ghp_", result)

    def test_runner_captures_real_exit_and_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_test(("{python}", "-c", "import sys; print('evidence'); sys.exit(7)"),
                              Path(temp), 5)
        self.assertEqual(7, result["returncode"])
        self.assertIn("evidence", result["stdout"])

    def test_model_keys_not_inherited_by_test_process(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,
                {"PI_MAINTENANCE_API_KEY": "synthetic-private-key"}):
            result = run_test(("{python}", "-c", "import os; "
                              "assert 'PI_MAINTENANCE_API_KEY' not in os.environ"), Path(temp), 5)
        self.assertEqual(0, result["returncode"])


if __name__ == "__main__":
    unittest.main()
