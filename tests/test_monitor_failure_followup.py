"""Same-SHA CI successors use the original monitor account and synthetic CI only."""
import copy
from datetime import datetime, timezone
import json
import time
import unittest
import uuid

import test_ci_repair as fixture
from starlette.testclient import TestClient
from test_ci_evidence import ATTEMPT, JOB, JOBS
from repo_maintainer.ci_transport import CiJsonPage
from repo_maintainer.models import SafetyError
from repo_maintainer.repository_workbench import create_workbench_app


class MonitorFailureFollowupTests(unittest.TestCase):
    # Reuse the original real-Git, local Pi and synthetic CI transport fixture.
    setUp = fixture.CiOriginalRepairTests.setUp
    setUpBase = fixture.CiOriginalRepairTests.setUpBase
    new_hub = fixture.CiOriginalRepairTests.new_hub
    finish = fixture.CiOriginalRepairTests.finish
    worker = fixture.CiOriginalRepairTests.worker
    scope = fixture.CiOriginalRepairTests.scope
    view = fixture.CiOriginalRepairTests.view
    payload = fixture.CiOriginalRepairTests.payload
    preview = fixture.CiOriginalRepairTests.preview
    configure = fixture.CiOriginalRepairTests.configure
    session = fixture.CiOriginalRepairTests.session
    preserve_cleanup = fixture.CiOriginalRepairTests.preserve_cleanup
    grant_preview = fixture.CiOriginalRepairTests.grant_preview
    grant = fixture.CiOriginalRepairTests.grant
    commit = fixture.CiOriginalRepairTests.commit
    prepare_ci = fixture.CiOriginalRepairTests.prepare_ci
    ci_transport = fixture.CiOriginalRepairTests.ci_transport
    poll = fixture.CiOriginalRepairTests.poll

    def parent(self, mode='auto'):
        mid = self.prepare_ci(mode)
        parent = self.poll(mid)
        session = self.session(mid, parent)
        if mode == 'approval':
            self.assertEqual(parent['root_execution']['status'], 'awaiting_approval', parent)
            plan = next(iter(session.plans.values()))['view']
            outcome = session._operate('decision', {'plan_id': plan['id'],
                'fingerprint': plan['fingerprint'], 'decision': 'approve'})
            self.assertEqual(outcome['workflow_status'], 'candidate_ready')
            rid = outcome['run_id']
        else:
            self.assertEqual(parent['root_execution']['status'], 'candidate_ready', parent)
            rid = parent['root_execution']['run_id']
        self.preserve_cleanup(session)
        registry = self.hub.monitor.registries[mid]
        report = session.runs_dir / rid / 'run.json'
        return mid, parent, registry, report, report.read_bytes()

    def authorize(self, mid, parent, maximum=2):
        manager = self.hub.monitor.failure_followup
        expires = datetime.fromtimestamp(time.time()+1800, timezone.utc).isoformat(timespec='seconds')
        p = self.worker(manager.preview, {'monitor_id': mid, 'event_id': parent['id'],
                                          'max_successors': maximum, 'expires_at': expires})
        self.worker(manager.configure, {**p, 'confirmed': True})
        grants = manager.state(mid)
        self.assertEqual(len(grants), 1, grants)
        return grants[0]

    def next_attempt(self, *, changed=True, conclusion='failure'):
        self.rows[0].update(run_attempt=2, conclusion=conclusion)
        run = copy.deepcopy(self.transport.pages[ATTEMPT].data)
        run.update(run_attempt=2, conclusion=conclusion)
        self.transport.pages[ATTEMPT[:-1]+'2'] = CiJsonPage(run)
        listing = copy.deepcopy(self.transport.pages[JOBS+'1'].data)
        listing['jobs'][0]['run_attempt'] = 2
        detail = copy.deepcopy(self.transport.pages[JOB].data)
        detail['run_attempt'] = 2
        if changed:
            for job in (listing['jobs'][0], detail):
                job['steps'][0]['name'] += ' new failure detail'
            self.transport.logs[JOB+'/logs'] += b'new failure detail\n'
        self.transport.pages[JOBS.replace('/attempts/1/', '/attempts/2/')+'1'] = CiJsonPage(listing)
        self.transport.pages[JOB] = CiJsonPage(detail)

    def dispatch(self, mid, parent):
        with self.ci_transport():
            return self.worker(self.hub.monitor.failure_followup.dispatch, mid, parent['sha'])

    def test_new_attempt_requires_explicit_grant_and_runs_only_once_in_original_account(self):
        mid, parent, registry, report, before = self.parent()
        old_execution = copy.deepcopy(parent['root_execution'])
        old_tasks = set(registry.load('pi_task'))
        old_budget = registry.budget()
        self.next_attempt()
        self.dispatch(mid, parent)  # The existing future-event grant is not successor consent.
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), old_budget)
        self.authorize(mid, parent)
        self.dispatch(mid, parent)
        claims = registry.load('monitor_failure_claim')
        self.assertEqual(len(claims), 1, claims)
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(registry.budget()['requests_used'], old_budget['requests_used']+2)
        self.assertEqual(registry.budget()['request_budget'], old_budget['request_budget'])
        self.assertEqual(len(set(registry.load('pi_task'))-old_tasks), 1)
        self.assertEqual(report.read_bytes(), before)
        self.assertEqual(registry.load('event')[parent['id']]['root_execution'], old_execution)
        children = self.hub.monitor.failure_followup.state(mid)[0]['children']
        self.assertEqual(len(children), 1, children)
        self.assertNotEqual(children[0]['event_id'], parent['id'])
        child = registry.load('event')[children[0]['event_id']]
        self.assertEqual(child['failure_successor']['parent_event_id'], parent['id'])
        self.assertEqual(child['failure_successor']['parent_run_id'], parent['root_execution']['run_id'])
        self.assertEqual(child['sha'], parent['sha'])
        self.assertEqual(child['root_execution']['binding']['ci_failure']['attempt'], 2)
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(len(registry.load('monitor_failure_claim')), 1)

    def test_same_failure_on_new_attempt_stops_without_model_or_rewriting_parent(self):
        mid, parent, registry, report, before = self.parent()
        self.authorize(mid, parent)
        budget = registry.budget()
        self.next_attempt(changed=False)
        self.dispatch(mid, parent)
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(report.read_bytes(), before)
        grants = self.hub.monitor.failure_followup.state(mid)
        self.assertEqual(grants[0]['status'], 'blocked', grants)
        self.assertIn('没有进展', grants[0]['error'])
        claims = registry.load('monitor_failure_claim')
        self.assertEqual(len(claims), 1, claims)
        self.assertEqual(next(iter(claims.values()))['status'], 'blocked_no_replay')
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.failure_followup.control, 'monitor_failure_pause',
                        {'monitor_id': mid, 'grant_id': grants[0]['id'], 'revision': grants[0]['revision']})

    def test_cancelled_attempt_never_starts_model(self):
        mid, parent, registry, report, before = self.parent()
        self.authorize(mid, parent)
        budget = registry.budget()
        self.next_attempt(conclusion='cancelled')
        self.dispatch(mid, parent)
        self.assertEqual(self.hub.monitor.failure_followup.state(mid)[0]['status'], 'blocked')
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(report.read_bytes(), before)

    def test_foreign_source_never_starts_model(self):
        mid, parent, registry, report, before = self.parent()
        self.authorize(mid, parent)
        budget = registry.budget()
        self.next_attempt()
        self.rows[0].update(conclusion='failure', repository={'id': 999, 'full_name': 'elsewhere/repo'})
        self.dispatch(mid, parent)
        self.assertEqual(self.hub.monitor.failure_followup.state(mid)[0]['status'], 'blocked')
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(report.read_bytes(), before)

    def test_different_run_id_without_proven_order_blocks_before_claim_or_model(self):
        mid, parent, registry, report, before = self.parent()
        self.authorize(mid, parent)
        budget = registry.budget()
        # Even when the list places this failed run first, a different run ID
        # alone cannot establish that it is newer than the original run.
        self.rows[0].update(id=102, run_attempt=1, conclusion='failure')
        self.dispatch(mid, parent)
        grant = self.hub.monitor.failure_followup.state(mid)[0]
        self.assertEqual(grant['status'], 'blocked', grant)
        self.assertTrue(grant['error'], grant)
        self.assertEqual(registry.load('monitor_failure_claim'), {})
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(report.read_bytes(), before)

    def test_unpublished_child_stops_before_attempt_three_without_another_claim(self):
        mid, parent, registry, report, before = self.parent()
        self.authorize(mid, parent)
        self.next_attempt()
        self.dispatch(mid, parent)
        grants = self.hub.monitor.failure_followup.state(mid)
        self.assertEqual(len(grants[0]['children']), 1, grants)
        child_id = grants[0]['children'][0]['event_id']
        child = registry.load('event')[child_id]
        self.assertEqual(child['root_execution']['status'], 'candidate_ready', child)
        child_execution = copy.deepcopy(child['root_execution'])
        claims = copy.deepcopy(registry.load('monitor_failure_claim'))
        budget = registry.budget()
        self.rows[0]['run_attempt'] = 3
        self.dispatch(mid, parent)
        grant = self.hub.monitor.failure_followup.state(mid)[0]
        self.assertEqual(grant['status'], 'blocked', grant)
        self.assertTrue(grant['error'], grant)
        self.assertEqual(registry.load('monitor_failure_claim'), claims)
        self.assertEqual(registry.load('event')[child_id]['root_execution'], child_execution)
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(report.read_bytes(), before)

    def test_unknown_original_consumption_blocks_successor(self):
        mid, parent, registry, report, before = self.parent()
        self.authorize(mid, parent)
        self.next_attempt()
        self.worker(registry.reserve)
        budget = registry.budget()
        self.assertGreater(budget['requests_reserved_unknown'], 0)
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(report.read_bytes(), before)

    def test_revoked_original_model_permission_blocks_successor(self):
        mid, parent, registry, report, before = self.parent()
        self.authorize(mid, parent)
        original = self.hub.monitor.model_authorization.current(mid)
        self.worker(self.hub.monitor.model_authorization.control, 'monitor_model_revoke',
                    {'monitor_id': mid, 'grant_id': original['id']}, uuid.uuid4().hex)
        self.next_attempt()
        budget = registry.budget()
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(registry.load('monitor_failure_claim'), {})
        self.assertFalse(self.hub.monitor.failure_followup.state(mid)[0]['active'])
        self.assertEqual(report.read_bytes(), before)

    def test_pause_and_service_restart_do_not_launch_or_replay_a_new_attempt(self):
        mid, parent, registry, report, before = self.parent()
        grant = self.authorize(mid, parent)
        manager = self.hub.monitor.failure_followup
        self.worker(manager.control, 'monitor_failure_pause',
                    {'monitor_id': mid, 'grant_id': grant['id'], 'revision': grant['revision']})
        self.next_attempt()
        budget = registry.budget()
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(registry.load('monitor_failure_claim'), {})
        paused = manager.state(mid)[0]
        self.worker(manager.control, 'monitor_failure_resume',
                    {'monitor_id': mid, 'grant_id': grant['id'], 'revision': paused['revision']})
        connection = self.hub.github
        self.hub.close()
        self.hub = self.new_hub()
        self.hub.monitor.stop()
        self.hub.github = connection
        self.assertEqual(self.hub.monitor.failure_followup.state(mid)[0]['status'], 'restart_paused')
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(self.hub.monitor.registries[mid].budget(), budget)
        self.assertEqual(report.read_bytes(), before)

    def test_stale_or_unconfirmed_successor_consent_does_not_create_authority(self):
        mid, parent, registry, report, before = self.parent()
        manager = self.hub.monitor.failure_followup
        expires = datetime.fromtimestamp(time.time()+1800, timezone.utc).isoformat(timespec='seconds')
        request = {'monitor_id': mid, 'event_id': parent['id'], 'max_successors': 2, 'expires_at': expires}
        receipt = self.worker(manager.preview, request)
        with self.assertRaises(SafetyError):
            self.worker(manager.configure, {**receipt, 'confirmed': False})
        forged = copy.deepcopy(receipt)
        forged['preview']['max_successors'] = 999
        with self.assertRaises(SafetyError):
            self.worker(manager.configure, {**forged, 'confirmed': True})
        self.assertEqual(manager.state(mid), [])
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(report.read_bytes(), before)

    def test_started_claim_is_interrupted_on_restart_and_cannot_be_replayed(self):
        mid, parent, registry, report, before = self.parent()
        self.authorize(mid, parent)
        self.next_attempt()
        self.dispatch(mid, parent)
        claims = registry.load('monitor_failure_claim')
        self.assertEqual(len(claims), 1, claims)
        claim = next(iter(claims.values()))
        self.assertEqual(claim['status'], 'finished', claim)
        # Model a persisted started receipt at the process-interruption boundary.
        claim['status'] = 'started'
        self.worker(registry.put, 'monitor_failure_claim', claim['id'], claim)
        budget = registry.budget()
        connection = self.hub.github
        self.hub.close()
        self.hub = self.new_hub()
        self.hub.monitor.stop()
        self.hub.github = connection
        registry = self.hub.monitor.registries[mid]
        self.assertEqual(registry.load('monitor_failure_claim')[claim['id']]['status'], 'interrupted_no_replay')
        grant = self.hub.monitor.failure_followup.state(mid)[0]
        with self.assertRaises(SafetyError):
            self.worker(self.hub.monitor.failure_followup.control, 'monitor_failure_resume',
                        {'monitor_id': mid, 'grant_id': grant['id'], 'revision': grant['revision']})
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(report.read_bytes(), before)

    def test_http_pause_requires_auth_and_runs_immediately_while_workbench_is_busy(self):
        mid, parent, registry, report, before = self.parent()
        manager = self.hub.monitor.failure_followup
        route = '/api/actions/monitor_failure_pause'
        headers = {'Authorization': 'Bearer '+self.hub.token}
        with TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345') as client:
            def action(name, payload):
                response = client.post('/api/actions/'+name,
                    json={**payload, 'request_id':uuid.uuid4().hex}, headers=headers)
                self.assertEqual(response.status_code,202,response.text)
                self.worker(lambda:None)
                job = self.hub.jobs[response.json()['id']]
                self.assertEqual(job['status'],'finished',job)
                self.assertNotIn('error',job,job)
                return job['result']
            expires = datetime.fromtimestamp(time.time()+1800, timezone.utc).isoformat(timespec='seconds')
            preview = action('monitor_failure_preview', {'monitor_id':mid,'event_id':parent['id'],
                'max_successors':2,'expires_at':expires})
            grant = action('monitor_failure_configure', {**preview,'confirmed':True})
            body = {'monitor_id': mid, 'grant_id': grant['id'], 'revision': grant['revision'],
                    'request_id': uuid.uuid4().hex}
            self.assertEqual(client.post(route, json=body).status_code, 401)
            self.assertEqual(client.post(route, json=body,
                             headers={**headers, 'Origin': 'https://outside.invalid'}).status_code, 403)
            self.assertEqual(client.post(route, json={**body, 'extra': True}, headers=headers).status_code, 409)
            self.hub.active = 'unrelated-job'
            try:
                response=client.post(route, json=body, headers=headers)
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(manager.state(mid)[0]['status'], 'paused')
                paused=manager.state(mid)[0]
                self.assertEqual(client.post('/api/actions/monitor_failure_resume',headers=headers,
                    json={'monitor_id':mid,'grant_id':grant['id'],'revision':paused['revision'],
                          'request_id':uuid.uuid4().hex}).status_code,409)
            finally:
                self.hub.active = None
            resumed=action('monitor_failure_resume',{'monitor_id':mid,'grant_id':grant['id'],'revision':paused['revision']})
            self.assertTrue(resumed['active'])
            action('monitor_failure_pause',{'monitor_id':mid,'grant_id':grant['id'],'revision':resumed['revision']})
        self.next_attempt()
        budget = registry.budget()
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(report.read_bytes(), before)

    def test_expired_successor_consent_stops_without_reserving_or_calling_model(self):
        mid, parent, registry, report, before = self.parent()
        grant = self.authorize(mid, parent)
        self.next_attempt()
        budget = registry.budget()
        authorization = self.hub.monitor.model_authorization
        old_clock = authorization.clock
        authorization.clock = lambda: grant['core']['expires_epoch'] + 1
        try:
            self.dispatch(mid, parent)
        finally:
            authorization.clock = old_clock
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(registry.budget(), budget)
        self.assertEqual(registry.load('monitor_failure_claim'), {})
        self.assertFalse(self.hub.monitor.failure_followup.state(mid)[0]['active'])
        self.assertEqual(report.read_bytes(), before)

    def test_manual_successor_remains_a_plan_until_new_approval(self):
        mid, parent, registry, report, before = self.parent('approval')
        self.authorize(mid, parent)
        self.next_attempt()
        self.dispatch(mid, parent)
        grant = self.hub.monitor.failure_followup.state(mid)[0]
        self.assertEqual(len(grant['children']), 1, grant)
        child_id = grant['children'][0]['event_id']
        child = registry.load('event')[child_id]
        self.assertEqual(child['root_execution']['status'], 'awaiting_approval', child)
        server = self.hub.monitor.servers[mid, 'root-'+child_id]
        self.assertEqual(server.session.runs, {})
        self.assertTrue(any(p['view']['status'] == 'awaiting_approval' for p in server.session.plans.values()))
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(report.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
