"""Native T1b sessions with actual source-memory and structural provenance gates."""
import json
import unittest
from test_task_planning import SyntheticModel
import test_ui_structure as source_fixtures
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.source_memory import build_memory, CATALOG, canonical
from repo_maintainer.task_session import KIND
from repo_maintainer import structure_index as structure


class NativeSourceTaskTests(unittest.TestCase):
    def setUp(self):
        # The extractor fixture supplies structural metadata; Pi/transport are real here.
        source_fixtures.UIStructureTests.setUp(self)
        self.model=SyntheticModel();self.addCleanup(self.model.close)
        self.documents=self.base/'documents';self.documents.mkdir()
        self.rule=self.documents/'rule.md'
        self.rule.write_text('状态：确认\naddition requires explicit approval.',encoding='utf-8')
        (self.documents/CATALOG).write_bytes(canonical({'schema_version':1,'entries':[
            dict(id='rule',topic='addition',path='rule.md',start_line=1,end_line=2,kind='document',classification='confirmed')]}))
        self.memory_index=self.base/'source.memory.sqlite3'
        build_memory(self.documents,self.repo,self.memory_index)
        self.open()
    finish=source_fixtures.UIStructureTests.finish
    def open(self):
        if self.session:self.session.close()
        provider=PiProvider(self.policy.allowed_paths,self.model.url,self.policy.readonly_paths,50)
        provider.memory_root,provider.memory_index=self.documents,self.memory_index
        self.session=UISession(self.repo,self.policy,provider,self.base/'native-runs',
            structure_index=self.index,structure_context_query='add')
        self.assertIsNotNone(self.session.task_planner)
    def plan(self):return self.session._operate('plan',{'issue':'addition'})

    def test_native_memory_structure_receipts_and_restart_continue(self):
        plan=self.plan()
        evidence=self.session.plans[plan['plan_id']]['view']['planning_evidence']
        self.assertEqual(1,evidence['task_session']['revision'])
        self.assertIn('memory_context_sha256',evidence);self.assertIn('structure_context_sha256',evidence)
        run=self.session._operate('decision',{**plan,'decision':'reject'})
        self.open();self.assertEqual(2,len(self.model.requests))
        preview=self.session._resume_preview(run['run_id'])
        continued=self.session._operate('resume_run',{'run_id':run['run_id'],'revision':preview['revision'],'mode':'approval'})
        second=self.session.plans[continued['plan_id']]['view']['planning_evidence']
        self.assertEqual(evidence['task_session']['task_id'],second['task_session']['task_id'])
        self.assertEqual(2,second['task_session']['revision'])
        self.assertEqual(evidence['memory_context_sha256'],second['memory_context_sha256'])
        self.assertEqual(4,self.session.registry.budget()['requests_used'])

    def test_changed_required_document_or_graph_blocks_before_model_reservation(self):
        run=self.session._operate('decision',{**self.plan(),'decision':'reject'})
        for file in (self.rule,self.index/structure.GRAPH):
            preview=self.session._resume_preview(run['run_id'])
            original=file.read_bytes();file.write_bytes(original+b' ')
            with self.subTest(source=file.name):
                before=self.session.registry.budget()
                with self.assertRaises(SafetyError):self.session._resume_preview(run['run_id'])
                with self.assertRaises(SafetyError):
                    self.session._operate('resume_run',{'run_id':run['run_id'],'revision':preview['revision'],'mode':'approval'})
                self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
            file.write_bytes(original)

    def test_source_change_during_inference_preserves_unknown_and_uncommitted_history(self):
        original=self.rule.read_bytes()
        self.model.on_request=lambda _body,n:self.rule.write_bytes(original+b' ') if n==2 else None
        with self.assertRaises(SafetyError):self.plan()
        budget=self.session.registry.budget()
        self.assertEqual(0,budget['requests_used']);self.assertEqual(50,budget['requests_reserved_unknown'])
        task=next(iter(self.session.registry.load(KIND).values()))
        self.assertEqual(0,task['revision']);self.assertIsNone(task['checkpoint'])
        self.assertEqual([],self.session.state()['plans'])
        file=next((self.session.task_planner.store.directory(task['task_id'])/'sessions').glob('*.jsonl'))
        self.assertTrue(file.is_file())
        self.rule.write_bytes(original);self.open()
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
