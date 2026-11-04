"""Parent-authored checks for the Antigravity draft and the reviewed CLI."""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.cli import main
from repo_maintainer.comparison_summary import REQUIRED_ARMS, load_comparison_summary, format_comparison_summary
from repo_maintainer.models import SafetyError


def fixture():
    metrics = {arm: dict(tasks=5, repair_tasks=4, graded_repairs=1,
                        false_acceptances=0, failed_candidates_retained=0,
                        approval_violations=0, synthetic_model_requests=0,
                        workflow_duration_seconds=0.01) for arm in REQUIRED_ARMS}
    metrics["guarded_two"]["graded_repairs"] = 3
    return dict(schema_version=1, evidence_kind="fixture-scripted", model_accuracy_claim=False,
                real_model_requests=0, passed=True, metrics=metrics, dataset_sha256="a" * 64,
                inputs_and_first_patches_matched=True)


class ComparisonSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "summary.json"
        self.data = fixture()

    def tearDown(self):
        self.temp.cleanup()

    def load(self, data=None):
        self.path.write_text(json.dumps(self.data if data is None else data), encoding="utf-8")
        return load_comparison_summary(self.path)

    def test_full_and_thin_inputs_have_same_allowlisted_summary(self):
        thin = self.load()
        self.data["cells"] = [{"private_key": "must-not-display"}]
        self.data["private_key"] = "must-not-display"
        self.data["metrics"]["guarded_two"]["unknown"] = "must-not-display"
        full = self.load()
        self.assertEqual(thin, full)
        self.assertNotIn("must-not-display", json.dumps(full))
        self.assertNotIn("cells", full)
        self.assertIn("3/4", format_comparison_summary(full))
        self.assertIn("JUnit", format_comparison_summary(full))
        self.assertNotIn("\x1b", format_comparison_summary(full))

    def test_partial_is_preserved_and_shown_explicitly(self):
        self.data["passed"] = False
        self.data["inputs_and_first_patches_matched"] = False
        result = self.load()
        self.assertEqual("partial", result["status"])
        self.assertFalse(result["passed"])
        self.assertIn("partial", format_comparison_summary(result))

    def test_booleans_cannot_masquerade_as_counts_or_schema(self):
        for field in self.data["metrics"]["guarded_two"]:
            invalid = deepcopy(self.data)
            invalid["metrics"]["guarded_two"][field] = True
            with self.subTest(field=field), self.assertRaises(SafetyError): self.load(invalid)
        for field in ("schema_version", "real_model_requests"):
            invalid = deepcopy(self.data); invalid[field] = True
            with self.subTest(field=field), self.assertRaises(SafetyError): self.load(invalid)

    def test_missing_negative_and_out_of_range_metrics_are_rejected(self):
        for field in self.data["metrics"]["guarded_two"]:
            invalid = deepcopy(self.data); invalid["metrics"]["guarded_two"].pop(field)
            with self.subTest(missing=field), self.assertRaises(SafetyError): self.load(invalid)
            invalid = deepcopy(self.data); invalid["metrics"]["guarded_two"][field] = -1
            with self.subTest(negative=field), self.assertRaises(SafetyError): self.load(invalid)
        for field in ("repair_tasks", "graded_repairs", "false_acceptances", "failed_candidates_retained", "approval_violations"):
            invalid = deepcopy(self.data); invalid["metrics"]["guarded_two"][field] = 6
            with self.subTest(range=field), self.assertRaises(SafetyError): self.load(invalid)

    def test_nonfinite_and_overflowing_durations_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), 10**1000, "0.1"):
            invalid = deepcopy(self.data); invalid["metrics"]["guarded_two"]["workflow_duration_seconds"] = value
            with self.subTest(type=type(value).__name__), self.assertRaises(SafetyError): self.load(invalid)

    def test_all_four_arms_are_required(self):
        for extra in (False, True):
            invalid = deepcopy(self.data)
            if extra: invalid["metrics"]["other"] = invalid["metrics"]["guarded_one"]
            else: invalid["metrics"].pop("guarded_one")
            with self.subTest(extra=extra), self.assertRaises(SafetyError): self.load(invalid)

    def test_unknown_evidence_and_real_model_claims_are_rejected(self):
        for field, value in (("evidence_kind", "live"), ("model_accuracy_claim", True),
                             ("real_model_requests", 1), ("passed", 1),
                             ("inputs_and_first_patches_matched", 1), ("dataset_sha256", "bad")):
            invalid = deepcopy(self.data); invalid[field] = value
            with self.subTest(field=field), self.assertRaises(SafetyError): self.load(invalid)

    def test_scripted_requests_zero_and_loopback_requests_visible(self):
        self.data["metrics"]["guarded_two"]["synthetic_model_requests"] = 16
        with self.assertRaises(SafetyError): self.load()
        self.data["evidence_kind"] = "pi-loopback-scripted"
        self.assertEqual(16, self.load()["metrics"]["guarded_two"]["synthetic_model_requests"])

    def test_limits_malformed_utf8_and_non_object_input_are_rejected(self):
        for content in (b"x" * (8 * 1024 * 1024 + 1), b"not json", b"\xff", b"[]"):
            self.path.write_bytes(content)
            with self.subTest(size=len(content)), self.assertRaises(SafetyError): load_comparison_summary(self.path)
        with self.assertRaises(SafetyError): load_comparison_summary(Path(self.temp.name))
        with self.assertRaises(SafetyError): load_comparison_summary(self.path.with_name("missing.json"))

    def test_symbolic_link_is_not_followed(self):
        self.load()
        link = self.path.with_name("linked.json")
        try: os.symlink(self.path, link)
        except OSError as error: self.skipTest("Local symlink creation unavailable: " + str(error))
        with self.assertRaises(SafetyError): load_comparison_summary(link)

    def test_cli_both_formats_are_readonly_without_subprocess_or_provider(self):
        self.load()
        before = self.path.read_bytes()
        for json_mode in (False, True):
            stdout = io.StringIO()
            argv = ["comparison-summary", "--input", str(self.path)] + (["--json"] if json_mode else [])
            with patch("subprocess.Popen", side_effect=AssertionError("Readonly command launched process")), \
                 patch("repo_maintainer.cli.repair", side_effect=AssertionError("Started repair")), \
                 patch("repo_maintainer.cli.PiProvider", side_effect=AssertionError("Started provider")), redirect_stdout(stdout):
                self.assertEqual(0, main(argv))
            if json_mode: self.assertEqual("completed", json.loads(stdout.getvalue())["status"])
            else: self.assertIn("3/4", stdout.getvalue())
            self.assertEqual(before, self.path.read_bytes())

    def test_cli_partial_succeeds_invalid_input_fails_without_echo(self):
        self.data["passed"] = False; self.load()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(0, main(["comparison-summary", "--input", str(self.path), "--json"]))
        self.assertEqual("partial", json.loads(stdout.getvalue())["status"])
        self.path.write_text('{"unknown_secret":"never-echo"}', encoding="utf-8")
        before = self.path.read_bytes()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(1, main(["comparison-summary", "--input", str(self.path)]))
        self.assertEqual("", stdout.getvalue())
        self.assertNotIn("never-echo", stderr.getvalue())
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__": unittest.main()
