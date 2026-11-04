import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.integration import integrate, prepare_integration
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.workflow import cleanup_run, repair


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repository")
        self.source = RepositoryGateway(self.repo)
        self.before = self.source.snapshot()

    def candidate(self, command=DEMO_TEST):
        result = repair(self.repo, "addition", MockProvider(), Policy(("calculator.py",), (command,)),
                        "auto", self.root / "runs")
        self.assertEqual("candidate_ready", result["status"], result.get("error"))
        self.run_dir = Path(result["workspace"]).parent
        self.record = result
        self.target = "integration/" + result["run_id"]
        self.addCleanup(cleanup_run, self.run_dir)
        return result

    def prepared(self):
        self.candidate()
        review = prepare_integration(self.run_dir)
        self.assertEqual("prepared", review["status"], review)
        return review

    def target_sha(self):
        return self.source.command("show-ref", "--verify", "--hash", "refs/heads/" + self.target,
                                   check=False).decode().strip()

    def test_publish_sealed_commit_leaves_source_and_cleanup_preserves_publication(self):
        review = self.prepared()
        self.assertEqual("", self.target_sha())
        self.assertEqual(self.before, self.source.snapshot(exclude_branch=self.record["branch"]))
        result = integrate(self.run_dir, review["review_sha256"])
        self.assertEqual("published", result["status"])
        self.assertEqual(review["review"]["commit"], self.target_sha())
        self.assertEqual(b"def add(a, b):\n    return a + b\n",
                         self.source.command("show", self.target + ":calculator.py"))
        self.assertEqual(self.before["head"], self.source.head)
        cleanup = cleanup_run(self.run_dir)
        self.assertEqual("discarded", cleanup["status"])
        self.assertTrue(cleanup["source_unchanged"])
        self.assertEqual(review["review"]["commit"], self.target_sha())
        self.assertEqual(self.before, self.source.snapshot(exclude_branch=self.target))

    def test_pending_reject_and_edit_approval_cannot_publish(self):
        review = self.prepared()
        self.assertEqual("awaiting_approval", integrate(self.run_dir)["status"])
        self.assertEqual("rejected", integrate(self.run_dir, reject=True)["status"])
        with self.assertRaises(SafetyError):
            integrate(self.run_dir, self.record["plan_sha256"])
        self.assertEqual("", self.target_sha())
        self.assertTrue(Path(self.record["workspace"]).exists())
        # Later explicit approval still binds the same sealed review.
        self.assertEqual("published", integrate(self.run_dir, review["review_sha256"])["status"])

    def test_candidate_change_after_review_rejected_and_preserved(self):
        review = self.prepared()
        path = Path(self.record["workspace"]) / "calculator.py"
        path.write_text("# human changed candidate\n")
        with self.assertRaises(SafetyError):
            integrate(self.run_dir, review["review_sha256"])
        self.assertEqual("", self.target_sha())
        self.assertEqual("# human changed candidate\n", path.read_text())

    def test_staged_candidate_metadata_change_rejected(self):
        review = self.prepared()
        candidate = RepositoryGateway(Path(self.record["workspace"]))
        candidate.command("add", "calculator.py")
        with self.assertRaises(SafetyError):
            integrate(self.run_dir, review["review_sha256"])
        self.assertEqual("", self.target_sha())

    def test_dirty_source_rejected_without_stash_or_reset(self):
        review = self.prepared()
        (self.repo / "notes.txt").write_text("human untracked work")
        before = self.source.snapshot()
        with self.assertRaises(SafetyError):
            integrate(self.run_dir, review["review_sha256"])
        self.assertEqual(before, self.source.snapshot())
        self.assertEqual("", self.target_sha())

    def test_source_branch_advance_rejected(self):
        review = self.prepared()
        self.source.command("-c", "user.name=Human", "-c", "user.email=human@example.invalid",
                            "commit", "--allow-empty", "-m", "human continued work")
        before = self.source.snapshot()
        with self.assertRaises(SafetyError):
            integrate(self.run_dir, review["review_sha256"])
        self.assertEqual(before, self.source.snapshot())
        self.assertEqual("", self.target_sha())

    def test_tampered_test_evidence_cannot_reuse_approval(self):
        review = self.prepared()
        review["review"]["tests"][0]["stdout"] = "changed transcript"
        (self.run_dir / "integration-review.json").write_text(json.dumps(review))
        with self.assertRaises(SafetyError):
            integrate(self.run_dir, review["review_sha256"])
        self.assertEqual("", self.target_sha())

    def test_sealed_commit_is_tested_again_and_failed_verification_not_published(self):
        command = ("{python}", "-c", "import calculator,sys; from pathlib import Path; "
                   "sys.exit(2 if Path.cwd().name=='integration-verification' "
                   "else (0 if calculator.add(2,3)==5 else 1))")
        self.candidate(command)
        with self.assertRaises(SafetyError):
            prepare_integration(self.run_dir)
        review = json.loads((self.run_dir / "integration-review.json").read_text())
        self.assertEqual("failed", review["status"])
        self.assertEqual(2, review["failed_tests"][0]["returncode"])
        self.assertFalse((self.run_dir / "integration-verification").exists())
        self.assertEqual("", self.target_sha())
        self.assertEqual(self.before, self.source.snapshot(exclude_branch=self.record["branch"]))

    def test_atomic_transaction_does_not_overwrite_racing_target(self):
        review = self.prepared()
        original = RepositoryGateway.command
        def racing_command(repository, *args, **kwargs):
            if args[:2] == ("update-ref", "--no-deref"):
                original(repository, "update-ref", "refs/heads/" + self.target, self.before["head"])
            return original(repository, *args, **kwargs)
        with patch.object(RepositoryGateway, "command", racing_command), self.assertRaises(SafetyError):
            integrate(self.run_dir, review["review_sha256"])
        self.assertEqual(self.before["head"], self.target_sha())
        self.assertEqual(self.before["head"], self.source.head)

    def test_atomic_transaction_checks_source_ref_at_publication(self):
        review = self.prepared()
        original = RepositoryGateway.command
        advanced = []
        def racing_command(repository, *args, **kwargs):
            if args[:2] == ("update-ref", "--no-deref"):
                tree = original(repository, "rev-parse", self.before["head"] + "^{tree}").decode().strip()
                commit = original(repository, "-c", "user.name=Human", "-c", "user.email=human@example.invalid",
                                  "commit-tree", tree, "-p", self.before["head"], "-m", "concurrent human commit").decode().strip()
                original(repository, "update-ref", self.before["branch"], commit, self.before["head"])
                advanced.append(commit)
            return original(repository, *args, **kwargs)
        with patch.object(RepositoryGateway, "command", racing_command), self.assertRaises(SafetyError):
            integrate(self.run_dir, review["review_sha256"])
        self.assertEqual(advanced[0], self.source.head)
        self.assertEqual("", self.target_sha())
        self.assertEqual(self.before["files"], self.source.snapshot()["files"])

    def test_real_process_exit_after_publication_recovers_durable_authorization(self):
        review = self.prepared()
        src = str(Path(__file__).resolve().parents[1] / "src")
        code = """import os,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.integration import integrate
original=RepositoryGateway.command
def stop(repository,*args,**kwargs):
    result=original(repository,*args,**kwargs)
    if args[:2]==('update-ref','--no-deref'): os._exit(99)
    return result
with patch.object(RepositoryGateway,'command',stop):
    integrate(Path(sys.argv[2]),sys.argv[3])
"""
        process = subprocess.run([sys.executable, "-c", code, src, str(self.run_dir), review["review_sha256"]],
                                 capture_output=True, timeout=45)
        self.assertEqual(99, process.returncode, process.stderr)
        saved = json.loads((self.run_dir / "integration-review.json").read_text())
        self.assertEqual("publishing", saved["status"])
        self.assertEqual("approve", saved["authorization"]["decision"])
        self.assertEqual(review["review"]["commit"], self.target_sha())
        recovered = integrate(self.run_dir, review["review_sha256"])
        self.assertEqual("published", recovered["status"])
        self.assertTrue(recovered["source_unchanged"])
        self.assertEqual("published", integrate(self.run_dir, review["review_sha256"])["status"])
        self.assertEqual(self.before["head"], self.source.head)
