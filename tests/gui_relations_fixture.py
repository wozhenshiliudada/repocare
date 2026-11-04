"""Self-authored two-member decision fixture; no credentials or provider calls."""
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.decision_relations import register_relations
from repo_maintainer.memory_collection import compose_memory
from repo_maintainer.models import Policy, digest
from repo_maintainer.source_memory import CATALOG
from repo_maintainer.ui_backup import encoded


def fixture(directory):
    directory = directory.resolve()
    directory.mkdir()  # Never replace a previous trial.
    repo = create_fixture(directory / "repository")
    policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
    (directory / "policy.json").write_bytes(encoded(asdict(policy)))
    old = b"Status: confirmed\nLegacy addition policy.\n"
    new = b"Status: confirmed\nCurrent addition policy.\n\nSupersedes: new -> old\n\n"
    # Source-memory classification markers use the existing Chinese protocol.
    old = old.replace(b"Status: confirmed", "状态：确认".encode())
    new = new.replace(b"Status: confirmed", "状态：确认".encode())
    members, selections = [], []
    for member, entry, data in (("previous", "old", old), ("current", "new", new)):
        root = directory / member; root.mkdir()
        (root / "decision.md").write_bytes(data)
        entries = [{"id": entry, "topic": "addition", "path": "decision.md", "start_line": 1,
                    "end_line": len(data.splitlines()), "kind": "document", "classification": "confirmed"}]
        if member == "current":
            (root / "unhit.md").write_text("状态：待定\nUnrelated zeta scope.\n", encoding="utf-8")
            entries.append({"id": "unhit", "topic": "zeta", "path": "unhit.md", "start_line": 1,
                            "end_line": 2, "kind": "document", "classification": "pending"})
            selections.append({"id": "unhit", "member_id": member, "entry_id": "unhit"})
        (root / CATALOG).write_bytes(encoded({"schema_version": 1, "entries": entries}))
        members.append({"id": member, "source_root": str(root)})
        selections.append({"id": entry, "member_id": member, "entry_id": entry})
    spec = directory / "selection.private.json"
    spec.write_bytes(encoded({"schema_version": 1, "members": members, "selections": selections}))
    root, index = directory / "memory-collections" / "decisions", directory / "decisions.memory.sqlite3"
    compose_memory(spec, root, repo, index)
    relation_spec = directory / "relations.private.json"
    relation_spec.write_bytes(encoded({"schema_version": 1, "relations": [{"id": "addition-policy",
        "newer_id": "new", "older_id": "old", "newer_sha256": digest(new), "older_sha256": digest(old),
        "statement_line": 4, "statement_sha256": digest(b"Supersedes: new -> old")}]}))
    register_relations(relation_spec, root, repo, index, directory / "decisions.memory-relations.json")


if __name__ == "__main__":
    fixture(Path(sys.argv[1]))
    print(json.dumps({"status": "self_authored_relations_fixture_ready"}))
