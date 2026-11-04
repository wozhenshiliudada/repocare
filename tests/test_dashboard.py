from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.cli import main
from repo_maintainer.dashboard import export_dashboard, project_run
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.integration import prepare_integration
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.workflow import cleanup_run, repair


def payload(path):
    return json.loads(re.search(r'<script id="evidence" type="application/json">(.*?)</script>',
                               path.read_text(encoding="utf-8"), re.S)[1])


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.repo = create_fixture(cls.root / "repo")
        cls.gateway = RepositoryGateway(cls.repo)
        record = repair(cls.repo, "Addition bug", MockProvider(), Policy(("calculator.py",), (DEMO_TEST,)),
                        "auto", cls.root / "runs")
        cls.run_dir = Path(record["workspace"]).parent
        cls.review = prepare_integration(cls.run_dir)
        cls.saved = (cls.run_dir / "run.json").read_bytes()
        cls.saved_review = (cls.run_dir / "integration-review.json").read_bytes()
        cls.saved_diff = (cls.run_dir / "integration.diff").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cleanup_run(cls.run_dir)
        cls.temp.cleanup()

    def setUp(self):
        self.local = tempfile.TemporaryDirectory()
        self.addCleanup(self.local.cleanup)
        self.destination = Path(self.local.name) / "dashboard.html"
        (self.run_dir / "run.json").write_bytes(self.saved)
        (self.run_dir / "integration-review.json").write_bytes(self.saved_review)
        (self.run_dir / "integration.diff").write_bytes(self.saved_diff)

    def test_real_evidence_export_preserves_git_and_records(self):
        before = self.gateway.snapshot(exclude_branch="maintenance/" + self.run_dir.name)
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run_dir.iterdir() if p.is_file()}
        result = export_dashboard([self.run_dir.parent, self.run_dir], self.destination)
        self.assertEqual(1, result["runs"])
        self.assertTrue(result["read_only"])
        record = payload(self.destination)["runs"][0]
        self.assertTrue(record["review"]["hash_matches"])
        self.assertTrue(record["review"]["diff_hash_matches"])
        self.assertTrue(record["rounds"][0]["plan_hash_matches"])
        self.assertEqual(0, record["candidate_tests"][0]["returncode"])
        self.assertEqual(before, self.gateway.snapshot(exclude_branch="maintenance/" + self.run_dir.name))
        self.assertEqual(files, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run_dir.iterdir() if p.is_file()})

    def test_injection_is_data_and_unknown_fields_are_excluded(self):
        record = json.loads(self.saved)
        attack = '</script><img src=x onerror="window.injected=1">@@APP@@'
        record["issue"] = attack
        record["arbitrary_secret"] = "do-not-export-this-value"
        record["authorization"] = {"decision": "approve", "api_key": "do-not-export-this-value"}
        record["error"] = "Bearer abcdefghijklmnopqrstuvwxyz"
        (self.run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
        export_dashboard([self.run_dir], self.destination)
        html = self.destination.read_text(encoding="utf-8")
        self.assertNotIn(attack, html)
        self.assertNotIn("do-not-export-this-value", html)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", html)
        self.assertEqual(attack, payload(self.destination)["runs"][0]["issue"])
        self.assertIn("connect-src 'none'", html)
        self.assertIn("Twitter, Inc.", html)

    def test_changed_review_diff_and_plan_are_flagged(self):
        record = json.loads(self.saved)
        record["rounds"][0]["plan"]["risk"] = "changed"
        (self.run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
        review = json.loads(self.saved_review)
        review["review"]["tests"][0]["stdout"] = "changed"
        (self.run_dir / "integration-review.json").write_text(json.dumps(review), encoding="utf-8")
        (self.run_dir / "integration.diff").write_text("changed", encoding="utf-8")
        view = project_run(self.run_dir)
        self.assertFalse(view["review"]["hash_matches"])
        self.assertFalse(view["review"]["diff_hash_matches"])
        self.assertFalse(view["rounds"][0]["plan_hash_matches"])

    def test_missing_review_is_distinct_from_bad_review(self):
        (self.run_dir / "integration-review.json").unlink()
        self.assertIsNone(project_run(self.run_dir)["review"])
        (self.run_dir / "integration-review.json").write_bytes(self.saved_review)
        (self.run_dir / "integration.diff").unlink()
        self.assertIsNone(project_run(self.run_dir)["review"]["diff_hash_matches"])

    def test_aborted_preparation_keeps_partial_tests_and_cleanup_visible(self):
        review = json.loads(self.saved_review)
        partial = review.pop("review")["tests"]
        review.pop("review_sha256")
        review.update(status="aborted", operation_stage="verification_removed", verification_tests=partial,
                      cleanup={"result": "verification_removed"})
        (self.run_dir / "integration-review.json").write_text(json.dumps(review), encoding="utf-8")
        record = json.loads(self.saved)
        record["integration_preparation"] = {"status": "aborted", "attempt_id": "a" * 32,
                                              "cleanup": {"result": "verification_removed"},
                                              "arbitrary_secret": "do-not-export-this-value"}
        (self.run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
        export_dashboard([self.run_dir], self.destination)
        view = payload(self.destination)["runs"][0]
        self.assertEqual("aborted", view["review"]["status"])
        self.assertEqual(partial[0]["returncode"], view["review"]["tests"][0]["returncode"])
        self.assertEqual("aborted", view["integration_preparation"]["status"])
        self.assertIsNone(view["review"]["hash_matches"])
        self.assertIsNone(view["review"]["diff_hash_matches"])
        self.assertNotIn("do-not-export-this-value", self.destination.read_text(encoding="utf-8"))

    def test_output_and_input_paths_are_guarded(self):
        for output in (self.run_dir / "view.html", self.repo / "new.html", self.destination.with_suffix(".txt")):
            with self.assertRaises(SafetyError):
                export_dashboard([self.run_dir], output)
            self.assertFalse(output.exists())
        self.destination.write_text("preserve", encoding="utf-8")
        with self.assertRaises(SafetyError):
            export_dashboard([self.run_dir], self.destination)
        self.assertEqual("preserve", self.destination.read_text())

    def test_malformed_empty_and_large_records_fail_without_output(self):
        for data in ([], {"run_id": "wrong"}, {"run_id": self.run_dir.name, "rounds": "wrong"}):
            (self.run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises((SafetyError, ValueError)):
                export_dashboard([self.run_dir], self.destination)
            self.assertFalse(self.destination.exists())
        (self.run_dir / "run.json").write_bytes(self.saved)
        with patch("repo_maintainer.dashboard.MAX_FILE", 10):
            with self.assertRaises(SafetyError):
                export_dashboard([self.run_dir], self.destination)
        with patch("repo_maintainer.dashboard.MAX_EXPORT", 10):
            with self.assertRaises(SafetyError):
                export_dashboard([self.run_dir], self.destination)
        with self.assertRaises(SafetyError):
            export_dashboard([Path(self.local.name)], self.destination)
        self.assertFalse(self.destination.exists())

    def test_cli_exports_without_workflow_or_provider_calls(self):
        with patch("repo_maintainer.cli.repair", side_effect=AssertionError("mutation")), \
             patch("repo_maintainer.cli.PiProvider", side_effect=AssertionError("provider")), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(0, main(["dashboard", "--input", str(self.run_dir), "--output", str(self.destination)]))
            self.assertTrue(json.loads(stdout.getvalue())["read_only"])

    def test_symlink_is_not_followed(self):
        link = Path(self.local.name) / self.run_dir.name
        try:
            os.symlink(self.run_dir, link, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.run_dir)], check=True, capture_output=True)
        # Remove the link itself, never recurse through a Windows junction.
        self.addCleanup(lambda: link.rmdir() if not link.is_symlink() else link.unlink())
        with self.assertRaises(SafetyError):
            export_dashboard([link], self.destination)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
