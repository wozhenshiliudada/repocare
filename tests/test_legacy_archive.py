"""Fixed legacy archives: provenance, malformed evidence and no execution rights."""
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
from filelock import FileLock
from repo_maintainer import legacy_archive as legacy
from repo_maintainer.cli import main
from repo_maintainer.models import SafetyError
from repo_maintainer.ui_backup import MAX_FILE


class LegacyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.key = "a" * 32
        self.source = self.base / "old-runs" / self.key
        self.source.mkdir(parents=True)
        self.root = self.base / "private-runtime"
        self.record = {"run_id":self.key, "schema_version":2, "mode":"auto", "status":"candidate_ready",
                       "finished_at":"historical-time", "source_repo":"untrusted-not-followed",
                       "workspace":"untrusted-not-followed", "requests_remaining":999,
                       "secret_unknown":"not-in-summary"}
        self.save()
        (self.source / "report.md").write_text("original report", encoding="utf-8")

    def save(self):
        (self.source / "run.json").write_text(json.dumps(self.record), encoding="utf-8")

    def imported(self):
        self.result = legacy.import_legacy_run(self.source, self.root)
        self.archive = self.root / legacy.NAMESPACE / self.result["archive_id"]
        return self.result

    def test_native_bytes_provenance_and_explicit_current_source_comparison(self):
        original = (self.source / "run.json").read_bytes()
        result = self.imported()
        self.assertEqual("historical_run_record", result["record_state"])
        self.assertFalse(result["runtime_authorized"])
        self.assertEqual("unknown_not_imported", result["budget"])
        self.assertEqual("not_checked", result["source_state"])
        self.assertEqual(original, (self.archive / "files" / self.key / "run.json").read_bytes())
        self.assertEqual(original, (self.source / "run.json").read_bytes())
        compared = legacy.verify_legacy_run(self.root, result["archive_id"], self.source)
        self.assertEqual("same_evidence", compared["source_state"])
        (self.source / "report.md").write_text("later human change")
        self.assertEqual("changed_evidence", legacy.verify_legacy_run(self.root,result["archive_id"],self.source)["source_state"])
        self.assertEqual(result, legacy.verify_legacy_run(self.root,result["archive_id"]))

    def test_missing_corrupt_unknown_schema_and_incomplete_are_distinct_archives(self):
        variants = [(None,"missing_run_record"), (b'{bad',"invalid_run_record"),
                    (json.dumps({**self.record,"schema_version":99}).encode(),"unsupported_run_schema"),
                    (json.dumps({**self.record,"schema_version":True}).encode(),"unsupported_run_schema"),
                    (json.dumps({**self.record,"finished_at":None}).encode(),"incomplete_run_record"),
                    (json.dumps({**self.record,"run_id":"b"*32}).encode(),"identity_mismatch"),
                    (b'{"run_id":1,"run_id":2}',"invalid_run_record"),
                    (b'{"bad":NaN}',"invalid_run_record")]
        for content, state in variants:
            with self.subTest(state=state,content=content):
                if content is None:
                    (self.source / "run.json").unlink()
                else:
                    (self.source / "run.json").write_bytes(content)
                result = self.imported()
                self.assertEqual(state,result["record_state"])
                self.assertFalse(result["runtime_authorized"])
                self.assertEqual(result,legacy.verify_legacy_run(self.root,result["archive_id"]))
                if content is not None:
                    self.assertEqual(content,(self.source / "run.json").read_bytes())

    def test_fixed_whitelist_and_partial_integration_archives_only(self):
        (self.source / ".env").write_text("private-value")
        (self.source / "workspace").mkdir()
        (self.source / "workspace" / "human.txt").write_text("private-code")
        (self.source / "unselected.json").write_text("private-other")
        (self.source / "run.json.tmp").write_bytes(b"torn write")
        empty = self.source / "integration-attempts" / ("b" * 32)
        empty.mkdir(parents=True)
        result = self.imported()
        files = self.archive / "files" / self.key
        self.assertFalse((files / ".env").exists())
        self.assertFalse((files / "workspace").exists())
        self.assertFalse((files / "unselected.json").exists())
        self.assertEqual(b"torn write",(files / "run.json.tmp").read_bytes())
        self.assertTrue((files / "integration-attempts" / ("b"*32)).is_dir())
        self.assertIn({"file":self.key+"/run.json.tmp","reason":"invalid_json_object"},result["anomalies"])

    def test_ui_and_run_native_locks_deny_without_archive(self):
        state = self.source.parent / "ui-state"
        state.mkdir()
        for path in (state / "owner.lock", self.source / "integration-operation.lock"):
            with FileLock(path):
                with self.assertRaisesRegex(SafetyError,"active"):
                    legacy.import_legacy_run(self.source,self.root)
                self.assertFalse((self.root / legacy.NAMESPACE).exists())

    def test_existing_ui_database_and_budget_are_never_opened_or_rewritten(self):
        state = self.root / "ui-state"
        state.mkdir(parents=True)
        db = state / "registry.sqlite3"
        db.write_bytes(b"unknown original database preserve")
        with patch("sqlite3.connect",side_effect=AssertionError("database")), \
             patch("repo_maintainer.cli.PiProvider",side_effect=AssertionError("provider")), \
             patch("subprocess.Popen",side_effect=AssertionError("execute")):
            self.imported()
            legacy.verify_legacy_run(self.root,self.result["archive_id"],self.source)
        self.assertEqual(b"unknown original database preserve",db.read_bytes())
        self.assertFalse((self.root / "ui-sessions").exists())
        self.assertFalse((self.root / self.key).exists())

    def test_duplicate_imports_are_new_ids_and_never_overwrite(self):
        first = self.imported()
        saved = (self.archive / "manifest.json").read_bytes()
        with self.assertRaises(SafetyError):
            legacy.import_legacy_run(self.archive / "files" / self.key,self.base / "another-archive-root")
        self.assertEqual(first,legacy.verify_legacy_run(self.root,first["archive_id"]))
        second = legacy.import_legacy_run(self.source,self.root)
        self.assertNotEqual(first["archive_id"],second["archive_id"])
        self.assertEqual(saved,(self.archive / "manifest.json").read_bytes())
        with patch("repo_maintainer.legacy_archive.uuid.uuid4") as fixed:
            fixed.return_value.hex = first["archive_id"]
            with self.assertRaises(FileExistsError):
                legacy.import_legacy_run(self.source,self.root)
        self.assertEqual(saved,(self.archive / "manifest.json").read_bytes())

    def test_archive_changed_missing_extra_and_authority_tamper_refused(self):
        result = self.imported()
        report = self.archive / "files" / self.key / "report.md"
        original = report.read_bytes()
        report.write_text("tampered")
        with self.assertRaises(SafetyError):
            legacy.verify_legacy_run(self.root,result["archive_id"])
        report.unlink()
        with self.assertRaises(SafetyError):
            legacy.verify_legacy_run(self.root,result["archive_id"])
        report.write_bytes(original)
        extra = self.archive / "extra-empty-dir"
        extra.mkdir()
        with self.assertRaisesRegex(SafetyError,"Unexpected"):
            legacy.verify_legacy_run(self.root,result["archive_id"])
        extra.rmdir()
        manifest = self.archive / "manifest.json"
        content = json.loads(manifest.read_bytes())
        content["runtime_authorized"] = True
        manifest.write_text(json.dumps(content))
        with self.assertRaisesRegex(SafetyError,"Unsupported"):
            legacy.verify_legacy_run(self.root,result["archive_id"])

    def test_source_mutation_during_copy_preserves_human_change_and_incomplete_archive(self):
        original = legacy.write
        def changed(path, content):
            original(path,content)
            if path.name == "run.json":
                (self.source / "report.md").write_text("human changed")
        with patch.object(legacy,"write",side_effect=changed):
            with self.assertRaisesRegex(SafetyError,"changed"):
                legacy.import_legacy_run(self.source,self.root)
        incomplete = next((self.root / legacy.NAMESPACE).iterdir())
        self.assertEqual("human changed",(self.source / "report.md").read_text())
        self.assertFalse((incomplete / "manifest.json").exists())
        with self.assertRaisesRegex(SafetyError,"Incomplete"):
            legacy.verify_legacy_run(self.root,incomplete.name)

    def test_absolute_fixed_ids_and_git_input_output_boundaries(self):
        for root in (self.source / "inside", self.base / ".git" / "store"):
            with self.assertRaises(SafetyError):
                legacy.import_legacy_run(self.source,root)
        checkout = self.base / "checkout"
        checkout.mkdir()
        (checkout / ".git").write_text("gitdir: not-followed")
        with self.assertRaises(SafetyError):
            legacy.import_legacy_run(self.source,checkout / "archives")
        source = checkout / self.key
        source.mkdir()
        with self.assertRaises(SafetyError):
            legacy.import_legacy_run(source,self.root)
        for key in ("../outside", "B"*32, "short"):
            with self.assertRaises(SafetyError):
                legacy.verify_legacy_run(self.root,key)

    def test_symlink_and_permission_errors_preserve_source(self):
        link = self.base / ("b"*32)
        os.symlink(self.source,link,target_is_directory=True)
        self.addCleanup(link.unlink)
        with self.assertRaises(SafetyError):
            legacy.import_legacy_run(link,self.root)
        original = (self.source / "run.json").read_bytes()
        with patch("repo_maintainer.ui_backup.read_bytes",side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                legacy.import_legacy_run(self.source,self.root)
        self.assertEqual(original,(self.source / "run.json").read_bytes())

    def test_size_limit_and_empty_source_refused(self):
        (self.source / "candidate.diff").write_bytes(b"x"*(MAX_FILE+1))
        with self.assertRaises(SafetyError):
            legacy.import_legacy_run(self.source,self.root)
        self.assertFalse((self.root / legacy.NAMESPACE).exists())
        empty = self.base / ("b"*32)
        empty.mkdir()
        with self.assertRaisesRegex(SafetyError,"No fixed"):
            legacy.import_legacy_run(empty,self.root)

    def test_real_process_exit_retains_incomplete_and_new_retry(self):
        script = """
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from repo_maintainer import legacy_archive
original=legacy_archive.write
def crash(path,content):
    original(path,content)
    if path.name=='run.json': os._exit(97)
legacy_archive.write=crash
legacy_archive.import_legacy_run(Path(sys.argv[2]),Path(sys.argv[3]))
"""
        result = subprocess.run([sys.executable,"-c",script,str(Path(__file__).resolve().parents[1]/"src"),
                                 str(self.source),str(self.root)],capture_output=True,timeout=30)
        self.assertEqual(97,result.returncode,result.stderr.decode(errors="replace"))
        incomplete = next((self.root / legacy.NAMESPACE).iterdir())
        with self.assertRaisesRegex(SafetyError,"Incomplete"):
            legacy.verify_legacy_run(self.root,incomplete.name)
        new = self.imported()
        self.assertNotEqual(incomplete.name,new["archive_id"])
        self.assertTrue(incomplete.exists())

    def test_cli_summary_and_optional_source_comparison_never_print_unknown_values(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0,main(["import-legacy-run","--run-dir",str(self.source),"--archive-root",str(self.root)]))
        result = json.loads(output.getvalue())
        self.assertNotIn("not-in-summary",output.getvalue())
        second = io.StringIO()
        with contextlib.redirect_stdout(second):
            self.assertEqual(0,main(["verify-legacy-run","--archive-root",str(self.root),"--archive-id",result["archive_id"],
                                     "--source-run-dir",str(self.source)]))
        self.assertEqual("same_evidence",json.loads(second.getvalue())["source_state"])
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(1,main(["verify-legacy-run","--archive-root",str(self.root),"--archive-id","../outside"]))

    def test_archive_verify_never_follows_record_paths_or_implicit_source(self):
        self.record.update(source_repo="https://example.invalid/secret",workspace="../external",
                           commands=["never-execute"],credential_file="../secret")
        self.save()
        result = self.imported()
        with patch.object(legacy,"source_run",side_effect=AssertionError("implicit source read")), \
             patch("subprocess.Popen",side_effect=AssertionError("execute")):
            self.assertEqual(result,legacy.verify_legacy_run(self.root,result["archive_id"]))
        other = self.base / "another-source" / self.key
        other.mkdir(parents=True)
        with self.assertRaisesRegex(SafetyError,"explicit original"):
            legacy.verify_legacy_run(self.root,result["archive_id"],other)

    def test_unknown_manifest_version_boolean_and_duplicate_json_refused(self):
        result = self.imported()
        path = self.archive / "manifest.json"
        original = path.read_bytes()
        for version in (2,True):
            value = json.loads(original)
            value["schema_version"] = version
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(SafetyError,"Unsupported"):
                legacy.verify_legacy_run(self.root,result["archive_id"])
        path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(SafetyError,"Duplicate"):
            legacy.verify_legacy_run(self.root,result["archive_id"])
        path.write_bytes(original)
        self.assertEqual(result,legacy.verify_legacy_run(self.root,result["archive_id"]))

    def test_malformed_mode_and_unknown_status_preserved_without_raw_status_output(self):
        self.record["mode"] = ["auto"]
        self.save()
        self.assertEqual("invalid_run_record",self.imported()["record_state"])
        self.record["mode"] = "auto"
        self.record["status"] = "private-marker-never-print"
        self.save()
        result = self.imported()
        self.assertEqual("unrecognized_run_status",result["record_state"])
        self.assertIsNone(result["recorded_status"])
        self.assertNotIn("private-marker-never-print",json.dumps(result))
        self.assertIn(b"private-marker-never-print",(self.archive / "files" / self.key / "run.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
