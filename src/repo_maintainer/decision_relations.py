"""Explicit replacement claims with original evidence; never select active policy."""
from __future__ import annotations

from pathlib import Path
import re

from .legacy_archive import archive_root
from .memory_collection import exact, valid_id, HEX
from .models import SafetyError, digest
from .recovery import unlinked
from .source_memory import CATALOG, LABELS, canonical, location, search_memory, sources
from .store import now
from .ui_backup import encoded, json_value, read_bytes, write

MAX_FILE = 128 * 1024
FIELDS = "id newer_id older_id newer_sha256 older_sha256 statement_line statement_sha256"


def snapshot(root, repository, index):
    catalog_sha, rows = sources(root)
    original = read_bytes(index, 16 * 1024 * 1024)
    result = search_memory(root, repository, index, "relations", limit=1)
    if result["context"]["index_sha256"] != digest(original):
        raise SafetyError("Relation index changed while reading")
    if sources(root) != (catalog_sha, rows) or read_bytes(index, 16 * 1024 * 1024) != original:
        raise SafetyError("Relation sources changed while reading")
    binding = {"source_root": str(root), "repository": str(repository), "index": str(index),
               "catalog_sha256": catalog_sha, "sources_sha256": digest(canonical(rows)),
               "index_sha256": digest(original)}
    return binding, rows


def output_location(output, root, repository, index):
    output = archive_root(output)
    if not output.name.endswith(".memory-relations.json"):
        raise SafetyError("Explicit .memory-relations.json file required")
    protected = [root, repository, index]
    catalog = json_value(read_bytes(root / CATALOG, MAX_FILE))
    catalogs = [catalog]
    if catalog["schema_version"] == 3:
        for member in catalog["members"]:
            directory = unlinked(Path(member["source_root"]))
            protected.append(directory)
            catalogs.append(json_value(read_bytes(directory / CATALOG, MAX_FILE)))
    for original in catalogs:
        if original["schema_version"] == 2:
            protected.append(unlinked(Path(original["report_source"]["source_run_dir"])))
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
        raise SafetyError("Relations must be outside repository, index, every source corpus and original report evidence")
    return output


def source_lines(root, row):
    directory = root
    if "origin" in row:
        catalog = json_value(read_bytes(root / CATALOG, MAX_FILE))
        directory = Path(next(m["source_root"] for m in catalog["members"]
                              if m["id"] == row["origin"]["member_id"]))
    raw = read_bytes(directory / row["path"], 512 * 1024)
    if digest(raw) != row["source_sha256"]:
        raise SafetyError("Replacement statement source changed")
    return raw.decode("utf-8").splitlines()


def declaration(lines, number):
    """Only an isolated column-zero marker outside fences/comments is eligible."""
    if (number > 1 and lines[number - 2].strip()) or (number < len(lines) and lines[number].strip()):
        raise SafetyError("Replacement statement requires surrounding blank lines")
    fence, comment, html = None, False, []
    html_blocks = {"pre", "code", "script", "style", "textarea", "div", "details", "blockquote",
                   "table", "section", "article", "aside", "main", "body", "html"}
    for line in lines[:number]:
        if fence is not None:
            closing = re.fullmatch(r"\s*(`{3,}|~{3,})\s*", line)
            if closing and closing[1][0] == fence[0] and len(closing[1]) >= len(fence):
                fence = None
            eligible = False
            continue
        # This deliberately rejects even a marker appearing on a comment boundary.
        eligible = not comment and "<!--" not in line
        if comment or "<!--" in line:
            if "<!--" in line:
                comment = line.rfind("<!--") > line.rfind("-->")
            elif "-->" in line:
                comment = False
            continue
        opening = re.match(r"^\s*(`{3,}|~{3,})", line)
        if opening:
            fence, eligible = opening[1], False
            continue
        for tag in re.finditer(r"<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", line):
            name = tag[2].casefold()
            if name in html_blocks:
                if tag[1] and html and html[-1] == name:
                    html.pop()
                elif not tag[1] and not tag[0].endswith("/>"):
                    html.append(name)
    return eligible and fence is None and not comment and not html


def validate_relations(root, rows, relations):
    if not isinstance(relations, list) or not 1 <= len(relations) <= 50:
        raise SafetyError("Select 1-50 explicit replacement claims")
    by_id = {r["id"]: r for r in rows}
    seen, edges, graph = set(), set(), {}
    for relation in relations:
        if (not exact(relation, FIELDS)
                or not all(valid_id(relation[k]) for k in ("id", "newer_id", "older_id"))
                or any(not isinstance(relation[k], str) or not HEX.fullmatch(relation[k])
                       for k in ("newer_sha256", "older_sha256", "statement_sha256"))
                or type(relation["statement_line"]) is not int):
            raise SafetyError("Invalid replacement identity, hashes or evidence fields")
        newer_id, older_id = relation["newer_id"], relation["older_id"]
        if (relation["id"] in seen or (newer_id, older_id) in edges or newer_id == older_id):
            raise SafetyError("Duplicate or self replacement claim")
        if newer_id not in by_id or older_id not in by_id:
            raise SafetyError("Dangling replacement endpoint")
        newer, older = by_id[newer_id], by_id[older_id]
        if any(r["kind"] != "document" or r["classification"] not in LABELS
               or r["classification_origin"] != "source_marker" for r in (newer, older)):
            raise SafetyError("Replacement endpoints must remain explicitly classified decisions")
        if newer["topic"] != older["topic"]:
            raise SafetyError("Replacement decisions must share an explicit topic")
        if relation["newer_sha256"] != newer["source_sha256"] or relation["older_sha256"] != older["source_sha256"]:
            raise SafetyError("Replacement endpoint hash differs from original source")
        number = relation["statement_line"]
        if not newer["start_line"] <= number <= newer["end_line"]:
            raise SafetyError("Replacement statement must be inside the newer selected range")
        lines = source_lines(root, newer)
        statement = f"Supersedes: {newer_id} -> {older_id}"
        if (lines[number - 1] != statement or digest(statement.encode()) != relation["statement_sha256"]
                or not declaration(lines, number)):
            raise SafetyError("Missing unquoted explicit replacement statement or evidence hash differs")
        seen.add(relation["id"]); edges.add((newer_id, older_id))
        graph.setdefault(newer_id, set()).add(older_id)
    visited, active = set(), set()

    def visit(node):
        if node in active:
            raise SafetyError("Cyclic replacement claims")
        if node in visited:
            return
        active.add(node)
        for other in graph.get(node, ()):
            visit(other)
        active.remove(node); visited.add(node)

    for node in graph:
        visit(node)


def citation(row):
    fields = ("id", "topic", "path", "start_line", "end_line", "classification", "source_sha256")
    value = {k: row[k] for k in fields}
    if "origin" in row:
        value["origin"] = row["origin"]
    return value


def projections(rows, relations):
    by_id = {r["id"]: r for r in rows}
    return [{"id": r["id"], "kind": "explicit_supersedes_claim",
             "newer": citation(by_id[r["newer_id"]]), "older": citation(by_id[r["older_id"]]),
             "statement_line": r["statement_line"], "statement_sha256": r["statement_sha256"]}
            for r in sorted(relations, key=lambda r: r["id"])]


def register_relations(spec_file, root, repository, index, output):
    root, repository, index = location(root, repository, index)
    binding, rows = snapshot(root, repository, index)
    output = output_location(output, root, repository, index)
    spec_file = unlinked(spec_file)
    raw = read_bytes(spec_file, MAX_FILE)
    spec = json_value(raw)
    if (not exact(spec, "schema_version relations") or type(spec["schema_version"]) is not int
            or spec["schema_version"] != 1):
        raise SafetyError("Invalid explicit replacement specification")
    validate_relations(root, rows, spec["relations"])
    if read_bytes(spec_file, MAX_FILE) != raw or snapshot(root, repository, index) != (binding, rows):
        raise SafetyError("Replacement specification or sources changed before registration")
    value = {"schema_version": 1, "purpose": "explicit_decision_relations", "binding": binding,
             "relations": spec["relations"], "created_at": now(), "runtime_authorized": False}
    content = encoded(value)
    if len(content) > MAX_FILE:
        raise SafetyError("Replacement manifest exceeds its bounded size")
    try:
        write(output, content)
    except FileExistsError as error:
        raise SafetyError("Relations output exists; preserve it and choose a new path") from error
    result = verify_relations(output, root, repository, index)
    return {**result, "status": "memory_relations_registered"}


def verify_relations(manifest, root, repository, index):
    root, repository, index = location(root, repository, index)
    binding, rows = snapshot(root, repository, index)
    manifest = output_location(manifest, root, repository, index)
    raw = read_bytes(manifest, MAX_FILE)
    value = json_value(raw)
    if (not exact(value, "schema_version purpose binding relations created_at runtime_authorized")
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["purpose"] != "explicit_decision_relations" or value["runtime_authorized"] is not False
            or not isinstance(value["created_at"], str) or not 0 < len(value["created_at"]) <= 80
            or canonical(value["binding"]) != canonical(binding)):
        raise SafetyError("Replacement manifest binding or protocol differs")
    validate_relations(root, rows, value["relations"])
    if snapshot(root, repository, index) != (binding, rows) or read_bytes(manifest, MAX_FILE) != raw:
        raise SafetyError("Replacement manifest or sources changed during verification")
    return {"status": "memory_relations_verified", "runtime_authorized": False, "claims_only": True,
            "manifest_sha256": digest(raw), "index_sha256": binding["index_sha256"],
            "relations": projections(rows, value["relations"])}


def preview_relations(manifest, root, repository, index, query, limit=5):
    result = verify_relations(manifest, root, repository, index)
    searched = search_memory(root, repository, index, query, limit)
    final = verify_relations(manifest, root, repository, index)
    if result != final or searched["context"]["index_sha256"] != result["index_sha256"]:
        raise SafetyError("Replacement evidence changed during preview")
    hits = searched["context"]["hits"]
    ids = {r["id"] for r in hits}
    return {**result, "status": "memory_relations_preview", "hits": [citation(r) for r in hits],
            "mixed_topics": searched["mixed_topics"], "planning_policy": "unchanged",
            "relations": [r for r in result["relations"] if r["newer"]["id"] in ids or r["older"]["id"] in ids]}
