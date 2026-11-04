"""History is data; it cannot restore a provider, capability or consumed authority."""
import copy
import json
import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient
import test_maintenance_monitor as fixture
import test_monitor_root_rules as root_fixture
from repo_maintainer.dashboard import read_json
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.monitor_history import history, recovery_preview
from repo_maintainer.repository_workbench import create_workbench_app
from repo_maintainer.workflow import cleanup_run


class MonitorHistoryTests(unittest.TestCase):
    setUp = fixture.MaintenanceTests.setUp
    finish = fixture.MaintenanceTests.finish
    new_hub = fixture.MaintenanceTests.new_hub
    worker = fixture.MaintenanceTests.worker
    scope = fixture.MaintenanceTests.scope
    start = fixture.MaintenanceTests.start
    view = fixture.MaintenanceTests.view
    payload = root_fixture.MonitorRootRulesTests.payload
    preview = root_fixture.MonitorRootRulesTests.preview
    configure = root_fixture.MonitorRootRulesTests.configure

    def event(self):
        mid = self.start()
        return mid, self.view()['events'][0]

    def read(self, mid, event):
        return history(self.hub.monitor, mid, event['id'])

    def recover(self, mid, event):
        return self.worker(recovery_preview,self.hub.monitor,{'monitor_id':mid,'event_id':event['id']})

    def test_stale_history_is_readonly_even_without_source_or_model_config(self):
        mid,event = self.event()
        before = self.hub.monitor.registries[mid].budget()
        repo=RepositoryGateway(self.repo);repo.command('commit','--allow-empty','-m','new version')
        self.config.unlink()
        source=self.hub.monitor.root/mid/'events'/event['id']/'source'
        source.rename(source.with_name('source-preserved'))
        with patch.object(self.hub.monitor,'_provider',side_effect=AssertionError('history created provider')):
            value=self.read(mid,event)
        self.assertTrue(value['read_only']);self.assertFalse(value['runtime_authorized'])
        self.assertEqual(value['plans'][0]['plan']['base_sha'],event['sha'])
        self.assertIn('+',value['plans'][0]['diff']);self.assertEqual(value['errors'],[])
        self.assertEqual(before,self.hub.monitor.registries[mid].budget())
        self.assertEqual(fixture.CountingProvider.calls,1)
        self.assertNotIn('launch_url',json.dumps(value))

    def test_new_commit_recovery_reads_head_without_replaying_and_cached_open_rejects(self):
        mid,event=self.event()
        RepositoryGateway(self.repo).command('commit','--allow-empty','-m','next')
        value=self.recover(mid,event)
        self.assertEqual(value['status'],'new_commit');self.assertTrue(value['can_check_latest'])
        self.assertFalse(value['can_open_original'])
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.operate,'monitor_open',{'monitor_id':mid,'event_id':event['id']})
        self.assertEqual(fixture.CountingProvider.calls,1)

    def test_restart_history_never_reclaims_original_plan(self):
        mid,event=self.event()
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        self.assertEqual(self.hub.monitor.servers,{})
        value=self.read(mid,event)
        self.assertEqual(value['budget']['requests_used'],2)
        self.assertEqual(self.hub.monitor.servers,{})
        self.assertTrue(self.recover(mid,event)['can_open_original'])
        self.assertEqual(self.hub.monitor.servers,{})
        self.worker(self.hub.monitor.operate,'monitor_open',{'monitor_id':mid,'event_id':event['id']})
        session=self.hub.monitor.servers[mid,event['id']].session
        self.assertEqual(session.claimed_plans,set())
        self.assertEqual(fixture.CountingProvider.calls,1)

    def test_paused_history_remains_available_and_recovery_is_explanatory(self):
        mid,event=self.event()
        self.worker(self.hub.monitor.operate,'monitor_pause',{'monitor_id':mid})
        self.assertEqual(len(self.read(mid,event)['plans']),1)
        self.assertEqual(self.recover(mid,event)['status'],'paused')
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.operate,'monitor_open',{'monitor_id':mid,'event_id':event['id']})

    def test_rule_migration_preserves_fixed_plan_history_but_not_execution(self):
        mid,event=self.event()
        self.configure()
        self.assertEqual(len(self.read(mid,event)['plans']),1)
        self.assertEqual(self.recover(mid,event)['status'],'binding_changed')
        self.assertEqual(self.view()['requests_used'],2)

    def test_unknown_consumption_blocks_recovery_and_keeps_original_reservation(self):
        mid,event=self.event();registry=self.hub.monitor.registries[mid]
        self.worker(registry.reserve)
        before=registry.budget()
        self.assertEqual(self.recover(mid,event)['status'],'unknown_consumption')
        self.assertEqual(self.read(mid,event)['budget'],before)
        self.assertEqual(before,registry.budget())

    def test_plan_tamper_is_visible_as_invalid_without_repairing_evidence(self):
        mid,event=self.event()
        session=self.hub.monitor.servers[mid,event['id']].session
        path=next(iter(session.plans.values()))['path']
        path.write_bytes(path.read_bytes()+b' ')
        value=self.read(mid,event)
        self.assertTrue(value['errors']);self.assertEqual(value['plans'],[])
        self.assertEqual(self.recover(mid,event)['status'],'evidence_invalid')
        self.assertEqual(fixture.CountingProvider.calls,1)

    def test_missing_journal_is_not_recreated(self):
        mid,event=self.event()
        session=self.hub.monitor.servers[mid,event['id']].session
        path=session.registry.path
        session.registry.close()
        path.rename(path.with_suffix('.preserved'))
        value=self.read(mid,event)
        self.assertTrue(value['errors']);self.assertFalse(path.exists())

    def test_paths_in_record_are_not_followed(self):
        mid,event=self.event()
        session=self.hub.monitor.servers[mid,event['id']].session
        key=event['plan_id'];item=session.registry.load('plan')[key]
        item['session_id']='../../foreign'
        self.worker(session.registry.put,'plan',key,item)
        self.assertTrue(self.read(mid,event)['errors'])
        for bad in ('../../x','a'*15,'a'*17):
            with self.assertRaises(SafetyError):history(self.hub.monitor,bad,event['id'])

    def test_actual_candidate_tests_and_diff_survive_rule_change(self):
        mid,event=self.event();session=self.hub.monitor.servers[mid,event['id']].session
        plan=next(iter(session.plans.values()))['view']
        result=session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        directory=session.runs_dir/result['run_id'];self.addCleanup(cleanup_run,directory)
        before=(directory/'run.json').read_bytes()
        self.configure()
        value=self.read(mid,event)
        self.assertEqual(value['errors'],[],value)
        self.assertEqual(value['runs'][0]['status'],'candidate_ready')
        self.assertEqual(value['runs'][0]['candidate_tests'][0]['returncode'],0)
        self.assertEqual(before,(directory/'run.json').read_bytes())
        record,_=read_json(directory/'run.json');record['base_sha']='f'*40
        (directory/'run.json').write_text(json.dumps(record),encoding='utf-8')
        self.assertTrue(self.read(mid,event)['errors'])
        (directory/'run.json').write_bytes(before)

    def test_http_uses_boundary_read_pool_and_no_action_queue(self):
        mid,event=self.event()
        url=f'/api/monitors/{mid}/events/{event["id"]}/history'
        before=copy.deepcopy(self.hub.jobs)
        with TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345') as client:
            self.assertEqual(client.get(url).status_code,401)
            headers={'Authorization':'Bearer '+self.hub.token}
            self.assertEqual(client.get(url,headers={**headers,'Origin':'https://outside.invalid'}).status_code,403)
            response=client.get(url,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(client.get(url+'?path=../../secret',headers=headers).status_code,409)
            self.assertEqual(client.get(url.replace(event['id'],'0'*16),headers=headers).status_code,409)
            self.assertTrue(response.json()['read_only'])
        self.assertEqual(self.hub.jobs,before)
        self.assertEqual(fixture.CountingProvider.calls,1)

    def test_root_interruption_without_plan_is_readable_but_not_replayed(self):
        mid=self.configure();self.worker(self.hub.monitor.poll,mid)
        event=self.view()['events'][0]
        event['root_execution']={'status':'interrupted_no_replay','binding':{}}
        self.worker(self.hub.monitor._event,mid,event)
        value=self.read(mid,event)
        self.assertEqual(value['execution']['status'],'interrupted_no_replay')
        self.assertEqual(value['plans'],[])
        self.assertEqual(self.recover(mid,event)['status'],'needs_reconciliation')
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_root_cached_open_rechecks_model_binding_but_history_stays_readable(self):
        import test_monitor_execution as execution_fixture
        mid,event=execution_fixture.MonitorExecutionTests.prepare(self)
        execution=self.hub.monitor.execution
        value=self.worker(execution.preview,{'monitor_id':mid,'event_id':event['id'],'max_attempts':1})
        self.worker(execution.start,{**value,'confirmed':True})
        self.assertTrue(self.recover(mid,event)['can_open_original'])
        self.config.write_text('new model configuration',encoding='utf-8')
        self.assertEqual(self.recover(mid,event)['status'],'binding_changed')
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.operate,'monitor_open',{'monitor_id':mid,'event_id':event['id']})
        self.assertEqual(len(self.read(mid,event)['plans']),1)
        self.assertEqual(fixture.CountingProvider.calls,1)


if __name__=='__main__':unittest.main()
