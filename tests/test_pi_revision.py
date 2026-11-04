import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from repo_maintainer.models import Edit, Plan, SafetyError, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.pi_revision import RevisionLedger, test_focus as focus_tests, verified_reads, failure_summary
from repo_maintainer.pi_evidence import validate_evidence
from test_pi_evidence import evidence

HISTORICAL_EVIDENCE = Path(__file__).resolve().parents[1] / 'evidence/2026-10-18'


class RevisionTests(unittest.TestCase):
    @unittest.skipUnless(HISTORICAL_EVIDENCE.is_dir(), 'Historical private benchmark archive is excluded from the public release')
    def test_real_quality_trial_revisions_expose_the_actual_diagnostic_without_changing_legacy_excerpts(self):
        root=Path(__file__).resolve().parents[1]/'evidence/2026-10-18'
        original=(root/'swebench-cl-quality-retest/results.json').read_bytes()
        data=json.loads(original)
        diagnosis=json.loads((root/'quality-diagnosis/trace-diagnosis.json').read_bytes())
        previous={row['condition']:row for row in diagnosis['conditions']}
        for arm in data['conditions']:
            with self.subTest(condition=arm['condition']):
                tests=arm['rounds'][0]['tests']
                expected='DID NOT RAISE' if arm['condition'].startswith('A_') else 'ValueError: Imaginary coordinates'
                focus=focus_tests(tests)['tests'][0]
                self.assertIn(expected,focus['assertion_excerpt'])
                self.assertTrue(focus['failure_ids'])
                self.assertEqual(previous[arm['condition']]['first_revision_feedback']['failure_summary'],
                                 failure_summary(tests))
        self.assertEqual(original,(root/'swebench-cl-quality-retest/results.json').read_bytes())

    def test_named_failure_without_recognized_exception_still_has_diagnostic_fallback(self):
        output={'tests_status':{'FAIL_TO_PASS':{'success':[],'failure':['case']}},
                'failure_excerpt':'custom checker: wanted blue but received red'}
        focus=focus_tests([{'command':['test'],'returncode':1,'stdout':json.dumps(output),'stderr':''}])['tests'][0]
        self.assertIn('wanted blue',focus['fallback_tail'])
        self.assertNotIn('tests_status',focus['fallback_tail'])

    def test_focus_prioritizes_late_qualified_exception_over_metadata_and_falls_back_across_streams(self):
        output={'metadata':'x'*9000,'tests_status':{'FAIL_TO_PASS':{'success':[],'failure':['case']}},
                'failure_excerpt':'setup output\n'*2000+'  File "test.py", line 42\n'
                  '    raises(ValueError, lambda: Point(3, I))\n'
                  'sympy.testing.pytest.Failed: DID NOT RAISE\n'+'cleanup\n'*2000}
        focus=focus_tests([{'command':['test'],'returncode':1,'stdout':json.dumps(output),
                           'stderr':'NameError: Add is not defined'}])['tests'][0]
        self.assertIn('DID NOT RAISE',focus['assertion_excerpt'])
        self.assertIn('raises(ValueError',focus['assertion_excerpt'])
        self.assertIn('NameError: Add is not defined',focus['assertion_excerpt'])
        self.assertLessEqual(len(focus['assertion_excerpt']),1800)
        self.assertNotIn('metadata',focus['assertion_excerpt'])

    @unittest.skipUnless(HISTORICAL_EVIDENCE.is_dir(), 'Historical private benchmark archive is excluded from the public release')
    def test_archived_real_cl_failure_survives_metadata_without_rewriting_original_report(self):
        path=Path(__file__).resolve().parents[1]/'evidence/2026-10-18/swebench-cl-retest/history-failure/adapter-summary.json'
        raw=path.read_bytes();output=json.loads(raw)
        value=failure_summary([{'returncode':1,'stdout':raw.decode(),'stderr':'non-fatal wrapper warning'}])
        self.assertIn('AttributeError',output['failure_excerpt'])
        self.assertIn('AttributeError',value);self.assertIn('is_Mul',value)
        self.assertLessEqual(len(value),1600);self.assertEqual(raw,path.read_bytes())

    def test_failure_summary_uses_nested_exception_before_long_wrapper_and_both_streams(self):
        output = {'metadata': 'x'*9000, 'failure_excerpt':
                  'header\n'*2000+'File "sympy/geometry/point.py", line 270\n'
                  'E AttributeError: list has no attribute is_Mul\n> assert original_behavior'}
        value = failure_summary([{'returncode':1,'stdout':json.dumps(output),
                                  'stderr':'unrelated warning','timed_out':False}])
        self.assertIn('AttributeError: list has no attribute is_Mul',value)
        self.assertIn('point.py',value);self.assertIn('assert original_behavior',value)
        self.assertNotIn('metadata',value);self.assertLessEqual(len(value),1600)

    def test_failure_summary_prioritizes_failed_checks_and_handles_timeout_unknown_and_malformed_json(self):
        value=failure_summary([{'returncode':0,'stdout':'ValueError: irrelevant passed diagnostic'},
                              {'returncode':1,'stdout':'{broken json\nE ValueError: actual failure',
                               'stderr':'File "code.py", line 7'}])
        self.assertNotIn('irrelevant',value);self.assertIn('actual failure',value);self.assertIn('line 7',value)
        self.assertIn('timed out',failure_summary([{'returncode':-1,'timed_out':True}]))
        self.assertEqual('No test output',failure_summary([]))
        self.assertLessEqual(len(failure_summary([{'returncode':1,'stdout':'unknown output'*5000}])),1600)

    def setUp(self):
        self.original='def add(a,b):\n    return a - b\n'
        self.candidate=self.original.replace('a - b','a * b')
        self.repo=SimpleNamespace(head='a'*40,common_dir=Path('fixture/.git'),read=lambda path:self.candidate.encode())
        self.tests=(('{python}','test.py'),)
        self.files=[{'path':'calculator.py','before_sha256':digest(self.original.encode()),'content':self.original,'editable':True}]
        self.plan=Plan('addition',self.repo.head,'Multiplication candidate','low',
                       (Edit('calculator.py',self.files[0]['before_sha256'],self.candidate),),self.tests,'fixture')
        self.ledger=RevisionLedger()
        self.ledger.remember(self.repo,'addition',self.tests,self.files,self.plan,{'reads':[
            {'path':'calculator.py','before_sha256':self.files[0]['before_sha256'],'start_line':1,'end_line':2,'query':None}]})
        self.current=[{**self.files[0],'content':self.candidate,'before_sha256':digest(self.candidate.encode())}]
        self.feedback={'round':1,'previous_plan_sha256':self.plan.fingerprint,'tests':[
            {'command':[sys.executable,'test.py'],'returncode':1,'stderr':'FAIL: test_add\nAssertionError: 6 != 5','stdout':''}]}

    def test_revision_keeps_previous_diagnosis_and_historical_read_hash_but_binds_current_snapshot(self):
        value=self.ledger.prepare(self.repo,'addition',self.tests,self.current,self.feedback)
        self.assertEqual('Multiplication candidate',value['history'][0]['diagnosis'])
        self.assertEqual(self.files[0]['before_sha256'],value['history'][0]['reads'][0]['before_sha256'])
        self.assertIn('6 != 5',value['test_focus']['tests'][0]['assertion_excerpt'])
        self.assertNotEqual(value['history'][0]['reads'][0]['before_sha256'],self.current[0]['before_sha256'])

    def test_revision_compares_original_baseline_and_rejects_mismatched_baseline_before_transport(self):
        feedback = {**self.feedback, 'baseline_checks': [
            {'command': [sys.executable, 'test.py'], 'returncode': 0, 'timed_out': False}],
            'tests': [{**self.feedback['tests'][0], 'timed_out': False}]}
        value = self.ledger.prepare(self.repo, 'addition', self.tests, self.current, feedback)
        self.assertEqual('regressed', value['test_focus']['tests'][0]['transition'])
        bad = copy.deepcopy(feedback); bad['baseline_checks'][0]['command'] = ['unapproved']
        provider = PiProvider(('calculator.py',)); provider._revision = self.ledger
        with patch('repo_maintainer.provider_config.load_provider_config') as loader, \
                patch('repo_maintainer.pi_provider.subprocess.Popen') as transport:
            with self.assertRaisesRegex(SafetyError, 'check commands differ'):
                provider.revise(self.repo, 'addition', self.tests, bad)
            loader.assert_not_called(); transport.assert_not_called()

    def test_scope_plan_tests_and_candidate_mismatch_reject_before_credentials_or_transport(self):
        changes=[lambda p:setattr(p._revision,'scope',('other',)),
                 lambda p:p._revision.expected.update({'calculator.py':'0'*64}),
                 lambda p:p._revision.history[-1].update(plan_sha256='0'*64)]
        for change in changes:
            provider=PiProvider(('calculator.py',));provider._revision=copy.deepcopy(self.ledger)
            provider.config_file=Path('unused.env.local');change(provider)
            with patch('repo_maintainer.provider_config.load_provider_config') as loader, \
                    patch('repo_maintainer.pi_provider.subprocess.Popen') as transport:
                with self.assertRaisesRegex(SafetyError,'revision no longer matches'):
                    provider.revise(self.repo,'addition',self.tests,self.feedback)
                loader.assert_not_called();transport.assert_not_called()
        for key,value in [('round',2),('previous_plan_sha256','0'*64),('tests',[{'command':['unexpected']}])]:
            feedback={**self.feedback,key:value}
            with self.assertRaises(SafetyError):self.ledger.prepare(self.repo,'addition',self.tests,self.current,feedback)
        with self.assertRaises(SafetyError):self.ledger.prepare(self.repo,'another issue',self.tests,self.current,self.feedback)

    def test_test_index_extracts_official_json_counts_and_pytest_expected_actual(self):
        output={'tests_status':{'FAIL_TO_PASS':{'success':['one'],'failure':['two']},
                               'PASS_TO_PASS':{'success':['stable'],'failure':[]}}}
        value=focus_tests([{'command':['test'],'returncode':1,'stdout':json.dumps(output)+'\nE AssertionError: assert name == J','stderr':''}])
        focus=value['tests'][0]
        self.assertEqual(['two'],focus['failure_ids']);self.assertEqual(2,focus['passed_count'])
        self.assertEqual(1,focus['failed_count']);self.assertIn('name == J',focus['assertion_excerpt'])

    def test_external_judge_json_forwarded_trace_exposes_expected_actual_without_raw_patch(self):
        payload={'tests_status':{'FAIL_TO_PASS':{'success':[],'failure':['test_next']}},
                 'failure_excerpt':'>       assert next(iterator) == "J"\nE AssertionError: assert "John" == "J"'}
        value=focus_tests([{'command':['judge'],'returncode':1,'stdout':json.dumps(payload),'stderr':''}])
        self.assertIn('assert next(iterator) == "J"',value['tests'][0]['assertion_excerpt'])
        self.assertIn('"John" == "J"',value['tests'][0]['assertion_excerpt'])

    def test_unknown_test_format_keeps_bounded_raw_tail_and_marks_truncation(self):
        value=focus_tests([{'command':['test'],'returncode':2,'stdout':'x'*9000,'stderr':'unrecognized error','output_truncated':True}])
        self.assertEqual(1000,len(value['tests'][0]['fallback_tail']))
        self.assertTrue(value['tests'][0]['output_truncated'])

    def test_read_receipt_cannot_add_unread_paths_or_forge_hashes_or_ranges(self):
        valid={'path':'calculator.py','before_sha256':self.files[0]['before_sha256'],'start_line':1,'end_line':2,'query':None}
        for field,value in [('path','secrets.env'),('before_sha256','0'*64),('end_line',300),('query','x'*201)]:
            with self.assertRaises(SafetyError):verified_reads({'reads':[{**valid,field:value}]},self.files)

    def test_new_plan_clears_task_history_without_refunding_requests(self):
        class Probe(PiProvider):
            def _generate(inner,*args):return len(inner._revision.history)
        provider=Probe(('calculator.py',));provider._revision=self.ledger;provider.requests_used=5
        self.assertEqual(0,provider.plan(self.repo,'addition',self.tests));self.assertEqual(5,provider.requests_used)

    def test_safe_read_statistics_survive_failure_but_reject_text_and_impossible_counts(self):
        value=evidence();value['read_stats']={'read_requests':1,'read_repeats':1,'explicit_rereads':0}
        validate_evidence(value,2)
        for change in [{'text':'untrusted'}, {'read_repeats':3}, {'read_requests':True}]:
            bad=copy.deepcopy(value);bad['read_stats'].update(change)
            with self.assertRaises(SafetyError):validate_evidence(bad,2)


if __name__=='__main__':unittest.main()
