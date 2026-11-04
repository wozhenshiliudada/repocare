import copy
import unittest
import uuid

from test_issue_comment_continuity import CommentRemote
import test_issue_workbench as fixtures


class ProgressHttpTests(unittest.TestCase):
    def test_preexecution_investigation_and_missing_conditions_can_be_reported_without_model(self):
        case = fixtures.IssueWorkbenchTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.configure()
        job = case.execute('issue_investigate', {'number':7})
        controller = case.session.issue_workbench
        controller.comment_remote = remote = CommentRemote(case.github)
        budget = case.session.registry.budget()
        for stage in ('accepted','investigated','blocked'):
            preview = case.execute('issue_progress_investigation_preview', {'investigation_id':job['id'], 'stage':stage})
            self.assertIn('调查标识', preview['core']['body'])
            self.assertNotIn('运行：', preview['core']['body'])
            result = case.execute('issue_progress_investigation_send', {'investigation_id':job['id'],
                'progress_id':preview['id'], 'approval_sha256':preview['approval_sha256'], 'expected_actor_id':42})
            self.assertEqual(result['status'], 'posted', result)
        self.assertEqual(remote.posts, 3)
        self.assertFalse(controller.view()['executions'])
        self.assertEqual(len(case.model.requests), 0)
        self.assertEqual(case.session.registry.budget(), budget)
        # Original investigation remains usable after its own comments.
        self.assertEqual(case.execute('issue_environment', {'investigation_id':job['id']})['id'], job['id'])
        denied = case.wait(case.post('issue_progress_investigation_preview', {'investigation_id':job['id'], 'stage':'delivered'}))
        self.assertEqual(denied['status'], 'failed')

    def test_original_http_queue_explicit_comment_approval_unknown_and_continuity(self):
        case = fixtures.IssueWorkbenchTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        prepared = case.prepare()
        job = case.post('issue_execute', {'investigation_id':prepared['id'], 'revision':prepared['revision']})
        approval = case.pending()
        case.post('issue_decide', {'approval_id':approval['id'], 'revision':approval['revision'], 'decision':'approve'})
        finished = case.wait(job)
        self.assertEqual(finished['status'], 'finished', finished)
        row = finished['result']
        self.assertEqual(row['status'], 'awaiting-review')
        controller = case.session.issue_workbench
        controller.comment_remote = remote = CommentRemote(case.github)
        budget, calls = case.session.registry.budget(), len(case.model.requests)
        native = {str(p):p.read_bytes() for p in (case.root/'state/task-sessions').rglob('*.jsonl')}
        preview = case.execute('issue_progress_preview', {'execution_id':row['id'], 'stage':'accepted'})
        self.assertEqual(remote.posts, 0)
        self.assertEqual(preview['github_actor']['id'], 42)
        bad = case.wait(case.post('issue_progress_send', {'execution_id':row['id'], 'progress_id':preview['id'],
            'approval_sha256':'f'*64, 'expected_actor_id':42}))
        self.assertEqual(bad['status'], 'failed')
        self.assertEqual(remote.posts, 0)
        result = case.execute('issue_progress_send', {'execution_id':row['id'], 'progress_id':preview['id'],
            'approval_sha256':preview['approval_sha256'], 'expected_actor_id':42})
        self.assertEqual(result['status'], 'posted', result)
        self.assertNotIn('continuity_before', result)
        duplicate = case.wait(case.post('issue_progress_send', {'execution_id':row['id'], 'progress_id':preview['id'],
            'approval_sha256':preview['approval_sha256'], 'expected_actor_id':42}))
        self.assertEqual(duplicate['status'], 'failed')
        self.assertEqual(remote.posts, 1)
        # Own comment timestamp does not stale the original environment/candidate.
        review = case.execute('issue_prepare_local', {'execution_id':row['id']})
        case.execute('issue_publish_local', {'execution_id':row['id'], 'revision':review['review_sha256']})
        candidate = case.execute('issue_progress_preview', {'execution_id':row['id'], 'stage':'candidate_passed'})
        self.assertIn('尚未交付', candidate['core']['body'])
        remote.mode = 'timeout_created'
        unknown = case.wait(case.post('issue_progress_send', {'execution_id':row['id'], 'progress_id':candidate['id'],
            'approval_sha256':candidate['approval_sha256'], 'expected_actor_id':42}))
        self.assertEqual(unknown['status'], 'failed')
        saved = controller.view()['progress_comments']
        self.assertEqual(next(p for p in saved if p['id']==candidate['id'])['status'], 'post_unknown')
        observed = case.execute('issue_progress_reconcile', {'execution_id':row['id'], 'progress_id':candidate['id']})
        self.assertEqual(observed['status'], 'posted', observed)
        self.assertNotIn('continuity_before', observed)
        self.assertEqual(remote.posts, 2)
        current = controller._execution(row['id'])[2]
        controller.remote = None  # The original source connection remains the same synthetic GitHub.
        from repo_maintainer.issue_delivery import IssueDelivery
        IssueDelivery(current, None)._fresh(IssueDelivery(current, None)._local(row['id'])[1])
        self.assertEqual(case.session.registry.budget(), budget)
        self.assertEqual(len(case.model.requests), calls)
        self.assertEqual({str(p):p.read_bytes() for p in (case.root/'state/task-sessions').rglob('*.jsonl')}, native)
        self.assertEqual(case.github.issue['state'], 'open')
        self.assertNotIn('continuity_before', next(p for p in controller.view()['progress_comments'] if p['id']==candidate['id']))
        for fields in [{'execution_id':row['id'], 'stage':'accepted','body':'untrusted'},
                       {'execution_id':row['id'], 'stage':'close_issue'}]:
            response = case.client.post('/api/actions/issue_progress_preview', headers=case.headers,
                json={'request_id':uuid.uuid4().hex,**fields})
            self.assertEqual(response.status_code, 409)
        denied = case.client.post('/api/actions/issue_progress_preview',
            json={'request_id':uuid.uuid4().hex,'execution_id':row['id'],'stage':'accepted'})
        self.assertEqual(denied.status_code, 401)
        origin = case.client.post('/api/actions/issue_progress_preview',headers={**case.headers,'Origin':'https://evil.invalid'},
            json={'request_id':uuid.uuid4().hex,'execution_id':row['id'],'stage':'accepted'})
        self.assertEqual(origin.status_code, 403)


if __name__ == '__main__': unittest.main()
