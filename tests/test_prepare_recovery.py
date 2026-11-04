"""Real Git worktrees and abrupt child-process exits, without model requests."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import psutil
from repo_maintainer.cli import main
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.integration import integrate, prepare_integration, recover_integration, save_review
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.recovery import operation_lock, process_identity, require_test_stopped
from repo_maintainer.workflow import cleanup_run, repair


CRASH_SCRIPT = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from repo_maintainer import integration
from repo_maintainer.git import RepositoryGateway
stage = sys.argv[3]
command = RepositoryGateway.command
save = integration.save_review
def crash_command(repo, *args, **kwargs):
    match_add = args[:3] == ('worktree', 'add', '--detach')
    match_remove = args[:2] == ('worktree', 'remove')
    if match_add and stage == 'before_add': os._exit(91)
    value = command(repo, *args, **kwargs)
    if match_add and stage == 'after_add': os._exit(91)
    if match_remove and stage == 'after_remove': os._exit(91)
    return value
def crash_save(directory, review):
    save(directory, review)
    if review.get('operation_stage') == stage: os._exit(91)
RepositoryGateway.command = crash_command
integration.save_review = crash_save
integration.prepare_integration(Path(sys.argv[2]))
'''


class PrepareRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repository")
        self.source = RepositoryGateway(self.repo)
        self.before = self.source.snapshot()

    def candidate(self, command=DEMO_TEST):
        record = repair(self.repo, "addition", MockProvider(), Policy(("calculator.py",), (command,)),
                        "auto", self.root / "runs")
        self.assertEqual("candidate_ready", record["status"], record.get("error"))
        self.record = record
        self.run_dir = Path(record["workspace"]).parent
        self.candidate_before = RepositoryGateway(Path(record["workspace"])).snapshot()
        return record

    def crash(self, stage):
        result = subprocess.run([sys.executable, "-c", CRASH_SCRIPT,
                                 str(Path(__file__).resolve().parents[1] / "src"), str(self.run_dir), stage],
                                capture_output=True, timeout=40)
        self.assertEqual(91, result.returncode, result.stderr.decode(errors="replace"))
        return self.review()

    def review(self):
        return json.loads((self.run_dir / "integration-review.json").read_text(encoding="utf-8"))

    def unchanged(self):
        self.assertEqual(self.before, self.source.snapshot(exclude_branch=self.record["branch"]))
        self.assertEqual(self.candidate_before, RepositoryGateway(Path(self.record["workspace"])).snapshot())
        self.assertFalse(self.source.command("show-ref", "--verify", "--hash",
                                            "refs/heads/integration/" + self.record["run_id"], check=False))

    def test_real_exits_before_and_after_creation_testing_and_removal(self):
        for stage in ("before_add", "after_add", "verification_ready", "test_finished", "after_remove"):
            with self.subTest(stage=stage):
                self.candidate()
                old = self.crash(stage)
                with self.assertRaises(SafetyError):
                    cleanup_run(self.run_dir)
                result = recover_integration(self.run_dir)
                self.assertEqual("aborted", result["status"])
                self.assertFalse((self.run_dir / "integration-verification").exists())
                self.assertEqual("aborted", recover_integration(self.run_dir)["status"])
                self.unchanged()
                with self.assertRaises(SafetyError):
                    integrate(self.run_dir, old.get("review_sha256", "0" * 64))
                cleanup_run(self.run_dir)

    def test_retry_archives_old_evidence_reruns_tests_and_changes_approval(self):
        self.candidate()
        old = self.crash("after_remove")  # Complete tests/core, publication still unready.
        recover_integration(self.run_dir)
        evidence = (self.run_dir / "integration-review.json").read_bytes()
        with self.assertRaises(SafetyError):
            prepare_integration(self.run_dir)
        review = prepare_integration(self.run_dir, retry_aborted=True)
        self.assertEqual("prepared", review["status"])
        self.assertNotEqual(old["review"]["preparation_attempt_id"], review["review"]["preparation_attempt_id"])
        self.assertNotEqual(old["review_sha256"], review["review_sha256"])
        archive = self.run_dir / "integration-attempts" / old["preparation"]["attempt_id"]
        self.assertEqual(evidence, (archive / "integration-review.json").read_bytes())
        self.assertEqual(1, len(review["review"]["tests"]))
        self.assertEqual(0, review["review"]["tests"][0]["returncode"])
        with self.assertRaises(SafetyError):
            integrate(self.run_dir, old["review_sha256"])
        self.unchanged()
        self.assertEqual("published", integrate(self.run_dir, review["review_sha256"])["status"])
        with self.assertRaises(SafetyError):
            recover_integration(self.run_dir)
        cleanup_run(self.run_dir)

    def test_human_tracked_untracked_ignored_and_index_changes_are_retained(self):
        self.candidate()
        self.crash("verification_ready")
        workspace = self.run_dir / "integration-verification"
        gateway = RepositoryGateway(workspace)
        original = (workspace / "calculator.py").read_bytes()
        index = Path(gateway.command("rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip())
        original_index = index.read_bytes()
        for kind in ("tracked", "untracked", "ignored", "index"):
            with self.subTest(kind=kind):
                path = workspace / ("calculator.py" if kind == "tracked" else ".human-note")
                if kind == "index":
                    index.write_bytes(original_index + b"human metadata")
                else:
                    if kind == "ignored":
                        # Common Git exclude file, restored before retry; never user data.
                        exclude = self.source.common_dir / "info" / "exclude"
                        exclude_before = exclude.read_bytes()
                        exclude.write_bytes(exclude_before + b"\n.human-note\n")
                    path.write_text("human work\n", encoding="utf-8")
                with self.assertRaises(SafetyError):
                    recover_integration(self.run_dir)
                if kind == "index":
                    self.assertEqual(original_index + b"human metadata", index.read_bytes())
                    index.write_bytes(original_index)
                else:
                    self.assertEqual("human work\n", path.read_text(encoding="utf-8"))
                    if kind == "tracked": path.write_bytes(original)
                    else: path.unlink()
                    if kind == "ignored": exclude.write_bytes(exclude_before)
                self.unchanged()
        self.assertEqual("aborted", recover_integration(self.run_dir)["status"])
        cleanup_run(self.run_dir)

    def test_recorded_alive_test_blocks_recovery_and_cleanup(self):
        command = ("{python}", "-c", "from pathlib import Path; import calculator,sys,time; "
                   "time.sleep(30) if Path.cwd().name=='integration-verification' else None; "
                   "sys.exit(0 if calculator.add(2,3)==5 else 1)")
        self.candidate(command)
        review = self.crash("test_running")
        identity = review["test_inflight"]
        child = psutil.Process(identity["pid"])
        self.assertEqual(identity, process_identity(child.pid))
        try:
            with self.assertRaisesRegex(SafetyError, "still alive"):
                recover_integration(self.run_dir)
            with self.assertRaises(SafetyError):
                cleanup_run(self.run_dir)
            self.assertTrue((self.run_dir / "integration-verification").exists())
        finally:
            # Terminate only this test's known PID with matching creation time.
            if process_identity(child.pid) == identity:
                child.terminate()
                child.wait(timeout=10)
        self.assertEqual("aborted", recover_integration(self.run_dir)["status"])
        self.unchanged()
        cleanup_run(self.run_dir)

    def test_changed_registration_and_detached_commit_are_retained(self):
        self.candidate()
        review = self.crash("verification_ready")
        workspace = self.run_dir / "integration-verification"
        gateway = RepositoryGateway(workspace)
        self.source.command("worktree", "lock", str(workspace))
        with self.assertRaises(SafetyError): recover_integration(self.run_dir)
        self.assertTrue(workspace.exists())
        self.source.command("worktree", "unlock", str(workspace))
        gateway.command("-c", "user.name=Human", "-c", "user.email=human@example.invalid",
                        "commit", "--allow-empty", "-m", "human verification commit")
        changed_head = gateway.head
        with self.assertRaises(SafetyError): recover_integration(self.run_dir)
        self.assertEqual(changed_head, gateway.head)
        gateway.command("checkout", "--detach", review["preparation"]["commit"])
        self.assertEqual("aborted", recover_integration(self.run_dir)["status"])
        self.unchanged()
        cleanup_run(self.run_dir)

    def test_pid_launch_gap_is_explicitly_manual_and_pid_reuse_not_alive(self):
        self.candidate()
        self.crash("test_starting")
        with self.assertRaisesRegex(SafetyError, "manual process verification"):
            recover_integration(self.run_dir)
        review = self.review()
        # The injection exited before Popen, so this fixture has no test child.
        review["operation_stage"] = "verification_ready"
        save_review(self.run_dir, review)
        with patch("repo_maintainer.recovery.process_identity", return_value={"pid": 10, "created_at": 2}):
            require_test_stopped({"test_inflight": {"pid": 10, "created_at": 1}})
        recover_integration(self.run_dir)
        cleanup_run(self.run_dir)

    def test_native_cross_process_lock_releases_on_process_exit(self):
        self.candidate()
        script = ("import sys,os; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                  "from repo_maintainer.recovery import operation_lock; "
                  "\nwith operation_lock(Path(sys.argv[2])):\n print('locked',flush=True); "
                  "sys.stdin.readline(); os._exit(92)\n")
        child = subprocess.Popen([sys.executable, "-c", script, str(Path(__file__).resolve().parents[1] / "src"),
                                  str(self.run_dir)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(b"locked\n", child.stdout.readline().replace(b"\r\n", b"\n"))
            for operation in (prepare_integration, recover_integration, cleanup_run):
                with self.assertRaisesRegex(SafetyError, "Another operation"):
                    operation(self.run_dir)
        finally:
            child.communicate(b"done\n", timeout=10)
        self.assertEqual(92, child.returncode)
        with operation_lock(self.run_dir): pass
        cleanup_run(self.run_dir)

    def test_preexisting_target_legacy_and_tampered_intent_are_not_adopted(self):
        self.candidate()
        target = self.run_dir / "integration-verification"
        target.mkdir()
        note = target / "human.txt"
        note.write_text("retain")
        with self.assertRaises(SafetyError): prepare_integration(self.run_dir)
        self.assertEqual("retain", note.read_text())
        self.assertFalse((self.run_dir / "integration-review.json").exists())
        note.unlink(); target.rmdir()
        self.crash("before_add")
        original = self.review()
        changed = self.review()
        changed["preparation"]["verification_dir"] = str(self.root / "foreign")
        (self.run_dir / "integration-review.json").write_text(json.dumps(changed))
        with self.assertRaises(SafetyError): recover_integration(self.run_dir)
        changed.pop("preparation")
        (self.run_dir / "integration-review.json").write_text(json.dumps(changed))
        with self.assertRaisesRegex(SafetyError, "legacy"): recover_integration(self.run_dir)
        save_review(self.run_dir, original)
        recover_integration(self.run_dir)
        cleanup_run(self.run_dir)

    def test_recovery_cli_idempotence_and_aborted_report_repair(self):
        self.candidate()
        self.crash("before_add")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, main(["recover-integration", "--run-dir", str(self.run_dir)]))
        self.assertEqual("aborted", json.loads(output.getvalue())["status"])
        path = self.run_dir / "run.json"
        record = json.loads(path.read_text())
        record.pop("integration_preparation")
        record["events"] = [e for e in record["events"] if e["stage"] != "integration_preparation_aborted"]
        path.write_text(json.dumps(record))  # Simulate exit between review and run/report save.
        recover_integration(self.run_dir)
        recover_integration(self.run_dir)
        record = json.loads(path.read_text())
        self.assertEqual("aborted", record["integration_preparation"]["status"])
        self.assertEqual(1, len([e for e in record["events"] if e["stage"] == "integration_preparation_aborted"]))
        self.assertIn("aborted", (self.run_dir / "report.md").read_text())
        cleanup_run(self.run_dir)

    def test_ready_and_pending_reviews_preserved_without_recovery(self):
        self.candidate()
        review = prepare_integration(self.run_dir)
        with self.assertRaises(SafetyError): recover_integration(self.run_dir)
        integrate(self.run_dir)
        evidence = (self.run_dir / "integration-review.json").read_bytes()
        with self.assertRaises(SafetyError): recover_integration(self.run_dir)
        self.assertEqual(evidence, (self.run_dir / "integration-review.json").read_bytes())
        self.assertEqual(review["review_sha256"], self.review()["review_sha256"])
        self.unchanged()
        cleanup_run(self.run_dir)


if __name__ == "__main__": unittest.main()
