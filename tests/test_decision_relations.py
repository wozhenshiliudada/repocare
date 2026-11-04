"""Self-authored replacement claims preserve sources and runtime boundaries."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer import decision_relations as relations
from repo_maintainer.cli import main
from repo_maintainer.memory_collection import compose_memory
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.report_cases import export_report_case
from repo_maintainer.source_memory import CATALOG, build_memory, planning_context, search_memory
from repo_maintainer.ui_backup import encoded


class DecisionRelationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"; self.root.mkdir()
        self.repo = self.base / "repository"; self.repo.mkdir()
        self.index = self.base / "rules.memory.sqlite3"
        self.spec = self.base / "relations.private.json"
        self.output = self.base / "rules.memory-relations.json"
        self.entries = []
        self.add("old", "状态：确认\nLegacy alpha rule.\n", "confirmed")
        self.add("new", "状态：提议\nNew beta alternative.\n\nSupersedes: new -> old\n\n", "proposed")
        self.add("unhit", "状态：待定\nUnrelated gamma.\n", "pending", topic="other")
        self.claims = [self.claim("change", "new", "old", 4)]

    def add(self, name, content, status, topic="policy", kind="document"):
        (self.root / (name + ".md")).write_bytes(content.encode())
        self.entries.append({"id": name, "path": name + ".md", "topic": topic,
                             "kind": kind, "classification": status,
                             "start_line": 1, "end_line": len(content.splitlines())})
        self.catalog()

    def catalog(self):
        (self.root / CATALOG).write_bytes(encoded({"schema_version": 1, "entries": self.entries}))

    def claim(self, name, newer, older, line):
        def row_source(key):
            return self.root / next(r["path"] for r in self.entries if r["id"] == key)
        statement = row_source(newer).read_text(encoding="utf-8").splitlines()[line - 1]
        return {"id": name, "newer_id": newer, "older_id": older, "statement_line": line,
                "newer_sha256": digest(row_source(newer).read_bytes()),
                "older_sha256": digest(row_source(older).read_bytes()),
                "statement_sha256": digest(statement.encode())}

    def save_spec(self):
        self.spec.write_bytes(encoded({"schema_version": 1, "relations": self.claims}))

    def build(self):
        return build_memory(self.root, self.repo, self.index)

    def register(self):
        self.save_spec()
        return relations.register_relations(self.spec, self.root, self.repo, self.index, self.output)

    def verify(self):
        return relations.verify_relations(self.output, self.root, self.repo, self.index)

    def preview(self, query="beta", limit=5):
        return relations.preview_relations(self.output, self.root, self.repo, self.index, query, limit)

    def rewrite_new(self, content, line):
        (self.root / "new.md").write_bytes(content.encode())
        self.entries[1]["end_line"] = len(content.splitlines()); self.catalog()
        self.claims = [self.claim("change", "new", "old", line)]

    def test_registration_and_preview_preserve_statuses_ranges_and_original_bytes(self):
        self.build()
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        index_before = self.index.read_bytes()
        result = self.register()
        self.assertEqual("memory_relations_registered", result["status"])
        self.assertIs(result["runtime_authorized"], False)
        self.assertIs(result["claims_only"], True)
        claim = self.preview(limit=1)["relations"][0]
        self.assertEqual("proposed", claim["newer"]["classification"])
        self.assertEqual("confirmed", claim["older"]["classification"])
        self.assertEqual(4, claim["statement_line"])
        self.assertEqual(["new"], [r["id"] for r in self.preview(limit=1)["hits"]])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.iterdir()})
        self.assertEqual(index_before, self.index.read_bytes())
        displayed = json.dumps(result)
        self.assertNotIn(str(self.base), displayed)
        self.assertNotIn("excerpt", displayed)
        self.assertNotIn("budget", displayed)

    def test_relation_never_removes_mixed_status_gate_or_enters_pi_context(self):
        self.build(); before = search_memory(self.root, self.repo, self.index, "beta")
        self.register()
        self.assertEqual(["policy"], self.preview(limit=1)["mixed_topics"])
        with self.assertRaisesRegex(SafetyError, "Mixed"):
            planning_context(self.root, self.repo, self.index, "beta")
        self.assertEqual(before, search_memory(self.root, self.repo, self.index, "beta"))
        self.assertNotIn("relations", before["context"])

    def test_missing_statement_cannot_be_inferred_from_ids_dates_or_same_topic(self):
        self.rewrite_new("状态：提议\n2026-10-10 newer beta replaces alpha perhaps.\n", 2)
        self.build()
        with self.assertRaises(SafetyError): self.register()
        self.assertFalse(self.output.exists())

    def test_quoted_fenced_commented_indented_and_inline_markers_are_not_evidence(self):
        contents = [
            ("状态：提议\n\n> Supersedes: new -> old\n\n", 3),
            ("状态：提议\n\n```text\n\nSupersedes: new -> old\n\n```\n", 5),
            ("状态：提议\n\n<!--\n\nSupersedes: new -> old\n\n-->\n", 5),
            ("状态：提议\n\n    Supersedes: new -> old\n\n", 3),
            ("状态：提议\n\n`Supersedes: new -> old`\n\n", 3),
            ("状态：提议\n\ntext Supersedes: new -> old\n\n", 3),
            ("状态：提议\n> blockquote\nSupersedes: new -> old\n\n", 3),
            ("状态：提议\n\n<pre>\n\nSupersedes: new -> old\n\n</pre>\n", 5),
            ("状态：提议\n\n<details>\n\nSupersedes: new -> old\n\n</details>\n", 5),
        ]
        for number, (content, line) in enumerate(contents):
            with self.subTest(number=number):
                self.index = self.base / f"quoted-{number}.memory.sqlite3"
                self.rewrite_new(content, line); self.build()
                with self.assertRaisesRegex(SafetyError, "statement"): self.register()
                self.assertFalse(self.output.exists())

    def test_same_file_different_decision_ranges_are_supported(self):
        data = "状态：确认\nLegacy alpha.\n\n状态：提议\nNew beta.\n\nSupersedes: new -> old\n\n"
        (self.root / "both.md").write_bytes(data.encode())
        self.entries[0].update(path="both.md", start_line=1, end_line=2)
        self.entries[1].update(path="both.md", start_line=4, end_line=8)
        self.catalog(); self.claims = [self.claim("change", "new", "old", 7)]
        self.build(); self.register()
        edge = self.preview()["relations"][0]
        self.assertEqual(edge["newer"]["source_sha256"], edge["older"]["source_sha256"])
        self.assertNotEqual(edge["newer"]["start_line"], edge["older"]["start_line"])

    def test_selected_range_cannot_borrow_statement_elsewhere_in_same_file(self):
        self.entries[1]["end_line"] = 2; self.catalog(); self.build()
        with self.assertRaisesRegex(SafetyError, "range"): self.register()

    def test_endpoint_and_statement_hash_mismatch_rejected(self):
        self.build()
        original = copy.deepcopy(self.claims)
        for field in ("newer_sha256", "older_sha256", "statement_sha256"):
            with self.subTest(field=field):
                self.claims = copy.deepcopy(original); self.claims[0][field] = "0" * 64
                with self.assertRaisesRegex(SafetyError, "hash"): self.register()

    def test_dangling_self_duplicate_identity_and_duplicate_pair_rejected(self):
        self.build(); original = copy.deepcopy(self.claims)
        variants = []
        dangling = copy.deepcopy(original); dangling[0]["older_id"] = "missing"; variants.append(dangling)
        selflink = copy.deepcopy(original); selflink[0]["older_id"] = "new"; variants.append(selflink)
        variants.append(original + copy.deepcopy(original))
        duplicated = copy.deepcopy(original); duplicated[0]["id"] = "different"; variants.append(original + duplicated)
        for claims in variants:
            with self.subTest(claims=claims):
                self.claims = claims
                with self.assertRaises(SafetyError): self.register()

    def test_cycle_is_rejected_even_when_query_would_hit_no_endpoints(self):
        (self.root / "old.md").write_bytes("状态：确认\nLegacy alpha.\n\nSupersedes: old -> new\n\n".encode())
        self.entries[0]["end_line"] = 5; self.catalog()
        self.claims = [self.claim("a", "new", "old", 4), self.claim("b", "old", "new", 4)]
        self.build()
        with self.assertRaisesRegex(SafetyError, "Cyclic"): self.register()

    def test_chain_keeps_all_claims_and_does_not_choose_active_decision(self):
        self.add("next", "状态：确认\nLatest delta.\n\nSupersedes: next -> new\n\n", "confirmed")
        self.claims.append(self.claim("next-change", "next", "new", 4))
        self.build(); self.register()
        self.assertEqual(2, len(self.verify()["relations"]))
        self.assertEqual(1, len(self.preview("delta")["relations"]))
        self.assertNotIn("active_decision", self.verify())
        self.assertEqual(["policy"], self.preview("delta")["mixed_topics"])

    def test_competing_successors_preserved_as_claims_without_choosing_winner(self):
        self.add("rival", "状态：确认\nRival delta.\n\nSupersedes: rival -> old\n\n", "confirmed")
        self.claims.append(self.claim("rival-change", "rival", "old", 4))
        self.build(); self.register()
        shown = self.preview("alpha", limit=1)
        self.assertEqual(2, len(shown["relations"]))
        self.assertEqual({"proposed", "confirmed"}, {r["newer"]["classification"] for r in shown["relations"]})
        self.assertNotIn("active_decision", shown)
        with self.assertRaisesRegex(SafetyError, "Mixed"):
            planning_context(self.root, self.repo, self.index, "alpha")

    def test_dangling_unhit_manifest_claim_rejected_before_empty_query_preview(self):
        self.build(); self.register()
        value = json.loads(self.output.read_bytes())
        value["relations"][0]["older_id"] = "missing"
        self.output.write_bytes(encoded(value))
        with patch.object(relations, "search_memory", wraps=relations.search_memory) as search:
            with self.assertRaisesRegex(SafetyError, "Dangling"): self.preview("absent")
        self.assertFalse(any(len(call.args) > 3 and call.args[3] == "absent" for call in search.call_args_list))

    def test_different_topics_cannot_be_linked(self):
        self.entries[0]["topic"] = "different"; self.catalog(); self.build()
        with self.assertRaisesRegex(SafetyError, "topic"): self.register()

    def test_historical_and_reference_entries_cannot_replace_decisions(self):
        for kind, status in (("case", "historical"), ("document", "reference")):
            with self.subTest(status=status):
                self.index = self.base / (status + ".memory.sqlite3")
                self.entries[0].update(kind=kind, classification=status); self.catalog(); self.build()
                with self.assertRaisesRegex(SafetyError, "classified decisions"): self.register()

    def test_superseded_source_marker_stays_original_not_derived_by_link(self):
        (self.root / "old.md").write_bytes("状态：已替代\nLegacy alpha.\n".encode())
        self.entries[0]["classification"] = "superseded"; self.catalog()
        self.claims = [self.claim("change", "new", "old", 4)]
        self.build(); self.register()
        self.assertEqual("superseded", self.preview()["relations"][0]["older"]["classification"])
        with self.assertRaisesRegex(SafetyError, "Mixed"):
            planning_context(self.root, self.repo, self.index, "beta")

    def test_changed_unhit_source_and_outside_selected_range_invalidate_preview(self):
        self.build(); self.register()
        for name in ("unhit", "old", "new"):
            with self.subTest(name=name):
                path = self.root / (name + ".md"); original = path.read_bytes()
                path.write_bytes(original + b"\nUnselected trailing change.\n")
                with self.assertRaises(SafetyError): self.preview("gamma" if name != "unhit" else "beta")
                path.write_bytes(original)

    def test_missing_original_cannot_use_index_as_fallback(self):
        self.build(); self.register(); (self.root / "old.md").unlink()
        with self.assertRaises(SafetyError): self.preview("gamma")

    def test_manifest_protocol_binding_tamper_and_unknown_fields_rejected(self):
        self.build(); self.register(); original = self.output.read_bytes(); value = json.loads(original)
        mutations = [("schema_version", True), ("schema_version", 2), ("runtime_authorized", True),
                     ("purpose", "approve"), ("unknown", {}), ("created_at", "")]
        for field, item in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(value); changed[field] = item; self.output.write_bytes(encoded(changed))
                with self.assertRaises(SafetyError): self.verify()
        changed = copy.deepcopy(value); changed["binding"]["index_sha256"] = "0" * 64
        self.output.write_bytes(encoded(changed))
        with self.assertRaisesRegex(SafetyError, "binding"): self.verify()
        self.output.write_bytes(original)
        another = self.base / "another.memory.sqlite3"; another.write_bytes(self.index.read_bytes())
        with self.assertRaisesRegex(SafetyError, "binding"):
            relations.verify_relations(self.output, self.root, self.repo, another)

    def test_spec_unknown_fields_duplicates_versions_types_and_bounds_rejected(self):
        self.build(); original = copy.deepcopy(self.claims)
        variants = [[], original * 51, [{**original[0], "approval": True}],
                    [{**original[0], "statement_line": True}], [{**original[0], "statement_line": 0}],
                    [{**original[0], "newer_id": "../escape"}], [{**original[0], "newer_sha256": []}]]
        for claims in variants:
            with self.subTest(claims=claims):
                self.claims = claims
                with self.assertRaises(SafetyError): self.register()
        for raw in (b'{"schema_version":1,"schema_version":1,"relations":[]}',
                    b'{"schema_version":true,"relations":[]}',
                    b'{"schema_version":1,"relations":[],"extra":0}', b"[1]", b"{" ):
            with self.subTest(raw=raw):
                self.spec.write_bytes(raw)
                with self.assertRaises((SafetyError, ValueError)):
                    relations.register_relations(self.spec, self.root, self.repo, self.index, self.output)

    def test_existing_output_is_preserved_and_failed_postwrite_leaves_evidence(self):
        self.build(); self.output.write_bytes(b"existing evidence")
        with self.assertRaisesRegex(SafetyError, "exists"): self.register()
        self.assertEqual(b"existing evidence", self.output.read_bytes())
        self.output = self.base / "partial.memory-relations.json"
        with patch.object(relations, "verify_relations", side_effect=SafetyError("postwrite changed")):
            with self.assertRaisesRegex(SafetyError, "postwrite"): self.register()
        self.assertTrue(self.output.exists())
        self.assertFalse(json.loads(self.output.read_bytes())["runtime_authorized"])

    def test_output_cannot_write_into_repository_sources_or_index(self):
        self.build()
        for path in (self.repo / "new.memory-relations.json", self.root / "new.memory-relations.json",
                     self.index / "new.memory-relations.json", self.base / "wrong.json"):
            with self.subTest(path=path):
                self.output = path
                with self.assertRaises(SafetyError): self.register()
                self.assertFalse(path.exists())

    def test_source_or_spec_race_before_write_is_rejected_without_output(self):
        self.build(); self.save_spec()
        original = relations.validate_relations
        def change(*args):
            original(*args); self.spec.write_bytes(self.spec.read_bytes() + b" ")
        with patch.object(relations, "validate_relations", side_effect=change):
            with self.assertRaisesRegex(SafetyError, "changed"):
                relations.register_relations(self.spec, self.root, self.repo, self.index, self.output)
        self.assertFalse(self.output.exists())

    def test_manifest_race_during_preview_is_rejected(self):
        self.build(); self.register(); original = relations.search_memory
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            if len(args) > 3 and args[3] == "beta":
                self.output.write_bytes(self.output.read_bytes() + b" ")
            return result
        with patch.object(relations, "search_memory", side_effect=change):
            with self.assertRaisesRegex(SafetyError, "changed"): self.preview()

    def test_collection_preserves_member_provenance_and_unselected_source_pin(self):
        self.build()
        extra = self.base / "extra"; extra.mkdir()
        (extra / "other.md").write_bytes("状态：确认\nAnother epsilon.\n".encode())
        (extra / CATALOG).write_bytes(encoded({"schema_version": 1, "entries": [
            {"id": "old", "topic": "other", "path": "other.md", "kind": "document",
             "classification": "confirmed", "start_line": 1, "end_line": 2}]}))
        selected = [{"id": key, "member_id": "docs", "entry_id": key} for key in ("old", "new")]
        selected.append({"id": "other-old", "member_id": "extra", "entry_id": "old"})
        spec = self.base / "selection.json"
        spec.write_bytes(encoded({"schema_version": 1, "members": [
            {"id": "docs", "source_root": str(self.root)}, {"id": "extra", "source_root": str(extra)}],
            "selections": selected}))
        original_root = self.root
        self.root = self.base / "memory-collections" / "mixed"; self.index = self.base / "mixed.memory.sqlite3"
        compose_memory(spec, self.root, self.repo, self.index); self.register()
        edge = self.preview()["relations"][0]
        self.assertEqual("docs", edge["newer"]["origin"]["member_id"])
        self.assertEqual("old", edge["older"]["origin"]["entry_id"])
        self.assertNotIn("source_root", json.dumps(self.preview()))
        self.output = original_root / "blocked.memory-relations.json"
        with self.assertRaises(SafetyError): self.register()
        self.output = self.base / "rules.memory-relations.json"
        (original_root / "unhit.md").write_bytes("状态：待定\nChanged unrelated gamma.\n".encode())
        with self.assertRaises(SafetyError): self.preview("epsilon")

    def test_unrelated_report_json_change_invalidates_collection_relation(self):
        self.build(); docs = self.root
        run = self.base / "runs" / ("a" * 32); run.mkdir(parents=True)
        (run / "report.md").write_bytes(b"# Historical epsilon report\nOld approval only.\n")
        case = self.base / "report-memory-cases" / "first"
        export_report_case(run, case, self.repo, self.base / "case.memory.sqlite3", "case", "history", 1, 2)
        spec = self.base / "selection.json"
        spec.write_bytes(encoded({"schema_version": 1, "members": [
            {"id": "docs", "source_root": str(docs)}, {"id": "case", "source_root": str(case)}],
            "selections": [{"id": key, "member_id": "docs", "entry_id": key} for key in ("new", "old")]
                + [{"id": "history", "member_id": "case", "entry_id": "case"}]}))
        self.root = self.base / "memory-collections" / "mixed"; self.index = self.base / "mixed.memory.sqlite3"
        compose_memory(spec, self.root, self.repo, self.index); self.register()
        saved_output = self.output
        self.output = run / "blocked.memory-relations.json"
        with self.assertRaisesRegex(SafetyError, "original report evidence"): self.register()
        self.assertFalse(self.output.exists())
        self.output = saved_output
        (run / "run.json").write_bytes(b"{}")
        with self.assertRaises(SafetyError): self.preview("beta")

    def test_cli_register_verify_preview_no_provider_calls_and_errors_exit_one(self):
        self.build(); self.save_spec()
        common = ["--source-root", str(self.root), "--repo", str(self.repo), "--index", str(self.index)]
        commands = [(["memory-relations-register", "--spec", str(self.spec), "--output", str(self.output)], "registered"),
                    (["memory-relations-verify", "--relations", str(self.output)], "verified"),
                    (["memory-relations-preview", "--relations", str(self.output), "--query", "beta", "--limit", "1"], "preview")]
        with patch("repo_maintainer.cli.PiProvider", side_effect=AssertionError("must not construct provider")):
            for command, status in commands:
                with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(0, main(command + common))
                    self.assertEqual("memory_relations_" + status, json.loads(out.getvalue())["status"])
            (self.root / "old.md").unlink()
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(1, main(commands[-1][0] + common))
                self.assertEqual("", out.getvalue())


if __name__ == "__main__":
    unittest.main()
