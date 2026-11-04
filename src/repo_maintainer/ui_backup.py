"""Offline, verifiable archives; never restore, reopen approvals or reset budgets."""
from __future__ import annotations

import json
import os
from contextlib import ExitStack, closing
from pathlib import Path
import re
import sqlite3
import time
import uuid

from filelock import FileLock, Timeout

from .models import SafetyError, digest
from .recovery import operation_lock, unlinked
from .store import now

MAX_DB = 32 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_NATIVE = 64 * 1024 * 1024
# These are preserved as historical metadata, never restored as authority.
MEMORY_KINDS = {'stop','budget_change','continuation','pi_task','pi_task_link','pi_checkpoint_attempt',
    'pi_failure_feedback','pi_failure_seal','pi_failure_recovery','pi_failure_cleanup',
    'repository_experience','experience_review','experience_settings','experience_task','experience_capture',
    'repository_knowledge','knowledge_review','knowledge_task','retrieval_settings','retrieval_task','context_settings','pi_context_policy',
    'pi_compaction_receipt','pi_compaction_intent','maintenance_settings','maintenance_note','maintenance_note_version','maintenance_task'}
ID = re.compile(r"[0-9a-f]{32}")
RUN_FILES = ("run.json", "run.json.tmp", "report.md", "candidate.diff", "repair-execution.json",
             "repair-execution.json.tmp", "integration-review.json", "integration-review.json.tmp",
             "integration-review.md", "integration.diff", "round-01.diff", "round-02.diff", "round-03.diff")
ARCHIVE_FILES = ("run.json", "report.md", "integration-review.json", "integration-review.md", "integration.diff")


def identity(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SafetyError("Invalid archive object identity")
    return value


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def read_bytes(path, limit=MAX_FILE):
    path = unlinked(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise SafetyError("Archive input missing, nonregular or exceeds its limit")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise SafetyError("Archive input grew beyond its limit")
    return content


def present(path):
    try:
        unlinked(path).stat()
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


def json_value(content):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SafetyError("Duplicate archive JSON field")
            result[key] = value
        return result
    def constant(value):
        raise SafetyError("Nonfinite archive JSON value")
    return json.loads(content, object_pairs_hook=pairs, parse_constant=constant)


def write(path, content):
    path = unlinked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def readonly(path):
    read_bytes(path, MAX_DB)
    for suffix in ("-wal", "-shm", "-journal"):
        if present(Path(str(path) + suffix)):
            raise SafetyError("SQLite sidecar present; preserve it and stop writers before archiving")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2, isolation_level=None)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def snapshot(db):
    if db.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise SafetyError("Unsupported journal schema; never create a zero-budget replacement")
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise SafetyError("Journal integrity check failed; preserve original")
    tables = {name: kind for name, kind in db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger')")}
    if tables != {"metadata":"table", "objects":"table", "budget":"table"}:
        raise SafetyError("Unexpected journal schema objects")
    columns = {"metadata": ["body"], "objects": ["kind", "id", "body"], "budget": ["id", "ceiling", "used", "held"]}
    for name, expected in columns.items():
        if [row[1] for row in db.execute("PRAGMA table_info(" + name + ")")] != expected:
            raise SafetyError("Journal columns changed")
    metadata = db.execute("SELECT body FROM metadata").fetchall()
    budgets = db.execute("SELECT id,ceiling,used,held FROM budget").fetchall()
    if len(metadata) != 1 or len(budgets) != 1 or budgets[0][0] != 1:
        raise SafetyError("Journal metadata/budget identity changed")
    binding = json_value(metadata[0][0])
    ceiling, used, held = budgets[0][1:]
    if (any(type(n) is not int or n < 0 for n in (ceiling, used, held)) or used + held > ceiling
            or type(binding["provider"]["request_budget"]) is not int
            or ceiling != binding["provider"]["request_budget"]):
        raise SafetyError("Invalid durable request budget")
    rows = db.execute("SELECT kind,id,body FROM objects ORDER BY rowid LIMIT 1001").fetchall()
    if len(rows) > 1000:
        raise SafetyError("Archive object count exceeds its limit")
    objects = []
    counted_used = counted_held = 0
    for kind, key, body in rows:
        if kind not in {"job", "plan", "run", "attempt"} | MEMORY_KINDS:
            raise SafetyError("Unknown journal object kind")
        if kind == 'pi_task_link':
            prefix, separator, target = key.partition(':')
            if separator != ':' or prefix not in {'plan','run'}:
                raise SafetyError('Invalid archived task link identity')
            identity(target)
        elif kind in {'experience_settings','context_settings','maintenance_settings','retrieval_settings'}:
            if key != 'default':
                raise SafetyError('Invalid archived memory settings identity')
        else:
            identity(key)
        value = json_value(body)
        if not isinstance(value, dict):
            raise SafetyError("Journal object is not an object")
        if kind == "plan":
            identity(value["session_id"])
            if value["view"]["status"] not in {"consumed", "awaiting_approval"}:
                raise SafetyError("Unknown plan state")
        if kind == "job" and value["status"] not in {"running", "finished", "failed", "interrupted", "stopped"}:
            raise SafetyError("Unknown job state")
        if kind == "run" and value.get("run_id") != key:
            raise SafetyError("Journal run identity changed")
        if kind == "attempt":
            reserved = value["reserved"]
            if type(reserved) is not int or reserved < 0:
                raise SafetyError("Invalid reservation")
            if value["status"] in {"started", "unknown"}:
                counted_held += reserved
            elif value["status"] == "finished":
                count = value["evidence"]["requests"]
                if type(count) is not int or not 0 <= count <= reserved:
                    raise SafetyError("Invalid settled usage")
                counted_used += count
            else:
                raise SafetyError("Unknown attempt state")
        objects.append({"kind":kind, "id":key, "body":value})
    if (used, held) != (counted_used, counted_held):
        raise SafetyError("Budget and model attempt history disagree")
    return {"schema_version":1, "binding":binding,
            "budget":{"ceiling":ceiling,"used":used,"held":held}, "objects":objects}


def evidence_limit(relative):
    return MAX_NATIVE if relative.startswith('task-sessions/') else MAX_FILE


def native_name(value):
    if (not isinstance(value,str) or not 1 <= len(value) <= 160 or value.startswith('.')
            or re.fullmatch(r'[A-Za-z0-9_.-]+\.jsonl',value) is None
            or value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',
                *(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
            or any(c in value for c in '/\\:')
            or any(ord(c)<32 for c in value)):
        raise SafetyError('Invalid archived native session filename')
    return value


def capture(root, data):
    files, missing, anomalies, archives_seen = {}, [], [], {}
    total = 0
    def add(relative):
        nonlocal total
        if len(files) + len(missing) >= 5000:
            raise SafetyError("Evidence archive entry count exceeds its limit")
        path = unlinked(root / relative)
        if not present(path):
            missing.append(relative)
            return
        content = read_bytes(path, evidence_limit(relative))
        total += len(content)
        if total > MAX_TOTAL or len(files) >= 5000:
            raise SafetyError("Evidence archive exceeds its bounded size/count")
        files[relative] = {"sha256":digest(content), "bytes":len(content)}
    for row in data["objects"]:
        key, value = row["id"], row["body"]
        if row['kind'] == 'pi_task':
            identity(key)
            if value.get('task_id') != key:
                raise SafetyError('Archived native task identity changed')
            base = 'task-sessions/' + key + '/sessions/'
            checkpoint = value.get('checkpoint')
            expected = native_name(checkpoint['file']) if checkpoint is not None else None
            sessions = unlinked(root / base)
            names = set()
            if present(sessions):
                if not sessions.is_dir():
                    raise SafetyError('Native session container is not a directory')
                for item in sessions.iterdir():
                    names.add(native_name(item.name))
                    if len(names)>100:
                        raise SafetyError('Native session archive count exceeded')
            if expected:names.add(expected)
            for name in sorted(names):add(base+name)
        elif row["kind"] == "plan":
            relative = "ui-sessions/" + identity(value["session_id"]) + "/" + key + ".json"
            add(relative)
            if relative not in files or files[relative]["sha256"] != value["file_sha"]:
                anomalies.append({"plan_id":key, "reason":"missing_or_changed_plan"})
        elif row["kind"] == "run":
            for name in RUN_FILES:
                add(key + "/" + name)
            archives = unlinked(root / key / "integration-attempts")
            if present(archives):
                archives_seen[key] = []
                for directory in sorted(archives.iterdir()):
                    identity(directory.name)
                    if not unlinked(directory).is_dir():
                        raise SafetyError("Unknown integration archive entry")
                    archives_seen[key].append(directory.name)
                    for name in ARCHIVE_FILES:
                        add(key + "/integration-attempts/" + directory.name + "/" + name)
    return {"files":files, "missing":sorted(missing), "plan_anomalies":anomalies, "archives":archives_seen}


def report(data, manifest):
    b = data["budget"]
    return {"status":"verified_archive", "backup_id":manifest["backup_id"],
            "runtime_authorized":False, "schema_version":1,
            "snapshot_sha256":manifest["snapshot_sha256"],
            "requests_used":b["used"], "requests_reserved_unknown":b["held"],
            "requests_remaining":b["ceiling"]-b["used"]-b["held"],
            "objects":len(data["objects"]), "evidence_files":len(manifest["evidence"]["files"]),
            'native_session_files':sum(name.startswith('task-sessions/') for name in manifest['evidence']['files']),
            'native_history_is_archival_only':True,
            "consumed_plans":sum(row["kind"] == "plan" and row["body"]["view"]["status"] == "consumed" for row in data["objects"]),
            "plan_anomalies":manifest["evidence"]["plan_anomalies"]}


def verify_ui_backup(runs_dir: Path, backup_id: str):
    try:
        directory = unlinked(runs_dir / "ui-backups" / identity(backup_id))
        if not unlinked(directory / "manifest.json").is_file():
            raise SafetyError("Incomplete backup: completion manifest is absent")
        manifest = json_value(read_bytes(directory / "manifest.json"))
        if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["status"] != "complete"
                or manifest["backup_id"] != backup_id):
            raise SafetyError("Unsupported or incomplete backup manifest")
        with closing(readonly(directory / "registry.sqlite3")) as db:
            data = snapshot(db)
        if (encoded(data) != read_bytes(directory / "snapshot.json", 2 * MAX_DB)
                or digest(encoded(data)) != manifest["snapshot_sha256"]):
            raise SafetyError("Database and readable archive snapshot differ")
        evidence = capture(directory / "files", data)
        if evidence != manifest["evidence"]:
            raise SafetyError("Archived evidence changed or disappeared")
        expected = {"intent.json", "registry.sqlite3", "snapshot.json", "manifest.json"}
        expected.update("files/" + name for name in evidence["files"])
        expected_dirs = {"files"} | {parent.as_posix() for name in expected for parent in Path(name).parents if parent.as_posix() != "."}
        for key, archives in evidence["archives"].items():
            directory_paths = ["files/" + key + "/integration-attempts"]
            directory_paths += [directory_paths[0] + "/" + value for value in archives]
            for name in directory_paths:
                expected_dirs.add(name)
                expected_dirs.update(parent.as_posix() for parent in Path(name).parents if parent.as_posix() != ".")
        def failed(error):
            raise error
        for parent, directories, files in os.walk(directory, followlinks=False, onerror=failed):
            for name in directories + files:
                path = unlinked(Path(parent) / name)
                relative = path.relative_to(directory).as_posix()
                if relative not in (expected_dirs if path.is_dir() else expected):
                    raise SafetyError("Unexpected archive entry; retain it")
        expected_hashes = {name: {"sha256":digest(read_bytes(directory / name, MAX_DB if name == "registry.sqlite3" else 2*MAX_DB)),
                                 "bytes":(directory / name).stat().st_size}
                           for name in ("intent.json", "registry.sqlite3", "snapshot.json")}
        if expected_hashes != manifest["archive_files"]:
            raise SafetyError("Archive file fingerprint changed")
        return report(data, manifest)
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as error:
        raise SafetyError("Archive validation failed; original and archive retained") from error


def backup_ui_history(runs_dir: Path):
    """Freeze cooperative writers, copy committed SQLite state and fixed evidence."""
    root = unlinked(runs_dir)
    if any(parent.name.casefold() == ".git" for parent in (root, *root.parents)):
        raise SafetyError("Archive root cannot be inside Git metadata")
    source = unlinked(root / "ui-state" / "registry.sqlite3")
    original_sha = digest(read_bytes(source, MAX_DB))  # Never create a missing journal.
    guard = FileLock(unlinked(root / "ui-state" / "owner.lock"), timeout=0, blocking=False,
                     fallback_to_soft=False, preserve_lock_file=True)
    try:
        with guard, ExitStack() as locks, closing(readonly(source)) as db:
            db.execute("BEGIN")
            data = snapshot(db)
            repo = unlinked(Path(data["binding"]["repository"]))
            if not Path(data["binding"]["repository"]).is_absolute() or root.is_relative_to(repo):
                raise SafetyError("Archive root must be outside its recorded source repository")
            for row in data["objects"]:
                if row['kind'] == 'pi_task':
                    directory = unlinked(root / 'task-sessions' / identity(row['id']))
                    if present(directory):
                        locks.enter_context(FileLock(str(unlinked(directory/'session.lock')),timeout=0,blocking=False,
                            fallback_to_soft=False,preserve_lock_file=True))
                if row["kind"] == "run" and present(root / row["id"] / "run.json"):
                    locks.enter_context(operation_lock(root / row["id"]))
            before = capture(root, data)
            key = identity(uuid.uuid4().hex)
            directory = unlinked(root / "ui-backups" / key)
            directory.mkdir(parents=True, exist_ok=False)
            write(directory / "intent.json", encoded({"schema_version":1, "backup_id":key, "source_runs_dir":str(root),
                                                      "created_at":now(), "purpose":"archive_only"}))
            target = directory / "registry.sqlite3"
            write(target, b"")
            deadline = time.monotonic() + 15
            def progress(*_):
                if time.monotonic() > deadline:
                    raise SafetyError("SQLite backup deadline exceeded; retain incomplete archive")
            with closing(sqlite3.connect(target)) as destination:
                db.backup(destination, pages=128, progress=progress, sleep=.05)
                if snapshot(destination) != data:
                    raise SafetyError("SQLite backup snapshot differs")
            readable = encoded(data)
            if len(readable) > 2 * MAX_DB:
                raise SafetyError("Readable snapshot exceeds its size limit")
            write(directory / "snapshot.json", readable)
            (directory / "files").mkdir()
            for run_id, archives in before["archives"].items():
                parent = directory / "files" / run_id / "integration-attempts"
                parent.mkdir(parents=True, exist_ok=False)
                for attempt_id in archives:
                    (parent / attempt_id).mkdir()
            for relative, expected in before["files"].items():
                content = read_bytes(root / relative, evidence_limit(relative))
                if {"sha256":digest(content), "bytes":len(content)} != expected:
                    raise SafetyError("Evidence changed while copying; retain incomplete archive")
                write(directory / "files" / relative, content)
            if (capture(root, data) != before or snapshot(db) != data
                    or digest(read_bytes(source, MAX_DB)) != original_sha):
                raise SafetyError("Source journal/evidence changed; retain incomplete archive")
            for suffix in ("-wal", "-shm", "-journal"):
                if present(Path(str(source) + suffix)):
                    raise SafetyError("External SQLite writer observed; retain incomplete archive")
            manifest = {"schema_version":1, "status":"complete", "backup_id":key,
                        "snapshot_sha256":digest(encoded(data)), "evidence":before,
                        "archive_files":{name:{"sha256":digest(read_bytes(directory / name, 2*MAX_DB)),
                                               "bytes":(directory / name).stat().st_size}
                                         for name in ("intent.json", "registry.sqlite3", "snapshot.json")}}
            write(directory / "manifest.json", encoded(manifest))
            return verify_ui_backup(root, key)
    except Timeout as error:
        raise SafetyError("UI server is active; close it before archiving history") from error
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as error:
        raise SafetyError("Backup failed; original journal and incomplete archive retained") from error
