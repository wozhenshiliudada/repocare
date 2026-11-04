"""Explicit optional-model integration checks; intentionally outside automatic test discovery.

REPOCARE_TEST_SEMANTIC_MANIFEST must point to operator-selected pinned public data.
No download, credentials or remote language-model call is performed.
"""
import copy
import json
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_task_planning as planning
import test_retrieval_runtime as runtime
import test_knowledge_memory as knowledge
from repo_maintainer.semantic_retrieval import ModelPackage,LocalEncoder,HYBRID
from repo_maintainer.retrieval_evaluation import validate_suite,metrics,aggregate
from repo_maintainer.models import SafetyError,digest


def package():return ModelPackage(Path(os.environ['REPOCARE_TEST_SEMANTIC_MANIFEST']))


class EncoderIntegrationTests(unittest.TestCase):
    def test_actual_encoder_frozen_fixture_no_remote_calls_or_threshold_tuning(self):
        model=package();encoder=LocalEncoder(model);self.addCleanup(encoder.clear)
        file=Path(__file__).parents[1]/'benchmarks/memory_retrieval_v1.json';raw=file.read_bytes();suite=validate_suite(json.loads(raw))
        docs={r['id']:r['text'] for r in suite['documents']};rows=[]
        with patch('socket.create_connection',side_effect=AssertionError('no encoder network')):
            for query in suite['queries']:
                ranked,cosines=encoder.rank(query['query'],docs)
                rows.append({**query,'ranked_ids':[i for _,i in ranked[:5]],'cosines':cosines,
                    'metrics':metrics([i for _,i in ranked],query['relevant'])})
        stats={split:aggregate([r for r in rows if r['split']==split]) for split in {r['split'] for r in rows}}
        self.assertEqual(1,stats['development']['recall_at_3']);self.assertEqual(1,stats['regression']['recall_at_3'])
        self.assertEqual(.6875,stats['semantic_probe']['recall_at_3']);self.assertEqual(0,stats['negative']['negative_false_positive_rate'])
        destination=os.environ.get('REPOCARE_TEST_SEMANTIC_RESULT')
        if destination:
            with Path(destination).open('x',encoding='utf-8') as stream:json.dump({'suite_sha256':digest(raw),'strategy':HYBRID,
                'model_manifest_sha256':model.sha,'stats':stats,'rows':rows,'paid_language_model_requests':0},stream,ensure_ascii=False,indent=2)

    def test_actual_long_text_windows_tail_and_explicit_capacity_failure(self):
        encoder=LocalEncoder(package());self.addCleanup(encoder.clear)
        tail='Signed addition must preserve negative operands and mixed sign regression behavior.'
        long='Neutral document introduction. '*180+tail
        encoder.encode(['warmup'])
        original=encoder.engine;sent=[]
        def run(*args):
            sent.extend(zip(args[1]['input_ids'].tolist(),args[1]['attention_mask'].tolist()))
            return original.run(*args)
        encoder.engine=SimpleNamespace(run=run,get_inputs=original.get_inputs)
        vectors=encoder.encode([long])
        expected=encoder.tokenizer.encode(tail).ids
        # Prove the actual model received the tail, rather than asserting an arbitrary semantic score.
        self.assertGreater(len(vectors[0]),1)
        self.assertTrue(any(row[:len(expected)]==expected for row,mask in sent))
        sent.clear();unpunctuated=' '.join('unit'+str(i) for i in range(300))
        full=encoder.tokenizer.encode(unpunctuated,add_special_tokens=False).ids
        self.assertGreater(len(full),256);encoder.encode([unpunctuated]);restored=[]
        for n,(row,mask) in enumerate(sent):
            content=row[1:sum(mask)-1];restored.extend(content if n==0 else content[32:])
        self.assertEqual(full,restored,'All original tokens including the tail must reach actual inference windows')
        with self.assertRaises(SafetyError):encoder.encode(['x'*64001])
        with self.assertRaises(SafetyError):encoder.encode([' '.join('unit'+str(i) for i in range(4000))])
        # A changed registration is detected even with cached vectors.
        with patch.object(encoder.package,'raw',encoder.package.raw+b' '):
            with self.assertRaises(SafetyError):encoder.encode([tail])


class SemanticRuntimeTests(unittest.TestCase):
    setUp=planning.TaskPlanningTests.setUp
    restart=planning.TaskPlanningTests.restart
    plan=planning.TaskPlanningTests.plan
    decision=planning.TaskPlanningTests.decision
    record=planning.TaskPlanningTests.record
    episode=runtime.RetrievalRuntimeTests.episode
    accept=runtime.RetrievalRuntimeTests.accept
    enable=runtime.RetrievalRuntimeTests.enable
    envelope=runtime.RetrievalRuntimeTests.envelope
    proposal=runtime.RetrievalRuntimeTests.proposal
    review=runtime.RetrievalRuntimeTests.review

    def open(self,auto=False,attempts=1):
        from repo_maintainer.pi_provider import PiProvider
        from repo_maintainer.local_ui import UISession
        from repo_maintainer.models import Policy
        from repo_maintainer.demo import DEMO_TEST
        self.inner=PiProvider(('calculator.py',),self.model.url,readonly_paths=('tests/test_calculator.py',),max_requests=50)
        self.session=UISession(self.source,Policy(('calculator.py',),(DEMO_TEST,)),self.inner,self.root/'state',
            allow_auto=auto,max_attempts=attempts,semantic_model_manifest=getattr(self,'registered_manifest',None))

    def register(self):
        # Register through the production startup path, which also binds the controller's memory host.
        # Replacing UISession.retrieval alone leaves that original host pointing at the old object.
        self.registered_manifest=package().path
        self.restart()
        self.assertIs(self.session.knowledge.session.retrieval,self.session.retrieval)

    def test_local_semantic_new_task_original_ledger_new_approval_and_old_pin_stability(self):
        run,_=self.episode();self.accept(run);self.model.reflection_candidate=copy.deepcopy(knowledge.NOTE)
        note=self.proposal(run)['knowledge_id'];self.review(note);self.enable();self.model.scenario='correct'
        old=self.plan();old_pin=copy.deepcopy(self.session.registry.load('knowledge_task'));budget=self.session.registry.budget()
        self.register();self.assertFalse(self.session.retrieval.settings()['enabled'])
        self.session._operate('retrieval_settings',{'enabled':True,'revision':self.session.retrieval.revision()})
        self.assertEqual(old_pin,self.session.registry.load('knowledge_task'));self.assertEqual(budget,self.session.registry.budget())
        issue='负数加法必须保留正负号，不能取绝对值'
        before=self.session.registry.path.read_bytes();count=len(self.model.requests)
        search=self.session.knowledge.search(issue)
        self.assertIn(note,[r['id'] for r in search['hits']]);self.assertEqual(HYBRID,search['retrieval'])
        self.assertEqual(before,self.session.registry.path.read_bytes());self.assertEqual(count,len(self.model.requests))
        self.restart();self.register();self.assertTrue(self.session.retrieval.settings()['enabled'])
        old_view=next(p for p in self.session.state()['plans'] if p['id']==old['plan_id'])
        self.session._operate('claim_plan',{'plan_id':old['plan_id'],'revision':old_view['revision']})
        self.assertEqual('rejected',self.decision(old,'reject')['workflow_status'])
        plan=self.session._operate('plan',{'issue':issue});task=self.record()['task_id']
        self.assertIn(note,[r['id'] for r in self.envelope()['repository_knowledge']['hits']])
        self.assertEqual(HYBRID,self.session.registry.load('retrieval_task')[task]['channels']['knowledge']['strategy'])
        count=len(self.model.requests);self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])
        self.assertEqual(count,len(self.model.requests));self.assertEqual(count,self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])
        budget=self.session.registry.budget();self.review(note,'rejected')
        self.assertFalse(self.session.knowledge.search(issue)['hits'])
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))
