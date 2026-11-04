"""Explicit, nonrecursive composition of pinned document/report corpora."""
from __future__ import annotations

from pathlib import Path
import re

from .legacy_archive import archive_root
from .models import SafetyError, digest
from .recovery import unlinked
from .store import now
from .ui_backup import encoded, json_value, read_bytes, write

IDENTITY = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
HEX = re.compile(r"[0-9a-f]{64}")


def exact(value, fields):
    return isinstance(value, dict) and set(value) == set(fields.split())


def valid_id(value):
    return isinstance(value, str) and bool(IDENTITY.fullmatch(value))


def selections(value, members):
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        raise SafetyError("Select 1-50 explicit collection entries")
    seen, originals = set(), set()
    for row in value:
        if (not exact(row, "id member_id entry_id") or not all(valid_id(v) for v in row.values())
                or row["member_id"] not in members or row["id"] in seen
                or (row["member_id"], row["entry_id"]) in originals):
            raise SafetyError("Invalid or duplicate collection selection")
        seen.add(row["id"]); originals.add((row["member_id"], row["entry_id"]))
    if {r["member_id"] for r in value} != set(members):
        raise SafetyError("Each collection member needs an explicit selection")


def member_snapshot(directory):
    from .source_memory import CATALOG, canonical, sources
    raw = read_bytes(directory / CATALOG, 128 * 1024)
    catalog = json_value(raw)
    if (not isinstance(catalog, dict) or type(catalog.get("schema_version")) is not int
            or catalog["schema_version"] not in {1, 2, 4}):
        raise SafetyError("Collection members must be document/report/migrated corpora; nesting is forbidden")
    catalog_sha, rows = sources(directory, _allow_collection=False)
    if catalog_sha != digest(raw):
        raise SafetyError("Collection member catalog changed while reading")
    run_id = catalog["report_source"]["run_id"] if catalog["schema_version"] == 2 else None
    return catalog["schema_version"], catalog_sha, digest(canonical(rows)), rows, run_id


def protect_member_outputs(root, index, repository=None):
    """A later memory-build must not write its new index into an original member."""
    from .source_memory import CATALOG
    catalog = json_value(read_bytes(root / CATALOG, 128 * 1024))
    for member in catalog["members"]:
        directory = unlinked(Path(member["source_root"]))
        if index.is_relative_to(directory) or directory.is_relative_to(index):
            raise SafetyError("Collection index must be outside every original member corpus")
        if member['schema_version']==4:
            from .memory_migration import protect_migration_outputs
            require_catalog=json_value(read_bytes(directory/CATALOG,128*1024))
            if repository is None:raise SafetyError('Migration members require an explicit target repository')
            protect_migration_outputs(directory,repository,index,require_catalog,root)


def collection_rows(root, catalog):
    from .source_memory import canonical
    if (not exact(catalog, "schema_version members selections intent_sha256")
            or type(catalog["schema_version"]) is not int or catalog["schema_version"] != 3
            or not isinstance(catalog["members"], list) or not 1 <= len(catalog["members"]) <= 10
            or not isinstance(catalog["intent_sha256"], str) or not HEX.fullmatch(catalog["intent_sha256"])):
        raise SafetyError("Invalid memory collection catalog")
    root = archive_root(root)
    members, paths, snapshots = {}, set(), {}
    total = 0
    for member in catalog["members"]:
        if (not exact(member, "id source_root schema_version catalog_sha256 sources_sha256")
                or not valid_id(member["id"]) or member["id"] in members
                or not isinstance(member["source_root"], str) or not Path(member["source_root"]).is_absolute()
                or type(member["schema_version"]) is not int or member["schema_version"] not in {1, 2, 4}
                or any(not isinstance(member[k], str) or not HEX.fullmatch(member[k])
                       for k in ("catalog_sha256", "sources_sha256"))):
            raise SafetyError("Invalid collection member binding")
        directory = unlinked(Path(member["source_root"]))
        if (str(directory) != member["source_root"] or directory in paths
                or root.is_relative_to(directory) or directory.is_relative_to(root)):
            raise SafetyError("Collection member paths overlap outputs or repeat")
        snapshot = member_snapshot(directory)
        if snapshot[:3] != (member["schema_version"], member["catalog_sha256"], member["sources_sha256"]):
            raise SafetyError("Collection member sources changed; explicitly rebuild")
        total += len(snapshot[3])
        if total > 50:
            raise SafetyError("All member catalogs together may contain at most 50 entries")
        members[member["id"]] = member; paths.add(directory); snapshots[member["id"]] = snapshot
    selections(catalog["selections"], members)
    raw = read_bytes(root / "intent.json", 128 * 1024)
    intent = json_value(raw)
    expected = {"schema_version": 1, "purpose": "mixed_source_memory", "runtime_authorized": False,
                "budget": "unknown_not_imported", "members": catalog["members"],
                "selections": catalog["selections"],
                "created_at": intent.get("created_at") if isinstance(intent, dict) else None}
    if (encoded(intent) != encoded(expected) or type(intent.get("schema_version")) is not int
            or intent.get("runtime_authorized") is not False
            or not isinstance(intent["created_at"], str) or not 0 < len(intent["created_at"]) <= 80
            or digest(raw) != catalog["intent_sha256"]):
        raise SafetyError("Collection intent differs or is incomplete")
    result = []
    for selection in catalog["selections"]:
        member = members[selection["member_id"]]
        snapshot = snapshots[member["id"]]
        original = next((r for r in snapshot[3] if r["id"] == selection["entry_id"]), None)
        if original is None:
            raise SafetyError("Selected collection entry missing from original catalog")
        origin = {"member_id": member["id"], "entry_id": original["id"], "path": original["path"],
                  "catalog_sha256": member["catalog_sha256"], "sources_sha256": member["sources_sha256"],
                  "kind": "report_case" if member["schema_version"] == 2 else "catalog",
                  "run_id": snapshot[4]}
        result.append({**original, "id": selection["id"], "origin": origin})
    # Verify ALL members again, including entries outside the query and selected subset.
    for name, member in members.items():
        if canonical(member_snapshot(Path(member["source_root"]))) != canonical(snapshots[name]):
            raise SafetyError("Collection member changed during verification")
    if read_bytes(root / "intent.json", 128 * 1024) != raw:
        raise SafetyError("Collection intent changed during verification")
    return sorted(result, key=lambda r: r["id"])


def compose_memory(selection_file, root, repository, index):
    from .source_memory import CATALOG, build_memory, location
    selection_file = unlinked(selection_file)
    spec_raw = read_bytes(selection_file, 128 * 1024)
    spec = json_value(spec_raw)
    if (not exact(spec, "schema_version members selections") or type(spec["schema_version"]) is not int
            or spec["schema_version"] != 1 or not isinstance(spec["members"], list)
            or not 1 <= len(spec["members"]) <= 10):
        raise SafetyError("Invalid explicit collection selection file")
    root = archive_root(root)
    root, repository, index = location(root, repository, index)
    if root.is_relative_to(repository) or repository.is_relative_to(root) or root.is_relative_to(index):
        raise SafetyError("Collection corpus must be independent of repository and index")
    if root.exists() or index.exists():
        raise SafetyError("Collection output exists; select new paths and preserve evidence")
    members, paths = {}, set()
    for item in spec["members"]:
        if (not exact(item, "id source_root") or not valid_id(item["id"]) or item["id"] in members
                or not isinstance(item["source_root"], str) or not Path(item["source_root"]).is_absolute()):
            raise SafetyError("Invalid explicit collection member")
        directory = unlinked(Path(item["source_root"]))
        if (directory in paths or root.is_relative_to(directory) or directory.is_relative_to(root)
                or index.is_relative_to(directory) or directory.is_relative_to(index)):
            raise SafetyError("Collection outputs must be outside every original corpus")
        schema, catalog_sha, source_sha, _, _ = member_snapshot(directory)
        if schema==4:
            from .memory_migration import protect_migration_outputs
            protect_migration_outputs(directory,repository,index,json_value(read_bytes(directory/CATALOG,128*1024)),root)
        members[item["id"]] = {"id": item["id"], "source_root": str(directory), "schema_version": schema,
                               "catalog_sha256": catalog_sha, "sources_sha256": source_sha}
        paths.add(directory)
    selections(spec["selections"], members)
    intent = {"schema_version": 1, "purpose": "mixed_source_memory", "runtime_authorized": False,
              "budget": "unknown_not_imported", "members": list(members.values()),
              "selections": spec["selections"], "created_at": now()}
    if read_bytes(selection_file, 128 * 1024) != spec_raw:
        raise SafetyError("Collection selection changed while reading")
    root.mkdir(parents=True, exist_ok=False)
    write(root / "intent.json", encoded(intent))
    catalog = {"schema_version": 3, "members": intent["members"], "selections": intent["selections"],
               "intent_sha256": digest(encoded(intent))}
    collection_rows(root, catalog)
    write(root / CATALOG, encoded(catalog))
    result = build_memory(root, repository, index)
    return {**result, "status": "mixed_source_memory_ready", "members": len(members),
            "budget": "unknown_not_imported"}
