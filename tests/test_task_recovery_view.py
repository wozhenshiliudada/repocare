"""T4 read-only projection, real Pi feedback and original authorization boundaries."""
import json
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
import test_task_feedback as feedback
import test_task_planning as planning
import test_task_planning_sources as sources
import test_feedback_cleanup as cleanup
from repo_maintainer.demo import create_fixture
from repo_maintainer.local_ui import UISession,create_app
from repo_maintainer.models import SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.task_feedback import KIND


class RecoveryCardTests(unittest.TestCase):
    setUp=feedback.TaskFeedbackTests.setUp
    open=feedback.TaskFeedbackTests.open
    restart=feedback.TaskFeedbackTests.restart
    plan=feedback.TaskFeedbackTests.plan
    decision=feedback.TaskFeedbackTests.decision
    session_file=feedback.TaskFeedbackTests.session_file
    failed=feedback.TaskFeedbackTests.failed
    cleanup=cleanup.FailureCleanupTests.cleanup

    def record(self):
        # Reopening must not bind a live planning identity on GET. The fixture owns exactly one task.
        tasks=self.session.registry.load('pi_task')
        self.assertEqual(1,len(tasks))
        return next(iter(tasks.values()))

    def card(self,kind,identity):
        return next(c for c in self.session.state()['recovery_cards'] if c['kind']==kind and c['id']==identity)

    def snapshot(self):
        return (self.session.registry.path.read_bytes(), self.session_file().read_bytes(),
                {str(p):p.read_bytes() for p in (self.root/'state').glob('*/*.json')},len(self.model.requests))

    def test_restart_get_preserves_state_then_explicit_same_task_new_approval(self):
        run=self.failed();task_id=self.record()['task_id'];self.restart()
        before=self.snapshot()
        with TestClient(create_app(self.session,12345),base_url='http://127.0.0.1:12345') as client:
            for _ in range(3):
                response=client.get('/api/state',headers={'Authorization':'Bearer '+self.session.token})
                self.assertEqual(200,response.status_code)
                card=next(c for c in response.json()['recovery_cards'] if c['kind']=='run')
                self.assertEqual('available',card['status']);self.assertEqual(task_id,card['task']['id'])
                self.assertEqual(1,card['task']['completed_rounds'])
                self.assertEqual('verified',card['latest_failure']['status'])
                self.assertEqual([1],card['latest_failure']['test_exits'])
                self.assertEqual(['approval'],[a['mode'] for a in card['actions']])
                self.assertFalse(card['compression_enabled'])
        self.assertEqual(before,self.snapshot())
        operation=card['actions'][0]
        new=self.session._operate('resume_run',{'run_id':run['run_id'],'revision':operation['revision'],'mode':'approval'})
        pending=self.card('plan',new['plan_id'])
        self.assertEqual(task_id,pending['task']['id']);self.assertEqual(2,pending['task']['completed_rounds'])
        self.assertEqual('review_plan',pending['status']);self.assertTrue(pending['new_approval_required'])
        self.assertEqual('candidate_ready',self.decision(new,'approve')['workflow_status'])
        self.assertEqual(4,len(self.model.requests));self.assertEqual(4,self.session.registry.budget()['requests_used'])

    def test_source_native_provider_and_stale_challenge_block_without_consumption(self):
        run=self.failed();old=self.card('run',run['run_id'])['actions'][0];self.restart()
        with self.assertRaises(SafetyError):
            self.session._operate('resume_run',{'run_id':run['run_id'],'revision':old['revision'],'mode':'approval'})
        source=self.root/'source/calculator.py';native=self.session_file()
        for file in (source,native):
            raw=file.read_bytes();file.write_bytes(raw+b' invalid')
            try:
                before=self.session.registry.budget();card=self.card('run',run['run_id'])
                self.assertNotEqual('available',card['status']);self.assertFalse(card['actions'])
                self.assertEqual(before,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
            finally:file.write_bytes(raw)
        url=self.inner.mock_url;self.inner.mock_url='http://127.0.0.1:1/changed'
        try:
            card=self.card('run',run['run_id']);self.assertEqual('blocked',card['task']['status'])
            self.assertIsNone(card['task']['completed_rounds']);self.assertFalse(card['actions'])
        finally:self.inner.mock_url=url
        new=self.card('run',run['run_id'])['actions'][0]
        with self.assertRaises(SafetyError):
            self.session._operate('resume_run',{'run_id':run['run_id'],'revision':new['revision'],'mode':'auto'})
        self.assertEqual(2,len(self.model.requests))

    def test_malformed_binding_and_issue_are_readonly_not_server_errors(self):
        run=self.failed()
        original_plan=next(iter(self.session.registry.load('plan')))
        self.assertEqual('plan_completed',self.card('plan',original_plan)['status'])
        identity='run:'+run['run_id'];link=self.session.registry.load('pi_task_link')[identity]
        self.session.registry.put('pi_task_link',identity,{'task_id':[]})
        try:
            before=self.session.registry.path.read_bytes()
            with TestClient(create_app(self.session,12345),base_url='http://127.0.0.1:12345') as client:
                result=client.get('/api/state',headers={'Authorization':'Bearer '+self.session.token})
                self.assertEqual(200,result.status_code)
                card=next(c for c in result.json()['recovery_cards'] if c['kind']=='run')
                self.assertEqual('blocked',card['status']);self.assertFalse(card['actions'])
            self.assertEqual(before,self.session.registry.path.read_bytes())
        finally:self.session.registry.put('pi_task_link',identity,link)
        path=self.root/'state'/run['run_id']/'run.json';raw=path.read_bytes()
        value=json.loads(raw);value['issue']=['corrupt original metadata'];path.write_text(json.dumps(value))
        try:
            before=self.session.registry.path.read_bytes();card=self.card('run',run['run_id'])
            self.assertEqual('blocked',card['status']);self.assertFalse(card['actions'])
            self.assertEqual(before,self.session.registry.path.read_bytes())
        finally:path.write_bytes(raw)
        self.assertEqual(2,len(self.model.requests));self.assertEqual(2,self.session.registry.budget()['requests_used'])

    def test_display_poll_does_not_compete_with_active_task_writer(self):
        run=self.failed();record=self.record();before=self.snapshot()
        with self.session.task_planner.store.lease(record['task_id'],record['scope'],record['revision']):
            self.session.active='owned-test-operation'
            try:
                # Real original lease is held by the operation, not a mocked validator.
                card=self.card('run',run['run_id'])
                self.assertEqual('busy',card['status']);self.assertEqual('busy',card['task']['status'])
                self.assertFalse(card['actions']);self.assertIsNone(card['latest_failure'])
                self.assertEqual(before,self.snapshot())
            finally:self.session.active=None
        self.assertEqual('available',self.card('run',run['run_id'])['status'])

    def test_unlinked_initial_attempt_unknown_is_visible_without_replay_or_fake_checkpoint(self):
        identity=self.session.task_planner.begin(planning.ISSUE)
        self.session.registry.reserve();self.restart()
        before=self.session.registry.path.read_bytes()
        card=self.card('task',identity)
        self.assertEqual('unknown',card['status']);self.assertEqual(identity,card['task']['id'])
        self.assertEqual(0,card['task']['completed_rounds']);self.assertEqual('missing',card['task']['status'])
        self.assertEqual(50,card['budget']['requests_reserved_unknown']);self.assertFalse(card['actions'])
        self.assertEqual(before,self.session.registry.path.read_bytes());self.assertFalse(self.model.requests)

    def test_feedback_and_cleanup_gap_offer_only_original_explicit_metadata_actions(self):
        run=self.failed()
        with self.session.registry.transaction() as db:db.execute('DELETE FROM objects WHERE kind=? AND id=?',(KIND,run['run_id']))
        before=self.snapshot();card=self.card('run',run['run_id'])
        self.assertEqual('feedback_pending',card['status']);self.assertEqual('sealed',card['latest_failure']['status'])
        self.assertEqual(['recover_feedback'],[a['action'] for a in card['actions']]);self.assertEqual(before,self.snapshot())
        self.session._operate('recover_feedback',{'run_id':run['run_id'],'revision':card['actions'][0]['revision']})
        with patch.object(self.session.feedback_cleanup,'finish',side_effect=SafetyError('controlled cleanup gap')):
            with self.assertRaises(SafetyError):self.cleanup(run)
        before=self.snapshot();card=self.card('run',run['run_id'])
        self.assertEqual('cleanup_pending',card['status'])
        self.assertEqual(['reconcile_feedback_cleanup'],[a['action'] for a in card['actions']])
        self.assertEqual(before,self.snapshot())
        self.session._operate('reconcile_feedback_cleanup',{'run_id':run['run_id'],'revision':card['actions'][0]['revision']})
        self.assertEqual('available',self.card('run',run['run_id'])['status'])
        self.assertEqual(2,len(self.model.requests));self.assertEqual(2,self.session.registry.budget()['requests_used'])

    def test_other_repository_cannot_read_or_authorize_this_task(self):
        run=self.failed();a=self.card('run',run['run_id'])
        beta=create_fixture(self.root/'beta')
        provider=PiProvider(('calculator.py',),self.model.url,('tests/test_calculator.py',),50)
        other=UISession(beta,self.session.policy,provider,self.root/'beta-state');self.addCleanup(other.close)
        with TestClient(create_app(other,12346),base_url='http://127.0.0.1:12346') as client:
            self.assertEqual(401,client.get('/api/state',headers={'Authorization':'Bearer '+self.session.token}).status_code)
            value=client.get('/api/state',headers={'Authorization':'Bearer '+other.token}).json()
            self.assertFalse(value['recovery_cards']);self.assertNotIn(a['task']['id'],json.dumps(value))
        with self.assertRaises(SafetyError):
            other._operate('resume_run',{'run_id':run['run_id'],'revision':a['actions'][0]['revision'],'mode':'approval'})
        self.assertEqual(0,other.registry.budget()['requests_used']);self.assertEqual(2,len(self.model.requests))


class RecoverySourceCardTests(unittest.TestCase):
    setUp=sources.NativeSourceTaskTests.setUp
    open=sources.NativeSourceTaskTests.open
    finish=sources.NativeSourceTaskTests.finish
    plan=sources.NativeSourceTaskTests.plan

    def test_required_document_and_graph_drift_are_visible_and_cannot_be_ignored(self):
        run=self.session._operate('decision',{**self.plan(),'decision':'reject'})
        for file in (self.rule,self.index/sources.structure.GRAPH):
            raw=file.read_bytes();file.write_bytes(raw+b' ')
            try:
                before=self.session.registry.path.read_bytes()
                card=next(c for c in self.session.state()['recovery_cards'] if c['kind']=='run')
                self.assertEqual('source_invalid',card['status']);self.assertFalse(card['actions'])
                self.assertIn('invalid',card['sources'].values())
                self.assertEqual(before,self.session.registry.path.read_bytes());self.assertEqual(2,len(self.model.requests))
            finally:file.write_bytes(raw)
