"""Mixed sources remain pinned context, not execution state or approval."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.cli import main
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.memory_collection import compose_memory
from repo_maintainer.report_cases import export_report_case
from repo_maintainer.source_memory import CATALOG, build_memory, planning_context, search_memory
from repo_maintainer.ui_backup import encoded
from repo_maintainer.dashboard import memory_evidence
from repo_maintainer.ui_memory import UIMemory
from repo_maintainer.pi_provider import PiProvider


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repository"; self.repo.mkdir()
        self.docs = self.base / "documents"; self.docs.mkdir()
        (self.docs / "rule.md").write_bytes("状态：确认\r\naddition must require current approval.\r\n".encode())
        (self.docs / "idea.md").write_text("状态：提议\nfuture semantic graph addition.", encoding="utf-8")
        self.doc_entries = [dict(id=name, topic=topic, path=name+".md", start_line=1, end_line=2,
                                 kind="document", classification=status)
                            for name, topic, status in [("rule", "approval", "confirmed"), ("idea", "future", "proposed")]]
        self.save_docs()
        self.members = [{"id": "docs", "source_root": str(self.docs)}]
        self.selected = [{"id": "current-rule", "member_id": "docs", "entry_id": "rule"},
                         {"id": "future-idea", "member_id": "docs", "entry_id": "idea"}]
        self.runs, self.cases = [], []
        for name in ("a", "b"):
            run = self.base / "runs" / (name*32); run.mkdir(parents=True)
            (run / "report.md").write_bytes(b"# Historical addition Run\r\nOld approval cannot authorize.\r\n")
            if name == "a":
                (run / "run.json").write_bytes(encoded({"schema_version":2, "run_id":run.name,
                     "mode":"approval", "status":"discarded", "finished_at":"old",
                     "authorization":{"decision":"approve"}, "budget":{"remaining":999}}))
            case = self.base / "report-memory-cases" / name
            export_report_case(run, case, self.repo, self.base / (name+".memory.sqlite3"), "old-run", "repair", 1, 2)
            self.runs.append(run); self.cases.append(case)
            self.members.append({"id":name, "source_root":str(case)})
            self.selected.append({"id":"history-"+name, "member_id":name, "entry_id":"old-run"})
        self.spec = self.base / "selection.json"
        self.root = self.base / "memory-collections" / "mixed"
        self.index = self.base / "mixed.memory.sqlite3"
        self.save_spec()

    def save_docs(self):
        (self.docs / CATALOG).write_bytes(encoded({"schema_version":1, "entries":self.doc_entries}))

    def save_spec(self):
        self.spec.write_bytes(encoded({"schema_version":1, "members":self.members, "selections":self.selected}))

    def compose(self):
        return compose_memory(self.spec, self.root, self.repo, self.index)

    def search(self, query="addition", limit=5):
        return search_memory(self.root, self.repo, self.index, query, limit)

    def test_two_documents_two_reports_original_classifications_and_unambiguous_origins(self):
        originals = {p:p.read_bytes() for p in [self.docs / CATALOG, self.docs / "rule.md",
                      *(r / "report.md" for r in self.runs)]}
        result = self.compose(); self.assertFalse(result["runtime_authorized"])
        self.assertEqual((4, 3, "unknown_not_imported"), (result["entries"], result["members"], result["budget"]))
        context = self.search()["context"]; self.assertEqual(2, context["schema_version"])
        rows = {r["id"]:r for r in context["hits"]}
        self.assertEqual({"confirmed", "proposed", "historical"}, {r["classification"] for r in rows.values()})
        self.assertEqual({"a", "b"}, {rows[k]["origin"]["member_id"] for k in ("history-a", "history-b")})
        self.assertEqual("old-run", rows["history-b"]["origin"]["entry_id"])
        self.assertEqual(self.runs[1].name, rows["history-b"]["origin"]["run_id"])
        self.assertEqual("rule.md", rows["current-rule"]["origin"]["path"])
        self.assertEqual(digest(originals[self.docs / "rule.md"]), rows["current-rule"]["source_sha256"])
        for path, raw in originals.items(): self.assertEqual(raw, path.read_bytes())
        self.assertNotIn("source_root", json.dumps(context))
        self.assertFalse((self.root / "ui-state").exists())

    def test_unhit_and_unselected_document_changes_invalidate_all_queries(self):
        self.selected = [r for r in self.selected if r["id"] != "future-idea"]; self.save_spec(); self.compose()
        (self.docs / "idea.md").write_text("状态：提议\nchanged unrelated file", encoding="utf-8")
        with self.assertRaisesRegex(SafetyError, "changed"): self.search("nohit")

    def test_original_json_change_and_previously_missing_json_appearance_invalidate(self):
        self.compose()
        for run in self.runs:
            with self.subTest(run=run.name):
                path = run / "run.json"; original = path.read_bytes() if path.exists() else None
                path.write_bytes(b"{}");
                with self.assertRaisesRegex(SafetyError, "changed"): self.search("nohit")
                if original is None: path.unlink()
                else: path.write_bytes(original)

    def test_lost_original_or_member_corpus_never_falls_back_to_index(self):
        self.compose(); original = self.runs[0] / "report.md"; saved = original.read_bytes(); original.unlink()
        with self.assertRaises(SafetyError): self.search("nohit")
        original.write_bytes(saved)
        catalog = self.cases[1] / CATALOG; saved = catalog.read_bytes(); catalog.unlink()
        with self.assertRaises(SafetyError): self.search("nohit")

    def test_member_catalog_change_rejected_without_silent_reselection(self):
        self.compose(); self.doc_entries[0]["topic"] = "new-topic"; self.save_docs()
        with self.assertRaisesRegex(SafetyError, "changed"): self.search()

    def test_mixed_decision_status_across_corpora_blocks_even_top_one(self):
        idea = self.doc_entries.pop(); self.save_docs()
        other = self.base / "proposals"; other.mkdir()
        (other / "idea.md").write_bytes((self.docs / "idea.md").read_bytes())
        idea["topic"] = "approval"
        (other / CATALOG).write_bytes(encoded({"schema_version":1, "entries":[idea]}))
        self.members.append({"id":"proposals", "source_root":str(other)})
        self.selected[1]["member_id"] = "proposals"; self.save_spec(); self.compose()
        result = self.search(limit=1); self.assertEqual(["approval"], result["mixed_topics"])
        with self.assertRaisesRegex(SafetyError, "Mixed"): planning_context(self.root, self.repo, self.index, "addition")

    def test_no_nested_collection_following_or_cycles(self):
        self.compose()
        self.members = [{"id":"nested", "source_root":str(self.root)}]
        self.selected = [{"id":"entry", "member_id":"nested", "entry_id":"current-rule"}]; self.save_spec()
        other = self.base / "other"
        with self.assertRaisesRegex(SafetyError, "nesting"):
            compose_memory(self.spec, other, self.repo, self.base / "other.memory.sqlite3")
        self.assertFalse(other.exists())

    def test_duplicate_ids_member_paths_and_selections_rejected(self):
        original_members, original_selected = list(self.members), list(self.selected)
        for kind in ("id", "path", "selection", "entry"):
            with self.subTest(kind=kind):
                self.members, self.selected = list(original_members), [dict(r) for r in original_selected]
                if kind == "id": self.members.append(dict(self.members[0]))
                if kind == "path": self.members.append({**self.members[0], "id":"other"})
                if kind == "selection": self.selected[1]["id"] = self.selected[0]["id"]
                if kind == "entry": self.selected.append({**self.selected[0], "id":"another"})
                self.save_spec()
                with self.assertRaises(SafetyError): self.compose()
                self.assertFalse(self.root.exists())

    def test_missing_entry_and_member_change_during_composition_preserve_partial_intent(self):
        self.selected[0]["entry_id"] = "missing"; self.save_spec()
        with self.assertRaisesRegex(SafetyError, "missing"): self.compose()
        self.assertTrue((self.root / "intent.json").exists()); self.assertFalse((self.root / CATALOG).exists())
        with self.assertRaisesRegex(SafetyError, "exists"): self.compose()

    def test_collection_and_index_existing_outputs_are_never_overwritten(self):
        self.compose(); original = self.index.read_bytes(); catalog = (self.root / CATALOG).read_bytes()
        with self.assertRaises(SafetyError): self.compose()
        with self.assertRaises(SafetyError): build_memory(self.root, self.repo, self.index)
        self.assertEqual(original, self.index.read_bytes()); self.assertEqual(catalog, (self.root / CATALOG).read_bytes())

    def test_collection_intent_tampering_and_unknown_fields_rejected(self):
        self.compose(); path = self.root / "intent.json"; original = path.read_bytes()
        data = json.loads(original); data["budget"] = "refunded"; path.write_bytes(encoded(data))
        with self.assertRaisesRegex(SafetyError, "intent"): self.search()
        path.write_bytes(original); catalog = self.root / CATALOG; data = json.loads(catalog.read_bytes())
        data["authority"] = True; catalog.write_bytes(encoded(data))
        with self.assertRaises(SafetyError): self.search()

    def test_paths_inside_corpus_repository_and_relative_members_rejected(self):
        for root, index in [(self.docs / "nested", self.index), (self.repo / "nested", self.index),
                            (self.root, self.docs / "bad.memory.sqlite3")]:
            with self.subTest(root=root, index=index), self.assertRaises(SafetyError):
                compose_memory(self.spec, root, self.repo, index)
        self.members[0]["source_root"] = "relative"; self.save_spec()
        with self.assertRaises(SafetyError): self.compose()

    def test_strict_numeric_types_and_limits(self):
        for value in (True, 1.0, 99):
            data = {"schema_version":value, "members":self.members, "selections":self.selected}
            self.spec.write_bytes(encoded(data))
            with self.assertRaises(SafetyError): self.compose()
        self.members = [{"id":f"member-{i}", "source_root":str(self.docs)} for i in range(11)]; self.save_spec()
        with self.assertRaises(SafetyError): self.compose()

    def test_cli_compose_and_search_no_provider_calls(self):
        with patch.object(PiProvider, "plan", side_effect=AssertionError("provider called")), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main(["memory-compose", "--selection",str(self.spec), "--source-root",str(self.root),
                                      "--repo",str(self.repo), "--index",str(self.index)]))
        self.assertEqual("mixed_source_memory_ready", json.loads(output.getvalue())["status"])
        self.assertEqual([], self.search("unrelated")["context"]["hits"])
        self.assertEqual(2, self.search("unrelated")["context"]["schema_version"])

    def test_ui_and_report_projection_keep_origins_without_excerpts_or_absolute_roots(self):
        self.compose(); provider = PiProvider(("calculator.py",), "http://127.0.0.1:12345/v1")
        provider.memory_root, provider.memory_index = self.root, self.index
        memory = UIMemory(provider, self.repo); preview = memory.preview("addition")
        self.assertEqual(4, len(preview["citations"])); self.assertEqual(0, provider.requests_used)
        self.assertTrue(all("origin" in r and "excerpt" not in r for r in preview["citations"]))
        projected = memory_evidence({"memory_citations":self.search()["context"]["hits"]})
        self.assertEqual(preview["citations"], projected["memory_citations"])
        self.assertNotIn(str(self.docs), json.dumps(preview))
        (self.docs / "idea.md").write_text("changed", encoding="utf-8")
        self.assertEqual("invalid", memory.view()["status"])
        with self.assertRaises(SafetyError): memory.require_current("addition")

    def test_source_changes_between_initial_pin_and_completion_leave_no_ready_catalog(self):
        from repo_maintainer import memory_collection as module
        original = module.write
        def change_after_intent(path, data):
            original(path, data)
            if path.name == "intent.json": (self.docs / "rule.md").write_text("状态：确认\nchanged decision", encoding="utf-8")
        with patch.object(module, "write", side_effect=change_after_intent):
            with self.assertRaisesRegex(SafetyError, "changed"): self.compose()
        self.assertTrue((self.root / "intent.json").exists())
        self.assertFalse((self.root / CATALOG).exists()); self.assertFalse(self.index.exists())

    def test_later_build_cannot_write_index_inside_original_member(self):
        self.compose(); target = self.docs / "new.memory.sqlite3"
        with self.assertRaisesRegex(SafetyError, "outside every"):
            build_memory(self.root, self.repo, target)
        self.assertFalse(target.exists())

    def test_total_member_entries_limit_includes_unselected_sources(self):
        self.doc_entries = [{**self.doc_entries[0], "id":f"entry-{i}"} for i in range(49)]
        self.selected = [r for r in self.selected if r["member_id"] != "docs"]
        self.selected.insert(0, {"id":"first", "member_id":"docs", "entry_id":"entry-0"})
        self.save_docs(); self.save_spec()
        with self.assertRaisesRegex(SafetyError, "at most 50"): self.compose()
        self.assertFalse(self.index.exists()); self.assertFalse((self.root / CATALOG).exists())

    def test_pin_fields_strict_types_and_origin_bound_to_original_catalog(self):
        self.compose(); path = self.root / CATALOG; original = path.read_bytes()
        for field, value in [("schema_version",True), ("schema_version",1.0),
                              ("catalog_sha256","0"*64), ("sources_sha256","0"*64), ("source_root","relative")]:
            with self.subTest(field=field, value=value):
                catalog = json.loads(original); catalog["members"][0][field] = value
                path.write_bytes(encoded(catalog))
                with self.assertRaises(SafetyError): self.search("nohit")
        path.write_bytes(original)
        catalog = json.loads(original); catalog["selections"][0]["topic"] = "override"
        path.write_bytes(encoded(catalog))
        with self.assertRaises(SafetyError): self.search()

    def test_duplicate_selection_json_field_rejected_without_creation(self):
        self.spec.write_bytes(b'{"schema_version":1,"schema_version":1,"members":[],"selections":[]}')
        with self.assertRaisesRegex(SafetyError, "Duplicate"): self.compose()
        self.assertFalse(self.root.exists())

    def test_collection_build_failure_retains_catalog_partial_index_and_originals(self):
        import sqlite3
        with patch("repo_maintainer.source_memory.sqlite3.connect", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaisesRegex(SafetyError, "partial index"): self.compose()
        self.assertTrue((self.root / CATALOG).is_file()); self.assertTrue(self.index.is_file())
        self.assertTrue(all((run / "report.md").is_file() for run in self.runs))
        with self.assertRaisesRegex(SafetyError, "exists"): self.compose()

    def test_report_origin_projection_excludes_unknown_fields_and_rejects_nested_values(self):
        self.compose(); rows = self.search()["context"]["hits"]
        rows[0]["origin"]["source_root"] = "private-not-for-export"
        result = memory_evidence({"memory_citations":rows})
        self.assertNotIn("private-not-for-export", json.dumps(result))
        rows[0]["origin"]["member_id"] = {"private":"nested-not-for-export"}
        with self.assertRaisesRegex(SafetyError, "Malformed memory origin"):
            memory_evidence({"memory_citations":rows})


if __name__ == "__main__": unittest.main()
