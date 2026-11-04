"""Original monitor/UI/worktree/venv, counted mock and real Pi loopback only."""
import copy
from datetime import datetime, timedelta, timezone
import json
import shutil
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

import test_maintenance_monitor as fixture
import test_monitor_root_rules as root_fixture
from test_repository_workbench import FakeGitHub, FakeServer
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import Edit, Plan, SafetyError, digest
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app
from repo_maintainer.workflow import cleanup_run


class MonitorExecutionTests(unittest.TestCase):
    setUp = fixture.MaintenanceTests.setUp
    finish = fixture.MaintenanceTests.finish
    scope = fixture.MaintenanceTests.scope
    view = fixture.MaintenanceTests.view
    payload = root_fixture.MonitorRootRulesTests.payload
    preview = root_fixture.MonitorRootRulesTests.preview
    configure = root_fixture.MonitorRootRulesTests.configure

    def new_hub(self):
        return RepositoryWorkbench(self.root/'hub',self.config,request_budget=6,allow_auto=True,
            github=FakeGitHub(),provider_factory=lambda _:fixture.CountingProvider(),server_factory=FakeServer)

    def worker(self, function, *args):
        return self.hub.executor.submit(function,*args).result(timeout=180)

    def prepare(self, mode='approval'):
        identity=self.configure(allow_local_code=True,mode=mode)
        self.worker(self.hub.monitor.poll,identity)
        event=self.view()['events'][0]
        self.assertEqual(event['root_preparation']['status'],'reproduced',event)
        return identity,event

    def repair_preview(self, identity, event):
        return self.worker(self.hub.monitor.execution.preview,
            {'monitor_id':identity,'event_id':event['id'],'max_attempts':3})

    def execute(self, value):
        return self.worker(self.hub.monitor.execution.start,{**value,'confirmed':True})

    def session(self, identity, event):
        return self.hub.monitor.servers[identity,'root-'+event['id']].session

    def preserve_cleanup(self, session):
        for run_id in session.runs:
            directory=session.runs_dir/run_id
            if (directory/'workspace').exists():self.addCleanup(cleanup_run,directory)

    def test_preview_is_readonly_exact_scope_and_original_budget(self):
        identity,event=self.prepare()
        before=self.view()
        value=self.repair_preview(identity,event)['preview']
        self.assertEqual(value['policy']['allowed_paths'],['calculator.py'])
        self.assertEqual(value['max_attempts'],1)
        self.assertTrue(value['may_charge']);self.assertFalse(value['push_or_merge'])
        self.assertEqual(before,self.view())
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_manual_plan_then_new_approval_runs_same_environment_red_green(self):
        identity,event=self.prepare()
        result=self.execute(self.repair_preview(identity,event))
        self.assertEqual(result['status'],'awaiting_approval')
        session=self.session(identity,event)
        self.assertEqual(session.runs,{})
        self.assertEqual(self.view()['requests_used'],2)
        plan=next(iter(session.plans.values()))['view']
        result=session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.preserve_cleanup(session)
        self.assertEqual(result['workflow_status'],'candidate_ready')
        record=json.loads((session.runs_dir/result['run_id']/'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['baseline_tests'][0]['category'],'test_failed')
        self.assertEqual(record['candidate_tests'][0]['category'],'passed')
        self.assertTrue(record['rounds'][0]['environment_comparison']['candidate_verified'])
        self.assertTrue(record['source_unchanged'])
        self.assertEqual(fixture.CountingProvider.calls,1)
        self.assertEqual(self.view()['requests_used'],2)
        self.assertEqual(RepositoryGateway(self.repo).command('status','--porcelain'),b'')

    def test_auto_mode_uses_original_controller_and_shared_budget(self):
        identity,event=self.prepare('auto')
        result=self.execute(self.repair_preview(identity,event))
        session=self.session(identity,event);self.preserve_cleanup(session)
        self.assertEqual(result['status'],'candidate_ready',session.jobs)
        self.assertEqual(self.view()['requests_used'],2)
        self.assertEqual(session.budget_registry.path,self.hub.monitor.registries[identity].path)
        self.assertEqual(session.registry.budget()['requests_used'],0)
        with self.assertRaises(SafetyError):self.repair_preview(identity,event)
        self.worker(self.hub.monitor.poll,identity)
        self.assertEqual(fixture.CountingProvider.calls,1)

    def prepare_future_node_failure(self, write_root):
        implementation = 'js/logic/policy.mjs'
        test = 'js/smoke.test.mjs'
        good = b'export const total = 2;\n'
        bad = b'export const total = 3;\n'
        (self.repo/'js/logic').mkdir(parents=True)
        (self.repo/'js/review').mkdir()
        (self.repo/'js/review/readme.txt').write_bytes(b'Reserved review directory.\n')
        (self.repo/implementation).write_bytes(good)
        (self.repo/test).write_bytes(b"import assert from 'node:assert/strict';\n"
            b"import { test } from 'node:test';\nimport { total } from './logic/policy.mjs';\n"
            b"test('fixed check', () => assert.equal(total, 2));\n")
        repo = RepositoryGateway(self.repo)
        repo.command('add', 'js')
        repo.command('commit', '-m', 'passing Node check')
        mid = self.configure(allow_local_code=True, mode='auto', read_roots=['js'],
                             write_roots=[write_root], check_families=['node-native'])
        self.worker(self.hub.monitor.poll, mid)
        prep = self.view()['events'][0]['root_preparation']
        self.assertEqual(prep['status'], 'baseline_passes', prep)
        auth = self.hub.monitor.model_authorization
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        receipt = self.worker(auth.preview, {'monitor_id':mid, 'expires_at':expires.isoformat(timespec='seconds'),
                                             'max_attempts':1})
        self.worker(auth.configure, {**receipt, 'confirmed':True})
        (self.repo/implementation).write_bytes(bad)
        repo.command('add', implementation)
        repo.command('commit', '-m', 'fault in imported implementation')
        return mid, repo, implementation, test, good

    @unittest.skipUnless(shutil.which('node'), 'Node native tests require Node')
    def test_shallow_future_node_failure_auto_dispatches_with_original_check_readonly(self):
        mid, repo, implementation, test, good = self.prepare_future_node_failure('js')
        test_bytes = repo.read(test)
        original = repo.snapshot()

        def propose(provider, source, issue, checks):
            provider.evidence = {'requests':1, 'transport':'synthetic-counting'}
            self.assertEqual(checks, (('node', '--test', test),))
            return Plan(issue, source.head, 'Synthetic fixture repair', 'low',
                        (Edit(implementation, digest(source.read(implementation)), good.decode()),),
                        checks, 'synthetic-counting')

        with patch.object(fixture.CountingProvider, 'plan', autospec=True, side_effect=propose) as model:
            self.worker(self.hub.monitor.poll, mid)
            event = next(e for e in self.view()['events'] if e['sha']==repo.head)
            prep = event['root_preparation']
            self.assertIsNone(prep['report']['git']['base_sha'])
            self.assertEqual(prep['proposed_policy']['allowed_paths'], [implementation])
            self.assertEqual(prep['proposed_policy']['readonly_paths'], [test])
            self.assertEqual(event['model_dispatch']['status'], 'finished', event)
            self.assertEqual(event['root_execution']['status'], 'candidate_ready', event)
            session = self.session(mid,event)
            self.preserve_cleanup(session)
            run = session.runs_dir/event['root_execution']['run_id']
            result = json.loads((run/'run.json').read_text(encoding='utf-8'))
            self.assertEqual(result['baseline_tests'][0]['category'], 'test_failed')
            self.assertEqual(result['candidate_tests'][0]['category'], 'passed')
            self.assertEqual((run/'workspace'/test).read_bytes(), test_bytes)
            self.assertEqual((run/'workspace'/implementation).read_bytes(), good)
            self.assertEqual(repo.snapshot(), original)
            self.worker(self.hub.monitor.poll, mid)
            model.assert_called_once()
        self.assertEqual(self.view()['requests_used'], 1)
        self.assertEqual(self.view()['requests_reserved_unknown'], 0)

    @unittest.skipUnless(shutil.which('node'), 'Node native tests require Node')
    def test_scope_blocked_node_failure_preserves_notice_and_has_no_manual_preview(self):
        mid, repo, _, _, _ = self.prepare_future_node_failure('js/review')
        self.worker(self.hub.monitor.poll, mid)
        event = next(e for e in self.view()['events'] if e['sha']==repo.head)
        self.assertEqual(event['root_preparation']['status'], 'reproduced')
        self.assertEqual(event['model_dispatch']['status'], 'blocked_no_replay')
        self.assertEqual(event['message'], event['model_dispatch']['message'])
        self.assertIn('请核对失败检查和维护规则', event['message'])
        self.assertTrue(event['notification'])
        self.assertFalse(event['can_prepare_repair'])
        self.assertNotIn('root_execution', event)
        with self.assertRaisesRegex(SafetyError, 'No editable implementation'):
            self.repair_preview(mid, event)
        self.worker(self.hub.monitor.poll, mid)
        self.assertEqual(fixture.CountingProvider.calls, 0)
        self.assertEqual(self.view()['requests_used'], 0)

    def test_explicit_consent_and_browser_number_roundtrip(self):
        identity,event=self.prepare()
        preview=self.repair_preview(identity,event)
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.execution.start,{**preview,'confirmed':False})
        bad=copy.deepcopy(preview);bad['preview']['policy']['allowed_paths']=['tests/test_calculator.py']
        with self.assertRaises(SafetyError):self.execute(bad)
        preview=json.loads(json.dumps(preview),parse_float=lambda n:int(float(n)) if float(n).is_integer() else float(n))
        self.assertEqual(self.execute(preview)['status'],'awaiting_approval')
        self.assertTrue(self.view()['events'][0]['can_open'])

    def test_current_source_or_budget_changes_reject_before_model(self):
        identity,event=self.prepare()
        preview=self.repair_preview(identity,event)
        registry=self.hub.monitor.registries[identity]
        self.worker(registry.reserve)
        with self.assertRaises(SafetyError):self.execute(preview)
        self.assertEqual(fixture.CountingProvider.calls,0)
        self.assertEqual(registry.budget()['requests_reserved_unknown'],6)

    def test_new_commit_rejects_old_preview_and_approval(self):
        identity,event=self.prepare()
        value=self.repair_preview(identity,event)
        repo=RepositoryGateway(self.repo);repo.command('commit','--allow-empty','-m','changed')
        with self.assertRaises(SafetyError):self.execute(value)
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_baseline_report_tamper_and_green_evidence_cannot_authorize(self):
        identity,event=self.prepare()
        value=self.repair_preview(identity,event)
        run_id=event['root_preparation']['baseline']['run_id']
        report=self.hub.monitor.root/identity/'baselines'/run_id/'run.json'
        original=report.read_text(encoding='utf-8')
        report.write_text(original.replace('baseline_ready','baseline_passes'),encoding='utf-8')
        with self.assertRaises(SafetyError):self.execute(value)
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_config_changed_after_plan_refuses_approval_without_resetting_usage(self):
        identity,event=self.prepare()
        self.execute(self.repair_preview(identity,event))
        session=self.session(identity,event)
        plan=next(iter(session.plans.values()))['view']
        self.config.write_text('changed author model config',encoding='utf-8')
        with self.assertRaises(SafetyError):
            session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.assertEqual(session.runs,{})
        self.assertEqual(self.view()['requests_used'],2)

    def test_interrupted_start_restores_without_replay(self):
        identity,event=self.prepare()
        preview=self.repair_preview(identity,event)['preview']
        event['root_execution']={'status':'started','binding':preview['binding'],'max_attempts':1,'job_id':uuid.uuid4().hex}
        self.worker(self.hub.monitor._event,identity,event)
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        self.assertEqual(self.view()['events'][0]['root_execution']['status'],'interrupted_no_replay')
        with self.assertRaises(SafetyError):self.repair_preview(identity,event)
        self.assertEqual(fixture.CountingProvider.calls,0)

    def test_failed_planning_retains_unknown_and_blocks_duplicate_after_restart(self):
        identity,event=self.prepare()
        value=self.repair_preview(identity,event)
        with patch.object(fixture.CountingProvider,'plan',side_effect=RuntimeError('Author transport failure')):
            with self.assertRaises(SafetyError):self.execute(value)
        self.assertEqual(self.view()['requests_reserved_unknown'],6)
        self.assertEqual(self.view()['events'][0]['root_execution']['status'],'failed_no_replay')
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        with self.assertRaises(SafetyError):self.repair_preview(identity,event)
        self.assertEqual(self.view()['requests_reserved_unknown'],6)

    def test_restart_preserves_plan_and_requires_new_claim(self):
        identity,event=self.prepare()
        self.execute(self.repair_preview(identity,event))
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        self.worker(self.hub.monitor.operate,'monitor_open',{'monitor_id':identity,'event_id':event['id']})
        session=self.session(identity,event)
        self.assertEqual(session.claimed_plans,set())
        self.assertEqual(len(session.plans),1)
        self.assertEqual(fixture.CountingProvider.calls,1)
        self.assertNotEqual(self.view()['events'][0].get('display_status'),'evidence_invalid')

    def test_shared_http_keeps_auth_origin_and_confirms_server_preview(self):
        from starlette.testclient import TestClient
        identity,event=self.prepare()
        with TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345') as client:
            payload={'monitor_id':identity,'event_id':event['id'],'max_attempts':3,'request_id':uuid.uuid4().hex}
            route='/api/actions/monitor_repair_preview'
            self.assertEqual(client.post(route,json=payload).status_code,401)
            headers={'Authorization':'Bearer '+self.hub.token}
            self.assertEqual(client.post(route,json=payload,headers={**headers,'Origin':'https://outside.invalid'}).status_code,403)
            self.assertEqual(client.post(route,json=payload,headers=headers).status_code,202)
            self.worker(lambda:None)
            value=self.hub.jobs[payload['request_id']]['result']
            job_id=uuid.uuid4().hex
            self.assertEqual(client.post('/api/actions/monitor_repair_start',headers=headers,
                json={**value,'confirmed':True,'request_id':job_id}).status_code,202)
            self.worker(lambda:None)
            self.assertEqual(self.hub.jobs[job_id]['status'],'finished',self.hub.jobs[job_id])

    def test_real_pi_native_uses_existing_session_with_original_monitor_ledger(self):
        from test_task_planning import SyntheticModel
        from repo_maintainer.pi_provider import PiProvider
        self.hub.close()
        model=SyntheticModel();self.addCleanup(model.close)
        self.hub=RepositoryWorkbench(self.root/'pi-hub',self.config,request_budget=6,
            github=FakeGitHub(),server_factory=FakeServer,
            provider_factory=lambda p:PiProvider(p.allowed_paths,model.url,readonly_paths=p.readonly_paths,max_requests=6))
        self.hub.monitor.stop()
        identity,event=self.prepare()
        self.execute(self.repair_preview(identity,event))
        session=self.session(identity,event)
        self.assertIsNotNone(session.task_planner)
        tasks=session.budget_registry.load('pi_task')
        self.assertEqual(len(tasks),1)
        task=next(iter(tasks.values()));self.assertEqual(task['revision'],1)
        self.assertEqual(self.view()['requests_used'],2)
        self.assertEqual(self.view()['requests_reserved_unknown'],0)
        plan=next(iter(session.plans.values()))['view']
        result=session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.preserve_cleanup(session)
        self.assertEqual(result['workflow_status'],'candidate_ready')
        self.assertEqual(session.registry.load('pi_task_link')['run:'+result['run_id']]['task_id'],task['task_id'])
        self.assertTrue(any(c['task']['status']=='verified' for c in session.state()['recovery_cards']))
        self.assertEqual(len(model.requests),2)
