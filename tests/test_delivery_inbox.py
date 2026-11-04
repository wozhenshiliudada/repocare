"""Original run evidence and read-only HTTP aggregation; no external models."""
import json
import uuid
from unittest.mock import patch
import unittest

import test_workbench_read as fixture
import test_monitor_execution as monitor_fixture
from repo_maintainer.dashboard import read_json
from repo_maintainer.delivery_inbox import projection
from repo_maintainer.models import Policy,digest
from repo_maintainer.provider import MockProvider
from repo_maintainer.workflow import repair,cleanup_run
from repo_maintainer.repository_workbench import RepositoryWorkbench,create_workbench_app
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.git import RepositoryGateway


class DeliveryInboxTests(unittest.TestCase):
    setUp=fixture.WorkbenchReadTests.setUp
    wait=fixture.WorkbenchReadTests.wait

    def candidate(self):
        session=self.hub.workspaces[self.record['id']].session
        eid,rid=uuid.uuid4().hex,uuid.uuid4().hex
        token,task,rules='a'*64,'b'*32,'c'*64
        binding={'execution_id':eid,'issue_number':7,'issue_token':token,'task_id':task,
                 'rules_sha256':rules,'policy_binding_sha256':'d'*64}
        def make():
            before=RepositoryGateway(session.repo).snapshot()
            session._reserve_run(rid)
            result=repair(session.repo,'Fix calculator addition',MockProvider(),session.policy,'auto',session.runs_dir,
                          run_id=rid,issue_binding=binding)
            session._register(result)
            execution={'id':eid,'run_id':rid,'status':'awaiting-review','issue_number':7,'issue_token':token,
                'task_id':task,'rules_sha256':rules,'policy_binding':{'binding_sha256':'d'*64},
                'source_before':before,'issue_key':'e'*64}
            session.registry.put('issue_execution',eid,execution)
            session.registry.put('issue_execution_head',execution['issue_key'],{'execution_id':eid})
            return execution
        row=session.executor.submit(make).result(timeout=90)
        self.addCleanup(lambda:cleanup_run(session.runs_dir/rid))
        return session,row

    def get_inbox(self):
        response=self.client.get('/api/delivery',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_auth_query_and_method_are_enforced_before_read(self):
        self.assertEqual(self.client.get('/api/delivery').status_code,401)
        self.assertEqual(self.client.get('/api/delivery',headers={**self.headers,'Origin':'https://outside.invalid'}).status_code,403)
        self.assertEqual(self.client.get('/api/delivery?path=C:/',headers=self.headers).status_code,409)
        self.assertEqual(self.client.post('/api/delivery',headers=self.headers,json={}).status_code,405)

    def test_real_run_binding_and_zero_effects_or_new_authority(self):
        session,row=self.candidate();before=session.registry.path.read_bytes()
        with (patch('repo_maintainer.git.RepositoryGateway.command',side_effect=AssertionError('GET used Git')),
             patch('repo_maintainer.pi_provider.PiProvider.__init__',side_effect=AssertionError('GET allocated provider'))):
            first=self.get_inbox();second=self.get_inbox()
        self.assertEqual(first,second)
        item=first['items'][0]
        self.assertEqual(item['status'],'candidate_review')
        self.assertEqual(item['evidence']['tests_run'],4)
        self.assertEqual(item['origin']['execution_id'],row['id'])
        self.assertEqual(item['origin']['workspace_id'],self.record['id'])
        self.assertFalse(item['runtime_authorized']);self.assertTrue(item['read_only'])
        self.assertEqual(item['freshness'],'not_revalidated')
        self.assertEqual(session.registry.path.read_bytes(),before)

    def test_bad_report_binding_cannot_be_promoted_and_does_not_hide_the_item(self):
        session,row=self.candidate();path=session.runs_dir/row['run_id']/'run.json'
        record,_=read_json(path);record['issue_execution']['execution_id']='f'*32
        path.write_text(json.dumps(record),encoding='utf-8')
        item=self.get_inbox()['items'][0]
        self.assertEqual(item['status'],'evidence_unavailable');self.assertIsNone(item['evidence'])

    def test_missing_report_never_creates_a_replacement(self):
        session,row=self.candidate();path=session.runs_dir/row['run_id']/'run.json'
        saved=path.with_name('run-preserved.json');path.rename(saved)
        try:
            self.assertEqual(self.get_inbox()['items'][0]['status'],'evidence_unavailable')
            self.assertFalse(path.exists())
        finally:saved.rename(path)

    def test_workspace_rebinding_and_budget_changes_do_not_repeat_the_notice(self):
        session,row=self.candidate();first=self.get_inbox()['items'][0]
        # MockProvider starts with a zero ceiling. A valid original budget is
        # required; used=1 with ceiling=0 must correctly fail the read guard.
        with session.registry.transaction() as db:db.execute('UPDATE budget SET ceiling=2,used=1 WHERE id=1')
        old=list(self.hub.workspaces.items());self.hub.workspaces.clear()
        try:
            second=self.get_inbox()['items'][0]
            self.assertEqual(first['id'],second['id']);self.assertEqual(first['notice_id'],second['notice_id'])
            self.assertNotEqual(first['revision'],second['revision']);self.assertIsNone(second['origin']['workspace_id'])
        finally:self.hub.workspaces.update(old)

    def test_original_pr_unknown_and_observed_version_change_remain_explicit(self):
        session,row=self.candidate()
        session.registry.put('issue_delivery',row['id'],{'id':row['id'],'status':'creation_unknown'})
        session.registry.put('issue_watch_observation','old',{'number':7,'token':'f'*64})
        item=self.get_inbox()['items'][0]
        self.assertEqual(item['delivery']['pr']['status'],'creation_unknown')
        self.assertEqual(item['status'],'needs_review');self.assertTrue(item['source_changed_observed'])
        self.assertFalse(item['runtime_authorized'])
        session.registry.put('issue_closure',row['id'],{'status':'closed-verified'})
        changed=self.get_inbox()['items'][0]
        self.assertEqual(changed['status'],'needs_review','Old closure must not hide observed source changes')

    def test_zero_tests_and_truncated_output_never_become_passed_candidates(self):
        session,row=self.candidate();path=session.runs_dir/row['run_id']/'run.json'
        original=json.loads(path.read_text(encoding='utf-8'))
        for text,truncated in [('Ran 0 tests in 0.001s\nOK',False),('Ran 4 tests in 0.001s\nOK',True)]:
            record=json.loads(json.dumps(original))
            record['candidate_tests']=[{'command':['python','-m','unittest'],'returncode':0,
                'timed_out':False,'stdout':text,'stderr':'','output_truncated':truncated}]
            path.write_text(json.dumps(record),encoding='utf-8')
            item=self.get_inbox()['items'][0]
            self.assertEqual(item['status'],'needs_review');self.assertFalse(item['evidence']['checks_passed'])

    def test_row_count_is_bounded_and_incomplete_is_not_empty_success(self):
        session,row=self.candidate()
        with session.registry.transaction() as db:
            for n in range(101):session.registry.put('issue_execution',f'{n:032x}',{**row,'id':f'{n:032x}'},db)
        result=self.get_inbox();self.assertFalse(result['complete']);self.assertTrue(result['errors'])
        self.assertEqual(result['items'],[])


class MonitorDeliveryInboxTests(unittest.TestCase):
    setUp=monitor_fixture.MonitorExecutionTests.setUp
    finish=monitor_fixture.MonitorExecutionTests.finish
    scope=monitor_fixture.MonitorExecutionTests.scope
    view=monitor_fixture.MonitorExecutionTests.view
    payload=monitor_fixture.MonitorExecutionTests.payload
    preview=monitor_fixture.MonitorExecutionTests.preview
    configure=monitor_fixture.MonitorExecutionTests.configure
    new_hub=monitor_fixture.MonitorExecutionTests.new_hub
    worker=monitor_fixture.MonitorExecutionTests.worker
    prepare=monitor_fixture.MonitorExecutionTests.prepare
    repair_preview=monitor_fixture.MonitorExecutionTests.repair_preview
    execute=monitor_fixture.MonitorExecutionTests.execute

    def test_original_root_candidate_and_restart_without_session_are_readable(self):
        mid,event=self.prepare('auto');result=self.execute(self.repair_preview(mid,event))
        first=projection(self.hub)
        self.assertEqual(len(first['items']),1,first)
        item=first['items'][0];self.assertEqual(item['status'],'candidate_review',item)
        self.assertEqual(item['origin']['kind'],'monitor');self.assertEqual(item['evidence']['tests_run'],4)
        budget=self.hub.monitor.registries[mid].budget()
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        with patch('repo_maintainer.git.RepositoryGateway.command',side_effect=AssertionError('history used Git')):
            restored=projection(self.hub)['items'][0]
        self.assertEqual(item['id'],restored['id']);self.assertEqual(item['notice_id'],restored['notice_id'])
        self.assertIsNone(restored['origin']['workspace_id'])
        self.assertEqual(self.hub.monitor.registries[mid].budget(),budget)


if __name__=='__main__':unittest.main()
