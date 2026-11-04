import unittest
import test_issue_workbench as fixtures
from repo_maintainer.workflow import cleanup_run

class ReviewHttpTests(unittest.TestCase):
    setUp=fixtures.IssueWorkbenchTests.setUp
    new_session=fixtures.IssueWorkbenchTests.new_session
    finish=fixtures.IssueWorkbenchTests.finish
    post=fixtures.IssueWorkbenchTests.post
    wait=fixtures.IssueWorkbenchTests.wait
    execute=fixtures.IssueWorkbenchTests.execute
    config=fixtures.IssueWorkbenchTests.config
    configure=fixtures.IssueWorkbenchTests.configure
    prepare=fixtures.IssueWorkbenchTests.prepare
    pending=fixtures.IssueWorkbenchTests.pending

    def test_request_changes_stores_exact_feedback_then_explicit_successor_requires_new_approval(self):
        prepared=self.prepare()
        job=self.post('issue_execute',{'investigation_id':prepared['id'],'revision':prepared['revision']})
        approval=self.pending();reason='Recheck the arithmetic; prior acceptance was only a proposal.'
        self.assertIn('execution_id',approval)
        self.post('issue_request_changes',{'approval_id':approval['id'],'revision':approval['revision'],'reason':reason})
        first=self.wait(job)['result'];self.assertEqual(first['workflow_status'],'rejected',first)
        self.assertEqual(len(self.model.requests),2)
        saved=self.session.registry.load('issue_approval')[approval['id']]
        self.assertEqual(saved['review_reason'],reason);self.assertEqual(saved['execution_id'],first['id'])
        self.assertEqual(saved['decision_kind'],'request_changes')
        oldrun=self.session.issue_workbench._execution(first['id'])[2].runs/first['run_id']/'run.json'
        old=oldrun.read_bytes()
        preview=self.execute('issue_review_revision_preview',{'execution_id':first['id'],'reason':reason})
        self.assertEqual(len(self.model.requests),2)
        job=self.post('issue_revise_review',{'execution_id':first['id'],'reason':reason,'revision':preview['revision']})
        new=self.pending();self.assertNotEqual(new['id'],approval['id'])
        self.assertEqual(self.session.registry.budget()['requests_used'],4)
        self.post('issue_decide',{'approval_id':new['id'],'revision':new['revision'],'decision':'approve'})
        result=self.wait(job);self.assertEqual(result['status'],'finished',result)
        second=result['result'];self.assertEqual(second['status'],'awaiting-review',second)
        self.assertEqual(first['task_id'],second['task_id']);self.assertEqual(oldrun.read_bytes(),old)
        self.assertEqual(self.session.registry.budget()['requests_used'],4)
        self.assertEqual(self.session.registry.budget()['requests_reserved_unknown'],0)
        reads=len(self.model.requests)
        self.session.state();self.session.state();self.assertEqual(reads,len(self.model.requests))

    def test_invalid_feedback_is_rejected_before_task_or_model(self):
        import uuid
        for reason in ('',' x ','x'*2001,'bad\x00text'):
            response=self.client.post('/api/actions/issue_request_changes',headers=self.headers,
                json={'request_id':uuid.uuid4().hex,'approval_id':'a'*32,'revision':'b'*64,'reason':reason})
            self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.model.requests,[])
