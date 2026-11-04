"""Frozen authored corpus, deterministic ranking and abstention; no repair-rate claims."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from repo_maintainer import memory_search as search,retrieval_evaluation as evaluation
from repo_maintainer.models import SafetyError,digest
from repo_maintainer.memory_items import ADAPTERS

FIXTURE=Path(__file__).parents[1]/'benchmarks/memory_retrieval_v1.json'
SHA='1566fb876776f7c55db1af6d1bb65f3fc33743169b344351c7a26d698bafca96'


class RetrievalEvaluationTests(unittest.TestCase):
    def suite(self):return json.loads(FIXTURE.read_bytes())

    def test_frozen_bytes_splits_and_adapter_coverage(self):
        self.assertEqual(SHA,digest(FIXTURE.read_bytes()));suite=evaluation.validate_suite(self.suite())
        self.assertEqual(set(ADAPTERS),{r['adapter'] for r in suite['documents']})
        self.assertEqual({'development':16,'regression':20,'semantic_probe':8,'negative':4},
            {split:sum(r['split']==split for r in suite['queries']) for split in {r['split'] for r in suite['queries']}})

    def test_lexical_regressions_pass_and_semantic_gaps_stay_visible(self):
        result=evaluation.evaluate_fixture(self.suite())
        for split in ('development','regression'):self.assertEqual(1,result['aggregates'][split]['recall_at_3'])
        self.assertEqual(.4375,result['aggregates']['semantic_probe']['recall_at_3'])
        self.assertEqual(0,result['aggregates']['negative']['negative_false_positive_rate'])
        self.assertEqual(0,result['paid_model_requests']);self.assertFalse(result['runtime_authorized'])

    def test_known_multi_relevant_metrics_and_negative_denominators(self):
        metrics=evaluation.metrics(['x','b','a'],['a','b'])
        self.assertEqual(0,metrics['recall_at_1']);self.assertEqual(1,metrics['recall_at_3']);self.assertEqual(.5,metrics['mrr_at_5'])
        self.assertEqual(0,evaluation.metrics([],['a'])['mrr_at_5'])
        rows=[{'metrics':metrics},{'metrics':evaluation.metrics(['x'],[])}]
        result=evaluation.aggregate(rows)
        self.assertEqual(.5,result['mrr_at_5']);self.assertEqual(1,result['negative_false_positive_rate'])
        with self.assertRaises(SafetyError):evaluation.metrics(['a','a'],['a'])

    def test_normalization_code_words_and_query_only_single_han(self):
        for query in ('http retry policy','HTTPRetryPolicy','ＨＴＴＰＲｅｔｒｙＰｏｌｉｃｙ'):
            self.assertEqual('right',search.rank(query,{'right':'HTTPRetryPolicy handles errors','wrong':'guide'})[0][1])
        self.assertEqual('right',search.rank('锁',{'right':'会话锁保护','wrong':'正常信息'})[0][1])
        self.assertEqual([],search.rank('the and a i',{'a':'the and a i'}))
        self.assertEqual('f',search.rank('f',{'f':'function f(x)','g':'function g(x)'})[0][1])
        self.assertEqual(['breaker','circuit','invariant'],search.matched_terms('circuit breaker invariant','CircuitBreakerInvariant'))

    def test_hash_seed_does_not_change_floating_ties(self):
        script="import json;from repo_maintainer.memory_search import rank;print(json.dumps(rank('alpha beta gamma',{'b':'beta alpha gamma','a':'gamma alpha beta'})))"
        results=[]
        for seed in ('1','19','random'):
            results.append(subprocess.check_output([sys.executable,'-c',script],env={**os.environ,'PYTHONHASHSEED':seed}))
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0],results[2])

    def test_invalid_suites_fail_without_creating_results(self):
        for mutate in (lambda s:s['documents'].append(copy.deepcopy(s['documents'][0])),
                       lambda s:s['documents'][0].update(adapter=[]),
                       lambda s:s['queries'][0].update(relevant=['missing']),
                       lambda s:s['queries'][0].update(split=[]),
                       lambda s:s['queries'][0].update(split='negative'),
                       lambda s:s['queries'][0].update(relevant=[])):
            suite=self.suite();mutate(suite)
            with self.assertRaises(SafetyError):evaluation.validate_suite(suite)

    def test_exclusive_output_preserves_fixture_and_previous_results(self):
        before=FIXTURE.read_bytes()
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'result';value=evaluation.evaluate_file(FIXTURE,output)
            self.assertEqual(SHA,value['suite_sha256']);self.assertTrue((output/'report.md').is_file())
            raw=(output/'result.json').read_bytes()
            with self.assertRaises(SafetyError):evaluation.evaluate_file(FIXTURE,output)
            self.assertEqual(raw,(output/'result.json').read_bytes())
        self.assertEqual(before,FIXTURE.read_bytes())
