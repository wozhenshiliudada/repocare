"""Explicit legacy evidence import; archives never become runnable registrations."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import uuid

from filelock import FileLock, Timeout

from .models import SafetyError, digest
from .recovery import unlinked
from .store import now
from .ui_backup import capture, encoded, identity, json_value, present, read_bytes, write

NAMESPACE = "legacy-run-archives"
RECORDED_STATES = frozenset(("inspecting", "planned", "rejected", "awaiting_approval", "creating_workspace",
                           "baseline_testing", "editing", "candidate_testing", "candidate_ready", "replanning",
                           "interrupted", "failed", "rolled_back", "cleanup_required", "source_changed",
                           "discarded", "repair_aborted", "integrated"))


def archive_root(path):
    root = unlinked(path)
    for parent in (root, *root.parents):
        if parent.name.casefold() in {".git", NAMESPACE, "ui-backups"} or present(parent / ".git"):
            raise SafetyError("Legacy paths must be outside Git and existing archive namespaces")
    return root


def source_run(path):
    directory = archive_root(path)
    identity(directory.name)
    if not directory.is_dir() or any(p.name.casefold() == ".git" for p in (directory, *directory.parents)):
        raise SafetyError("Select one existing legacy run directory outside Git metadata")
    return directory


@contextmanager
def source_locks(directory):
    # Fixed lock paths only; malformed run.json cannot select a lock or repository.
    try:
        with ExitStack() as stack:
            state = unlinked(directory.parent / "ui-state")
            paths = [directory / "integration-operation.lock"]
            if present(state):
                if not state.is_dir():
                    raise SafetyError("Unexpected UI state entry; retain source")
                paths.insert(0, state / "owner.lock")
            for path in paths:
                stack.enter_context(FileLock(unlinked(path), timeout=0, blocking=False,
                                             fallback_to_soft=False, preserve_lock_file=True))
            yield
    except Timeout as error:
        raise SafetyError("Source UI/run operation is active; stop writers before importing") from error


def inventory(parent, run_id):
    return capture(parent, {"objects":[{"kind":"run", "id":run_id, "body":{}}]})


def describe(parent, run_id, evidence):
    """Interpret bounded fixed JSON only; every field remains a historical claim."""
    parsed, anomalies = {}, []
    for relative in sorted(evidence["files"]):
        if relative.endswith((".json", ".json.tmp")):
            try:
                value = json_value(read_bytes(parent / relative))
                if not isinstance(value, dict):
                    raise ValueError("not an object")
                parsed[relative] = value
            except (ValueError, TypeError, SafetyError, RecursionError):
                anomalies.append({"file":relative, "reason":"invalid_json_object"})
    name = run_id + "/run.json"
    record = parsed.get(name)
    status = schema = None
    if name not in evidence["files"]:
        state = "missing_run_record"
    elif record is None:
        state = "invalid_run_record"
    elif record.get("run_id") != run_id:
        state = "identity_mismatch"
    elif type(record.get("schema_version")) is not int or record["schema_version"] not in {1, 2}:
        state = "unsupported_run_schema"
    elif (not isinstance(record.get("status"), str) or not 0 < len(record["status"]) <= 80
          or not isinstance(record.get("mode"), str) or record["mode"] not in {"approval", "auto"}
          or (record.get("finished_at") is not None and not isinstance(record["finished_at"], str))):
        state = "invalid_run_record"
    elif record["status"] not in RECORDED_STATES:
        state = "unrecognized_run_status"
    else:
        status, schema = record["status"], record["schema_version"]
        state = "historical_run_record" if record.get("finished_at") else "incomplete_run_record"
    return {"record_state":state, "recorded_status":status, "record_schema":schema,
            "record_sha256":evidence["files"].get(name, {}).get("sha256"), "anomalies":anomalies}


def summary(manifest):
    return {"status":"verified_legacy_archive", "archive_id":manifest["archive_id"],
            "run_id":manifest["run_id"], "read_only":True, "runtime_authorized":False,
            "budget":"unknown_not_imported", "source_state":"not_checked",
            "evidence_files":len(manifest["evidence"]["files"]), **manifest["record"]}


def verify_legacy_run(root: Path, archive_id: str, source: Path | None = None):
    directory = unlinked(archive_root(root) / NAMESPACE / identity(archive_id))
    if not present(directory / "manifest.json"):
        raise SafetyError("Incomplete legacy archive: completion manifest absent; retain it")
    try:
        manifest = json_value(read_bytes(directory / "manifest.json"))
        if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                or manifest["kind"] != "legacy_run_archive" or manifest["status"] != "complete"
                or manifest["archive_id"] != archive_id or manifest["runtime_authorized"] is not False
                or manifest["budget"] != "unknown_not_imported"):
            raise SafetyError("Unsupported legacy archive manifest")
        key = identity(manifest["run_id"])
        evidence = inventory(directory / "files", key)
        if evidence != manifest["evidence"] or describe(directory / "files", key, evidence) != manifest["record"]:
            raise SafetyError("Legacy archive evidence changed or its description differs")
        intent_bytes = read_bytes(directory / "intent.json")
        intent = json_value(intent_bytes)
        if (type(intent["schema_version"]) is not int or intent["schema_version"] != 1
                or intent["archive_id"] != archive_id or intent["run_id"] != key
                or intent["purpose"] != "read_only_legacy_archive"
                or not isinstance(intent["source_run_dir"], str) or not Path(intent["source_run_dir"]).is_absolute()
                or intent["source_run_dir"] != manifest["source_run_dir"]
                or digest(intent_bytes) != manifest["intent_sha256"]):
            raise SafetyError("Legacy archive provenance differs")
        expected = {"intent.json", "manifest.json"} | {"files/" + name for name in evidence["files"]}
        folders = {"files", "files/" + key}
        for name in expected:
            folders.update(p.as_posix() for p in Path(name).parents if p.as_posix() != ".")
        for run_id, archives in evidence["archives"].items():
            folders.add("files/" + run_id + "/integration-attempts")
            folders.update("files/" + run_id + "/integration-attempts/" + a for a in archives)
        def failed(error):
            raise error
        for parent, dirs, files in os.walk(directory, followlinks=False, onerror=failed):
            for name in dirs + files:
                entry = unlinked(Path(parent) / name)
                if entry.relative_to(directory).as_posix() not in (folders if entry.is_dir() else expected):
                    raise SafetyError("Unexpected legacy archive entry; retain it")
        result = summary(manifest)
        if source is not None:
            selected = source_run(source)
            if selected.name != key or str(selected) != intent["source_run_dir"]:
                raise SafetyError("Select the explicit original source run for comparison")
            with source_locks(selected):
                result["source_state"] = "same_evidence" if inventory(selected.parent, key) == evidence else "changed_evidence"
        return result
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        raise SafetyError("Legacy archive validation failed; original and archive retained") from error


def import_legacy_run(source: Path, root: Path):
    selected, destination = source_run(source), archive_root(root)
    if destination.is_relative_to(selected) or selected.is_relative_to(destination / NAMESPACE):
        raise SafetyError("Legacy output cannot contain or be inside selected archive evidence")
    with source_locks(selected):
        key = selected.name
        before = inventory(selected.parent, key)
        # Empty evidence is not an import; missing run.json with other reports is.
        if not before["files"]:
            raise SafetyError("No fixed legacy evidence found")
        description = describe(selected.parent, key, before)
        archive_id = identity(uuid.uuid4().hex)
        directory = unlinked(destination / NAMESPACE / archive_id)
        directory.mkdir(parents=True, exist_ok=False)
        intent = {"schema_version":1, "archive_id":archive_id, "run_id":key,
                  "purpose":"read_only_legacy_archive", "source_run_dir":str(selected), "imported_at":now()}
        write(directory / "intent.json", encoded(intent))
        (directory / "files" / key).mkdir(parents=True)
        for run_id, archives in before["archives"].items():
            parent = directory / "files" / run_id / "integration-attempts"
            parent.mkdir()
            for attempt in archives:
                (parent / attempt).mkdir()
        for relative, expected in before["files"].items():
            content = read_bytes(selected.parent / relative)
            if {"sha256":digest(content), "bytes":len(content)} != expected:
                raise SafetyError("Legacy source changed while copying; retain incomplete archive")
            write(directory / "files" / relative, content)
        if inventory(selected.parent, key) != before:
            raise SafetyError("Legacy source changed; retain incomplete archive")
        manifest = {"schema_version":1, "kind":"legacy_run_archive", "status":"complete",
                    "archive_id":archive_id, "run_id":key, "runtime_authorized":False,
                    "budget":"unknown_not_imported", "source_run_dir":str(selected),
                    "intent_sha256":digest(encoded(intent)), "evidence":before, "record":description}
        write(directory / "manifest.json", encoded(manifest))
        return verify_legacy_run(destination, archive_id)
