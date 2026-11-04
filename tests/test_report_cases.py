"""Explicit report selection, source invalidation and retained historical uncertainty."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from filelock import FileLock
from repo_maintainer.cli import main
from repo_maintainer.models import SafetyError, digest
from repo_maintainer import report_cases as cases
from repo_maintainer.source_memory import CATALOG, build_memory, search_memory, sources


class ReportCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "runs" / ("a" * 32); self.source.mkdir(parents=True)
        self.repo = self.base / "repository"; self.repo.mkdir()
        self.root = self.base / "report-memory-cases" / "first"
        self.index = self.base / "private" / "case.memory.sqlite3"
        self.record = {"schema_version": 2, "run_id": self.source.name, "mode": "approval",
                       "status": "candidate_ready", "finished_at": "2026-10-10T00:00:00Z",
                       "authorization": {"decision": "approve", "actor": "old-operator"},
                       "budget": {"used": 1, "remaining": 999}, "command": ["never-execute"]}
        self.save_record()
        (self.source / "report.md").write_bytes(b"# Historical Run\r\n\r\nOld approval and budget 999.\r\nUnselected command: never-execute\r\n")

    def save_record(self):
        (self.source / "run.json").write_text(json.dumps(self.record), encoding="utf-8")

    def export(self, **overrides):
        arguments = dict(source=self.source, root=self.root, repository=self.repo, index=self.index,
                         identifier="old-run", topic="approval", start_line=1, end_line=3)
        return cases.export_report_case(**(arguments | overrides))

    def search(self, query="approval"):
        return search_memory(self.root, self.repo, self.index, query)

    def test_original_bytes_ranges_and_no_authority_or_json_injection(self):
        originals = {p.name: p.read_bytes() for p in (self.source / "run.json", self.source / "report.md")}
        result = self.export(); self.assertFalse(result["runtime_authorized"])
        self.assertEqual("unknown_not_imported", result["budget"])
        self.assertEqual("historical_run_record", result["record"]["record_state"])
        for name, data in originals.items():
            self.assertEqual(data, (self.root / name).read_bytes())
            self.assertEqual(data, (self.source / name).read_bytes())
        row = self.search()["context"]["hits"][0]
        self.assertEqual((1, 3, "historical", "historical_report"),
                         (row["start_line"], row["end_line"], row["classification"], row["classification_origin"]))
        self.assertEqual(digest(originals["report.md"]), row["source_sha256"])
        self.assertNotIn("never-execute", row["excerpt"])
        self.assertNotIn("old-operator", row["excerpt"])
        self.assertEqual([], self.search("Unselected")["context"]["hits"])
        self.assertFalse((self.root / "ui-state").exists())

    def test_source_report_changes_outside_range_invalidate_even_no_hit(self):
        self.export(); (self.source / "report.md").write_bytes((self.source / "report.md").read_bytes() + b"changed\n")
        with self.assertRaisesRegex(SafetyError, "changed"): self.search("nohit")

    def test_source_record_changes_without_report_change_invalidate(self):
        self.export(); self.record["budget"]["remaining"] = 0; self.save_record()
        with self.assertRaisesRegex(SafetyError, "changed"): self.search()

    def test_missing_source_report_is_failure_not_empty_search(self):
        self.export(); (self.source / "report.md").unlink()
        with self.assertRaises(SafetyError): self.search("nohit")

    def test_missing_source_record_invalidates_previously_present_record(self):
        self.export(); (self.source / "run.json").unlink()
        with self.assertRaises(SafetyError): self.search()

    def test_missing_unknown_invalid_partial_and_failed_are_historical(self):
        variants = [(None, "missing_run_record", None),
                    (b"not-json", "invalid_run_record", None),
                    ({**self.record, "schema_version": 99}, "unsupported_run_schema", None),
                    ({**self.record, "finished_at": None}, "incomplete_run_record", "candidate_ready"),
                    ({**self.record, "status": "failed"}, "historical_run_record", "failed"),
                    ({**self.record, "status": "success invented"}, "unrecognized_run_status", None)]
        for number, (record, state, status) in enumerate(variants):
            with self.subTest(state=state, status=status):
                path = self.source / "run.json"
                if record is None: path.unlink()
                else: path.write_bytes(record if isinstance(record, bytes) else json.dumps(record).encode())
                root, index = self.base / f"case-{number}", self.base / f"case-{number}.memory.sqlite3"
                result = self.export(root=root, index=index)
                self.assertEqual((state, status), (result["record"]["record_state"], result["record"]["recorded_status"]))
                hit = search_memory(root, self.repo, index, "approval")["context"]["hits"][0]
                self.assertEqual("historical", hit["classification"])
                self.assertFalse(result["runtime_authorized"])

    def test_appearance_of_previously_missing_record_invalidates(self):
        (self.source / "run.json").unlink(); self.export(); self.save_record()
        with self.assertRaisesRegex(SafetyError, "changed"): self.search()

    def test_copies_intent_and_catalog_tampering_fail_closed(self):
        self.export()
        for name in ("run.json", "report.md", "intent.json", CATALOG):
            path = self.root / name; original = path.read_bytes()
            path.write_bytes(original + b"\nchanged")
            with self.assertRaises(SafetyError): self.search()
            path.write_bytes(original)
        catalog = json.loads((self.root / CATALOG).read_bytes())
        catalog["entries"][0]["classification"] = "confirmed"
        (self.root / CATALOG).write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaises(SafetyError): sources(self.root)

    def test_existing_corpus_and_index_never_overwritten(self):
        self.export(); originals = [(self.root / CATALOG).read_bytes(), self.index.read_bytes()]
        with self.assertRaisesRegex(SafetyError, "exists"): self.export()
        self.assertEqual(originals, [(self.root / CATALOG).read_bytes(), self.index.read_bytes()])
        newroot = self.base / "new-case"
        with self.assertRaises(SafetyError): self.export(root=newroot)
        self.assertFalse(newroot.exists())

    def test_invalid_ranges_identifiers_and_output_overlap_do_not_create_corpus(self):
        for override in ({"start_line": 0}, {"end_line": 101}, {"end_line": 9},
                         {"start_line": True}, {"identifier": "../escape"}, {"topic": "KEY"},
                         {"root": self.source / "case"}, {"root": self.source.parent},
                         {"index": self.source / "case.memory.sqlite3"}):
            with self.subTest(override=override), self.assertRaises(SafetyError): self.export(**override)
        self.assertFalse(self.root.exists())

    def test_no_scanning_or_following_report_command_and_path_fields(self):
        (self.source / "extra.md").write_text("unselected sensitive text", encoding="utf-8")
        self.record["source"] = str(self.base / "nonexistent-other-project"); self.save_record()
        with patch("os.walk", side_effect=AssertionError("No scanning")), \
             patch("subprocess.run", side_effect=AssertionError("No execution")):
            self.export(); self.search()
        self.assertFalse((self.root / "extra.md").exists())

    def test_active_run_and_ui_lock_refuse_before_new_files(self):
        for lockpath in (self.source / "integration-operation.lock", self.source.parent / "ui-state" / "owner.lock"):
            lockpath.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(lockpath, timeout=0, fallback_to_soft=False), self.assertRaisesRegex(SafetyError, "active"):
                self.export()
            self.assertFalse(self.root.exists())

    def test_non_utf8_oversized_and_missing_report_refuse(self):
        for raw in (b"\xff", b"x" * (cases.REPORT_LIMIT + 1), b""):
            (self.source / "report.md").write_bytes(raw)
            with self.assertRaises(SafetyError): self.export()
            self.assertFalse(self.root.exists())
        (self.source / "report.md").unlink()
        with self.assertRaises(SafetyError): self.export()

    def test_partial_export_preserved_without_completion_or_model(self):
        original_write = cases.write
        def fail(path, content):
            if path.name == "report.md": raise OSError("synthetic copy interruption")
            return original_write(path, content)
        with patch.object(cases, "write", side_effect=fail), self.assertRaises(OSError): self.export()
        self.assertTrue((self.root / "intent.json").is_file())
        self.assertFalse((self.root / CATALOG).exists()); self.assertFalse(self.index.exists())
        with self.assertRaises(SafetyError): sources(self.root)
        with self.assertRaisesRegex(SafetyError, "exists"): self.export()

    def test_source_change_during_copy_retains_partial_case(self):
        original_write = cases.write
        def mutate(path, content):
            original_write(path, content)
            if path.name == "report.md": (self.source / "report.md").write_bytes(content + b"mutated\n")
        with patch.object(cases, "write", side_effect=mutate), self.assertRaisesRegex(SafetyError, "changed"):
            self.export()
        self.assertTrue((self.root / "report.md").exists()); self.assertFalse((self.root / CATALOG).exists())

    def test_cli_export_and_unknown_schema_preserve_output(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["memory-export-report", "--run-dir", str(self.source), "--source-root", str(self.root),
                         "--repo", str(self.repo), "--index", str(self.index), "--case-id", "old-run",
                         "--topic", "approval", "--start-line", "1", "--end-line", "3"])
        self.assertEqual(0, code); self.assertEqual("report_case_memory_ready", json.loads(output.getvalue())["status"])
        catalog = json.loads((self.root / CATALOG).read_bytes()); catalog["schema_version"] = 3
        (self.root / CATALOG).write_text(json.dumps(catalog), encoding="utf-8")
        before = self.index.read_bytes()
        with self.assertRaises(SafetyError): self.search()
        self.assertEqual(before, self.index.read_bytes())

    def test_original_journal_consumption_and_unknown_reservation_untouched(self):
        from repo_maintainer.ui_registry import UIRegistry
        binding = {"provider": {"request_budget": 6}}
        registry = UIRegistry(self.source.parent, binding)
        try:
            first, _ = registry.reserve(); registry.settle(first, {"requests": 2}, success=True)
            second, _ = registry.reserve(); registry.settle(second, {"requests": None}, success=False)
            registry.put("plan", "b" * 32, {"status": "consumed", "run_id": self.source.name})
            before_budget, before_plan = registry.budget(), registry.load("plan")
        finally: registry.close()
        database = self.source.parent / "ui-state" / "registry.sqlite3"
        before = database.read_bytes()
        self.export(); self.search()
        self.assertEqual(before, database.read_bytes())
        registry = UIRegistry(self.source.parent, binding)
        try:
            self.assertEqual(before_budget, registry.budget()); self.assertEqual(before_plan, registry.load("plan"))
            with self.assertRaises(SafetyError): registry.reserve()
        finally: registry.close()

    def test_index_failure_preserves_complete_corpus_and_partial_index(self):
        import sqlite3
        with patch("repo_maintainer.source_memory.sqlite3.connect", side_effect=sqlite3.OperationalError("synthetic failure")), \
             self.assertRaisesRegex(SafetyError, "partial index"):
            self.export()
        self.assertTrue((self.root / CATALOG).exists()); self.assertTrue(self.index.exists())
        self.assertEqual(b"", self.index.read_bytes())
        with self.assertRaises(SafetyError): self.export()
        alternate = self.base / "retry.memory.sqlite3"
        build_memory(self.root, self.repo, alternate)
        self.assertEqual("historical", search_memory(self.root, self.repo, alternate, "approval")["context"]["hits"][0]["classification"])

    def test_git_metadata_or_checkout_paths_are_refused(self):
        (self.repo / ".git").mkdir()
        for override in ({"root": self.repo / "case"}, {"index": self.repo / "case.memory.sqlite3"},
                         {"root": self.base / ".git" / "case"}):
            with self.subTest(override=override), self.assertRaises(SafetyError): self.export(**override)
        (self.source.parent / ".git").mkdir()
        with self.assertRaises(SafetyError): self.export()
        self.assertFalse(self.root.exists())

    def test_provenance_boolean_or_extra_fields_are_not_numeric_equivalents(self):
        self.record["schema_version"] = 1; self.save_record()
        self.export(); original = (self.root / CATALOG).read_bytes()
        catalog = json.loads(original); catalog["report_source"]["record"]["record_schema"] = True
        (self.root / CATALOG).write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaises(SafetyError): self.search()
        for field, value in (("files", {"report.md": {"sha256": "a" * 64, "bytes": True}, "run.json": None}),
                             ("record", {"record_state": "invented"}), ("extra", "unselected")):
            catalog = json.loads(original); catalog["report_source"][field] = value
            (self.root / CATALOG).write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaises(SafetyError): self.search()
        (self.root / CATALOG).write_bytes(original)

    def test_gui_memory_previews_case_and_marks_original_change_invalid_without_calls(self):
        from repo_maintainer.pi_provider import PiProvider
        from repo_maintainer.ui_memory import UIMemory
        self.export()
        provider = PiProvider(("calculator.py",), "http://127.0.0.1:1/v1", max_requests=2)
        provider.memory_root, provider.memory_index = self.root, self.index
        memory = UIMemory(provider, self.repo)
        result = memory.preview("approval")
        self.assertEqual("historical", result["citations"][0]["classification"])
        self.assertNotIn("excerpt", result["citations"][0]); self.assertEqual(0, provider.requests_used)
        self.record["status"] = "interrupted"; self.save_record()
        self.assertEqual("invalid", memory.view()["status"])
        with self.assertRaises(SafetyError): memory.require_current("approval")
        self.assertEqual(0, provider.requests_used)
