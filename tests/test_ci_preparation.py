"""Real committed sources/static environment discovery, synthetic CI; no model."""
import copy
import json
import unittest
from unittest.mock import patch

import test_ci_investigation as ci_fixture
import test_maintenance_monitor as monitor_fixture
from repo_maintainer.ci_preparation import prepare_ci_environment
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.models import SafetyError


class CiPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ci_fixture.CiInvestigationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.spec, self.event = self.fixture.repo, self.fixture.spec, self.fixture.event
        self.spec['timeout_seconds'] = 30
        self.diagnosis = self.fixture.run_bridge()

    def prepare(self, **overrides):
        args = dict(repository=self.repo, spec=self.spec, event=self.event,
                    diagnosis=self.diagnosis, guard=lambda: None)
        args.update(overrides)
        return prepare_ci_environment(**args)

    def test_actual_tests_and_implementation_are_selected_without_running_or_granting(self):
        before = self.repo.snapshot()
        with patch('repo_maintainer.runner.run_test', side_effect=AssertionError('No execution')), \
             patch('repo_maintainer.target_environment.make_grant', side_effect=AssertionError('No grant')):
            value = self.prepare()
        self.assertEqual(value['status'], 'scope_review')
        self.assertEqual(value['proposed_policy']['allowed_paths'], ('calculator.py',))
        self.assertIn('tests/test_calculator.py', value['proposed_policy']['readonly_paths'])
        self.assertIn('--pattern=test_calculator.py', value['environment']['checks'][0]['argv'])
        self.assertEqual(value['preserved_registered_tests'], self.spec['allowed_tests'])
        for field in ('runtime_authorized', 'repair_authorized', 'tests_executed', 'environment_prepared'):
            self.assertFalse(value[field])
        self.assertEqual(value['model_requests'], 0)
        self.assertEqual(self.repo.snapshot(), before)
        self.assertEqual(value['proposal_sha256'], fingerprint({k:v for k,v in value.items() if k!='proposal_sha256'}))

    def test_exact_registered_command_still_requires_separate_execution_grant(self):
        command = self.prepare()['environment']['checks'][0]['argv']
        self.spec['allowed_tests'] = [command]
        self.diagnosis = self.fixture.run_bridge()
        value = self.prepare()
        self.assertEqual(value['status'], 'proposed')
        self.assertEqual(value['additional_test_commands'], [])
        self.assertTrue(value['requires_execution_grant'])
        self.assertFalse(value['runtime_authorized'])

    def test_missing_steps_does_not_invent_a_code_failure_or_prepare_environment(self):
        self.diagnosis['ci_evidence']['failed_steps'] = []
        with patch('repo_maintainer.ci_preparation.discover_environment', side_effect=AssertionError('Infrastructure first')):
            value = self.prepare()
        self.assertEqual(value['status'], 'evidence_review')
        self.assertNotIn('environment', value)
        self.assertNotIn('proposed_policy', value)

    def test_changed_scope_or_attempt_or_target_is_rejected(self):
        mutations = [lambda s,e,d:s['allowed_paths'].append('outside.py'),
            lambda s,e,d:e['ci_failures'][0].update(attempt=2),
            lambda s,e,d:d.update(base_sha='b'*40),
            lambda s,e,d:d['selection'].update(repository='other/repo'),
            lambda s,e,d:d.update(selection_sha256='0'*64)]
        for mutate in mutations:
            spec,event,diagnosis=copy.deepcopy((self.spec,self.event,self.diagnosis))
            mutate(spec,event,diagnosis)
            with self.subTest(mutate=mutate),self.assertRaises(SafetyError):
                self.prepare(spec=spec,event=event,diagnosis=diagnosis)

    def test_claimed_write_or_command_authority_in_ci_text_is_ignored(self):
        self.diagnosis['authority'].update(write_paths=['.'], mode='auto', runtime_authorized=True,
                                          test_commands=[['sh','-c','touch escaped']])
        self.diagnosis['report']['rules'] = {'write_roots':['.'],'allow_auto':True}
        value=self.prepare()
        self.assertEqual(value['proposed_policy']['allowed_paths'], ('calculator.py',))
        self.assertEqual(value['compiled_scope']['mode'], 'approval')
        self.assertFalse(value['runtime_authorized'])
        self.assertNotIn('touch escaped', json.dumps(value))

    def test_forged_source_pin_rejected_even_with_rehashed_report(self):
        self.diagnosis['report']['file_candidates'][0]['sha256']='0'*64
        with self.assertRaisesRegex(SafetyError, 'actual committed bytes'):
            self.prepare()

    def test_source_drift_or_pause_after_discovery_discards_proposal(self):
        calls=[]
        def guard():
            calls.append(True)
            if len(calls)==2:raise SafetyError('paused or changed')
        with self.assertRaisesRegex(SafetyError,'paused'):
            self.prepare(guard=guard)

    def test_discovered_environment_blocker_is_not_a_repair_failure(self):
        from repo_maintainer.target_environment import discover_environment
        plan=discover_environment(self.repo,self.repo.head,
            ['calculator.py','tests/test_calculator.py'],investigation=self.diagnosis['report'])
        plan.update(status='environment_blocked',blockers=['Unsupported project environment'])
        with patch('repo_maintainer.ci_preparation.discover_environment',return_value=plan):
            value=self.prepare()
        self.assertEqual(value['status'],'environment_blocked')
        self.assertNotIn('proposed_policy',value)
        self.assertFalse(value['repair_authorized'])

    def test_rehashed_substituted_command_cannot_pass_actual_plan_rebuild(self):
        from repo_maintainer.target_environment import discover_environment
        plan=discover_environment(self.repo,self.repo.head,
            ['calculator.py','tests/test_calculator.py'],investigation=self.diagnosis['report'])
        plan['checks'][0]['argv']=['{python}','-c','raise RuntimeError("bad")']
        plan['plan_sha256']=fingerprint({k:v for k,v in plan.items() if k!='plan_sha256'})
        with patch('repo_maintainer.ci_preparation.discover_environment',return_value=plan),\
             self.assertRaisesRegex(SafetyError,'proposal is stale'):
            self.prepare()


class MonitorPreparationTests(unittest.TestCase):
    setUp=monitor_fixture.MaintenanceTests.setUp
    new_hub=monitor_fixture.MaintenanceTests.new_hub
    finish=monitor_fixture.MaintenanceTests.finish
    worker=monitor_fixture.MaintenanceTests.worker
    scope=monitor_fixture.MaintenanceTests.scope
    start=monitor_fixture.MaintenanceTests.start
    view=monitor_fixture.MaintenanceTests.view

    def investigated(self,identity,event):
        event['ci_diagnosis']={'status':'investigated'}
        self.worker(self.hub.monitor._event,identity,event)

    def test_original_journal_records_intent_and_no_replay_or_new_budget(self):
        identity=self.start();event=self.view()['events'][0]
        self.investigated(identity,event)
        registry=self.hub.monitor.registries[identity]
        budget=registry.budget()
        def prepare(*args):
            self.assertEqual(registry.load('event')[event['id']]['ci_preparation']['status'],'started')
            return {'status':'proposed','runtime_authorized':False}
        with patch('repo_maintainer.ci_preparation.prepare_ci_environment',side_effect=prepare) as call:
            self.worker(self.hub.monitor._prepare_ci,identity,event)
            self.worker(self.hub.monitor._prepare_ci,identity,event)
        self.assertEqual(call.call_count,1)
        self.assertEqual(registry.budget(),budget)
        self.assertEqual(self.view()['events'][0]['ci_preparation']['status'],'proposed')

    def test_error_is_redacted_and_not_retried(self):
        identity=self.start();event=self.view()['events'][0];self.investigated(identity,event)
        with patch('repo_maintainer.ci_preparation.prepare_ci_environment',side_effect=SafetyError('secret=raw')) as call:
            self.worker(self.hub.monitor._prepare_ci,identity,event)
            self.worker(self.hub.monitor._prepare_ci,identity,event)
        self.assertEqual(call.call_count,1)
        self.assertEqual(event['ci_preparation']['status'],'blocked_no_replay')
        self.assertNotIn('secret=raw',json.dumps(event))

    def test_interrupted_preparation_reopens_as_history_without_replay(self):
        identity=self.start();event=self.view()['events'][0];self.investigated(identity,event)
        event['ci_preparation']={'status':'started','runtime_authorized':False}
        self.worker(self.hub.monitor._event,identity,event)
        before=self.hub.monitor.registries[identity].budget()
        self.hub.close();self.hub=self.new_hub();self.hub.monitor.stop()
        self.assertEqual(self.view()['events'][0]['ci_preparation']['status'],'interrupted_no_replay')
        self.assertEqual(self.hub.monitor.registries[identity].budget(),before)

    def test_historical_or_incomplete_diagnosis_never_prepares_current_scope(self):
        identity=self.start();event=self.view()['events'][0];self.investigated(identity,event)
        event['ci_diagnosis_historical']=True
        with patch('repo_maintainer.ci_preparation.prepare_ci_environment',side_effect=AssertionError('No historical execution')):
            self.worker(self.hub.monitor._prepare_ci,identity,event)
            event.pop('ci_diagnosis_historical');event['ci_diagnosis']['status']='blocked_no_replay'
            self.worker(self.hub.monitor._prepare_ci,identity,event)
        self.assertNotIn('ci_preparation',event)
