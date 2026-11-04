"""Actual SQLite snapshots, fixed evidence, corruption and abrupt process exit."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.cli import main
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.recovery import operation_lock
from repo_maintainer import ui_backup
from repo_maintainer.ui_registry import UIRegistry, provider_binding


class FixtureProvider:
    max_requests = 6
    credential_source = "environment"


class UIBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.runs = self.root / "runs"
        self.binding = {"repository":str(self.repo), "policy":{}, "provider":provider_binding(FixtureProvider())}
        self.registry = UIRegistry(self.runs, self.binding)
        first, _ = self.registry.reserve()
        self.registry.settle(first, {"requests":2}, success=True)
        second, _ = self.registry.reserve()
        self.registry.settle(second, {"requests":None,"consumption":"unknown"}, success=False)
        self.plan_id, self.run_id, self.session_id = "a"*32, "b"*32, "e"*32
        self.plan_path = self.runs / "ui-sessions" / self.session_id / (self.plan_id + ".json")
        self.plan_path.parent.mkdir(parents=True)
        self.plan_path.write_text('{"fixture":"saved plan"}', encoding="utf-8")
        self.registry.put("plan", self.plan_id, {"session_id":self.session_id,
            "file_sha":digest(self.plan_path.read_bytes()), "view":{"status":"consumed","run_id":self.run_id}})
        self.registry.put("run", self.run_id, {"run_id":self.run_id})
        self.registry.put("job", "d"*32, {"status":"running","signature":"original-intent"})
        self.directory = self.runs / self.run_id
        self.directory.mkdir()
        (self.directory / "run.json").write_text(json.dumps({"run_id":self.run_id,"status":"candidate_ready"}))
        (self.directory / "report.md").write_text("original report", encoding="utf-8")
        self.registry.close()
        self.db_path = self.runs / "ui-state" / "registry.sqlite3"
        self.original_db = self.db_path.read_bytes()

    def backup(self):
        result = ui_backup.backup_ui_history(self.runs)
        self.backup_dir = self.runs / "ui-backups" / result["backup_id"]
        return result

    def unchanged(self):
        self.assertEqual(self.original_db, self.db_path.read_bytes())
        self.assertEqual("original report", (self.directory / "report.md").read_text())

    def test_native_snapshot_preserves_usage_unknown_consumed_and_original_job_state(self):
        result = self.backup()
        self.assertEqual((2,4,0,1), (result["requests_used"],result["requests_reserved_unknown"],result["requests_remaining"],result["consumed_plans"]))
        self.assertFalse(result["runtime_authorized"])
        self.assertEqual(result, ui_backup.verify_ui_backup(self.runs, result["backup_id"]))
        data = json.loads((self.backup_dir / "snapshot.json").read_text(encoding="utf-8"))
        job = next(row["body"] for row in data["objects"] if row["kind"] == "job")
        self.assertEqual("running", job["status"], "Archiving must not normalize or replay jobs")
        self.assertEqual("original report", (self.backup_dir / "files" / self.run_id / "report.md").read_text())
        self.unchanged()
        reopened = UIRegistry(self.runs, self.binding)
        try:
            self.assertEqual(4, reopened.budget()["requests_reserved_unknown"])
            self.assertEqual("consumed", reopened.load("plan")[self.plan_id]["view"]["status"])
            self.assertEqual("interrupted", reopened.load("job")["d"*32]["status"])
        finally:
            reopened.close()

    def test_only_registered_fixed_artifacts_are_copied(self):
        (self.runs / ".env").write_text("fixture-private-marker")
        (self.directory / "workspace").mkdir()
        (self.directory / "workspace" / "human.txt").write_text("human-work-marker")
        (self.directory / "unexpected.json").write_text("unknown-file-marker")
        result = self.backup()
        archive_text = (self.backup_dir / "snapshot.json").read_text(encoding="utf-8")
        self.assertNotIn("fixture-private-marker", archive_text)
        self.assertFalse((self.backup_dir / "files" / ".env").exists())
        self.assertFalse((self.backup_dir / "files" / self.run_id / "workspace").exists())
        self.assertFalse((self.backup_dir / "files" / self.run_id / "unexpected.json").exists())
        self.assertFalse(result["runtime_authorized"])
        self.unchanged()

    def test_archive_attempts_including_empty_archive_are_retained(self):
        empty = self.directory / "integration-attempts" / ("1"*32)
        empty.mkdir(parents=True)
        saved = self.directory / "integration-attempts" / ("2"*32)
        saved.mkdir()
        (saved / "integration-review.json").write_text('{"status":"aborted"}')
        result = self.backup()
        self.assertTrue((self.backup_dir / "files" / self.run_id / "integration-attempts" / ("1"*32)).is_dir())
        self.assertEqual(result, ui_backup.verify_ui_backup(self.runs, result["backup_id"]))
        self.unchanged()

    def test_missing_or_changed_original_plan_is_explicit_archive_anomaly(self):
        for content in (None, "human changed original plan"):
            with self.subTest(content=content):
                if content is None:
                    self.plan_path.unlink()
                else:
                    self.plan_path.write_text(content)
                result = self.backup()
                self.assertEqual([{"plan_id":self.plan_id,"reason":"missing_or_changed_plan"}], result["plan_anomalies"])
                self.assertFalse(result["runtime_authorized"])
                self.assertEqual(1,result["consumed_plans"])
                self.assertEqual(4,result["requests_reserved_unknown"])
        self.unchanged()

    def test_active_ui_and_run_operations_refuse_backup_without_new_archive(self):
        active = UIRegistry(self.runs, self.binding)
        try:
            with self.assertRaisesRegex(SafetyError, "UI server is active"):
                ui_backup.backup_ui_history(self.runs)
        finally:
            active.close()
        # Reopening legitimately changed running -> interrupted, not archiving.
        self.original_db = self.db_path.read_bytes()
        with operation_lock(self.directory):
            with self.assertRaisesRegex(SafetyError, "Another operation"):
                ui_backup.backup_ui_history(self.runs)
        self.assertFalse((self.runs / "ui-backups").exists())
        self.unchanged()

    def test_unknown_schema_corrupt_and_missing_database_never_create_replacement(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("PRAGMA user_version=99")
        changed = self.db_path.read_bytes()
        with self.assertRaisesRegex(SafetyError, "Unsupported journal schema"):
            ui_backup.backup_ui_history(self.runs)
        self.assertEqual(changed, self.db_path.read_bytes())
        self.db_path.write_bytes(b"fixture corrupt database")
        with self.assertRaises(SafetyError):
            ui_backup.backup_ui_history(self.runs)
        self.assertEqual(b"fixture corrupt database", self.db_path.read_bytes())
        self.db_path.unlink()
        with self.assertRaises(SafetyError):
            ui_backup.backup_ui_history(self.runs)
        self.assertFalse(self.db_path.exists())
        self.assertFalse((self.runs / "ui-backups").exists())

    def test_budget_attempt_mismatch_and_unknown_objects_are_refused(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("UPDATE budget SET held=0")
        changed = self.db_path.read_bytes()
        with self.assertRaisesRegex(SafetyError, "history disagree"):
            ui_backup.backup_ui_history(self.runs)
        self.assertEqual(changed, self.db_path.read_bytes())
        self.db_path.write_bytes(self.original_db)
        with contextlib.closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("INSERT INTO objects VALUES('unknown',?, '{}')", ("f"*32,))
        with self.assertRaisesRegex(SafetyError, "Unknown journal object"):
            ui_backup.backup_ui_history(self.runs)
        self.assertFalse((self.runs / "ui-backups").exists())

    def test_sidecars_and_large_evidence_are_retained(self):
        sidecar = Path(str(self.db_path) + "-journal")
        sidecar.write_text("fixture unknown sidecar")
        with self.assertRaisesRegex(SafetyError, "sidecar present"):
            ui_backup.backup_ui_history(self.runs)
        self.assertEqual("fixture unknown sidecar", sidecar.read_text())
        sidecar.unlink()
        (self.directory / "report.md").write_bytes(b"x" * (ui_backup.MAX_FILE + 1))
        with self.assertRaisesRegex(SafetyError, "exceeds its limit"):
            ui_backup.backup_ui_history(self.runs)
        self.assertFalse((self.runs / "ui-backups").exists())
        self.assertEqual(self.original_db, self.db_path.read_bytes())

    def test_existing_destination_is_never_overwritten(self):
        result = self.backup()
        before = (self.backup_dir / "manifest.json").read_bytes()
        with patch("repo_maintainer.ui_backup.uuid.uuid4", return_value=uuid.UUID(result["backup_id"])):
            with self.assertRaises(SafetyError):
                ui_backup.backup_ui_history(self.runs)
        self.assertEqual(before, (self.backup_dir / "manifest.json").read_bytes())
        self.assertEqual(result, ui_backup.verify_ui_backup(self.runs,result["backup_id"]))
        self.unchanged()

    def test_evidence_changes_during_copy_preserve_human_edit_and_incomplete_backup(self):
        original_write = ui_backup.write
        def changed(path, content):
            original_write(path, content)
            if path.name == "report.md":
                (self.directory / "report.md").write_text("human edit during backup")
        with patch("repo_maintainer.ui_backup.write", side_effect=changed):
            with self.assertRaisesRegex(SafetyError, "changed"):
                self.backup()
        self.assertEqual("human edit during backup", (self.directory / "report.md").read_text())
        directory = next((self.runs / "ui-backups").iterdir())
        self.assertFalse((directory / "manifest.json").exists())
        with self.assertRaisesRegex(SafetyError,"Incomplete backup"):
            ui_backup.verify_ui_backup(self.runs,directory.name)
        self.assertEqual(self.original_db,self.db_path.read_bytes())

    def test_tampered_missing_and_unexpected_archive_files_are_refused_without_cleanup(self):
        for mode in ("changed", "missing", "extra", "database", "snapshot"):
            with self.subTest(mode=mode):
                result = self.backup()
                path = self.backup_dir / "files" / self.run_id / "report.md"
                if mode == "changed": path.write_text("changed archive")
                elif mode == "missing": path.unlink()
                elif mode == "extra": (self.backup_dir / "human-note.txt").write_text("keep")
                elif mode == "database": (self.backup_dir / "registry.sqlite3").write_bytes(b"bad archive")
                else: (self.backup_dir / "snapshot.json").write_text("{}")
                with self.assertRaises(SafetyError):
                    ui_backup.verify_ui_backup(self.runs,result["backup_id"])
                self.assertTrue(self.backup_dir.exists())
        self.unchanged()

    def test_invalid_identity_link_and_namespace_in_source_are_refused(self):
        for key in ("../elsewhere", "a"*31, "A"*32):
            with self.assertRaises(SafetyError):
                ui_backup.verify_ui_backup(self.runs,key)
        result = self.backup()
        linked = self.backup_dir / "human-link"
        try:
            linked.symlink_to(self.repo, target_is_directory=True)
        except OSError:
            self.skipTest("This machine cannot create the fixture symlink")
        with self.assertRaises(SafetyError):
            ui_backup.verify_ui_backup(self.runs,result["backup_id"])
        linked.unlink()
        with contextlib.closing(sqlite3.connect(self.db_path)) as db, db:
            changed = {**self.binding,"repository":str(self.runs)}
            db.execute("UPDATE metadata SET body=?", (json.dumps(changed),))
        with self.assertRaisesRegex(SafetyError,"outside"):
            ui_backup.backup_ui_history(self.runs)
        with self.assertRaisesRegex(SafetyError,"Git metadata"):
            ui_backup.backup_ui_history(self.root / ".git" / "history")

    def test_actual_process_exit_keeps_incomplete_archive_and_native_lock_releases(self):
        script = """
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from repo_maintainer import ui_backup
original=ui_backup.write
def crash(path,content):
    original(path,content)
    if path.name=='snapshot.json': os._exit(96)
ui_backup.write=crash
ui_backup.backup_ui_history(Path(sys.argv[2]))
"""
        result = subprocess.run([sys.executable,"-c",script,str(Path(__file__).resolve().parents[1]/"src"),str(self.runs)],
                                capture_output=True,timeout=30)
        self.assertEqual(96,result.returncode,result.stderr.decode(errors="replace"))
        incomplete = next((self.runs / "ui-backups").iterdir())
        with self.assertRaisesRegex(SafetyError,"Incomplete backup"):
            ui_backup.verify_ui_backup(self.runs,incomplete.name)
        new = self.backup()
        self.assertNotEqual(incomplete.name,new["backup_id"])
        self.assertTrue(incomplete.exists())
        self.unchanged()

    def test_cli_outputs_only_summary_and_verifies_fixed_archive(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0,main(["backup-ui-history","--runs-dir",str(self.runs)]))
        result = json.loads(output.getvalue())
        self.assertNotIn("saved plan",output.getvalue())
        second = io.StringIO()
        with contextlib.redirect_stdout(second):
            self.assertEqual(0,main(["verify-ui-backup","--runs-dir",str(self.runs),"--backup-id",result["backup_id"]]))
        self.assertEqual(result,json.loads(second.getvalue()))
        self.unchanged()

    def test_manifest_unknown_version_boolean_and_duplicate_fields_are_rejected(self):
        result = self.backup()
        manifest_path = self.backup_dir / "manifest.json"
        original = manifest_path.read_bytes()
        for version in (2, True):
            changed = json.loads(original)
            changed["schema_version"] = version
            manifest_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(SafetyError,"Unsupported"):
                ui_backup.verify_ui_backup(self.runs,result["backup_id"])
        manifest_path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(SafetyError,"Duplicate"):
            ui_backup.verify_ui_backup(self.runs,result["backup_id"])
        manifest_path.write_bytes(original)
        self.assertEqual(result,ui_backup.verify_ui_backup(self.runs,result["backup_id"]))
        self.unchanged()


if __name__ == "__main__":
    unittest.main()
