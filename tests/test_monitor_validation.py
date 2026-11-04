"""Original monitor queue/ledger/Pi/isolated environment; loopback replies only."""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient
import test_monitor_continuation as original
from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.repository_workbench import create_workbench_app
from repo_maintainer.workflow_behavior import is_behavior, verify_pair
from test_task_planning import SyntheticModel


class MonitorValidationTests(unittest.TestCase):
    worker = original.MonitorContinuationTests.worker
    payload = original.MonitorContinuationTests.payload
    preview = original.MonitorContinuationTests.preview
    configure = original.MonitorContinuationTests.configure
    view = original.MonitorContinuationTests.view
    new_hub = original.MonitorContinuationTests.new_hub
    repair_preview = original.MonitorContinuationTests.repair_preview
    execute = original.MonitorContinuationTests.execute
    session = original.MonitorContinuationTests.session
    preserve_cleanup = original.MonitorContinuationTests.preserve_cleanup

    def setUp(self):
        # Deep monitor/venv/native paths must also work on Windows. All Git
        # mutations are confined to this fixture and have an explicit author.
        parent = Path.home()/'.codex'/'tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='mv-', dir=parent)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root/'s')
        self.git = RepositoryGateway(self.repo)
        self.git.command('-c', 'core.longpaths=true', 'config', 'core.longpaths', 'true')
        self.git.command('-c', 'core.longpaths=true', 'config', 'user.name', 'Synthetic Monitor Validation')
        self.git.command('-c', 'core.longpaths=true', 'config', 'user.email', 'monitor-validation@example.invalid')
        self.config = self.root/'provider.local'
        self.config.write_text('synthetic; not read', encoding='utf-8')
        self.model = SyntheticModel()
        self.addCleanup(self.finish)
        self.hub = self.new_hub()
        self.hub.monitor.stop()
        self.request = 'add(2,3) must return 5. Preserve the existing public interface.'

    def finish(self):
        try:
            self.hub.close()
        finally:
            self.model.close()
            self.temp.cleanup()

    def action(self, suffix, payload):
        # Exercise the same existing monitor action dispatcher as the HTTP job.
        return self.worker(self.hub.monitor.operate, 'monitor_validation_'+suffix, payload)

    def event(self, event_id):
        return next(e for e in self.view()['events'] if e['id'] == event_id)

    def location(self, mid, event):
        return {'monitor_id':mid, 'event_id':event['id']}

    def register(self, mode='approval', no_tests=False, green=False, future=False, expected_status=None):
        if no_tests:
            self.git.command('-c', 'core.longpaths=true', 'rm', 'tests/test_calculator.py')
            self.git.command('-c', 'core.longpaths=true', 'commit', '-m', 'fixture with no tests')
        if green:
            path = self.repo/'calculator.py'
            path.write_bytes(path.read_bytes().replace(b'a - b', b'a + b'))
            self.git.command('-c', 'core.longpaths=true', 'add', 'calculator.py')
            self.git.command('-c', 'core.longpaths=true', 'commit', '-m', 'fixture green existing behavior')
        mid = self.configure(allow_local_code=True, mode=mode)
        if future:
            expires = datetime.fromtimestamp(time.time()+3600,timezone.utc).isoformat(timespec='seconds')
            value = self.worker(self.hub.monitor.model_authorization.preview,
                {'monitor_id':mid, 'expires_at':expires, 'max_attempts':1})
            self.worker(self.hub.monitor.model_authorization.configure,{**value, 'confirmed':True})
            self.git.command('-c', 'core.longpaths=true', 'commit', '--allow-empty', '-m', 'future original monitor event')
        before = self.git.snapshot()
        self.worker(self.hub.monitor.poll, mid)
        event = self.view()['events'][0]
        self.assertEqual(expected_status or ('validation_needed' if no_tests else 'baseline_passes' if green else 'reproduced'),
                         event['root_preparation']['status'], event)
        self.assertEqual([], self.model.requests)
        self.assertEqual(before, self.git.snapshot())
        return mid, event

    def proposal(self, function='add', missing=False):
        return {'requirement':self.request, 'rationale':'The maintenance request states an exact input and expected output',
            'limitations':'Only this stated Python behavior', 'clarification':False,
            'cases':[{'id':'requested_sum', 'module':'calculator.py', 'callable':function,
                'args':[2,3], 'kwargs':{}, 'expect':{'kind':'json', 'value':5}, 'missing_behavior':missing}]}

    def propose(self, mid, event, function='add', missing=False):
        self.model.schema_calls = [('propose_validation', self.proposal(function, missing))]
        location = self.location(mid, event)
        before = self.hub.monitor.registries[mid].budget()
        preview = self.action('preview', {**location, 'request_text':self.request})
        self.assertEqual(before, self.hub.monitor.registries[mid].budget())
        row = self.action('propose', {**preview, 'confirmed':True})
        self.assertEqual('awaiting_validation_review', row['status'], row)
        self.assertFalse(row['runtime_authorized'])
        visible = self.event(event['id'])['validation']
        self.assertEqual(row['id'], visible['id'])
        self.assertEqual(row['revision'], visible['revision'])
        self.assertEqual(1, len(self.model.requests))
        self.assertEqual(['propose_validation','read_source'],
                         sorted(t['function']['name'] for t in self.model.requests[0]['tools']))
        return row

    def accept(self, mid, event, row):
        return self.action('review', {**self.location(mid,event), 'revision':row['revision'], 'decision':'accept'})

    def prepare_validation(self, mid, event, accepted):
        self.action('prepare', {**self.location(mid,event), 'revision':accepted['revision']})
        current = self.event(event['id'])
        self.assertEqual('reproduced', current['root_preparation']['status'], current)
        self.assertEqual(accepted['contract'], current['root_preparation']['environment']['behavior_contract'])
        self.assertEqual(digest(self.request.encode()), accepted['contract']['request_sha256'])
        self.assertIn(event['root_preparation'], current['root_preparation_history'])
        self.assertNotIn('root_execution', current)
        return current

    def assert_candidate(self, mid, event, run_id, *, expected_tasks=2, expected_requests=3):
        session = self.session(mid,event)
        self.preserve_cleanup(session)
        record = json.loads((session.runs_dir/run_id/'run.json').read_text(encoding='utf-8'))
        self.assertEqual('candidate_ready', record['status'], record.get('error'))
        self.assertEqual(self.request, record['issue'])
        self.assertTrue(record['source_unchanged'])
        self.assertTrue(verify_pair(record, RepositoryGateway(session.repo), session.policy)['candidate_verified'])
        self.assertTrue(any(t['returncode'] != 0 for t in record['baseline_tests']))
        self.assertTrue(all(t['returncode'] == 0 for t in record['candidate_tests']))
        self.assertEqual(1, sum(is_behavior(t['command']) for t in record['candidate_tests']))
        registry = self.hub.monitor.registries[mid]
        self.assertEqual(expected_tasks, len(registry.load('pi_task')))
        self.assertEqual(expected_requests, registry.budget()['requests_used'])
        self.assertEqual(0, registry.budget()['requests_reserved_unknown'])
        self.assertEqual(registry.path, session.budget_registry.path)
        self.assertEqual(0, session.registry.budget()['requests_used'])
        return record

    def test_no_tests_review_original_red_baseline_and_auto_candidate_preserves_source(self):
        mid,event = self.register('auto', no_tests=True)
        before = self.git.snapshot()
        row = self.propose(mid,event)
        with self.assertRaises(SafetyError):self.repair_preview(mid,event)
        with self.assertRaises(SafetyError):
            self.action('prepare', {**self.location(mid,event), 'revision':row['revision']})
        accepted = self.accept(mid,event,row)
        current = self.prepare_validation(mid,event,accepted)
        self.assertEqual(1, len(self.model.requests))
        self.model.schema_calls = []
        preview = self.repair_preview(mid,current)
        self.assertEqual(self.request, preview['preview']['binding']['canonical_request'])
        result = self.execute(preview)
        self.assertEqual('candidate_ready', result['status'], result)
        session = self.session(mid,current)
        record = self.assert_candidate(mid,current,next(reversed(session.runs)))
        self.assertEqual(1, len(record['candidate_tests']))
        self.assertEqual(before, self.git.snapshot())

    def test_manual_new_plan_contains_behavior_context_before_separate_approval(self):
        mid,event = self.register(no_tests=True)
        accepted = self.accept(mid,event,self.propose(mid,event))
        current = self.prepare_validation(mid,event,accepted)
        self.model.schema_calls = []
        result = self.execute(self.repair_preview(mid,current))
        self.assertEqual('awaiting_approval', result['status'], result)
        session = self.session(mid,current)
        self.assertEqual({}, session.runs)
        plan = next(iter(session.plans.values()))['view']
        self.assertIsNone(session.provider.inner._behavior_contract)
        self.assertIsNone(session.provider.inner._behavior_source)
        user = next(m for m in self.model.requests[1]['messages'] if m['role']=='user')['content']
        packet = json.loads(user if isinstance(user,str) else user[0]['text'])
        self.assertEqual(accepted['contract']['contract_sha256'],
                         digest(packet['behavior_context']['body_json'].encode()))
        self.assertIn('requested_sum', packet['behavior_context']['body_json'])
        outcome = session._operate('decision', {'plan_id':plan['id'], 'fingerprint':plan['fingerprint'], 'decision':'approve'})
        self.assertEqual('candidate_ready', outcome['workflow_status'], outcome)
        self.assert_candidate(mid,current,outcome['run_id'])
        self.assertEqual(3, len(self.model.requests))

    def test_green_existing_tests_missing_behavior_preserves_all_regressions(self):
        self.request = 'bounded_add(2,3) must return 5. Keep existing add behavior.'
        mid,event = self.register('auto', green=True)
        old_checks = copy.deepcopy(event['root_preparation']['environment']['checks'])
        before = self.git.snapshot()
        accepted = self.accept(mid,event,self.propose(mid,event,'bounded_add',True))
        current = self.prepare_validation(mid,event,accepted)
        checks = current['root_preparation']['environment']['checks']
        for check in old_checks:self.assertIn(check, checks)
        self.assertEqual(len(old_checks)+1, len(checks))
        self.assertEqual(['unittest'], self.hub.monitor.root_rules.current(mid)['settings']['check_families'])
        self.model.schema_calls = [('read_source', {'path':'calculator.py'}), ('propose_repair', {
            'diagnosis':'Provide the requested missing public function', 'risk':'fixture only',
            'edits':[{'path':'calculator.py', 'replacements':[{'oldText':'return a + b',
                'newText':'return a + b\n\ndef bounded_add(a, b):\n    return a + b'}]}]})]
        result = self.execute(self.repair_preview(mid,current))
        self.assertEqual('candidate_ready', result['status'], result)
        session = self.session(mid,current)
        record = self.assert_candidate(mid,current,next(reversed(session.runs)))
        self.assertEqual(2, len(record['candidate_tests']))
        self.assertEqual(before, self.git.snapshot())

    def test_four_original_test_files_keep_unrelated_red_regression_in_candidate(self):
        self.request = 'bounded_add(2,3) must return 5. Keep every existing regression check.'
        for name, assertion in (
            ('test_a_regression.py','self.assertTrue(True)'),
            ('test_b_regression.py','self.assertEqual(4,4)'),
            ('test_z_unrelated.py',"self.assertEqual(7,8,'UNRELATED_REGRESSION_MUST_REMAIN')")):
            (self.repo/'tests'/name).write_text(
                'import unittest\n\nclass RegressionTests(unittest.TestCase):\n'
                '    def test_retained(self):\n        '+assertion+'\n',encoding='utf-8')
        self.git.command('-c','core.longpaths=true','add','tests')
        self.git.command('-c','core.longpaths=true','commit','-m','four original regression test files')
        # Register commits calculator.py last, so task-oriented source discovery
        # would select its related tests and omit the unrelated failing file.
        mid,event = self.register('auto',green=True,expected_status='reproduced')
        self.assertEqual('reproduced',event['root_preparation']['status'])
        old_checks = copy.deepcopy(event['root_preparation']['environment']['checks'])
        old_results = event['root_preparation']['baseline']['checks']
        self.assertIn('UNRELATED_REGRESSION_MUST_REMAIN',json.dumps(old_results))
        self.assertEqual(4,len(list((self.repo/'tests').glob('test_*.py'))))
        before = self.git.snapshot()
        accepted = self.accept(mid,event,self.propose(mid,event,'bounded_add',True))
        current = self.prepare_validation(mid,event,accepted)
        checks = current['root_preparation']['environment']['checks']
        for check in old_checks:self.assertIn(check,checks)
        self.assertEqual(len(old_checks)+1,len(checks))
        self.model.schema_calls = [('read_source',{'path':'calculator.py'}),('propose_repair',{
            'diagnosis':'Implement only the explicitly requested new function', 'risk':'fixture only',
            'edits':[{'path':'calculator.py','replacements':[{'oldText':'return a + b',
                'newText':'return a + b\n\ndef bounded_add(a, b):\n    return a + b'}]}]})]
        preview = self.worker(self.hub.monitor.execution.preview,
            {**self.location(mid,event),'max_attempts':1})
        outcome = self.execute(preview)
        self.assertEqual('failed_no_replay',outcome['status'],outcome)
        session = self.session(mid,current)
        self.preserve_cleanup(session)
        run_id = next(reversed(session.runs))
        record = json.loads((session.runs_dir/run_id/'run.json').read_text(encoding='utf-8'))
        self.assertEqual('rolled_back',record['status'],record.get('error'))
        ordinary = [c for c in record['candidate_tests'] if not is_behavior(c['command'])]
        behavior = [c for c in record['candidate_tests'] if is_behavior(c['command'])]
        self.assertEqual(1,len(behavior))
        self.assertEqual(0,behavior[0]['returncode'])
        self.assertTrue(any(c['returncode']!=0 for c in ordinary))
        self.assertIn('UNRELATED_REGRESSION_MUST_REMAIN',json.dumps(ordinary))
        self.assertTrue(record['source_unchanged'])
        self.assertEqual(before,self.git.snapshot())

    def test_unknown_one_held_with_remaining_budget_blocks_before_new_native(self):
        mid,event = self.register(no_tests=True)
        location = self.location(mid,event)
        preview = self.action('preview', {**location, 'request_text':self.request})
        registry = self.hub.monitor.registries[mid]
        self.worker(registry.reserve, 1)
        before = registry.budget()
        self.assertEqual(1, before['requests_reserved_unknown'])
        self.assertGreater(before['requests_remaining'], 0)
        with self.assertRaises(SafetyError):self.action('propose', {**preview, 'confirmed':True})
        self.assertEqual(before, registry.budget())
        self.assertEqual({}, registry.load('pi_task'))
        self.assertEqual({}, registry.load('validation_proposal'))
        self.assertEqual([], self.model.requests)

    def test_http_preview_requires_original_auth_origin_and_uses_original_queue(self):
        mid,event = self.register(no_tests=True)
        registry = self.hub.monitor.registries[mid]
        budget = registry.budget()
        with TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345') as client:
            data = {**self.location(mid,event),'request_text':self.request,'request_id':uuid.uuid4().hex}
            route = '/api/actions/monitor_validation_preview'
            self.assertEqual(401,client.post(route,json=data).status_code)
            headers = {'Authorization':'Bearer '+self.hub.token}
            self.assertEqual(403,client.post(route,json=data,headers={**headers,'Origin':'https://outside.invalid'}).status_code)
            self.assertEqual(202,client.post(route,json=data,headers=headers).status_code)
            self.worker(lambda:None)
            job = self.hub.jobs[data['request_id']]
            self.assertEqual('finished',job['status'],job)
            self.assertEqual(self.request,job['result']['preview']['request_text'])
            self.assertFalse(job['result']['preview']['phase']['runtime_authorized'])
        self.assertEqual(budget,registry.budget())
        self.assertEqual({},registry.load('pi_task'))
        self.assertEqual([],self.model.requests)

    def test_original_review_revision_and_contract_cannot_be_replaced_by_browser(self):
        mid,event = self.register(no_tests=True)
        row = self.propose(mid,event)
        location = self.location(mid,event)
        with self.assertRaises(SafetyError):
            self.action('review',{**location,'revision':'0'*64,'decision':'accept'})
        with self.assertRaises(SafetyError):
            self.action('review',{**location,'revision':row['revision'],'decision':'accept','contract':row['contract']})
        accepted = self.accept(mid,event,row)
        with self.assertRaises(SafetyError):self.accept(mid,event,row)
        with self.assertRaises(SafetyError):
            self.action('preview',{**location,'request_text':self.request+' Change the target requirement.'})
        registry = self.hub.monitor.registries[mid]
        original_row = copy.deepcopy(registry.load('validation_proposal')[row['id']])
        changed = copy.deepcopy(original_row)
        changed['contract']['cases'][0]['expect']['value'] = 999
        registry.put('validation_proposal',row['id'],changed)
        with self.assertRaises(SafetyError):
            self.action('prepare',{**location,'revision':accepted['revision']})
        registry.put('validation_proposal',row['id'],original_row)
        self.assertEqual(1,len(self.model.requests))
        self.assertEqual({},self.hub.monitor.servers)
        self.assertEqual(0,registry.budget()['requests_reserved_unknown'])

    def test_preview_consent_request_source_report_config_and_rules_are_exact(self):
        mid,event = self.register(no_tests=True)
        preview = self.action('preview', {**self.location(mid,event), 'request_text':self.request})
        before = self.hub.monitor.registries[mid].budget()
        with self.assertRaises(SafetyError):self.action('propose', {**preview, 'confirmed':False})
        changed = copy.deepcopy(preview)
        changed['preview']['request_text'] += ' Also rewrite every file.'
        with self.assertRaises(SafetyError):self.action('propose', {**changed, 'confirmed':True})
        source = self.hub.monitor.root/mid/'events'/event['id']/'source'/'calculator.py'
        saved = source.read_bytes()
        source.write_bytes(saved+b'\n# concurrent source edit\n')
        with self.assertRaises(SafetyError):self.action('propose', {**preview, 'confirmed':True})
        source.write_bytes(saved)
        config = self.config.read_bytes()
        self.config.write_bytes(config+b'changed')
        with self.assertRaises(SafetyError):self.action('propose', {**preview, 'confirmed':True})
        self.config.write_bytes(config)
        registry = self.hub.monitor.registries[mid]
        saved_event = copy.deepcopy(registry.load('event')[event['id']])
        changed_event = copy.deepcopy(saved_event)
        changed_event['root_preparation']['report']['untrusted_new_field'] = 'altered original report'
        self.worker(self.hub.monitor._event,mid,changed_event)
        with self.assertRaises(SafetyError):self.action('propose', {**preview, 'confirmed':True})
        self.worker(self.hub.monitor._event,mid,saved_event)
        self.configure(allow_local_code=True, check_families=['unittest','pytest'])
        with self.assertRaises(SafetyError):self.action('propose', {**preview, 'confirmed':True})
        self.assertEqual(before, registry.budget())
        self.assertEqual({}, registry.load('pi_task'))
        self.assertEqual([], self.model.requests)

    def test_prepare_single_resume_and_restart_never_repeat_baseline(self):
        mid,event = self.register(no_tests=True)
        accepted = self.accept(mid,event,self.propose(mid,event))
        self.prepare_validation(mid,event,accepted)
        current = copy.deepcopy(self.event(event['id']))
        budget = self.hub.monitor.registries[mid].budget()
        with patch('repo_maintainer.monitor_preparation.collect_environment_baseline',
                   side_effect=AssertionError('Original validation baseline must not replay')):
            with self.assertRaises(SafetyError):
                self.action('prepare', {**self.location(mid,event), 'revision':accepted['revision']})
            self.worker(self.hub.monitor.poll,mid)
            self.hub.close();self.hub = self.new_hub();self.hub.monitor.stop()
            self.worker(self.hub.monitor.poll,mid)
            fresh = self.event(event['id'])['validation']
            with self.assertRaises(SafetyError):
                self.action('prepare', {**self.location(mid,event), 'revision':fresh['revision']})
        self.assertEqual(current['root_preparation'], self.event(event['id'])['root_preparation'])
        self.assertEqual(current['root_preparation_history'], self.event(event['id'])['root_preparation_history'])
        self.assertEqual(budget, self.hub.monitor.registries[mid].budget())
        self.assertEqual(1, len(self.model.requests))

    def test_failed_prepare_intent_is_preserved_and_not_replayed_after_restart(self):
        mid,event = self.register(no_tests=True)
        accepted = self.accept(mid,event,self.propose(mid,event))
        original_prep = copy.deepcopy(event['root_preparation'])
        with patch('repo_maintainer.monitor_preparation.collect_environment_baseline',
                   side_effect=SafetyError('Synthetic original baseline failure')) as run:
            try:self.action('prepare', {**self.location(mid,event), 'revision':accepted['revision']})
            except SafetyError:pass
            self.assertEqual(1, run.call_count)
        current = copy.deepcopy(self.event(event['id']))
        self.assertIn(original_prep, current['root_preparation_history'])
        self.assertNotEqual('reproduced', current['root_preparation']['status'])
        self.hub.close();self.hub = self.new_hub();self.hub.monitor.stop()
        with patch('repo_maintainer.monitor_preparation.collect_environment_baseline',
                   side_effect=AssertionError('Failed validation preparation must not replay')):
            fresh = self.event(event['id'])['validation']
            with self.assertRaises(SafetyError):
                self.action('prepare', {**self.location(mid,event), 'revision':fresh['revision']})
            self.worker(self.hub.monitor.poll,mid)
        latest = self.event(event['id'])['root_preparation']
        self.assertEqual('interrupted_no_replay', latest['status'])
        self.assertEqual({k:v for k,v in current['root_preparation'].items() if k!='status'},
                         {k:v for k,v in latest.items() if k!='status'})
        self.assertEqual(1, len(self.model.requests))

    def test_late_ci_cannot_drop_existing_validation_and_automatically_reprepare(self):
        self.request = 'bounded_add(2,3) must return 5. Keep existing add behavior.'
        mid,event = self.register(green=True,future=True)
        self.assertIn('model_observation',event)
        accepted = self.accept(mid,event,self.propose(mid,event,'bounded_add',True))
        current = self.prepare_validation(mid,event,accepted)
        registry = self.hub.monitor.registries[mid]
        saved = copy.deepcopy(registry.load('event')[event['id']])
        # The old CI branch already knows how to reproduce a later failure. A
        # validation-bound event must be held before that old preparation path,
        # even under an otherwise valid original future-event authorization.
        saved['root_late_ci'] = {'status':'investigated',
            'root_rules_sha256':self.hub.monitor.root_rules.current(mid)['sha256'], 'result':{}}
        self.worker(self.hub.monitor._event,mid,saved)
        budget = registry.budget()
        with patch('repo_maintainer.monitor_preparation.prepare_monitor',
                   side_effect=AssertionError('Do not drop the accepted behavior contract')) as prepare:
            self.worker(self.hub.monitor._root_ci_followup,mid,saved,self.hub.monitor.root_rules.current(mid))
            self.assertEqual(0,prepare.call_count)
        latest = self.event(event['id'])
        self.assertEqual(current['root_preparation'], latest['root_preparation'])
        self.assertEqual(current['root_preparation_history'], latest['root_preparation_history'])
        self.assertEqual(accepted['contract'], latest['root_preparation']['environment']['behavior_contract'])
        self.assertNotIn('root_ci_followup',latest)
        self.assertNotIn('root_execution',latest)
        self.assertEqual(budget,registry.budget())
        self.assertEqual(1,len(self.model.requests))

    def test_exact_stop_cancels_inflight_proposal_and_preserves_original_unknown(self):
        mid,event = self.register(no_tests=True)
        self.model.schema_calls = [('propose_validation',self.proposal())]
        value = self.action('preview',{**self.location(mid,event),'request_text':self.request})
        entered,released = threading.Event(),threading.Event()
        self.model.gate = entered,released
        job_id = uuid.uuid4().hex
        self.hub.submit(job_id,'monitor_validation_propose',{**value,'confirmed':True})
        try:
            self.assertTrue(entered.wait(60),'Original Pi validation model request did not start')
            active = self.event(event['id'])['validation']
            self.assertEqual('started',active['status'])
            location = self.location(mid,event)
            with self.assertRaises(SafetyError):
                self.hub.submit(uuid.uuid4().hex,'monitor_validation_stop',{**location,'revision':'0'*64})
            stop_id = uuid.uuid4().hex
            stopped = self.hub.submit(stop_id,'monitor_validation_stop',
                {**location,'revision':active.get('stop_revision',active['revision'])})
            self.assertEqual('finished',stopped['status'])
            self.assertEqual(stopped,self.hub.submit(stop_id,'monitor_validation_stop',
                {**location,'revision':active.get('stop_revision',active['revision'])}))
            # Do not let a complete response race the owned cancellation.
            self.worker(lambda:None)
        finally:
            released.set()
        registry = self.hub.monitor.registries[mid]
        budget = registry.budget()
        self.assertGreater(budget['requests_reserved_unknown'],0)
        self.assertEqual(8,budget['request_budget'])
        self.assertEqual(1,len(self.model.requests))
        self.assertNotEqual('awaiting_validation_review',self.event(event['id'])['validation']['status'])
        self.assertNotIn('root_execution',self.event(event['id']))
        self.hub.close();self.hub = self.new_hub();self.hub.monitor.stop()
        with self.assertRaises(SafetyError):
            self.action('propose',{**value,'confirmed':True})
        self.assertEqual(budget,self.hub.monitor.registries[mid].budget())
        self.assertEqual(1,len(self.model.requests))

    def test_stop_after_settlement_before_publication_cannot_publish_review(self):
        from repo_maintainer.validation_proposal import ValidationProposal
        mid,event = self.register(no_tests=True)
        self.model.schema_calls = [('propose_validation',self.proposal())]
        value = self.action('preview',{**self.location(mid,event),'request_text':self.request})
        entered,released = threading.Event(),threading.Event()
        operation_lock = self.hub.monitor.validation.lock
        @contextmanager
        def completion():
            entered.set()
            if not released.wait(60):raise AssertionError('Publication barrier timed out')
            with operation_lock:yield
        original_init = ValidationProposal.__init__
        def init(service,*args,**kwargs):
            original_init(service,*args,**kwargs)
            service.completion_guard = completion
        with patch.object(ValidationProposal,'__init__',init):
            job_id = uuid.uuid4().hex
            self.hub.submit(job_id,'monitor_validation_propose',{**value,'confirmed':True})
            try:
                self.assertTrue(entered.wait(60),'Durable proposal did not reach final publication')
                registry = self.hub.monitor.registries[mid]
                budget = registry.budget()
                self.assertEqual(1,budget['requests_used'])
                self.assertEqual(0,budget['requests_reserved_unknown'])
                active = self.event(event['id'])['validation']
                self.assertEqual('started',active['status'])
                receipt = self.hub.submit(uuid.uuid4().hex,'monitor_validation_stop',
                    {**self.location(mid,event),'revision':active['stop_revision']})
                self.assertEqual('stop_requested',receipt['result']['status'])
            finally:released.set()
            self.worker(lambda:None)
        self.assertEqual('interrupted_no_replay',self.event(event['id'])['validation']['status'])
        self.assertEqual(budget,registry.budget())
        self.assertEqual(1,len(self.model.requests))

    def test_restart_marks_original_started_case_and_preparation_without_replay(self):
        mid,event = self.register(no_tests=True)
        row = self.propose(mid,event)
        registry = self.hub.monitor.registries[mid]
        saved = copy.deepcopy(registry.load('validation_proposal')[row['id']])
        saved['status'] = 'started'
        registry.put('validation_proposal',row['id'],saved)
        registry.put('monitor_validation_resume',event['id'],{'status':'started','proposal_id':row['id']})
        budget = registry.budget()
        self.hub.close();self.hub = self.new_hub();self.hub.monitor.stop()
        registry = self.hub.monitor.registries[mid]
        visible = self.event(event['id'])['validation']
        self.assertEqual('interrupted_no_replay',visible['status'])
        self.assertEqual('interrupted_no_replay',visible['resume']['status'])
        self.assertNotIn('stop_revision',visible)
        self.assertEqual(budget,registry.budget())
        self.assertEqual(1,len(self.model.requests))
        with self.assertRaises(SafetyError):
            self.hub.submit(uuid.uuid4().hex,'monitor_validation_stop',
                {**self.location(mid,event),'revision':visible['revision']})


if __name__ == '__main__':
    unittest.main()
