"""Real Pi: new task retrieval, review, provenance, budget and privilege boundaries."""
import copy
import json
import unittest
import uuid
from starlette.testclient import TestClient
import test_task_planning as fixture
from repo_maintainer.local_ui import create_app, validate_payload
from repo_maintainer.models import SafetyError
from repo_maintainer.experience_memory import KIND, PIN


class ExperienceTests(unittest.TestCase):
    setUp=fixture.TaskPlanningTests.setUp
    open=fixture.TaskPlanningTests.open
    restart=fixture.TaskPlanningTests.restart
    plan=fixture.TaskPlanningTests.plan
    decision=fixture.TaskPlanningTests.decision
    record=fixture.TaskPlanningTests.record

    def enable(self, value=True):
        view=self.session.state()['experience_memory']
        return self.session._operate('experience_settings',{'enabled':value,'revision':view['settings_revision']})

    def episode(self):
        self.model.scenario='repeat-failure'
        plan=self.plan();old=self.record()['task_id'];run=self.decision(plan,'approve')
        self.assertEqual('rolled_back',run['workflow_status'])
        episodes=self.session.registry.load(KIND)
        self.assertIn(run['run_id'],episodes,self.session.registry.load('experience_capture'))
        return run,old

    def accept(self, run, choice='accepted'):
        view=next(e for e in self.session.state()['experience_memory']['entries'] if e['id']==run['run_id'])
        return self.session._operate('experience_review',{'run_id':run['run_id'],
                    'revision':view['revision'],'review_status':choice})

    def envelope(self):
        messages=self.model.requests[-1]['messages']
        value=next(m for m in reversed(messages) if m['role']=='user')['content']
        return json.loads(value if isinstance(value,str) else value[0]['text'])

    def test_version3_quotes_actual_failure_and_legacy_versions_remain_valid_without_rewriting_review(self):
        from repo_maintainer.task_session import fingerprint
        run,_=self.episode();identity=run['run_id']
        path=self.root/'state'/identity/'run.json'
        record=json.loads(path.read_text(encoding='utf-8'))
        check=record['rounds'][0]['tests'][0]
        check['stderr']='warning without exception'
        check['stdout']=json.dumps({'metadata':'x'*7000,'failure_excerpt':
            'File "calculator.py", line 2\nE AttributeError: list has no attribute is_Mul'})
        path.write_text(json.dumps(record),encoding='utf-8')
        self.session.experience.capture(identity)
        entry=self.session.registry.load(KIND)[identity]
        self.assertEqual(3,entry['payload']['schema_version'])
        self.assertIn('AttributeError',entry['payload']['rounds'][0]['failure_excerpt'])
        self.assertNotIn('metadata',entry['payload']['rounds'][0]['failure_excerpt'])
        self.accept(run);self.enable();self.model.scenario='correct';self.plan()
        self.assertEqual(3,self.envelope()['repository_experience']['hits'][0]['episode']['schema_version'])
        for version in (1,2):
            entry=self.session.registry.load(KIND)[identity]
            entry['payload']=self.session.experience.original(identity,version=version)
            entry['payload_sha256']=fingerprint(entry['payload'])
            self.session.registry.put(KIND,identity,entry)
            before=self.session.registry.path.read_bytes()
            self.session.experience.validate(identity,entry)
            self.assertTrue(self.session.experience.capture(identity)['unchanged'])
            self.assertEqual(before,self.session.registry.path.read_bytes())
        entry['payload']['schema_version']=4
        entry['payload_sha256']=fingerprint(entry['payload'])
        with self.assertRaises(SafetyError):self.session.experience.validate(identity,entry)

    def test_new_task_receives_reviewed_failed_episode_after_restart_with_new_approval(self):
        run,old=self.episode();before=self.session.registry.budget();self.accept(run);self.enable()
        self.restart();self.assertEqual(before,self.session.registry.budget())
        self.model.scenario='correct';plan=self.plan();new=self.record()['task_id']
        self.assertNotEqual(old,new)
        episode=self.envelope()['repository_experience']['hits'][0]['episode']
        self.assertEqual(run['run_id'],episode['run_id']);self.assertEqual('test_failed',episode['outcome'])
        self.assertEqual([1],episode['rounds'][0]['test_exits']);self.assertFalse(episode['runtime_authorized'])
        self.assertNotIn('stdout',json.dumps(episode));self.assertNotIn('authorization',episode)
        self.assertEqual('awaiting_approval',self.session.state()['plans'][-1]['status'])
        self.assertEqual(4,self.session.registry.budget()['requests_used'])
        self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])
        self.assertTrue(self.session.state()['runs'][-1]['source_unchanged'])

    def test_pending_and_rejected_episodes_never_enter_new_task(self):
        run,_=self.episode();self.enable()
        self.plan();self.assertEqual([],self.envelope()['repository_experience']['hits'])
        self.accept(run,'rejected');self.plan()
        self.assertEqual([],self.envelope()['repository_experience']['hits'])
        self.assertEqual(6,len(self.model.requests))

    def test_readonly_state_search_stale_reviews_and_budget_preserve(self):
        run,_=self.episode();entry=self.session.state()['experience_memory']['entries'][0]
        before=self.session.registry.path.read_bytes();budget=self.session.registry.budget()
        with TestClient(create_app(self.session,12345),base_url='http://127.0.0.1:12345') as client:
            for _ in range(3):
                self.assertEqual(200,client.get('/api/state',headers={'Authorization':'Bearer '+self.session.token}).status_code)
        self.assertEqual(before,self.session.registry.path.read_bytes())
        self.assertFalse(self.session.experience.search(fixture.ISSUE)['hits'])
        self.accept(run)
        with self.assertRaises(SafetyError):
            self.session._operate('experience_review',{'run_id':run['run_id'],'revision':entry['revision'],'review_status':'rejected'})
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))

    def test_origin_drift_excluded_and_pinned_plan_approval_blocked(self):
        run,_=self.episode();self.accept(run);self.enable();self.model.scenario='correct';plan=self.plan()
        path=self.root/'state'/run['run_id']/'report.md';raw=path.read_bytes();path.write_bytes(raw+b' changed')
        try:
            before=self.session.registry.budget();result=self.session.experience.search(fixture.ISSUE)
            self.assertFalse(result['hits']);self.assertEqual(run['run_id'],result['excluded'][0]['id'])
            with self.assertRaises(SafetyError):self.decision(plan,'approve')
            self.assertEqual(before,self.session.registry.budget());self.assertEqual(4,len(self.model.requests))
        finally:path.write_bytes(raw)

    def test_revoking_acceptance_invalidates_pinned_plan_but_no_model_or_refund(self):
        run,_=self.episode();self.accept(run);self.enable();self.plan()
        current=self.session.state()['plans'][-1];self.accept(run,'rejected');before=self.session.registry.budget()
        with self.assertRaises(SafetyError):
            self.session._operate('decision',{'plan_id':current['id'],'fingerprint':current['fingerprint'],'decision':'approve'})
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(4,len(self.model.requests))

    def test_history_is_not_sufficient_to_read_secret_or_modify_tests(self):
        run,_=self.episode();self.accept(run);self.enable();self.model.scenario='injection'
        raw=(self.source/'tests/test_calculator.py').read_bytes();self.plan()
        self.assertTrue(self.envelope()['repository_experience']['hits'])
        messages=self.model.requests[-1]['messages']
        self.assertIn('Path not authorized for read',json.dumps(messages))
        self.assertIn('Edit path is read-only',json.dumps(messages))
        self.assertEqual(raw,(self.source/'tests/test_calculator.py').read_bytes())

    def test_original_task_is_not_implicitly_enriched_when_setting_changes(self):
        run,old=self.episode();self.accept(run);self.enable()
        preview=self.session._resume_preview(run['run_id']);self.model.scenario='correct'
        self.session._operate('resume_run',{'run_id':run['run_id'],'revision':preview['revision'],'mode':'approval'})
        self.assertEqual(old,self.record()['task_id']);self.assertNotIn('repository_experience',self.envelope())
        self.assertNotIn(old,self.session.registry.load(PIN))

    def test_cross_repository_origin_and_arbitrary_paths_cannot_be_submitted(self):
        run,_=self.episode();self.accept(run)
        from repo_maintainer.demo import create_fixture
        from repo_maintainer.pi_provider import PiProvider
        from repo_maintainer.local_ui import UISession
        other=UISession(create_fixture(self.root/'other'),self.session.policy,
            PiProvider(('calculator.py',),self.model.url,('tests/test_calculator.py',),50),self.root/'other-state')
        self.addCleanup(other.close)
        other.registry.put(KIND,run['run_id'],copy.deepcopy(self.session.registry.load(KIND)[run['run_id']]))
        self.assertFalse(other.experience.search(fixture.ISSUE)['hits'])
        self.assertEqual('invalid',other.experience.view()['entries'][0]['freshness'])
        with self.assertRaises(SafetyError):
            validate_payload('experience_capture',{'run_id':run['run_id'],'path':str(self.root),'request_id':uuid.uuid4().hex})
        self.assertEqual(0,other.registry.budget()['requests_used'])

    def test_passed_candidate_remains_historical_not_merged_or_success_rule(self):
        plan=self.plan();run=self.decision(plan,'approve')
        view=self.session.state()['experience_memory']['entries'][0]
        self.assertEqual('candidate_test_passed',view['outcome']);self.assertEqual('pending',view['review'])
        self.assertEqual([0],view['rounds'][0]['test_exits'])
        self.assertFalse(view['runtime_authorized'])

    def test_current_file_change_preserves_history_requires_revalidation_on_new_task(self):
        run,_=self.episode();self.accept(run);file=self.source/'calculator.py';raw=file.read_bytes()
        file.write_bytes(raw+b'# new source\n')
        try:
            before=self.session.registry.path.read_bytes();view=self.session.experience.view()['entries'][0]
            self.assertEqual('accepted',view['review']);self.assertEqual('verified',view['freshness'])
            self.assertEqual('revalidate_changed_files',view['applicability'])
            self.assertEqual('requires_revalidation',self.session.experience.search(fixture.ISSUE)['hits'][0]['applicability'])
            self.assertEqual(before,self.session.registry.path.read_bytes());self.assertEqual(2,len(self.model.requests))
            from repo_maintainer.git import RepositoryGateway
            repo=RepositoryGateway(self.source);repo.command('add','calculator.py')
            repo.command('-c','user.name=RepoCare fixture','-c','user.email=fixture@example.invalid',
                         'commit','-m','owned fixture source changed')
            self.enable();self.model.scenario='correct';self.plan()
            hit=self.envelope()['repository_experience']['hits'][0]
            self.assertEqual('requires_revalidation',hit['applicability']);self.assertEqual(run['run_id'],hit['id'])
        finally:file.write_bytes(raw)

    def test_missing_selection_and_revocation_disable_pending_plan_recovery_card(self):
        run,_=self.episode();self.accept(run);self.enable();plan=self.plan();task=self.record()['task_id']
        original=self.session.registry.load(PIN)[task]
        with self.session.registry.transaction() as db:db.execute('DELETE FROM objects WHERE kind=? AND id=?',(PIN,task))
        try:
            before=self.session.registry.budget()
            with self.assertRaises(SafetyError):self.decision(plan,'approve')
            card=next(c for c in self.session.state()['recovery_cards'] if c['kind']=='plan' and c['id']==plan['plan_id'])
            self.assertEqual('blocked',card['status']);self.assertFalse(card['actions'])
            self.assertEqual(before,self.session.registry.budget())
        finally:self.session.registry.put(PIN,task,original)
        self.accept(run,'rejected')
        card=next(c for c in self.session.state()['recovery_cards'] if c['kind']=='plan' and c['id']==plan['plan_id'])
        self.assertEqual('blocked',card['status']);self.assertEqual(4,len(self.model.requests))

    def test_evidence_change_during_inference_retains_unknown_and_uncommitted_turn(self):
        run,_=self.episode();self.accept(run);self.enable()
        path=self.root/'state'/run['run_id']/'report.md';raw=path.read_bytes()
        def change(body,count):
            if count==3:path.write_bytes(raw+b' drift during inference')
        self.model.on_request=change
        try:
            with self.assertRaises(SafetyError):self.plan()
            self.assertEqual(0,self.record()['revision'])
            budget=self.session.registry.budget();self.assertEqual(2,budget['requests_used'])
            self.assertEqual(48,budget['requests_reserved_unknown'])
        finally:path.write_bytes(raw);self.model.on_request=None

    def test_memory_cannot_replenish_exhausted_budget(self):
        run,_=self.episode();self.accept(run);self.enable()
        with self.session.registry.transaction() as db:db.execute('UPDATE budget SET used=50')
        before=self.session.registry.budget()
        self.assertTrue(self.session.experience.search(fixture.ISSUE)['hits'])
        with self.assertRaises(SafetyError):self.plan()
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))

    def test_cancelled_plan_has_no_fabricated_verification(self):
        plan=self.plan();run=self.decision(plan,'reject');view=self.session.state()['experience_memory']['entries'][0]
        self.assertEqual(run['run_id'],view['id']);self.assertEqual('not_verified',view['outcome'])
        self.assertEqual([],view['rounds'][0]['test_exits']);self.assertEqual('pending',view['review'])

    def test_rehashed_false_projection_is_excluded_before_model_reservation(self):
        run,_=self.episode();self.accept(run)
        entry=self.session.registry.load(KIND)[run['run_id']]
        entry['payload']['rounds'][0]['diagnosis']='Invented success from an unverified memory edit'
        from repo_maintainer.task_session import fingerprint
        entry['payload_sha256']=fingerprint(entry['payload'])
        self.session.registry.put(KIND,run['run_id'],entry)
        before=self.session.registry.budget();result=self.session.experience.search(fixture.ISSUE)
        self.assertFalse(result['hits']);self.assertEqual(run['run_id'],result['excluded'][0]['id'])
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
