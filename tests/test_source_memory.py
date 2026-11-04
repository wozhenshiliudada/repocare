"""Source provenance and fail-closed retrieval, using only self-authored documents."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from repo_maintainer import source_memory as memory
from repo_maintainer.cli import main
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.pi_evidence import validate_evidence


class SourceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/"documents";self.root.mkdir()
        self.repo=self.base/"repository";self.repo.mkdir()
        self.index=self.base/"private"/"test.memory.sqlite3"
        self.entries=[]
        self.add("rule","审批规则\n状态：确认\n新的方案需要新的审批。", "confirmed")
        self.add("proposal","状态：提议\nConsider graph indexing in future.","proposed")
        self.add("pending","状态：待定\nProvider evaluation remains undecided.","pending")
        self.add("old-case","Historical repair report: approved before. Budget was 999.","historical",kind="case")
        (self.root/"unselected.md").write_text("should never enter memory",encoding="utf-8")

    def add(self,identifier,text,classification,kind="document",topic=None):
        (self.root/(identifier+".md")).write_text(text,encoding="utf-8")
        self.entries.append(dict(id=identifier,topic=topic or identifier,path=identifier+".md",
                                 start_line=1,end_line=len(text.splitlines()),kind=kind,classification=classification))
        self.save()

    def save(self):
        (self.root/memory.CATALOG).write_text(json.dumps({"schema_version":1,"entries":self.entries},ensure_ascii=False),encoding="utf-8")

    def build(self):return memory.build_memory(self.root,self.repo,self.index)
    def search(self,query="审批",**kwargs):return memory.search_memory(self.root,self.repo,self.index,query,**kwargs)

    def test_source_ranges_fingerprints_classifications_and_short_chinese_retrieval(self):
        result=self.build();self.assertEqual(4,result["entries"]);self.assertFalse(result["runtime_authorized"])
        before=self.index.read_bytes();context=self.search()["context"];row=context["hits"][0]
        self.assertEqual("rule",row["id"]);self.assertEqual("confirmed",row["classification"])
        self.assertEqual(digest((self.root/"rule.md").read_bytes()),row["source_sha256"])
        self.assertEqual(digest(row["excerpt"].encode()),row["excerpt_sha256"])
        data={k:v for k,v in context.items() if k!="context_sha256"}
        self.assertEqual(digest(memory.canonical(data)),context["context_sha256"])
        self.assertEqual(before,self.index.read_bytes())
        self.assertEqual([],self.search("should never enter")["context"]["hits"])

    def test_cases_remain_historical_and_do_not_grant_runtime_rights(self):
        self.build();result=self.search("approved")
        self.assertFalse(result["runtime_authorized"])
        row=result["context"]["hits"][0];self.assertEqual("historical",row["classification"])
        self.assertEqual("historical_report",row["classification_origin"])
        self.entries[-1]["classification"]="confirmed";self.save()
        with self.assertRaises(SafetyError):memory.sources(self.root)

    def test_proposal_and_pending_require_original_status_not_curator_guess(self):
        self.build();self.assertEqual("proposed",self.search("graph")["context"]["hits"][0]["classification"])
        self.assertEqual("pending",self.search("undecided")["context"]["hits"][0]["classification"])
        self.entries[1]["classification"]="confirmed";self.save()
        with self.assertRaisesRegex(SafetyError,"marker"):memory.sources(self.root)

    def test_table_partial_pending_preserved_conservatively(self):
        (self.root/"pending.md").write_text("| D-010 | 部分待定 | architecture |",encoding="utf-8")
        self.entries[2]["end_line"]=1;self.save();self.build()
        row=self.search("architecture")["context"]["hits"][0]
        self.assertEqual("pending",row["classification"]);self.assertIn("部分待定",row["excerpt"])

    def test_mixed_topic_not_hidden_by_top_k_and_no_pi_injection(self):
        self.entries[1]["topic"]="rule";self.save();self.build()
        result=self.search(limit=1);self.assertEqual(["rule"],result["mixed_topics"])
        with self.assertRaisesRegex(SafetyError,"Mixed"):memory.planning_context(self.root,self.repo,self.index,"审批")

    def test_changed_source_even_outside_excerpt_stops_injection(self):
        self.build();(self.root/"rule.md").write_text("审批规则\n状态：确认\n新的方案需要新的审批。\nnew outside range",encoding="utf-8")
        with self.assertRaisesRegex(SafetyError,"sources changed"):self.search()

    def test_missing_source_is_error_not_empty_retrieval(self):
        self.build();(self.root/"proposal.md").unlink()
        with self.assertRaises(SafetyError):self.search()

    def test_catalog_changes_and_repository_binding_are_rejected(self):
        self.build();other=self.base/"other";other.mkdir()
        with self.assertRaisesRegex(SafetyError,"binding"):memory.search_memory(self.root,other,self.index,"审批")
        self.entries[0]["topic"]="changed";self.save()
        with self.assertRaisesRegex(SafetyError,"binding"):self.search()

    def test_index_entry_and_search_tampering_fail_without_rewriting(self):
        self.build()
        with contextlib.closing(sqlite3.connect(self.index)) as db, db:db.execute("UPDATE entries SET body='{}' WHERE id='rule'")
        before=self.index.read_bytes()
        with self.assertRaises(SafetyError):self.search()
        self.assertEqual(before,self.index.read_bytes())
        second=self.base/"second.memory.sqlite3";memory.build_memory(self.root,self.repo,second)
        with contextlib.closing(sqlite3.connect(second)) as db, db:db.execute("UPDATE search SET text='tampered' WHERE id='rule'")
        with self.assertRaisesRegex(SafetyError,"search contents"):memory.search_memory(self.root,self.repo,second,"审批")

    def test_unknown_schema_corrupt_or_extra_tables_never_reset(self):
        self.build()
        with contextlib.closing(sqlite3.connect(self.index)) as db, db:db.execute("PRAGMA user_version=99")
        before=self.index.read_bytes()
        with self.assertRaises(SafetyError):self.search()
        self.assertEqual(before,self.index.read_bytes())
        second=self.base/"extra.memory.sqlite3";memory.build_memory(self.root,self.repo,second)
        with contextlib.closing(sqlite3.connect(second)) as db, db:db.execute("CREATE TABLE unexpected(value)")
        with self.assertRaises(SafetyError):memory.search_memory(self.root,self.repo,second,"审批")
        corrupt=self.base/"corrupt.memory.sqlite3";corrupt.write_bytes(b"not a database")
        with self.assertRaises(SafetyError):memory.search_memory(self.root,self.repo,corrupt,"审批")
        self.assertEqual(b"not a database",corrupt.read_bytes())

    def test_exclusive_build_and_index_cannot_be_in_sources_or_repo(self):
        self.build();before=self.index.read_bytes()
        with self.assertRaises(SafetyError):self.build()
        self.assertEqual(before,self.index.read_bytes())
        for target in [self.root/"x.memory.sqlite3",self.repo/"x.memory.sqlite3",self.base/"wrong.sqlite3"]:
            with self.assertRaises(SafetyError):memory.build_memory(self.root,self.repo,target)
            self.assertFalse(target.exists())

    def test_malformed_catalog_types_ranges_paths_and_markers_rejected_before_write(self):
        original=copy.deepcopy(self.entries)
        for field,value in [("id",[]),("topic","../../bad"),("path",123),("path","../escape.md"),
                            ("path",".env.private.md"),("kind",[]),("classification",[]),
                            ("start_line",True),("end_line",101),("end_line",20),("classification","confirmed")]:
            self.entries=copy.deepcopy(original);self.entries[1][field]=value;self.save()
            with self.subTest(field=field,value=value),self.assertRaises(SafetyError):self.build()
            self.assertFalse(self.index.exists())
        self.entries=original;self.save()
        (self.root/"proposal.md").write_text("状态：提议\n状态：确认\ngraph",encoding="utf-8")
        with self.assertRaises(SafetyError):self.build()

    def test_duplicate_identity_duplicate_json_and_non_utf8_fail(self):
        self.entries.append(self.entries[0]);self.save()
        with self.assertRaises(SafetyError):self.build()
        (self.root/memory.CATALOG).write_bytes(b'{"schema_version":1,"schema_version":1,"entries":[]}')
        with self.assertRaises(SafetyError):self.build()
        self.entries.pop();self.save();(self.root/"rule.md").write_bytes(b"\xff")
        with self.assertRaises(SafetyError):self.build()

    def test_bounded_query_no_match_and_sql_metacharacters(self):
        self.build()
        for query,limit in [("",5),("***",5),("x"*4097,5),("graph",True),("graph",6)]:
            with self.assertRaises(SafetyError):self.search(query,limit=limit)
        self.assertEqual([],self.search("definitelyabsent")["context"]["hits"])
        self.search('graph" OR 1=1 --');self.assertEqual(4,self.build_count())

    def build_count(self):
        with contextlib.closing(sqlite3.connect(self.index)) as db:return db.execute("SELECT count(*) FROM entries").fetchone()[0]

    def test_cli_build_search_and_mock_memory_rejection(self):
        common=["--source-root",str(self.root),"--repo",str(self.repo),"--index",str(self.index)]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0,main(["memory-build",*common]))
        self.assertEqual("memory_index_ready",json.loads(output.getvalue())["status"])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0,main(["memory-search",*common,"--query","审批"]))
        self.assertEqual("rule",json.loads(output.getvalue())["context"]["hits"][0]["id"])
        policy=self.base/"policy.json";policy.write_text(json.dumps({"allowed_paths":["x.py"],"allowed_tests":[["python","test.py"]]}))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1,main(["plan","--repo",str(self.repo),"--issue","bug","--policy",str(policy),
                                     "--provider","mock","--memory-root",str(self.root),"--memory-index",str(self.index)]))

    def test_budget_exhaustion_and_stale_memory_stop_before_node_or_credentials(self):
        provider=PiProvider(("x.py",),"http://127.0.0.1:1/v1",max_requests=2)
        provider.memory_root=self.root;provider.memory_index=self.index;provider.requests_used=2
        with patch("repo_maintainer.pi_provider.subprocess.Popen") as process:
            with self.assertRaisesRegex(SafetyError,"budget"):provider.plan(None,"bug",())
            process.assert_not_called()
        self.build();(self.root/"rule.md").unlink();provider.requests_used=0
        from types import SimpleNamespace
        with patch("repo_maintainer.pi_provider.subprocess.Popen") as process:
            with self.assertRaises(SafetyError):provider.plan(SimpleNamespace(root=self.repo,common_dir=self.repo/".git"),"bug",())
            process.assert_not_called()

    def test_memory_receipt_field_accepts_only_fingerprint(self):
        value=dict(schema_version=1,status="completed",failure_category=None,count_scope="adapter_stream_invocations",
                   consumption="reported",requests=2,requests_started=2,model_cost_verified=False,
                   events_complete=True,events=[],usage=[],memory_context_sha256="a"*64)
        self.assertEqual("a"*64,validate_evidence(value,2)["memory_context_sha256"])
        for invalid in [None,{},"private memory text","A"*64]:
            with self.assertRaises(SafetyError):validate_evidence({**value,"memory_context_sha256":invalid},2)

    def test_redacted_excerpt_preserves_original_source_hash_without_secret_text(self):
        text="状态：提议\nBearer syntheticCredentialNotReal1234 graph sk-syntheticNotARealKey123456"
        (self.root/"proposal.md").write_text(text,encoding="utf-8");self.build()
        hit=self.search("graph")["context"]["hits"][0]
        self.assertEqual(digest((self.root/"proposal.md").read_bytes()),hit["source_sha256"])
        self.assertNotIn("syntheticCredential",hit["excerpt"]);self.assertNotIn("syntheticNotARealKey",hit["excerpt"])
        self.assertEqual(digest(hit["excerpt"].encode()),hit["excerpt_sha256"])

    def test_hit_limit_and_catalog_entry_bound(self):
        for number in range(6):self.add("extra-"+str(number),"状态：提议\nshared searchable fragment", "proposed")
        self.build();self.assertEqual(5,len(self.search("searchable")["context"]["hits"]))
        self.entries*=6;self.save()
        with self.assertRaisesRegex(SafetyError,"catalog"):memory.sources(self.root)

    def test_native_build_failure_retains_partial_index_and_disallows_overwrite(self):
        with patch.object(memory.sqlite3,"connect",side_effect=sqlite3.OperationalError("synthetic build failure")):
            with self.assertRaisesRegex(SafetyError,"partial index"):self.build()
        self.assertEqual(b"",self.index.read_bytes())
        with self.assertRaises(SafetyError):self.search()
        with self.assertRaisesRegex(SafetyError,"already exists"):self.build()
        replacement=self.base/"replacement.memory.sqlite3"
        self.assertEqual(4,memory.build_memory(self.root,self.repo,replacement)["entries"])

    def test_fenced_templates_and_ambiguous_status_lines_cannot_be_confirmed(self):
        self.entries[1]["classification"]="confirmed"
        for text,start,end in [("状态：确认 / 提议 / 待定 / 已替代",1,1),
                               ("Status: confirmed / proposed",1,1),("状态：确认anything",1,1),
                               ("```md\n状态：确认\n```",1,3),("```md\n状态：确认\n```",2,2),
                               ("~~~md\n| D | 确认 | example |\n~~~",2,2)]:
            (self.root/"proposal.md").write_text(text,encoding="utf-8")
            self.entries[1].update(start_line=start,end_line=end);self.save()
            with self.subTest(text=text),self.assertRaises(SafetyError):memory.sources(self.root)

    def test_redaction_expansion_cannot_exceed_context_byte_limit(self):
        (self.root/"proposal.md").write_text("状态：提议\n"+"abcd1234 "*620,encoding="utf-8")
        with patch.dict("os.environ",{"SYNTHETIC_MEMORY_SECRET":"abcd1234"}):
            with self.assertRaisesRegex(SafetyError,"Redacted"):self.build()
        self.assertFalse(self.index.exists())


if __name__=="__main__":unittest.main()
