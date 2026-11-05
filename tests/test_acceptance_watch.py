"""Real original monitor journal/Git delivery graph; synthetic GitHub reads only."""
import copy
from datetime import timedelta
import json
import unittest
import uuid
import threading
from unittest.mock import patch

from starlette.testclient import TestClient
import test_monitor_acceptance as fixtures
from repo_maintainer.acceptance_watch import KIND, OBSERVATIONS, formatted
from repo_maintainer.delivery_inbox import projection
from repo_maintainer.models import SafetyError
from repo_maintainer.repository_workbench import create_workbench_app, validate_request


class AcceptanceWatchTests(unittest.TestCase):
    def setUp(self):
        parent=fixtures.MonitorAcceptanceTests()
        self.addCleanup(parent.doCleanups)
        parent.setUp()
        self.parent,self.case,self.remote=parent,parent.case,parent.remote
        self.manager=self.case.hub.monitor.acceptance_watch
        self.time=self.manager.clock().replace(microsecond=0)
        self.manager.clock=lambda:self.time
        self.mid,self.rid=self.case.mid,self.case.rid

    def worker(self, fn, *args):return self.case.worker(fn,*args)
    def enable(self):
        p=self.worker(self.manager.preview,self.case.service,self.rid,60,formatted(self.time+timedelta(hours=1)))
        row=self.worker(self.manager.configure,self.case.service,p['core'],p['revision'],True)
        self.wid=row['id'];return row
    def poll(self, advance=0):
        self.time+=timedelta(seconds=advance)
        return self.worker(self.manager.poll,self.mid,self.wid)
    def control(self, action):
        row=self.manager.view(self.mid,self.wid)
        return self.worker(self.manager.control,action,self.mid,self.wid,row['challenge'])
    def preserved(self):self.parent.preserved()

    def test_preview_is_readonly_and_unchanged_green_deduplicates_evidence_and_notifications(self):
        registry=self.case.hub.monitor.registries[self.mid]
        before=registry.load(KIND)
        proposal=self.worker(self.manager.preview,self.case.service,self.rid,60,formatted(self.time+timedelta(hours=1)))
        self.assertEqual(before,registry.load(KIND))
        self.assertEqual([1001],proposal['core']['policy']['workflow_ids'])
        self.enable();first=self.poll()
        self.assertEqual('verified',first['status'],first)
        self.assertEqual(self.remote.head,first['last_observation']['outcome']['proof']['default_head_sha'])
        feed=projection(self.case.hub);notice=next(i for i in feed['items'] if i['origin']['run_id']==self.rid)['notice_id']
        second=self.poll(60)
        self.assertEqual(first['notification'],second['notification'])
        self.assertEqual(first['observations'],second['observations'])
        self.assertEqual(1,len(registry.load(OBSERVATIONS)))
        feed2=projection(self.case.hub)
        self.assertEqual(notice,next(i for i in feed2['items'] if i['origin']['run_id']==self.rid)['notice_id'])
        self.preserved()

    def test_timer_dispatches_readonly_watch_when_repair_monitor_is_paused(self):
        self.enable();monitor=self.case.hub.monitor
        monitor.records[self.mid]['enabled']=False
        self.worker(monitor._save,monitor.records[self.mid])
        observed=threading.Event()
        self.remote.after_read=lambda _:observed.set()
        monitor.stopping.clear()
        monitor.thread=threading.Thread(target=monitor._timer,daemon=True)
        monitor.thread.start()
        try:self.assertTrue(observed.wait(20),'原定时队列没有派发已启用的只读观察')
        finally:monitor.stop()
        self.worker(lambda:None)
        result=self.manager.view(self.mid,self.wid)
        self.assertEqual('verified',result['status'],result);self.assertEqual(1,result['checks'])
        self.preserved()

    def test_new_attempt_or_missing_failed_pending_checks_never_reuse_old_green(self):
        self.enable();first=self.poll();self.assertEqual('verified',first['status'])
        self.remote.runs[0].update(run_attempt=2,status='in_progress',conclusion=None)
        second=self.poll(60)
        self.assertEqual('waiting_ci',second['status']);self.assertIsNone(second['last_observation']['outcome']['proof'])
        self.assertEqual(2,second['last_observation']['outcome']['runs'][0]['run_attempt'])
        for conclusion in ('failure','cancelled','skipped','neutral'):
            self.remote.runs[0].update(status='completed',conclusion=conclusion)
            self.assertEqual('checks_failed',self.poll(60)['status'])
        self.remote.runs=[];self.assertEqual('checks_missing',self.poll(60)['status'])
        self.preserved()

    def test_real_default_advancement_is_reverified_and_unknown_workflow_stops_scope(self):
        self.enable();self.assertEqual('verified',self.poll()['status'])
        old=self.remote.head;source=self.remote.source
        tree=source.command('rev-parse',old+'^{tree}').decode().strip()
        new=source.command('-c','user.name=Watch Test','-c','user.email=test@example.invalid',
            'commit-tree',tree,'-p',old,input_data=b'next exact default version\n').decode().strip()
        self.remote.head=new;self.remote.pr['base']['sha']=new
        self.remote.runs[0].update(head_sha=new,run_number=9)
        self.remote.jobs[0]['head_sha']=new
        next_row=self.poll(60)
        self.assertEqual('verified',next_row['status'],next_row)
        self.assertEqual(new,next_row['last_observation']['outcome']['proof']['default_head_sha'])
        foreign=copy.deepcopy(self.remote.runs[0]);foreign.update(id=502,workflow_id=1002)
        self.remote.runs.append(foreign)
        changed=self.poll(60);self.assertEqual('scope_changed',changed['status']);self.assertFalse(changed['enabled'])
        self.assertIsNone(changed['last_observation']['outcome']['proof'])
        with self.assertRaises(SafetyError):self.control('resume')
        self.preserved()
    def test_open_pr_waits_and_closed_unmerged_pr_stops_without_merge_or_close(self):
        self.remote.pr.update(merged=False,state='open',draft=True,merged_at=None,merge_commit_sha=None)
        self.enable();self.assertEqual('waiting_merge',self.poll()['status'])
        self.remote.pr['state']='closed'
        closed=self.poll(60);self.assertEqual('closed_without_merge',closed['status']);self.assertFalse(closed['enabled'])
        self.preserved()

    def test_http_pause_cannot_rearm_terminal_or_legacy_paused_scope_without_new_confirmation(self):
        self.enable();self.assertEqual('verified',self.poll()['status'])
        foreign=copy.deepcopy(self.remote.runs[0]);foreign.update(id=502,workflow_id=1002)
        self.remote.runs.append(foreign)
        stopped=self.poll(60)
        self.assertEqual('scope_changed',stopped['status']);self.assertFalse(stopped['enabled'])
        hub=self.case.hub;registry=hub.monitor.registries[self.mid]
        budget=registry.budget();old_id=self.wid
        with TestClient(create_workbench_app(hub,12345),base_url='http://127.0.0.1:12345') as client:
            headers={'Authorization':'Bearer '+hub.token}
            def control(action):
                current=self.manager.view(self.mid,self.wid)
                body={**self.case.selection,'watch_id':self.wid,'revision':current['challenge'],
                      'request_id':uuid.uuid4().hex}
                response=client.post('/api/actions/monitor_delivery_watch_'+action,json=body,headers=headers)
                # Pause uses the immediate control path; a rejected pause is
                # HTTP 409, while queued resume rejection is a failed 202 job.
                self.assertIn(response.status_code,(202,409),response.text)
                if response.status_code==409:return {'status':'failed'}
                self.worker(lambda:None)
                return hub.jobs[body['request_id']]
            for action in ('pause','resume'):
                self.assertEqual('failed',control(action)['status'])
                self.assertEqual(stopped,self.manager.view(self.mid,self.wid))
            reads=len(self.remote.calls)
            self.assertEqual('scope_changed',self.poll(60)['status'])
            self.assertEqual(reads,len(self.remote.calls))
            # Old versions could persist paused/restart_paused with a terminal
            # receipt. A new process must not treat these as fresh permission.
            for status in ('paused','restart_paused'):
                legacy=copy.deepcopy(stopped);legacy.pop('challenge');legacy['status']=status
                self.worker(registry.put,KIND,self.wid,legacy)
                for action in ('pause','resume'):
                    self.assertEqual('failed',control(action)['status'])
                    self.assertFalse(self.manager.view(self.mid,self.wid)['enabled'])
            self.remote.runs.pop()
            renewed=self.enable()
            self.assertNotEqual(old_id,renewed['id'])
            self.assertEqual('verified',self.poll()['status'])
            self.assertEqual('finished',control('pause')['status'])
            self.assertEqual('finished',control('resume')['status'])
        self.assertEqual(budget,registry.budget());self.preserved()

    def test_github_name_case_does_not_weaken_exact_repository_pr_and_head_binding(self):
        self.remote.pr['html_url']='https://github.com/EXAMPLE/PROJECT/pull/9'
        self.enable();self.assertEqual('verified',self.poll()['status'])
        self.remote.pr['head']['sha']='f'*40
        self.assertEqual('blocked',self.poll(60)['status'])
        self.preserved()

    def test_job_source_or_skip_and_midread_attempt_change_cannot_produce_proof(self):
        self.enable()
        for patch_value in ({'conclusion':'skipped'},{'run_id':999},{'head_sha':'f'*40},{'run_attempt':99}):
            before=copy.deepcopy(self.remote.jobs[0]);self.remote.jobs[0].update(patch_value)
            row=self.poll(60)
            self.assertEqual('blocked',row['status'],row);self.assertIsNone(row['last_observation']['outcome']['proof'])
            self.remote.jobs[0]=before
        def changed(path):
            if '/attempts/' in path:self.remote.runs[0]['run_attempt']+=1
        self.remote.after_read=changed
        self.assertEqual('blocked',self.poll(60)['status']);self.preserved()

    def test_timeout_pause_during_get_and_expiry_do_not_sign_or_keep_reading(self):
        self.enable()
        def timeout(_):raise TimeoutError('synthetic unavailable endpoint')
        self.remote.after_read=timeout
        blocked=self.poll();self.assertEqual('network_blocked',blocked['status'])
        self.assertIsNone(blocked['last_observation']['outcome']['proof'])
        def pause(_):
            self.remote.after_read=None
            row=self.manager.view(self.mid,self.wid)
            self.manager.control('pause',self.mid,self.wid,row['challenge'])
        self.remote.after_read=pause;before=len(self.remote.calls)
        paused=self.poll(60);self.assertEqual('paused',paused['status']);self.assertEqual(before+1,len(self.remote.calls))
        self.control('resume')
        def expire(_):self.time+=timedelta(hours=2)
        self.remote.after_read=expire;before=len(self.remote.calls)
        expired=self.poll();self.assertEqual('expired',expired['status']);self.assertEqual(before+1,len(self.remote.calls))
        self.assertFalse(expired['enabled']);self.assertIsNone(expired['last_observation']['outcome']['proof'])
        with self.assertRaises(SafetyError):self.control('resume')
        self.preserved()

    def test_restart_requires_explicit_resume_and_expiry_before_poll_has_zero_reads(self):
        self.enable();self.poll();before=len(self.remote.calls)
        from test_monitor_execution import MonitorExecutionTests
        self.case.hub.close();self.case.hub=MonitorExecutionTests.new_hub(self.case);self.case.hub.monitor.stop()
        self.manager=self.case.hub.monitor.acceptance_watch;self.manager.clock=lambda:self.time
        self.assertEqual('restart_paused',self.manager.view(self.mid,self.wid)['status'])
        self.assertEqual(before,len(self.remote.calls));self.assertFalse(self.manager.due())
        self.case.hub.monitor_acceptance_remote=self.remote
        self.case.hub.monitor_delivery_remote=self.case.remote
        self.control('resume');self.time+=timedelta(hours=2)
        expired=self.poll();self.assertEqual('expired',expired['status']);self.assertEqual(before,len(self.remote.calls))
        self.assertEqual(self.case.budget,self.case.hub.monitor.registries[self.mid].budget())

    def test_corrupt_or_full_original_observation_journal_does_not_retry_or_rebuild(self):
        row=self.enable();registry=self.case.hub.monitor.registries[self.mid]
        corrupt=copy.deepcopy(row);corrupt.pop('challenge');corrupt['core']['binding']['repository_id']=999
        self.worker(registry.put,KIND,self.wid,corrupt);before=len(self.remote.calls)
        result=self.poll();self.assertEqual('journal_blocked',result['status'])
        self.assertEqual(corrupt,registry.load(KIND)[self.wid]);self.assertEqual(before,len(self.remote.calls))
        self.assertFalse(self.manager.due())
        clean=copy.deepcopy(row);clean.pop('challenge')
        self.worker(registry.put,KIND,self.wid,clean)
        self.manager.faults.clear()
        for n in range(100):self.worker(registry.put,OBSERVATIONS,uuid.uuid4().hex,{'synthetic_capacity_entry':n})
        self.assertEqual('capacity_reached',self.poll()['status'])
        self.assertEqual(before,len(self.remote.calls));self.assertFalse(self.manager.due())
        self.preserved()

    def test_changed_observation_receipt_cannot_be_displayed_as_verified(self):
        self.enable();row=self.poll();self.assertEqual('verified',row['status'])
        registry=self.case.hub.monitor.registries[self.mid]
        receipt=copy.deepcopy(row['last_observation']);receipt['outcome']['head_sha']='f'*40
        self.worker(registry.put,OBSERVATIONS,receipt['id'],receipt)
        with self.assertRaises(SafetyError):self.manager.view(self.mid,self.wid)
        feed=projection(self.case.hub)
        self.assertTrue(feed['errors'])
        self.assertFalse(any(i.get('delivery',{}).get('acceptance_watch',{}).get('status')=='verified' for i in feed['items']))
        self.preserved()

    def test_http_auth_origin_explicit_confirmation_and_pause_bypasses_busy_worker(self):
        hub=self.case.hub
        with TestClient(create_workbench_app(hub,12345),base_url='http://127.0.0.1:12345') as client:
            headers={'Authorization':'Bearer '+hub.token}
            body={**self.case.selection,'request_id':uuid.uuid4().hex,'interval_seconds':60,
                  'expires_at':formatted(self.time+timedelta(hours=1))}
            url='/api/actions/monitor_delivery_watch_preview'
            self.assertEqual(401,client.post(url,json=body).status_code)
            self.assertEqual(403,client.post(url,json=body,headers={**headers,'Origin':'https://outside.invalid'}).status_code)
            self.assertEqual(409,client.post(url,json={**body,'merge':True},headers=headers).status_code)
            response=client.post(url,json=body,headers=headers);self.assertEqual(202,response.status_code,response.text)
            self.worker(lambda:None);proposal=hub.jobs[body['request_id']]['result']
            self.assertIn('core',proposal,proposal)
            with self.assertRaises(SafetyError):
                self.worker(self.manager.configure,self.case.service,proposal['core'],proposal['revision'],False)
            row=self.worker(self.manager.configure,self.case.service,proposal['core'],proposal['revision'],True)
            self.wid=row['id']
            hub.active='unrelated'
            try:
                pause={**self.case.selection,'watch_id':self.wid,'revision':row['challenge'],'request_id':uuid.uuid4().hex}
                reply=client.post('/api/actions/monitor_delivery_watch_pause',json=pause,headers=headers)
                self.assertEqual(202,reply.status_code,reply.text)
                self.assertFalse(self.manager.view(self.mid,self.wid)['enabled'])
            finally:hub.active=None
        self.preserved()

    def test_bounds_cross_event_and_report_change_are_not_new_authority(self):
        for interval in (True,59,3601):
            with self.assertRaises(SafetyError):
                self.worker(self.manager.preview,self.case.service,self.rid,interval,formatted(self.time+timedelta(hours=1)))
        with self.assertRaises(SafetyError):
            self.worker(self.manager.preview,self.case.service,self.rid,60,formatted(self.time+timedelta(hours=25)))
        row=self.enable();reads=len(self.remote.calls)
        with self.assertRaises(SafetyError):
            self.case.action('watch_pause',watch_id=self.wid,revision=row['challenge'],event_id='f'*16)
        self.assertEqual(reads,len(self.remote.calls))
        path=self.case.session.runs_dir/self.rid/'run.json';raw=path.read_bytes();path.write_bytes(raw+b'\n')
        try:self.assertEqual('blocked',self.poll()['status'])
        finally:path.write_bytes(raw)
        self.preserved()


class AcceptanceWatchControlContractTests(unittest.TestCase):
    def test_pause_and_resume_preserve_terminal_receipts_including_legacy_rows(self):
        from types import SimpleNamespace
        import threading
        from repo_maintainer.acceptance_watch import AcceptanceWatch,utc
        from repo_maintainer.issue_intake import fingerprint
        clock=utc().replace(microsecond=0)
        monitor=SimpleNamespace(lock=threading.RLock(),hub=SimpleNamespace(
            runtime=SimpleNamespace(require_current=lambda:None)))
        for terminal in ('scope_changed','closed_without_merge','expired','capacity_reached'):
            for status in (terminal,'paused','restart_paused','blocked'):
                holder=[{'id':'d'*32,'status':status,'enabled':False,'revision':0,'observations':[],
                    'last_observation':{'outcome':{'status':terminal}},
                    'core':{'expires_at':formatted(clock+timedelta(hours=1))}}]
                manager=AcceptanceWatch(monitor,clock=lambda:clock)
                manager.view=lambda *_:{**copy.deepcopy(holder[0]),'challenge':fingerprint(holder[0])}
                manager._save=lambda mid,row,expected=None:holder.__setitem__(0,copy.deepcopy(row))
                before=copy.deepcopy(holder[0])
                for action in ('pause','resume'):
                    with self.subTest(terminal=terminal,status=status,action=action):
                        with self.assertRaises(SafetyError):
                            manager.control(action,'a'*16,'d'*32,fingerprint(holder[0]))
                        self.assertEqual(before,holder[0])

    def test_terminal_scope_cannot_resume_and_unknown_actions_do_not_touch_controller(self):
        from types import SimpleNamespace
        from repo_maintainer.acceptance_watch import AcceptanceWatch, utc
        from repo_maintainer.issue_intake import fingerprint
        clock=utc().replace(microsecond=0)
        monitor=SimpleNamespace(hub=SimpleNamespace())
        manager=AcceptanceWatch(monitor,clock=lambda:clock)
        with self.assertRaises(SafetyError):manager.operate(None,'monitor_delivery_watch_merge',{})
        for status in ('scope_changed','closed_without_merge','expired','capacity_reached'):
            row={'id':'d'*32,'status':status,'enabled':False,'revision':0,'observations':[],
                 'core':{'expires_at':formatted(clock+timedelta(hours=1))}}
            revision=fingerprint(row)
            manager.view=lambda *_,r=row:{**copy.deepcopy(r),'challenge':revision}
            with self.subTest(status=status),self.assertRaises(SafetyError):
                manager.control('resume','a'*16,'d'*32,revision)
