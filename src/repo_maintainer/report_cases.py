"""One explicitly selected report becomes historical context, never runtime state."""
from __future__ import annotations

from pathlib import Path
import re

from .legacy_archive import archive_root, describe, source_locks, source_run
from .models import SafetyError, digest
from .store import now
from .ui_backup import encoded, json_value, present, read_bytes, write

REPORT_LIMIT = 512 * 1024
IDENTITY = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def report_snapshot(directory):
    """Read fixed names only; absent run.json is a pinned fact, not a new run."""
    report = read_bytes(directory / "report.md", REPORT_LIMIT)
    record = read_bytes(directory / "run.json") if present(directory / "run.json") else None
    contents = {"report.md": report, "run.json": record}
    files = {name: None if content is None else {"sha256": digest(content), "bytes": len(content)}
             for name, content in contents.items()}
    return files, contents


def record_description(directory, files, run_id):
    evidence = {"files": {run_id + "/run.json": files["run.json"]} if files["run.json"] else {}}
    return describe(directory.parent, run_id, evidence)


def verify_report_source(root, catalog):
    """Schema2 deliberately supports one case; schema1 retains its old behavior."""
    try:
        if (set(catalog) != {"schema_version", "entries", "report_source"}
                or type(catalog["schema_version"]) is not int or catalog["schema_version"] != 2
                or not isinstance(catalog["entries"], list) or len(catalog["entries"]) != 1):
            raise SafetyError("Invalid report case catalog")
        entry, provenance = catalog["entries"][0], catalog["report_source"]
        if (not isinstance(entry, dict) or entry.get("kind") != "case"
                or entry.get("classification") != "historical" or entry.get("path") != "report.md"
                or not isinstance(provenance, dict)
                or set(provenance) != {"run_id", "source_run_dir", "files", "record", "intent_sha256"}
                or not isinstance(provenance["source_run_dir"], str)
                or not Path(provenance["source_run_dir"]).is_absolute()):
            raise SafetyError("Invalid historical report provenance")
        selected = source_run(Path(provenance["source_run_dir"]))
        root = archive_root(root)
        if (str(selected) != provenance["source_run_dir"] or selected.name != provenance["run_id"]
                or root.is_relative_to(selected) or selected.is_relative_to(root)):
            raise SafetyError("Report provenance source binding differs")
        original, original_contents = report_snapshot(selected)
        copied, copied_contents = report_snapshot(root)
        if encoded(original) != encoded(provenance["files"]) or copied != original or copied_contents != original_contents:
            raise SafetyError("Original report source or copied evidence changed; preserve and rebuild explicitly")
        description = record_description(selected, original, selected.name)
        if encoded(description) != encoded(provenance["record"]):
            raise SafetyError("Report historical record description differs")
        intent_raw = read_bytes(root / "intent.json", 128 * 1024)
        intent = json_value(intent_raw)
        expected = {"schema_version": 1, "purpose": "historical_report_case", "runtime_authorized": False,
                    "budget": "unknown_not_imported", "source_run_dir": str(selected), "entry": entry,
                    "files": original, "exported_at": intent.get("exported_at") if isinstance(intent, dict) else None}
        if (encoded(intent) != encoded(expected) or type(intent.get("schema_version")) is not int
                or intent.get("runtime_authorized") is not False
                or not isinstance(intent["exported_at"], str) or not 0 < len(intent["exported_at"]) <= 80
                or digest(intent_raw) != provenance["intent_sha256"]):
            raise SafetyError("Report case intent differs or is incomplete")
        # No locks or state writes during retrieval. Cooperative export locks are separate.
        if report_snapshot(selected)[0] != original or report_snapshot(root)[0] != copied:
            raise SafetyError("Report source changed while verifying")
        return description
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        raise SafetyError("Report case provenance validation failed; retain all evidence") from error


def export_report_case(source, root, repository, index, identifier, topic, start_line, end_line):
    from .source_memory import CATALOG, build_memory, location, sources

    selected, root = source_run(source), archive_root(root)
    root, repository, index = location(root, repository, index)
    if (root.is_relative_to(selected) or selected.is_relative_to(root)
            or index.is_relative_to(selected) or root.is_relative_to(index) or index.is_relative_to(root)):
        raise SafetyError("Select independent new corpus and index outside original report evidence")
    if root.exists() or index.exists():
        raise SafetyError("Report case output exists; preserve it and select new paths")
    if any(not isinstance(value, str) or not IDENTITY.fullmatch(value) for value in (identifier, topic)):
        raise SafetyError("Invalid case identity/topic")
    if (type(start_line) is not int or type(end_line) is not int
            or not 1 <= start_line <= end_line or end_line - start_line >= 100):
        raise SafetyError("Select an explicit report range of at most 100 lines")
    with source_locks(selected):
        files, contents = report_snapshot(selected)
        try:
            lines = contents["report.md"].decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise SafetyError("Selected report must be UTF-8 Markdown") from error
        excerpt = "\n".join(lines[start_line-1:end_line])
        if end_line > len(lines) or not excerpt or len(excerpt.encode()) > 6000:
            raise SafetyError("Selected report range missing, empty or oversized")
        description = record_description(selected, files, selected.name)
        entry = {"id": identifier, "topic": topic, "path": "report.md", "start_line": start_line,
                 "end_line": end_line, "kind": "case", "classification": "historical"}
        intent = {"schema_version": 1, "purpose": "historical_report_case", "runtime_authorized": False,
                  "budget": "unknown_not_imported", "source_run_dir": str(selected), "entry": entry,
                  "files": files, "exported_at": now()}
        root.mkdir(parents=True, exist_ok=False)
        write(root / "intent.json", encoded(intent))
        for name, content in contents.items():
            if content is not None:
                write(root / name, content)
        if report_snapshot(selected)[0] != files:
            raise SafetyError("Original report changed during export; retain partial case")
        catalog = {"schema_version": 2, "entries": [entry], "report_source": {
            "run_id": selected.name, "source_run_dir": str(selected), "files": files,
            "record": description, "intent_sha256": digest(encoded(intent))}}
        # Catalog is a completion record, not an authorization. A failed index is retained.
        write(root / CATALOG, encoded(catalog))
        sources(root)
        built = build_memory(root, repository, index)
        return {**built, "status": "report_case_memory_ready", "source_run_id": selected.name,
                "source_root": str(root), "index": str(index), "classification": "historical",
                "budget": "unknown_not_imported", "record": description}
