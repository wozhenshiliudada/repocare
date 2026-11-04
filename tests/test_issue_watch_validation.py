"""Same watch admission waits for case review and resumes without a new intake."""
import copy
import threading
import unittest

import test_issue_watch as fixtures


class IssueWatchValidationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.IssueWatchTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f
        f.repo.command('config','user.name','Synthetic Fixture')
        f.repo.command('config','user.email','synthetic@example.invalid')
        f.repo.command('rm','tests/test_calculator.py');f.repo.command('commit','-m','no existing test fixture')
        f.github.head=f.repo.head
        self.grant=f.grant()
        self.original=f.repo.snapshot()
        f.model.schema_calls=[('propose_validation',{'requirement':'add(2,3) must return 5',
            'rationale':'Explicit Issue expectation','limitations':'one sum case','clarification':False,
            'cases':[{'id':'sum','module':'calculator.py','callable':'add','args':[2,3],'kwargs':{},
                      'expect':{'kind':'json','value':5},'missing_behavior':False}]})]

    def waiting(self):
        f=self.f;row=f.execute('issue_watch_check',{})
        self.assertEqual('awaiting_validation_review',row['status'],row)
        self.assertEqual(1,len(f.model.requests));self.assertEqual({},f.session.registry.load('issue_execution'))
        self.assertEqual(1,len(f.session.registry.load('issue_watch_admission')))
        self.assertEqual('observed',f.execute('issue_watch_check',{})['status'])
        self.assertEqual(1,len(f.model.requests))
        return row,f.session.state()['issue_workbench']['validations'][0]

    def test_original_admission_continues_only_after_case_review_and_retains_original_budget(self):
        f=self.f;row,validation=self.waiting()
        f.execute('issue_validation_review',{'investigation_id':row['investigation_id'],
            'revision':validation['revision'],'decision':'accept'})
        f.model.schema_calls=[]
        resumed=f.execute('issue_watch_check',{})
        self.assertEqual('finished',resumed['status'],resumed)
        self.assertEqual('awaiting-review',resumed['result_status'],resumed)
        self.assertEqual(row['id'],resumed['id']);self.assertEqual(row['started_at'],resumed['started_at'])
        self.assertEqual(self.grant['id'],resumed['grant_id']);self.assertIn('resumed_after_validation_at',resumed)
        self.assertEqual(1,len(f.session.registry.load('issue_watch_admission')))
        self.assertEqual(2,len(f.session.registry.load('pi_task')))
        budget=f.session.registry.budget();self.assertEqual(0,budget['requests_reserved_unknown'])
        self.assertEqual(len(f.model.requests),budget['requests_used'])
        saved=copy.deepcopy(f.session.registry.load('issue_execution'))
        f.execute('issue_watch_check',{});self.assertEqual(saved,f.session.registry.load('issue_execution'))
        execution=saved[resumed['execution_id']]
        self.assertEqual(self.original,f.repo.snapshot(exclude_branch='maintenance/'+execution['run_id']))

    def test_changed_issue_after_review_does_not_resume_or_repropose(self):
        f=self.f;row,validation=self.waiting()
        f.execute('issue_validation_review',{'investigation_id':row['investigation_id'],
            'revision':validation['revision'],'decision':'accept'})
        f.github.issue['body']+=' Changed goal.';f.github.issue['updated_at']='2026-10-28T03:00:00Z'
        self.assertEqual('observed',f.execute('issue_watch_check',{})['status'])
        self.assertTrue(f.watch.view()['admissions'][0]['source_changed'])
        self.assertEqual(1,len(f.model.requests));self.assertEqual({},f.session.registry.load('issue_execution'))

    def test_paused_watch_case_approval_does_not_authorize_execution(self):
        f=self.f;row,validation=self.waiting()
        f.post('issue_watch_pause',{'grant_id':self.grant['id']})
        f.execute('issue_validation_review',{'investigation_id':row['investigation_id'],
            'revision':validation['revision'],'decision':'accept'})
        self.assertEqual('failed',f.wait(f.post('issue_watch_check',{}))['status'])
        self.assertEqual(1,len(f.model.requests));self.assertEqual({},f.session.registry.load('issue_execution'))

    def test_revoking_inflight_readonly_proposal_stops_original_job_without_replay_or_refund(self):
        f=self.f;entered,released=threading.Event(),threading.Event();f.model.gate=(entered,released)
        job=f.post('issue_watch_check',{})
        try:
            self.assertTrue(entered.wait(60))
            f.post('issue_watch_revoke',{'grant_id':self.grant['id']})
            result=f.wait(job)
        finally:released.set()
        row=result['result'];self.assertEqual('validation_interrupted_no_replay',row['status'],row)
        self.assertEqual('revoked',f.watch.current()['status'])
        self.assertEqual(1,len(f.model.requests));self.assertEqual({},f.session.registry.load('issue_execution'))
        self.assertGreater(f.session.registry.budget()['requests_reserved_unknown'],0)
        self.assertEqual(self.original,f.repo.snapshot())


if __name__=='__main__':unittest.main()
