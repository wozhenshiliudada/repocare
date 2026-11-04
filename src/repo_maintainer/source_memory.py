"""Bounded, source-pinned local retrieval. Memory is context, never approval."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from .legacy_archive import archive_root
from .models import SafetyError, digest, safe_context_path
from .recovery import unlinked
from .store import redact
from .ui_backup import json_value, read_bytes, readonly, write
from .source_view import source_snapshot

CATALOG = "memory-catalog.json"
LABELS = {"confirmed":"确认", "proposed":"提议", "pending":"待定", "superseded":"已替代"}
IDENTITY = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":"), allow_nan=False).encode()


def decision_markers(lines, start, end):
    """Quoted examples are not decisions, even when the selected range starts inside a fence."""
    markers, fence = set(), None
    for number, line in enumerate(lines[:end], 1):
        boundary = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if boundary:
            token, tail = boundary.groups()
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not tail.strip():
                fence = None
            continue
        if fence is not None or number < start:
            continue
        plain = line.replace("**", "")
        if re.match(r"^(?:状态[：:]|Status:)", plain):
            found = re.fullmatch(r"(?:状态[：:]\s*(确认|提议|待定|已替代)|Status:\s*(confirmed|proposed|pending|superseded))(.*)", plain)
            if not found:
                raise SafetyError("Unknown explicit memory status marker")
            chinese, english, tail = found.groups()
            if tail.strip() and tail.strip()[0] not in "。.(（":
                raise SafetyError("Ambiguous memory status marker; templates are not decisions")
            markers.add(chinese or LABELS[english])
        markers.update("待定" if value == "部分待定" else value
                       for value in re.findall(r"\|\s*(确认|提议|待定|部分待定|已替代)\s*\|", plain))
    return markers


@source_snapshot
def sources(root: Path, *, _allow_collection=True):
    root = unlinked(root)
    if not root.is_dir() or any(p.name.casefold() == ".git" for p in (root, *root.parents)):
        raise SafetyError("Select an explicit source directory outside Git metadata")
    raw = read_bytes(root / CATALOG, 128 * 1024)
    catalog = json_value(raw)
    if isinstance(catalog, dict) and type(catalog.get("schema_version")) is int and catalog["schema_version"] == 3:
        if not _allow_collection:
            raise SafetyError("Collection nesting is forbidden")
        from .memory_collection import collection_rows
        rows = collection_rows(root, catalog)
        if read_bytes(root / CATALOG, 128 * 1024) != raw:
            raise SafetyError("Collection catalog changed while reading")
        return digest(raw), rows
    if isinstance(catalog,dict) and type(catalog.get('schema_version')) is int and catalog['schema_version']==4:
        from .memory_migration import verify_migrated_sources
        verify_migrated_sources(root,catalog)
    if (not isinstance(catalog, dict) or type(catalog.get("schema_version")) is not int
            or catalog["schema_version"] not in {1, 2, 4}
            or set(catalog) != ({"schema_version", "entries"} if catalog["schema_version"] == 1
                                else {"schema_version", "entries", "migration_source" if catalog['schema_version']==4 else "report_source"})
            or not isinstance(catalog["entries"], list) or not 1 <= len(catalog["entries"]) <= 50):
        raise SafetyError("Invalid memory catalog")
    if catalog["schema_version"] == 2:
        from .report_cases import verify_report_source
        verify_report_source(root, catalog)
    result, seen, contents = [], set(), {}
    for row in catalog["entries"]:
        if not isinstance(row, dict) or set(row) != {"id","topic","path","start_line","end_line","kind","classification"}:
            raise SafetyError("Memory entries require exact source/range/classification fields")
        for field in ("id", "topic"):
            if not isinstance(row[field], str) or not IDENTITY.fullmatch(row[field]):
                raise SafetyError("Invalid memory identity/topic")
        if row["id"] in seen:
            raise SafetyError("Duplicate memory source identity")
        seen.add(row["id"])
        if not isinstance(row["path"], str) or len(row["path"]) > 512:
            raise SafetyError("Invalid bounded memory source path")
        relative = safe_context_path(row["path"])
        for part in relative.split("/"):
            safe_context_path(part)
        if Path(relative).suffix != ".md":
            raise SafetyError("Only explicitly selected Markdown sources enter memory")
        if (not isinstance(row["kind"], str) or not isinstance(row["classification"], str)
                or row["kind"] not in {"document", "case"} or row["classification"] not in {*LABELS,"reference","historical"}):
            raise SafetyError("Unknown memory classification")
        if (row["kind"] == "case") != (row["classification"] == "historical"):
            raise SafetyError("Case reports remain historical claims")
        if (type(row["start_line"]) is not int or type(row["end_line"]) is not int
                or not 1 <= row["start_line"] <= row["end_line"]
                or row["end_line"] - row["start_line"] >= 100):
            raise SafetyError("Invalid bounded source range")
        path = unlinked(root / relative)
        if relative not in contents:
            content = read_bytes(path, 512 * 1024)
            try:
                contents[relative] = (content, content.decode("utf-8").splitlines())
            except UnicodeDecodeError as error:
                raise SafetyError("Memory source must be UTF-8 Markdown") from error
        content, lines = contents[relative]
        if row["end_line"] > len(lines):
            raise SafetyError("Memory range exceeds original source")
        excerpt = "\n".join(lines[row["start_line"]-1:row["end_line"]])
        if not excerpt or len(excerpt.encode()) > 6000:
            raise SafetyError("Memory excerpt empty or exceeds 6000 bytes")
        label = row["classification"]
        if label in LABELS:
            markers = decision_markers(lines,row["start_line"],row["end_line"])
            if markers != {LABELS[label]}:
                raise SafetyError("Classification must match the sole explicit source marker")
        excerpt = redact(excerpt)
        if len(excerpt.encode()) > 6000:
            raise SafetyError("Redacted memory excerpt exceeds 6000 bytes")
        result.append({**row, "source_sha256":digest(content), "excerpt_sha256":digest(excerpt.encode()),
                       "classification_origin":"source_marker" if label in LABELS else "historical_report" if label == "historical" else "curated_reference",
                       "excerpt":excerpt})
    for relative, (content, _) in contents.items():
        if read_bytes(root / relative, 512 * 1024) != content:
            raise SafetyError("Memory source changed while reading")
    if read_bytes(root / CATALOG,128*1024) != raw:
        raise SafetyError("Memory catalog changed while reading")
    if catalog["schema_version"] == 2:
        verify_report_source(root, catalog)
    if catalog['schema_version']==4:verify_migrated_sources(root,catalog)
    return digest(raw), sorted(result, key=lambda r:r["id"])


def location(root, repository, index):
    root, repository, index = unlinked(root), unlinked(repository), unlinked(index)
    if not repository.is_dir() or not index.name.endswith(".memory.sqlite3"):
        raise SafetyError("Explicit repository and .memory.sqlite3 index required")
    if index.is_relative_to(root) or index.is_relative_to(repository):
        raise SafetyError("Memory index must be outside source corpus and repository")
    archive_root(index)
    if (root/'memory-catalog.json').is_file():
        try:
            catalog=json_value(read_bytes(root/'memory-catalog.json',128*1024))
            if isinstance(catalog,dict) and catalog.get('schema_version')==4:
                from .memory_migration import protect_migration_outputs
                protect_migration_outputs(root,repository,index,catalog)
        except (ValueError,KeyError,TypeError,RecursionError) as error:
            raise SafetyError('Memory catalog binding cannot be verified') from error
    return root, repository, index


def build_memory(root: Path, repository: Path, index: Path):
    root, repository, index = location(root,repository,index)
    catalog_sha, rows = sources(root)
    if any("origin" in r for r in rows):
        from .memory_collection import protect_member_outputs
        protect_member_outputs(root, index, repository)
    metadata = {"schema_version":1,"source_root":str(root),"repository":str(repository),"catalog_sha256":catalog_sha}
    try:
        write(index,b"")  # Exclusive creation; interrupted files are retained, never overwritten.
    except FileExistsError as error:
        raise SafetyError("Memory index already exists; choose a new path and preserve it") from error
    try:
        with closing(sqlite3.connect(index)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE metadata(body TEXT NOT NULL)")
            db.execute("CREATE TABLE entries(id TEXT PRIMARY KEY,body TEXT NOT NULL)")
            db.execute("CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,text,tokenize='trigram')")
            for row in rows:
                db.execute("INSERT INTO entries VALUES(?,?)",(row["id"],canonical(row).decode()))
                db.execute("INSERT INTO search(id,text) VALUES(?,?)",(row["id"],row["excerpt"]))
            db.execute("INSERT INTO metadata VALUES(?)",(canonical(metadata).decode(),))
            db.execute("PRAGMA user_version=1")
            db.commit()
    except sqlite3.Error as error:
        raise SafetyError("Memory build failed; preserve partial index and choose a new path") from error
    # Check current sources and schema before reporting a usable index.
    search_memory(root,repository,index,"verification",limit=1)
    return {"status":"memory_index_ready","entries":len(rows),"catalog_sha256":catalog_sha,
            "index_sha256":digest(read_bytes(index,16*1024*1024)),"runtime_authorized":False}


def search_memory(root: Path, repository: Path, index: Path, query: str, limit=5):
    root, repository, index = location(root,repository,index)
    if not isinstance(query,str) or not 1 <= len(query.encode()) <= 4096 or type(limit) is not int or not 1 <= limit <= 5:
        raise SafetyError("Invalid bounded memory query")
    tokens = list(dict.fromkeys(re.findall(r"[^\W_]+",query.casefold())))[:16]
    if not tokens:
        raise SafetyError("Memory query has no searchable terms")
    original = read_bytes(index,16*1024*1024)
    try:
        with closing(readonly(index)) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 1 or db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise SafetyError("Unknown or damaged memory index; preserve it")
            tables = {name:kind for name,kind in db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger')")}
            names = {"metadata","entries","search","search_data","search_idx","search_content","search_docsize","search_config"}
            if tables != {name:"table" for name in names}:
                raise SafetyError("Unexpected memory index schema")
            stored = db.execute("SELECT body FROM metadata").fetchall()
            catalog_sha, rows = sources(root)
            if any("origin" in r for r in rows):
                from .memory_collection import protect_member_outputs
                protect_member_outputs(root, index, repository)
            expected = {"schema_version":1,"source_root":str(root),"repository":str(repository),"catalog_sha256":catalog_sha}
            if len(stored) != 1 or json_value(stored[0][0]) != expected:
                raise SafetyError("Memory binding/catalog changed; explicitly build a new index")
            values = db.execute("SELECT id,body FROM entries ORDER BY id LIMIT 51").fetchall()
            if values != [(r["id"],canonical(r).decode()) for r in rows]:
                raise SafetyError("Memory sources changed or index entries differ; stop injection")
            if db.execute("SELECT id,text FROM search ORDER BY id LIMIT 51").fetchall() != [(r["id"],r["excerpt"]) for r in rows]:
                raise SafetyError("Memory search contents differ")
            ids = []
            long = [token for token in tokens if len(token) >= 3]
            if long:
                match = " OR ".join('"'+token.replace('"','""')+'"' for token in long)
                ids = [r[0] for r in db.execute("SELECT id FROM search WHERE search MATCH ? ORDER BY bm25(search),id",(match,))]
            short = [token for token in tokens if len(token) < 3]
            if short:
                clause = " OR ".join("text LIKE ?" for _ in short)
                for row in db.execute("SELECT id FROM search WHERE "+clause+" ORDER BY id",tuple("%"+t+"%" for t in short)):
                    if row[0] not in ids:
                        ids.append(row[0])
            by_id = {r["id"]:r for r in rows}
            hits = [by_id[key] for key in ids[:limit]]
    except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
        raise SafetyError("Memory index verification failed; preserve sources and index") from error
    if read_bytes(index,16*1024*1024) != original:
        raise SafetyError("Memory index changed during retrieval")
    topics = {}
    for row in rows:  # All curated entries, so a top-k cutoff cannot hide mixed statuses.
        if row["classification"] in LABELS:
            topics.setdefault(row["topic"],set()).add(row["classification"])
    mixed = sorted({r["topic"] for r in hits if len(topics.get(r["topic"],set())) > 1})
    data = {"schema_version":2 if any("origin" in r for r in rows) else 1,
            "index_sha256":digest(original),"query_sha256":digest(query.encode()),"hits":hits}
    return {"status":"verified_source_memory","runtime_authorized":False,"mixed_topics":mixed,
            "context":{**data,"context_sha256":digest(canonical(data))}}


def planning_context(root, repository, index, query):
    result = search_memory(root,repository,index,query)
    if result["mixed_topics"]:
        raise SafetyError("Mixed decision classifications require review before memory injection")
    return result["context"]
