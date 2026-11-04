"""Reproducible CLI evidence on a NEW self-authored directory; no provider."""
import contextlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.cli import main
from repo_maintainer.memory_collection import compose_memory
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.source_memory import CATALOG, planning_context, search_memory
from repo_maintainer.ui_backup import encoded


def exercise(directory):
    directory = directory.absolute()
    directory.mkdir(parents=True, exist_ok=False)
    docs, extra, repo = (directory / p for p in ("documents", "extra", "repository"))
    for path in (docs, extra, repo): path.mkdir()
    originals = {docs / "old.md": "状态：确认\nLegacy alpha requires reviewed approval.\n".encode(),
                 docs / "new.md": "状态：提议\nNew beta alternative remains proposed.\n\nSupersedes: new -> old\n\n".encode(),
                 extra / "other.md": "状态：待定\nUnselected gamma remains undecided.\n".encode(),
                 extra / "selected.md": "状态：确认\nSelected epsilon reference decision.\n".encode()}
    for path, raw in originals.items(): path.write_bytes(raw)
    for root, entries in ((docs, [("old", "confirmed", "policy"), ("new", "proposed", "policy")]),
                          (extra, [("other", "pending", "other"), ("selected", "confirmed", "other")])):
        (root / CATALOG).write_bytes(encoded({"schema_version": 1, "entries": [
            {"id": name, "topic": topic, "path": name + ".md", "kind": "document", "classification": status,
             "start_line": 1, "end_line": len(originals[root / (name + ".md")].decode().splitlines())}
            for name, status, topic in entries]}))
        originals[root / CATALOG] = (root / CATALOG).read_bytes()
    selection = directory / "selection.private.json"
    selection.write_bytes(encoded({"schema_version": 1, "members": [
        {"id": "docs", "source_root": str(docs)}, {"id": "extra", "source_root": str(extra)}],
        "selections": [{"id": name, "member_id": "docs", "entry_id": name} for name in ("old", "new")]
            + [{"id": "other", "member_id": "extra", "entry_id": "selected"}]}))
    root, index = directory / "memory-collections" / "mixed", directory / "demo.memory.sqlite3"
    compose_memory(selection, root, repo, index)
    original_index = index.read_bytes()
    original_context = search_memory(root, repo, index, "beta")
    spec, manifest = directory / "relations.private.json", directory / "demo.memory-relations.json"
    spec.write_bytes(encoded({"schema_version": 1, "relations": [{"id": "new-claims-old", "newer_id": "new",
        "older_id": "old", "newer_sha256": digest(originals[docs / "new.md"]),
        "older_sha256": digest(originals[docs / "old.md"]), "statement_line": 4,
        "statement_sha256": digest(b"Supersedes: new -> old")}]}))
    common = ["--source-root", str(root), "--repo", str(repo), "--index", str(index)]
    def command(args, expected=0):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            actual = main(args + common)
        assert actual == expected, (args[0], actual, expected)
        return json.loads(out.getvalue()) if expected == 0 else {"exit_code": actual}
    summary = {"fixture": "self_authored", "checks": {}, "provider_constructed": 0, "paid_requests": 0}
    with patch("repo_maintainer.cli.PiProvider", side_effect=AssertionError("No provider permitted")) as provider:
        register = ["memory-relations-register", "--spec", str(spec), "--output", str(manifest)]
        registered = command(register)
        verified = command(["memory-relations-verify", "--relations", str(manifest)])
        preview = ["memory-relations-preview", "--relations", str(manifest), "--query", "beta", "--limit", "1"]
        summary["preview"] = command(preview)
        assert registered["runtime_authorized"] is False and verified["claims_only"] is True
        edge = summary["preview"]["relations"][0]
        assert len(summary["preview"]["hits"]) == 1 and edge["older"]["classification"] == "confirmed"
        assert edge["newer"]["classification"] == "proposed" and edge["older"]["origin"]["member_id"] == "docs"
        summary["checks"]["register_verify_top1_both_origins_and_original_statuses"] = True
        empty = command(["memory-relations-preview", "--relations", str(manifest), "--query", "absent"])
        assert not empty["hits"] and not empty["relations"]
        summary["checks"]["no_hit_still_validated"] = True
        try: planning_context(root, repo, index, "beta")
        except SafetyError as error: assert "Mixed" in str(error)
        else: raise AssertionError("Mixed status must remain blocked")
        assert search_memory(root, repo, index, "beta") == original_context
        summary["checks"]["mixed_planning_block_and_pi_context_unchanged"] = True
        (extra / "other.md").write_bytes(originals[extra / "other.md"] + b"\nUnselected trailing change.\n")
        command(preview, expected=1)
        (extra / "other.md").write_bytes(originals[extra / "other.md"])
        summary["checks"]["unselected_unhit_member_change_blocks_preview"] = True
        (docs / "old.md").write_bytes(originals[docs / "old.md"] + b"\nOutside selected range.\n")
        command(preview, expected=1)
        (docs / "old.md").write_bytes(originals[docs / "old.md"])
        summary["checks"]["unhit_old_endpoint_outside_range_change_blocks_preview"] = True
        command(register, expected=1)
        summary["checks"]["existing_manifest_preserved"] = True
        assert all(path.read_bytes() == raw for path, raw in originals.items()) and index.read_bytes() == original_index
        assert not list(repo.iterdir())
        provider.assert_not_called()
        summary["checks"]["all_original_bytes_and_index_unchanged_zero_provider"] = True
    (directory / "cli-validation.json").write_bytes(encoded(summary))
    return summary


if __name__ == "__main__":
    print(json.dumps(exercise(Path(sys.argv[1])), indent=2, ensure_ascii=False))
