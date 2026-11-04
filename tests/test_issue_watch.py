"""Continuous Issue admission, genuine Pi/Native and local synthetic responses."""
import copy
from datetime import datetime,timedelta,timezone
import threading
import unittest
import uuid

import test_issue_workbench as base
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.models import SafetyError


class IssueWatchTests(unittest.TestCase):
    setUp=base.IssueWorkbenchTests.setUp
    finish=base.IssueWorkbenchTests.finish
    new_session=base.IssueWorkbenchTests.new_session
    post=base.IssueWorkbenchTests.post
    wait=base.IssueWorkbenchTests.wait
    execute=base.IssueWorkbenchTests.execute
    config=base.IssueWorkbenchTests.config
    pending=base.IssueWorkbenchTests.pending

    @property
    def watch(self):return self.session.issue_workbench.watch

    def grant(self,mode='auto'):
        self.session.issue_workbench.permit_auto=True
        preview=self.execute('issue_config_preview',self.config(mode=mode))
        self.execute('issue_configure',{'config':preview['configuration'],'revision':preview['revision']})
        self.watch.stopping.set()  # Deterministic tests drive the same original queued action.
        preview=self.execute('issue_watch_preview',{'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(timespec='seconds'),
            'interval_seconds':60,'max_attempts':3})
        result=self.execute('issue_watch_configure',{**preview,'confirmed':True})
        return result['authorization']

    def test_auto_first_issue_uses_original_pipeline_and_ledger_and_never_replays_changes(self):
        grant=self.grant();before=self.repo.snapshot()
        result=self.execute('issue_watch_check',{})
        self.assertEqual(result['result_status'],'awaiting-review',result)
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual(self.session.registry.budget()['requests_used'],2)
        self.assertEqual(len(self.session.registry.load('pi_task')),1)
        original=self.session.registry.load('issue_execution')[result['execution_id']]
        self.assertEqual(before,self.repo.snapshot(exclude_branch='maintenance/'+original['run_id']))
        self.execute('issue_watch_check',{})
        self.github.issue['body']+=' Changed requirement.'
        self.github.issue['updated_at']='2026-10-25T01:00:00Z'
        self.execute('issue_watch_check',{})
        self.assertTrue(self.watch.view()['admissions'][0]['source_changed'])
        self.github.events.extend([self.github.event(2,'unlabeled','maintainer'),self.github.event(3,'labeled','maintainer')])
        self.execute('issue_watch_check',{})
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual(self.watch.current()['id'],grant['id'])

    def test_second_distinct_issue_gets_own_task_on_same_original_budget(self):
        self.grant();first=self.execute('issue_watch_check',{})
        original_api=self.github.api
        second=copy.deepcopy(self.github.issue)
        second.update(number=9,id=109,html_url='https://github.com/example/project/issues/9')
        event=self.github.event(2,'labeled','maintainer')
        def api(path):
            if path=='/repos/example/project/issues/9':return copy.deepcopy(second)
            if path.startswith('/repos/example/project/issues/9/events?'):return [copy.deepcopy(event)]
            if '/issues?state=open' in path:return [copy.deepcopy(self.github.issue),copy.deepcopy(second)]
            return original_api(path)
        self.github.api=api
        result=self.execute('issue_watch_check',{})
        self.assertEqual(result['result_status'],'awaiting-review',result)
        self.assertNotEqual(first['execution_id'],result['execution_id'])
        self.assertEqual(len(self.session.registry.load('pi_task')),2)
        self.assertEqual(self.session.registry.budget()['requests_used'],4)
        self.assertEqual(self.session.registry.budget()['requests_reserved_unknown'],0)
        self.execute('issue_watch_check',{});self.assertEqual(len(self.model.requests),4)

    def test_manual_waiting_approval_rejects_removed_label_and_pause_bypasses_busy_queue(self):
        grant=self.grant('approval')
        job=self.post('issue_watch_check',{});approval=self.pending()
        self.assertEqual(self.session.registry.load('issue_execution').popitem()[1]['status'],'running')
        self.github.issue['labels']=[{'name':'bug'}]
        self.github.events.append(self.github.event(2,'unlabeled','maintainer'))
        response=self.client.post('/api/actions/issue_decide',headers=self.headers,json={'request_id':uuid.uuid4().hex,
            'approval_id':approval['id'],'revision':approval['revision'],'decision':'approve'})
        self.assertEqual(response.status_code,409)
        response=self.post('issue_watch_pause',{'grant_id':grant['id']})
        self.assertEqual(response['status'],'paused')
        result=self.wait(job)
        self.assertEqual(result['result']['status'],'finished',result)
        self.assertIn(result['result']['result_status'],{'paused','failed_no_replay'})
        self.assertEqual(len(self.model.requests),2)

    def test_existing_manual_work_is_only_linked_closed_and_untrusted_are_not_executed(self):
        self.grant()
        self.github.events=[self.github.event(1,'labeled','stranger')]
        self.execute('issue_watch_check',{});self.assertEqual(len(self.model.requests),0)
        self.github.events=[self.github.event(2,'labeled','maintainer')]
        original=self.execute('issue_investigate',{'number':7})
        self.execute('issue_watch_check',{})
        row=self.watch.view()['admissions'][0]
        self.assertEqual(row['status'],'existing_work_no_replay')
        self.assertEqual(row['investigation_id'],original['id'])
        self.github.issue['state']='closed'
        self.execute('issue_watch_check',{})
        self.assertEqual(self.watch.view()['observations'][0]['status'],'ignored_closed')
        self.assertEqual(len(self.model.requests),0)

    def test_old_preview_tamper_missing_consent_and_private_dispatch_are_rejected(self):
        self.grant()
        args={'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(timespec='seconds'),
              'interval_seconds':60,'max_attempts':3}
        preview=self.execute('issue_watch_preview',args)
        result=self.wait(self.post('issue_watch_configure',{**preview,'confirmed':False}))
        self.assertEqual(result['status'],'failed')
        forged=copy.deepcopy(preview);forged['preview']['binding']['request_ceiling']=999
        self.assertEqual(self.wait(self.post('issue_watch_configure',{**forged,'confirmed':True}))['status'],'failed')
        response=self.client.post('/api/actions/issue_watch_process',headers=self.headers,json={'request_id':uuid.uuid4().hex})
        self.assertEqual(response.status_code,409)
        self.assertEqual(len(self.model.requests),0)

    def test_restart_pause_expiry_unknown_and_stale_grant_preserve_budget(self):
        grant=self.grant();nonce=uuid.uuid4().hex;payload={'grant_id':grant['id']}
        first=self.session.submit(nonce,'issue_watch_pause',payload)
        self.assertEqual(self.session.submit(nonce,'issue_watch_pause',payload),first)
        with self.assertRaises(SafetyError):self.session.submit(nonce,'issue_watch_revoke',payload)
        new=self.grant()
        with self.assertRaises(SafetyError):self.session.submit(uuid.uuid4().hex,'issue_watch_pause',payload)
        self.watch.clock=lambda:new['core']['expires_epoch']+1
        self.assertEqual(self.watch.view()['status'],'expired')
        self.watch.clock=lambda:new['issued_epoch']+1
        with self.session.registry.transaction() as db:db.execute('UPDATE budget SET held=3 WHERE id=1')
        self.assertEqual(self.watch.view()['status'],'unknown_consumption')
        self.session.close();self.session=self.new_session()
        self.assertEqual(self.watch.current()['status'],'paused')
        self.assertEqual(self.watch.current()['reason'],'service_restart')
        self.assertEqual(self.session.registry.budget()['requests_reserved_unknown'],3)
        self.assertEqual(len(self.model.requests),0)

    def test_incomplete_listing_and_target_change_do_not_start_model(self):
        self.grant()
        old=self.github.api
        self.github.api=lambda path:[copy.deepcopy(self.github.issue)]*30 if '/issues?state=open' in path else old(path)
        result=self.wait(self.post('issue_watch_check',{}))
        self.assertEqual(result['status'],'failed')
        self.assertFalse(self.watch.view()['scan']['complete'])
        self.github.api=old
        ready=self.watch.scan()[0]
        self.github.head='f'*40
        with self.assertRaises(SafetyError):self.watch.process(ready)
        self.assertEqual(len(self.model.requests),0)

    def inflight(self,deadline=False):
        grant=self.grant();entered,released=threading.Event(),threading.Event();self.model.gate=(entered,released)
        job=self.post('issue_watch_check',{})
        try:
            self.assertTrue(entered.wait(60))
            if deadline:
                self.watch.thread.join();self.watch.stopping.clear()
                self.watch.clock=lambda:grant['core']['expires_epoch']+1
                self.watch.thread=threading.Thread(target=self.watch._timer,daemon=True);self.watch.thread.start()
            else:
                payload={'request_id':uuid.uuid4().hex,'grant_id':grant['id']}
                self.assertEqual(self.client.post('/api/actions/issue_watch_revoke',json=payload).status_code,401)
                self.assertEqual(self.client.post('/api/actions/issue_watch_revoke',headers={**self.headers,'Origin':'https://outside.invalid'},json=payload).status_code,403)
                self.assertEqual(self.client.post('/api/actions/issue_watch_revoke',headers=self.headers,json=payload).status_code,202)
            self.wait(job)
        finally:released.set()
        self.assertEqual(len(self.model.requests),1)
        budget=self.session.registry.budget()
        self.assertGreaterEqual(budget['requests_used']+budget['requests_reserved_unknown'],1)
        self.assertEqual(self.watch.view()['status'],'expired' if deadline else 'revoked')

    def test_revoke_inflight_is_authenticated_and_preserves_the_original_consumption(self):
        self.inflight()

    def test_deadline_during_model_call_uses_original_pause(self):
        self.inflight(deadline=True)


if __name__=='__main__':unittest.main()
