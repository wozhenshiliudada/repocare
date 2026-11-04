from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from junitparser import JUnitXml
from repo_maintainer.comparison import ARMS, _raw_arm, ScriptProvider, run_comparison
from repo_maintainer.comparison_fixtures import cases
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name) / "evaluation"
        cls.result = run_comparison(cls.directory, showcase=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_matching_inputs_and_first_patches_with_independent_grading(self):
        self.assertTrue(self.result["passed"])
        self.assertFalse(self.result["model_accuracy_claim"])
        self.assertEqual(0, self.result["real_model_requests"])
        self.assertEqual(20, len(self.result["cells"]))
        for fixture in cases():
            rows = [r for r in self.result["cells"] if r["case"] == fixture.id]
            self.assertEqual(4, len(rows))
            self.assertEqual(1, len({r["fixture_sha256"] for r in rows}))
            self.assertEqual(1, len({r["first_patch_sha256"] for r in rows}))
            for row in rows:
                self.assertNotEqual(0, row["baseline_tests"][0]["returncode"])
                self.assertEqual("tests", row["grader_tests"][0]["command"][-2])

    def test_negative_controls_and_rollback_are_visible_not_hidden_as_errors(self):
        metrics = self.result["metrics"]
        self.assertEqual(3, metrics["patch_only"]["false_acceptances"])
        self.assertEqual(0, metrics["test_gate"]["false_acceptances"])
        self.assertEqual(3, metrics["test_gate"]["failed_candidates_retained"])
        self.assertEqual(1, metrics["guarded_one"]["graded_repairs"])
        self.assertEqual(3, metrics["guarded_two"]["graded_repairs"])
        for arm in ("guarded_one", "guarded_two"):
            self.assertEqual(0, metrics[arm]["false_acceptances"])
            self.assertEqual(0, metrics[arm]["failed_candidates_retained"])
            self.assertTrue(all(r["source_copy_unchanged"] for r in self.result["cells"] if r["arm"] == arm))

    def test_refusal_never_edits_and_is_excluded_from_repair_denominator(self):
        for row in self.result["cells"]:
            if row["case"] != "approval_reject": continue
            self.assertFalse(row["accepted"])
            self.assertFalse(row["authorized_repair_task"])
            self.assertTrue(row["source_copy_unchanged"])
            self.assertEqual([], row["workflow_tests"])
            self.assertEqual("reject", row["authorization"]["decision"])
        for arm in ARMS:
            self.assertEqual(4, self.result["metrics"][arm]["repair_tasks"])
            self.assertEqual(0, self.result["metrics"][arm]["approval_violations"])

    def test_real_publication_approval_flow_and_junit_contract_semantics(self):
        row = next(r for r in self.result["cells"] if r["case"] == "shipping" and r["arm"] == "guarded_two")
        self.assertEqual(["prepared", "awaiting_approval", "rejected", "published"], row["showcase_integration"]["steps"])
        self.assertFalse(row["showcase_integration"]["pending_target_exists"])
        self.assertFalse(row["showcase_integration"]["rejected_target_exists"])
        repo = RepositoryGateway(self.directory / "shipping" / "guarded_two" / "repository")
        self.assertEqual(row["showcase_integration"]["commit"], repo.command("rev-parse", row["showcase_integration"]["branch"]).decode().strip())
        xml = JUnitXml.fromfile(str(self.directory / "contracts.xml"))
        self.assertEqual(20, xml.tests)
        self.assertEqual(0, xml.failures)
        self.assertEqual(0, xml.errors)
        self.assertIn("not repair success", (self.directory / "report.md").read_text())

    def test_existing_output_invalid_endpoint_and_traversal_leave_no_writes(self):
        sentinel = self.directory / "sentinel"
        sentinel.write_text("retain")
        with self.assertRaises(SafetyError): run_comparison(self.directory)
        self.assertEqual("retain", sentinel.read_text())
        for url in ("https://api.example.invalid/v1", "http://localhost:1234/v1", "http://127.0.0.1:1234/v1?key=x"):
            target = Path(self.temp.name) / "invalid-output"
            with self.assertRaises(SafetyError): run_comparison(target, url)
            self.assertFalse(target.exists())
        with self.assertRaises(SafetyError):
            run_comparison(Path(self.temp.name) / "bad-name", selected_cases=[replace(cases()[0], id="../escape")])
        self.assertFalse((Path(self.temp.name) / "bad-name").exists())

    def test_infrastructure_failure_keeps_partial_evidence_and_fails(self):
        target = Path(self.temp.name) / "infrastructure-failed"
        with patch("repo_maintainer.comparison.grade", side_effect=SafetyError("Synthetic grading infrastructure failure")):
            with self.assertRaises(SafetyError): run_comparison(target, selected_cases=cases()[:1])
        summary = json.loads((target / "summary.json").read_text())
        self.assertFalse(summary["passed"])
        self.assertIn("infrastructure failure", summary["error"])
        self.assertEqual([], summary["cells"])

    def test_ablation_controls_refuse_non_owned_repository(self):
        fixture = cases()[0]
        repo = RepositoryGateway(self.directory / "shipping" / "guarded_two" / "repository")
        before = repo.snapshot()
        with self.assertRaises(SafetyError):
            _raw_arm(fixture, "patch_only", repo, ScriptProvider(fixture), Path(self.temp.name) / "foreign")
        self.assertEqual(before, repo.snapshot())


if __name__ == "__main__": unittest.main()
