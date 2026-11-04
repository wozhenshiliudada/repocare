import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.evaluation import run_evaluation
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import Edit, Policy, SafetyError, digest
from repo_maintainer.provider import MockProvider
from repo_maintainer.workflow import cleanup_run, repair


class RevisingProvider(MockProvider):
    def __init__(self):
        super().__init__("broken")
        self.feedback = []

    def revise(self, repository, issue, tests, feedback):
        self.feedback.append(feedback)
        original = repository.read("calculator.py")
        # Keep the real request context and use the current candidate content hash.
        from repo_maintainer.models import Plan
        return Plan(issue, repository.head, "Fix observed multiplication failure", "fixture only",
                    (Edit("calculator.py", digest(original), "def add(a, b):\n    return a + b\n"),),
                    tests, "test-revision")


class MultiRoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repository")
        self.repository = RepositoryGateway(self.repo)
        self.before = self.repository.snapshot()
        self.policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))

    def run_repair(self, provider, mode="auto", approval=None, max_attempts=3, policy=None):
        record = repair(self.repo, "addition bug", provider, policy or self.policy, mode,
                        self.root / "runs", approval, max_attempts=max_attempts)
        if Path(record["workspace"]).exists():
            self.addCleanup(cleanup_run, Path(record["workspace"]).parent)
        return record

    def test_failed_candidate_revised_with_real_test_feedback(self):
        provider = RevisingProvider()
        record = self.run_repair(provider)
        self.assertEqual("candidate_ready", record["status"], record.get("error"))
        self.assertEqual(2, len(record["rounds"]))
        self.assertEqual("failed", record["rounds"][0]["verification"])
        self.assertEqual("passed", record["rounds"][1]["verification"])
        self.assertNotEqual(0, provider.feedback[0]["tests"][0]["returncode"])
        self.assertIn("FAIL", provider.feedback[0]["tests"][0]["stderr"])
        self.assertTrue(record["source_unchanged"])
        run_dir = Path(record["workspace"]).parent
        self.assertTrue((run_dir / "round-01.diff").exists())
        self.assertTrue((run_dir / "round-02.diff").exists())

    def test_new_patch_requires_its_own_approval(self):
        seen = []
        def approve(plan):
            seen.append(plan.fingerprint)
            if len(seen) == 2:
                workspace = next((self.root / "runs").iterdir()) / "workspace"
                self.assertIn("a * b", (workspace / "calculator.py").read_text())
                return "reject"
            return "approve"
        record = self.run_repair(RevisingProvider(), "approval", approve)
        self.assertEqual("rolled_back", record["status"])
        self.assertEqual("reject", record["approval_outcome"])
        self.assertNotEqual(seen[0], seen[1])
        self.assertEqual([], record["rounds"][1]["tests"])
        self.assertIn("a * b", record["diff"])
        self.assertNotIn("+    return a + b", record["diff"])
        self.assertEqual(self.before, self.repository.snapshot())

    def test_both_patches_must_be_explicitly_approved(self):
        record = self.run_repair(RevisingProvider(), "approval", lambda plan: "approve")
        self.assertEqual("candidate_ready", record["status"])
        self.assertEqual(["approve", "approve"], [r["authorization"]["decision"] for r in record["rounds"]])

    def test_concurrent_candidate_edit_preserved_for_approve_and_reject(self):
        for decision in ("approve", "reject"):
            with self.subTest(decision=decision):
                seen = []
                def approve(plan):
                    seen.append(plan)
                    if len(seen) == 2:
                        workspace = next(p for p in (self.root / "runs").iterdir()
                                         if (p / "workspace").exists()) / "workspace"
                        (workspace / "calculator.py").write_text("# human edit\n", encoding="utf-8")
                        return decision
                    return "approve"
                record = self.run_repair(RevisingProvider(), "approval", approve)
                self.assertEqual("failed", record["status"])
                self.assertEqual("candidate_retained_due_to_concurrent_change", record["recovery"]["result"])
                workspace = Path(record["workspace"])
                self.assertEqual("# human edit\n", (workspace / "calculator.py").read_text())
                self.assertEqual([], record["rounds"][1]["tests"])
                cleanup_run(workspace.parent)

    def test_approval_cannot_return_auto_scope_token(self):
        record = self.run_repair(RevisingProvider(), "approval", lambda plan: "policy_scope")
        self.assertEqual("failed", record["status"])
        self.assertFalse(Path(record["workspace"]).exists())

    def test_round_budget_stops_retry(self):
        provider = RevisingProvider()
        record = self.run_repair(provider, max_attempts=1)
        self.assertEqual("rolled_back", record["status"])
        self.assertEqual([], provider.feedback)
        self.assertEqual(1, len(record["rounds"]))

    def test_no_progress_reply_stops_without_running_more_tests(self):
        class NoProgress(RevisingProvider):
            def revise(self, repository, issue, tests, feedback):
                plan = super().revise(repository, issue, tests, feedback)
                return replace(plan, edits=(replace(plan.edits[0], content=repository.read("calculator.py").decode()),))
        record = self.run_repair(NoProgress())
        self.assertEqual("rolled_back", record["status"])
        self.assertIn("No progress", record["error"])
        self.assertEqual([], record["rounds"][1]["tests"])

    def test_revision_cannot_edit_protected_tests(self):
        class TestEditor(RevisingProvider):
            def revise(self, repository, issue, tests, feedback):
                plan = super().revise(repository, issue, tests, feedback)
                return replace(plan, edits=(Edit("tests/test_calculator.py",
                                               digest(repository.read("tests/test_calculator.py")), ""),))
        record = self.run_repair(TestEditor())
        self.assertEqual("rolled_back", record["status"])
        self.assertIn("outside authorized paths", record["error"])
        self.assertEqual(self.before, self.repository.snapshot())

    def test_revision_cannot_remove_required_regression_tests(self):
        class TestOmitter(RevisingProvider):
            def revise(self, repository, issue, tests, feedback):
                return replace(super().revise(repository, issue, tests, feedback), tests=(tests[0],))
        extra = ("{python}", "-c", "pass")
        policy = Policy(("calculator.py",), (DEMO_TEST, extra))
        record = self.run_repair(TestOmitter(), policy=policy)
        self.assertEqual("rolled_back", record["status"])
        self.assertIn("test scope", record["error"])

    def test_model_not_invoked_for_secret_context_scope(self):
        class ShouldNotRun:
            def plan(self, *args):
                raise AssertionError("Model must not receive secrets")
        record = self.run_repair(ShouldNotRun(), policy=Policy((".env",), (DEMO_TEST,)))
        self.assertEqual("failed", record["status"])
        self.assertIn("Sensitive configuration", record["error"])

    def test_policy_rejects_edit_and_readonly_overlap(self):
        with self.assertRaises(SafetyError):
            Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("Calculator.py",)).validate_scope()


class EvaluationTests(unittest.TestCase):
    def test_six_distinct_fixture_cases_reproduce_then_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "evaluation"
            result = run_evaluation(directory)
            self.assertEqual(6, result["total"])
            self.assertEqual(6, result["passed"], result)
            self.assertEqual(6, len({c["kind"] for c in result["cases"]}))
            self.assertFalse(result["model_accuracy_claim"])
            for case in result["cases"]:
                self.assertNotEqual([0], case["baseline_exit"])
                self.assertEqual([0], case["candidate_exit"])
                self.assertTrue(case["source_unchanged"])
