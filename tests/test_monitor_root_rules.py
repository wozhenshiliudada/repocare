"""Real Git/venv baseline, original journal, no external model or GitHub calls."""
import copy
import json
import uuid
from unittest.mock import patch
import unittest

import test_maintenance_monitor as fixture
from test_repository_workbench import FakeGitHub, FakeServer
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.github_connection import SnapshotError
from repo_maintainer.models import SafetyError
from repo_maintainer.repository_workbench import RepositoryWorkbench, inspect_repository, validate_request


class MonitorRootRulesTests(unittest.TestCase):
    setUp=fixture.MaintenanceTests.setUp
    finish=fixture.MaintenanceTests.finish
    worker=fixture.MaintenanceTests.worker
    scope=fixture.MaintenanceTests.scope
    start=fixture.MaintenanceTests.start
    view=fixture.MaintenanceTests.view

    def new_hub(self):
        return RepositoryWorkbench(self.root/'hub',self.config,request_budget=6,github=FakeGitHub(),
            provider_factory=lambda _:fixture.CountingProvider(),server_factory=FakeServer)

    def payload(self, **overrides):
        source=inspect_repository(self.repo)
        return {'path':str(self.repo),'revision':source['revision'],'branch':source['branch'],
            'github_url':'','interval_seconds':60,'timeout_seconds':30,'read_roots':['.'],
            'write_roots':['.'],'check_families':['unittest'],'mode':'approval',
            'allow_local_code':False,'allow_install':False,'allow_network':False,**overrides}

    def preview(self, **overrides):
        return self.worker(self.hub.monitor.root_rules.preview,self.payload(**overrides))

    def configure(self, **overrides):
        preview=self.preview(**overrides)
        return self.worker(self.hub.monitor.root_rules.configure,{**preview,'confirmed':True})['monitor_id']

    def test_preview_has_no_process_model_or_new_registry(self):
        with patch('repo_maintainer.target_environment.collect_environment_baseline',side_effect=AssertionError('No run')):
            value=self.preview()
        self.assertEqual(value['preview']['configuration']['settings']['read_roots'],['.'])
        self.assertEqual(self.hub.monitor.records,{})
        self.assertEqual(fixture.CountingProvider.calls,0)
        self.assertFalse(value['preview']['tests_executed'])

    def test_new_directory_registration_needs_no_file_or_command_arrays(self):
        identity=self.configure()
        self.assertEqual(self.view()['spec']['schema_version'],2)
        self.assertEqual(self.view()['spec']['allowed_tests'],[])
        before=self.hub.monitor.registries[identity].budget()
        self.worker(self.hub.monitor.poll,identity)
        value=self.view()['events'][0]['root_preparation']
        self.assertEqual(value['status'],'execution_consent_required')
        self.assertEqual(value['environment']['runtime'],'python')
        self.assertEqual(value['proposed_policy']['allowed_paths'],['calculator.py'])
        self.assertEqual(before,self.hub.monitor.registries[identity].budget())
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_explicit_local_code_grant_runs_actual_baseline_and_preserves_source(self):
        repo=RepositoryGateway(self.repo);before=repo.snapshot()
        identity=self.configure(allow_local_code=True)
        self.worker(self.hub.monitor.poll,identity)
        value=self.view()['events'][0]['root_preparation']
        self.assertEqual(value['status'],'reproduced',value)
        self.assertEqual(value['baseline']['status'],'baseline_ready')
        self.assertEqual(value['baseline']['checks'][0]['category'],'test_failed')
        self.assertTrue(value['baseline']['source_unchanged'])
        self.assertEqual(value['baseline']['cleanup'],'candidate_removed')
        self.assertTrue(value['tests_executed'])
        self.assertEqual(before,repo.snapshot())
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_snapshot_timeout_reports_preparation_block_without_using_budget(self):
        identity = self.configure(allow_local_code=True)
        registry = self.hub.monitor.registries[identity]
        before = registry.budget()
        message = 'Git fetch 在 120 秒内未完成；快照现场保留'
        with patch('repo_maintainer.maintenance_monitor.fetch_snapshot', side_effect=SnapshotError(message)) as fetch:
            self.worker(self.hub.monitor.poll, identity)
            self.worker(self.hub.monitor.poll, identity)
        fetch.assert_called_once()
        event = self.view()['events'][0]
        self.assertEqual(event['status'], 'blocked')
        self.assertEqual(event['message'], message)
        self.assertEqual(event['root_preparation']['status'], 'blocked_no_replay')
        self.assertNotIn('baseline', event['root_preparation'])
        self.assertNotIn('root_execution', event)
        self.assertEqual(registry.budget(), before)
        self.assertEqual(fixture.CountingProvider.calls, 0)

    def test_same_commit_and_rule_never_replay_baseline_after_poll_or_restart(self):
        identity=self.configure(allow_local_code=True)
        self.worker(self.hub.monitor.poll,identity)
        first=copy.deepcopy(self.view()['events'][0]['root_preparation'])
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        with patch('repo_maintainer.monitor_preparation.collect_environment_baseline',side_effect=AssertionError('No replay')):
            self.worker(self.hub.monitor.poll,identity)
        self.assertEqual(self.view()['events'][0]['root_preparation'],first)

    def test_explicit_rule_revision_preserves_history_and_same_budget(self):
        identity=self.configure();self.worker(self.hub.monitor.poll,identity)
        old=copy.deepcopy(self.view()['events'][0]['root_preparation'])
        registry=self.hub.monitor.registries[identity]
        reserved=registry.reserve(1)
        before=registry.budget()
        self.assertEqual(self.configure(allow_local_code=True),identity)
        self.worker(self.hub.monitor.poll,identity)
        event=self.view()['events'][0]
        self.assertEqual(event['root_preparation']['status'],'reproduced')
        self.assertEqual(event['root_preparation_history'],[old])
        self.assertEqual(registry.budget(),before)
        self.assertEqual(registry.budget()['requests_reserved_unknown'],1)
        self.assertEqual(self.view()['root_rules']['version'],2)

    def test_legacy_upgrade_does_not_reset_ledger_or_reuse_old_approval(self):
        identity=self.start()
        event=self.view()['events'][0]
        server=self.hub.monitor.servers[identity,event['id']]
        plan=next(iter(server.session.plans.values()))['view']
        before=self.hub.monitor.registries[identity].budget()
        original_spec=copy.deepcopy(self.view()['spec'])
        self.assertEqual(self.configure(),identity)
        self.assertEqual(self.view()['spec'],original_spec)
        with self.assertRaises(SafetyError):
            server.session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.worker(self.hub.monitor.poll,identity)
        self.assertFalse(self.view()['events'][0]['can_open'])
        self.assertEqual(self.view()['events'][0]['legacy_monitor_status'],'awaiting_approval')
        self.assertEqual(self.hub.monitor.registries[identity].budget(),before)
        self.assertEqual(fixture.CountingProvider.calls,1)

    def test_repeat_configuration_keeps_version_and_original_budget(self):
        identity=self.configure();before=self.hub.monitor.registries[identity].budget()
        self.assertEqual(self.configure(),identity)
        self.assertEqual(self.view()['root_rules']['version'],1)
        self.assertEqual(self.hub.monitor.registries[identity].budget(),before)

    def test_bad_permissions_roots_and_check_families_never_register(self):
        invalid=[{'read_roots':['tests'],'write_roots':['calculator.py']},
            {'allow_network':True},{'allow_install':True},{'mode':'auto'},
            {'check_families':['sh -c touch escaped']},{'check_families':[]},
            {'read_roots':['../']},{'allow_local_code':'yes'}]
        for values in invalid:
            with self.subTest(values=values),self.assertRaises(SafetyError):self.preview(**values)
        self.assertFalse(self.hub.monitor.records)

    def test_changed_preview_source_or_forged_configuration_is_rejected(self):
        value=self.preview()
        forged=copy.deepcopy(value)
        forged['preview']['configuration']['settings']['write_roots']=['tests']
        forged['revision']=self.hub.monitor.root_rules.challenge(forged['preview'])
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.root_rules.configure,{**forged,'confirmed':True})
        (self.repo/'calculator.py').write_text('user work',encoding='utf-8')
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.root_rules.configure,{**value,'confirmed':True})
        self.assertEqual((self.repo/'calculator.py').read_text(),'user work')

    def test_changing_legacy_operational_identity_cannot_create_another_budget(self):
        identity=self.start();before=self.hub.monitor.registries[identity].budget()
        with self.assertRaises(SafetyError):self.preview(interval_seconds=120)
        self.assertEqual(len(self.hub.monitor.records),1)
        self.assertEqual(self.hub.monitor.registries[identity].budget(),before)

    def test_disallowed_discovered_family_is_not_dropped_to_execute_remaining_checks(self):
        identity=self.configure(allow_local_code=True,check_families=['pytest'])
        with patch('repo_maintainer.monitor_preparation.collect_environment_baseline',side_effect=AssertionError('No run')):
            self.worker(self.hub.monitor.poll,identity)
        self.assertEqual(self.view()['events'][0]['root_preparation']['status'],'scope_review')

    def test_saved_baseline_intent_recovers_as_interrupted_without_replay(self):
        identity=self.configure();self.worker(self.hub.monitor.poll,identity)
        event=self.view()['events'][0]
        event['root_preparation']['status']='baseline_started'
        self.worker(self.hub.monitor._event,identity,event)
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        self.worker(self.hub.monitor.poll,identity)
        self.assertEqual(self.view()['events'][0]['root_preparation']['status'],'interrupted_no_replay')
        with self.assertRaisesRegex(SafetyError,'中断意图'):
            self.preview(allow_local_code=True)

    def test_http_request_fields_do_not_accept_arbitrary_commands_or_implicit_consent(self):
        good={**self.payload(),'request_id':uuid.uuid4().hex}
        self.assertEqual(validate_request('monitor_rules_preview',good),self.payload())
        with self.assertRaises(SafetyError):validate_request('monitor_rules_preview',{**good,'argv':['sh']})
        preview=self.preview()
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.root_rules.configure,{**preview,'confirmed':False})

    def test_original_environment_failure_writes_structured_error_report_instead_of_crashing(self):
        from repo_maintainer.target_environment import discover_environment,make_grant,collect_environment_baseline
        repo=RepositoryGateway(self.repo);before=repo.snapshot()
        plan=discover_environment(repo,repo.head,['.']);grant=make_grant(plan,allow_local_code=True)
        with patch.object(RepositoryGateway,'create_workspace',side_effect=PermissionError('synthetic setup failure')):
            value=collect_environment_baseline(repo,plan,grant,self.root/'baseline-report')
        self.assertEqual(value['status'],'environment_blocked')
        self.assertTrue(value['source_unchanged'])
        report=self.root/'baseline-report'/value['run_id']/'report.md'
        self.assertIn('PermissionError',report.read_text(encoding='utf-8'))
        self.assertEqual(before,repo.snapshot())

    def test_late_ci_runs_readonly_root_investigation_without_replaying_baseline(self):
        from types import SimpleNamespace
        from repo_maintainer.ci_transport import CiJsonPage
        from test_ci_evidence import FixtureTransport,HEAD
        from repo_maintainer.github_connection import fetch_snapshot
        repo=RepositoryGateway(self.repo)
        (self.repo/'calculator.py').write_bytes(repo.read('calculator.py').replace(b'return a - b',b'return a + b'))
        repo.command('add','calculator.py');repo.command('commit','-m','green baseline')
        repo.command('remote','add','origin','https://github.com/example/repo.git')
        failures=[]
        self.hub.github.branch_head=lambda *args:repo.head
        self.hub.github.workflow_runs=lambda *args:list(failures)
        self.hub.github.snapshot=lambda url,branch,sha,dest:fetch_snapshot(self.repo,branch,sha,dest)
        identity=self.configure(allow_local_code=True,github_url='https://github.com/example/repo.git')
        self.worker(self.hub.monitor.poll,identity)
        original=copy.deepcopy(self.view()['events'][0]['root_preparation'])
        self.assertEqual(original['status'],'baseline_passes')
        failures.append({'id':101,'attempt':1,'name':'calculator add'})
        transport=FixtureTransport();transport.policy=SimpleNamespace(hosts=())
        for key,page in transport.pages.items():
            transport.pages[key]=CiJsonPage(json.loads(json.dumps(page.data).replace(HEAD,repo.head)),page.has_next)
        with patch('repo_maintainer.ci_http.CiHttpTransport',return_value=transport) as reader,\
             patch('repo_maintainer.monitor_preparation.collect_environment_baseline',side_effect=AssertionError('No replay')):
            self.worker(self.hub.monitor.poll,identity);self.worker(self.hub.monitor.poll,identity)
        event=self.view()['events'][0]
        self.assertEqual(event['status'],'ci_attention')
        self.assertEqual(event['root_preparation'],original)
        self.assertEqual(event['root_late_ci']['status'],'investigated')
        self.assertEqual(reader.call_count,1)
        self.assertIn('calculator.py',[c['path'] for c in event['root_late_ci']['result']['report']['file_candidates']])
        failures[0]['attempt']=2
        self.worker(self.hub.monitor.poll,identity)
        self.assertTrue(self.view()['events'][0]['root_late_ci_historical'])
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_rule_storage_tamper_is_rejected_without_model_calls(self):
        identity=self.configure()
        registry=self.hub.monitor.registries[identity]
        value=copy.deepcopy(self.view()['root_rules']);value['settings']['write_roots']=['other']
        self.worker(registry.put,'monitor_root_rules','current',value)
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.root_rules.current,identity)
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_shared_http_requires_original_auth_origin_and_preview_confirmation(self):
        from starlette.testclient import TestClient
        from repo_maintainer.repository_workbench import create_workbench_app
        with TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345') as client:
            body={**self.payload(),'request_id':uuid.uuid4().hex}
            path='/api/actions/monitor_rules_preview'
            self.assertEqual(client.post(path,json=body).status_code,401)
            headers={'Authorization':'Bearer '+self.hub.token}
            self.assertEqual(client.post(path,headers={**headers,'Origin':'https://outside.invalid'},json=body).status_code,403)
            job=client.post(path,headers=headers,json=body)
            self.assertEqual(job.status_code,202)
            self.worker(lambda:None)
            preview=self.hub.jobs[body['request_id']]['result']
            # JavaScript JSON.stringify removes a .0 from integral numbers.
            preview=json.loads(json.dumps(preview),parse_float=lambda value:
                int(float(value)) if float(value).is_integer() else float(value))
            request_id=uuid.uuid4().hex
            result=client.post('/api/actions/monitor_rules_configure',headers=headers,
                json={**preview,'confirmed':True,'request_id':request_id})
            self.assertEqual(result.status_code,202)
            self.worker(lambda:None)
            self.assertEqual(self.hub.jobs[request_id]['status'],'finished',self.hub.jobs[request_id])
            self.assertEqual(len(self.hub.monitor.records),1)
            self.assertEqual(self.hub.state()['monitors'][0]['root_rules']['version'],1)
            self.assertEqual(fixture.CountingProvider.calls,0)
