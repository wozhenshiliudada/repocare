"""R2: new CI run order, Issue version successor, delivered chain tail.

The positive paths use the original Pi SDK with a loopback synthetic model, the
original registry/budget, real Git worktrees and real isolated checks. GitHub CI
and PRs are deterministic local fixtures; no remote or paid model is contacted.
"""
import copy
from datetime import datetime, timezone
import json
import threading
import time
import unittest

import test_ci_repair as ci_fixture
import test_issue_execution as issue_execution_fixture
from test_ci_evidence import ATTEMPT, JOB, JOBS
from test_issue_delivery import FakePulls
from test_issue_publication import LocalBranchTransport, prepare_bare
from repo_maintainer.ci_transport import CiJsonPage
from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.integration import integrate, prepare_integration
from repo_maintainer.issue_delivery import IssueDelivery
from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.issue_followup import IssueFollowup
from repo_maintainer.issue_intake import IssueReader, fingerprint, specification
from repo_maintainer.maintenance_rules import maintenance_rules
from repo_maintainer.models import SafetyError
from repo_maintainer.monitor_delivery import MonitorDelivery, MonitorPublication
from repo_maintainer.monitor_failure_followup import (select_run_candidate,
    verified_delivery_receipt,run_order_proof,verify_run_order_proof)
from repo_maintainer.target_environment import discover_environment, make_grant
from repo_maintainer.ui_registry import UIRegistry
from test_monitor_delivery import MonitorRemote
from test_repository_workbench import FakeGitHub, FakeServer


class RepoMonitorRemote(MonitorRemote):
    """The shared test adapter, pinned to this fixture's example/repo slug."""
    slug = 'example/repo'

    def api(self, path):
        self.reads += 1
        assert path == '/repos/' + self.slug
        return {'id': self.repo_id, 'full_name': self.slug, 'default_branch': self.default}

    def branch_head(self, url, branch):
        from test_issue_publication import bare_git
        self.reads += 1
        assert url == 'https://github.com/' + self.slug
        return bare_git(self.bare, 'show-ref', '--verify', '--hash',
                        'refs/heads/' + branch).decode().strip()

    def pulls_for_head(self, slug, head):
        self.reads += 1
        assert slug == self.slug
        return copy.deepcopy([r for r in self.rows if r['head']['ref'] == head])

    def create_draft(self, slug, payload):
        from test_issue_publication import bare_git
        assert slug == self.slug
        self.writes += 1
        if self.before_post:
            self.before_post()
        value = {'id': 901, 'number': 9,
                 'html_url': 'https://github.com/' + slug + '/pull/9',
                 'state': 'open', 'merged_at': None, **copy.deepcopy(payload)}
        for side in ('head', 'base'):
            branch = payload[side]
            value[side] = {'ref': branch,
                           'sha': self.branch_head('https://github.com/' + slug, branch),
                           'repo': {'id': self.repo_id, 'full_name': slug}}
        self.rows.append(value)
        return copy.deepcopy(value)


class RepoTransport(LocalBranchTransport):
    def command(self, directory, *args):
        argv = super().command(directory, *args)
        return [str(self.bare) if a == 'https://github.com/example/repo.git' else a
                for a in argv]


def run_row(*, run_id, run_number, attempt, created_at, conclusion='failure',
            workflow_id=301, head_sha='a' * 40, repository_id=901,
            repository='example/repo'):
    return {'id': run_id, 'run_number': run_number, 'run_attempt': attempt,
            'workflow_id': workflow_id, 'head_sha': head_sha, 'head_branch': 'main',
            'status': 'completed', 'conclusion': conclusion,
            'created_at': created_at,
            'repository': {'id': repository_id, 'full_name': repository}}


OLD_PIN = {'repository': 'example/repo', 'repository_id': 901, 'head_sha': 'a' * 40,
           'workflow_id': 301, 'run_id': 101, 'attempt': 1}


class RunOrderContractTests(unittest.TestCase):
    def select(self, rows):
        return select_run_candidate(OLD_PIN, rows)

    def test_new_run_strictly_later_and_latest_is_selected(self):
        parent = run_row(run_id=101, run_number=4, attempt=1,
                         created_at='2026-10-29T00:00:00Z')
        new = run_row(run_id=102, run_number=5, attempt=1,
                      created_at='2026-10-29T01:00:00Z')
        result = self.select([new, parent])
        self.assertEqual(result['kind'], 'new_run')
        self.assertEqual(result['pin']['run_id'], 102)
        self.assertEqual(result['pin']['attempt'], 1)

    def test_same_run_higher_attempt_remains_supported_without_new_fields(self):
        parent = run_row(run_id=101, run_number=4, attempt=1,
                         created_at='2026-10-29T00:00:00Z')
        new_attempt = {**parent, 'run_attempt': 2}
        result = self.select([new_attempt])
        self.assertEqual(result['kind'], 'same_run')
        self.assertEqual(result['pin']['attempt'], 2)

    def test_missing_parent_row_blocks(self):
        with self.assertRaises(SafetyError):
            self.select([run_row(run_id=102, run_number=5, attempt=1,
                                 created_at='2026-10-29T01:00:00Z')])

    def test_missing_order_fields_block_instead_of_guessing(self):
        parent = run_row(run_id=101, run_number=4, attempt=1,
                         created_at='2026-10-29T00:00:00Z')
        for field in ('run_number', 'created_at', 'id'):
            new = run_row(run_id=102, run_number=5, attempt=1,
                          created_at='2026-10-29T01:00:00Z')
            new.pop(field)
            with self.subTest(field=field), self.assertRaises(SafetyError):
                self.select([new, parent])

    def test_equal_or_reversed_order_blocks(self):
        parent = run_row(run_id=101, run_number=4, attempt=1,
                         created_at='2026-10-29T00:00:00Z')
        equal_time = run_row(run_id=102, run_number=5, attempt=1,
                             created_at=parent['created_at'])
        same_number = run_row(run_id=102, run_number=4, attempt=1,
                              created_at='2026-10-29T01:00:00Z')
        older = run_row(run_id=102, run_number=3, attempt=1,
                        created_at='2026-10-29T01:00:00Z')
        for rows in ([equal_time, parent], [same_number, parent], [older, parent]):
            with self.subTest(rows=rows), self.assertRaises(SafetyError):
                self.select(rows)

    def test_tied_latest_number_and_non_completed_latest_have_no_authority(self):
        parent = run_row(run_id=101, run_number=4, attempt=1,
                         created_at='2026-10-29T00:00:00Z')
        a = run_row(run_id=102, run_number=5, attempt=1,
                    created_at='2026-10-29T01:00:00Z')
        b = run_row(run_id=103, run_number=5, attempt=1,
                    created_at='2026-10-29T02:00:00Z')
        with self.assertRaises(SafetyError):
            self.select([a, b, parent])
        success = {**a, 'conclusion': 'success'}
        self.assertIsNone(self.select([success, parent]))

    def test_foreign_repository_sha_or_workflow_is_not_a_parent_candidate(self):
        parent = run_row(run_id=101, run_number=4, attempt=1,
                         created_at='2026-10-29T00:00:00Z')
        for change in ({'repository': {'id': 999, 'full_name': 'example/repo'}},
                       {'head_sha': 'b' * 40},
                       {'head_branch': 'other'}):
            new = {**run_row(run_id=102, run_number=5, attempt=1,
                             created_at='2026-10-29T01:00:00Z'), **change}
            with self.subTest(change=change), self.assertRaises(SafetyError):
                self.select([new, parent])

    def test_other_workflows_and_proven_older_runs_do_not_hide_the_latest_failure(self):
        parent=run_row(run_id=101,run_number=4,attempt=1,created_at='2026-10-29T00:00:00Z')
        older=run_row(run_id=100,run_number=3,attempt=1,created_at='2026-10-28T00:00:00Z')
        other=run_row(run_id=999,run_number=50,attempt=1,workflow_id=302,created_at='2026-10-29T02:00:00Z')
        latest=run_row(run_id=102,run_number=5,attempt=1,created_at='2026-10-29T01:00:00Z')
        self.assertIsNone(self.select([other,older,parent]))
        self.assertEqual(self.select([other,latest,older,parent])['pin']['run_id'],102)

    def test_new_run_order_proof_preserves_exact_source_and_rejects_missing_or_changed_rows(self):
        parent=run_row(run_id=101,run_number=4,attempt=1,created_at='2026-10-29T00:00:00Z')
        latest=run_row(run_id=102,run_number=5,attempt=1,created_at='2026-10-29T01:00:00Z')
        selection=self.select([latest,parent]);proof=run_order_proof(selection)
        verify_run_order_proof(OLD_PIN,selection['pin'],proof)
        invalid=[None,{**proof,'schema_version':True},{**proof,'extra':'not allowed'}]
        for field,value in [('head_sha','b'*40),('created_at',parent['created_at']),('run_attempt',2),('id',103)]:
            changed=copy.deepcopy(proof);changed['selected'][field]=value;invalid.append(changed)
        for changed in invalid:
            with self.subTest(proof=changed),self.assertRaises(SafetyError):
                verify_run_order_proof(OLD_PIN,selection['pin'],changed)


def _monitor_fixture():
    class Fixture(ci_fixture.CiOriginalRepairTests):
        pass
    return Fixture()


class R2MonitorRunTests(unittest.TestCase):
    def setUp(self):
        self.case = _monitor_fixture()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def __getattr__(self, name):
        case = self.__dict__.get('case')
        if name == 'case' or case is None:
            raise AttributeError(name)
        return getattr(case, name)

    def parent(self):
        mid = self.prepare_ci('auto')
        parent = self.poll(mid)
        self.assertEqual(parent['root_execution']['status'], 'candidate_ready', parent)
        self.preserve_cleanup(self.session(mid, parent))
        registry = self.hub.monitor.registries[mid]
        report = self.session(mid, parent).runs_dir / parent['root_execution']['run_id'] / 'run.json'
        old_budget = registry.budget()
        self.rows[0].update(run_number=1, created_at='2026-10-29T00:00:00Z')
        return mid, parent, registry, report, old_budget

    def authorize(self, mid, parent, maximum=3):
        manager = self.hub.monitor.failure_followup
        expires = datetime.fromtimestamp(time.time() + 1800, timezone.utc).isoformat(timespec='seconds')
        p = self.worker(manager.preview, {'monitor_id': mid, 'event_id': parent['id'],
                                          'max_successors': maximum, 'expires_at': expires})
        self.worker(manager.configure, {**p, 'confirmed': True})
        return manager.state(mid)[0]

    def add_run(self, *, run_id=102, run_number=2, attempt=1,
                created_at='2026-10-29T01:00:00Z', conclusion='failure',
                changed=True, workflow_id=301, head_sha=None, repository_id=901):
        old = self.rows[0]
        head = old['head_sha'] if head_sha is None else head_sha
        row = copy.deepcopy(old)
        row.update(id=run_id, run_number=run_number, run_attempt=attempt,
                   created_at=created_at, conclusion=conclusion,
                   workflow_id=workflow_id, head_sha=head, status='completed')
        row['repository'] = {'id': repository_id, 'full_name': 'example/repo'}
        self.case.rows = [row] + [r for r in self.rows if r.get('id') != run_id]
        prefix = f'/repos/example/repo/actions/runs/{run_id}/attempts/{attempt}'
        run_page = copy.deepcopy(self.transport.pages[ATTEMPT].data)
        run_page.update(id=run_id, run_attempt=attempt, workflow_id=workflow_id,
                        head_sha=head, conclusion=conclusion)
        self.transport.pages[prefix] = CiJsonPage(run_page)
        jobs_path = prefix + '/jobs?per_page=100&page=1'
        listing = copy.deepcopy(self.transport.pages[JOBS + '1'].data)
        job = listing['jobs'][0]
        job_id = 200 + run_id
        job.update(id=job_id, run_id=run_id, run_attempt=attempt, head_sha=head)
        if changed:
            job['steps'][0]['name'] += f' run {run_id} detail'
        self.transport.pages[jobs_path] = CiJsonPage(listing)
        detail = copy.deepcopy(self.transport.pages[JOB].data)
        detail.update(id=job_id, run_id=run_id, run_attempt=attempt, head_sha=head)
        if changed:
            detail['steps'][0]['name'] += f' run {run_id} detail'
        self.transport.pages[f'/repos/example/repo/actions/jobs/{job_id}'] = CiJsonPage(detail)
        self.transport.logs[f'/repos/example/repo/actions/jobs/{job_id}/logs'] = (
            f'FAIL newly observed run {run_id}\nAssertionError: add(2,3) expected 5, got -1\n').encode()
        return row

    def dispatch(self, mid, parent):
        with self.ci_transport():
            return self.worker(self.hub.monitor.failure_followup.dispatch, mid, parent['sha'])

    def test_new_run_positive_uses_original_pi_budget_native_and_preserves_parent(self):
        mid, parent, registry, report, old_budget = self.parent()
        old_execution = copy.deepcopy(parent['root_execution'])
        old_tasks = set(registry.load('pi_task'))
        self.authorize(mid, parent)
        self.add_run()
        self.dispatch(mid, parent)
        claims = registry.load('monitor_failure_claim')
        self.assertEqual(len(claims), 1, claims)
        claim = next(iter(claims.values()))
        self.assertEqual(claim['ci_failure']['run_id'], 102)
        self.assertEqual(claim['status'], 'finished',{
            'claim':claim,'reason':self.hub.monitor.failure_followup.state(mid)[0].get('error')})
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(registry.budget()['requests_used'], old_budget['requests_used'] + 2)
        self.assertEqual(registry.budget()['requests_reserved_unknown'], 0)
        self.assertEqual(len(set(registry.load('pi_task')) - old_tasks), 1)
        parent_now = registry.load('event')[parent['id']]
        self.assertEqual(parent_now['root_execution'], old_execution)
        children = self.hub.monitor.failure_followup.state(mid)[0]['children']
        self.assertEqual(len(children), 1, children)
        child = registry.load('event')[children[0]['event_id']]
        self.assertEqual(child['failure_successor']['parent_event_id'], parent['id'])
        self.assertEqual(child['failure_successor']['parent_run_id'], parent['root_execution']['run_id'])
        self.assertEqual(child['sha'], parent['sha'])
        self.assertEqual(child['root_execution']['binding']['ci_failure']['run_id'], 102)
        self.assertNotIn('chain_parent', child['failure_successor'])
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(len(registry.load('monitor_failure_claim')), 1)
        self.assertEqual(self.hub.monitor.failure_followup.state(mid)[0]['status'],'active')
        saved=copy.deepcopy(registry.load('monitor_failure_claim')[claim['id']])
        for missing in (False,True):
            changed=copy.deepcopy(saved)
            if missing:changed.pop('run_order')
            else:changed['run_order']['selected']['run_number']+=1
            registry.put('monitor_failure_claim',claim['id'],changed)
            with self.assertRaises(SafetyError):
                self.hub.monitor.failure_followup.validate_event(mid,child)
            registry.put('monitor_failure_claim',claim['id'],saved)
        self.assertEqual(len(self.model.requests),4)

    def test_new_run_missing_order_or_ambiguous_blocks_before_claim_and_model(self):
        for index,change in enumerate(({'run_number': None}, {'created_at': None}, {'run_number': 1},
                       {'created_at': '2026-10-29T00:00:00Z'})):
            with self.subTest(change=change):
                if index:
                    self.case.doCleanups();self.case=_monitor_fixture();self.case.setUp()
                    self.addCleanup(self.case.doCleanups)
                mid, parent, registry, report, old_budget = self.parent()
                self.authorize(mid, parent)
                row = self.add_run()
                for key, value in change.items():
                    if value is None:
                        row.pop(key)
                    else:
                        row[key] = value
                self.dispatch(mid, parent)
                grant = self.hub.monitor.failure_followup.state(mid)[0]
                self.assertEqual(grant['status'], 'blocked', grant)
                self.assertIsInstance(grant['error'], str)
                self.assertTrue(grant['error'])
                self.assertEqual(registry.load('monitor_failure_claim'), {})
                self.assertEqual(len(self.model.requests), 2)
                self.assertEqual(registry.budget(), old_budget)

    def test_new_run_success_or_tie_does_not_start_model(self):
        mid, parent, registry, report, old_budget = self.parent()
        self.authorize(mid, parent)
        self.add_run(conclusion='success')
        self.dispatch(mid, parent)
        self.assertEqual(registry.load('monitor_failure_claim'), {})
        self.assertEqual(len(self.model.requests), 2)

    def test_new_run_concurrency_creates_at_most_one_child(self):
        mid, parent, registry, report, old_budget = self.parent()
        self.authorize(mid, parent)
        self.add_run()
        results = []

        def call():
            try:
                self.hub.monitor.failure_followup.dispatch(mid, parent['sha'])
                results.append('ok')
            except SafetyError as error:
                results.append(str(error))

        threads = [threading.Thread(target=call) for _ in range(2)]
        with self.ci_transport():
            for thread in threads:thread.start()
            for thread in threads:thread.join(120)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(registry.load('monitor_failure_claim')), 1, results)
        self.assertEqual(len(registry.load('event')), 2, results)  # parent + one child
        self.assertEqual(len(self.hub.monitor.failure_followup.state(mid)[0]['children']), 1)

    def test_started_check_restart_does_not_replay(self):
        mid, parent, registry, report, old_budget = self.parent()
        self.authorize(mid, parent)
        self.add_run()
        self.dispatch(mid, parent)
        claims = registry.load('monitor_failure_claim')
        self.assertEqual(len(claims), 1)
        claim = next(iter(claims.values()))
        claim['status'] = 'started'
        self.worker(registry.put, 'monitor_failure_claim', claim['id'], claim)
        connection = self.hub.github
        self.hub.close()
        self.case.hub = self.new_hub()
        self.hub.monitor.stop()
        self.hub.github = connection
        registry = self.hub.monitor.registries[mid]
        self.assertEqual(registry.load('monitor_failure_claim')[claim['id']]['status'],
                         'interrupted_no_replay')
        self.dispatch(mid, parent)
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(registry.budget(), old_budget | {'requests_used': 4,
                         'requests_remaining': old_budget['request_budget'] - 4})

    def test_delivered_chain_tail_allows_exactly_one_more_child(self):
        mid, parent, registry, report, old_budget = self.parent()
        original_report=report.read_bytes()
        self.authorize(mid, parent)
        self.add_run(run_id=102, run_number=2, created_at='2026-10-29T01:00:00Z')
        self.dispatch(mid, parent)
        child_id = self.hub.monitor.failure_followup.state(mid)[0]['children'][0]['event_id']
        child = registry.load('event')[child_id]
        self.preserve_cleanup(self.session(mid, child))
        # Real approved/sealed local publication followed by the original
        # MonitorDelivery PR path. The test remote only reads a local bare repo.
        bare = self.root / 'r2-remote.git'
        prepare_bare(RepositoryGateway(self.repo), bare)
        remote = RepoMonitorRemote(bare)
        remote.repo_id = self.rows[0]['repository']['id']
        transport = RepoTransport(bare)
        self.hub.monitor_delivery_remote = remote
        self.hub.monitor_publication_transport = transport
        service = MonitorDelivery(self.hub.monitor, mid, child_id, remote)
        publisher = MonitorPublication(service, transport)
        selection = {'monitor_id': mid, 'event_id': child_id,
                     'run_id': child['root_execution']['run_id']}
        with self.ci_transport():
            prepared = self.worker(service.operate, 'monitor_delivery_prepare_local',
                                   selection, transport)
            self.worker(service.operate, 'monitor_delivery_publish_local',
                        {**selection, 'revision': prepared['review_sha256']}, transport)
            proposal = self.worker(publisher.prepare, selection['run_id'])
            self.worker(lambda: publisher.push(selection['run_id'], proposal['challenge'],
                        approve_sha256=proposal['approval_sha256'], actor='fixture'))
            draft = self.worker(service.prepare, selection['run_id'])
            delivered = self.worker(lambda: service.create(selection['run_id'], draft['challenge'],
                                    approve_sha256=draft['approval_sha256'], actor='fixture'))
        self.assertEqual(delivered['status'], 'awaiting-review')
        self.add_run(run_id=103, run_number=3, created_at='2026-10-29T02:00:00Z',
                     changed=True)
        # This operation now includes proving a published ancestor on every
        # original authorization guard, then preparing and repairing a third
        # candidate. Record its cost separately from the fixture's 180-second
        # single-operation helper. Product deadlines/guards are unchanged.
        started = time.monotonic()
        with self.ci_transport():
            self.hub.executor.submit(self.hub.monitor.failure_followup.dispatch,
                                     mid, parent['sha']).result(timeout=600)
        elapsed = time.monotonic() - started
        claims = registry.load('monitor_failure_claim')
        self.assertEqual(len(claims), 2, claims)
        grants = self.hub.monitor.failure_followup.state(mid)
        self.assertEqual(len(grants[0]['children']), 2, grants)
        tail_id = grants[0]['children'][1]['event_id']
        tail = registry.load('event')[tail_id]
        diagnostic = {'event_status': tail.get('status'), 'message': tail.get('message'),
                      'third_dispatch_seconds': elapsed, 'test_wait_seconds': 600,
                      'execution': tail.get('root_execution'),
                      'grant_status': grants[0]['status'], 'grant_error': grants[0].get('error'),
                      'claim': claims[tail['failure_successor']['selection_key']]}
        from pathlib import Path
        (Path(__file__).resolve().parents[1] / '.demo-r2-tail-diagnostic.json').write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertEqual(tail.get('root_execution', {}).get('status'), 'candidate_ready', diagnostic)
        self.assertEqual(tail['root_execution']['binding']['ci_failure']['run_id'], 103)
        self.assertEqual(tail['failure_successor']['chain_parent']['event_id'], child_id)
        self.assertEqual(tail['failure_successor']['chain_parent']['delivery_id'],
                         child['root_execution']['run_id'])
        self.assertEqual(report.read_bytes(),original_report)
        self.assertEqual(len(self.model.requests), 6)
        self.assertEqual(registry.budget()['requests_used'], old_budget['requests_used'] + 4)
        # No third child: the second is not delivered yet.
        self.add_run(run_id=104, run_number=4, created_at='2026-10-29T03:00:00Z')
        self.dispatch(mid, parent)
        self.assertEqual(len(registry.load('monitor_failure_claim')), 2)
        self.assertEqual(len(self.model.requests), 6)

    def test_chain_without_verified_delivery_receipt_blocks(self):
        mid, parent, registry, report, old_budget = self.parent()
        self.authorize(mid, parent)
        self.add_run(run_id=102, run_number=2, created_at='2026-10-29T01:00:00Z')
        self.dispatch(mid, parent)
        child_id = self.hub.monitor.failure_followup.state(mid)[0]['children'][0]['event_id']
        child = registry.load('event')[child_id]
        self.preserve_cleanup(self.session(mid, child))
        self.add_run(run_id=103, run_number=3, created_at='2026-10-29T02:00:00Z')
        self.dispatch(mid, parent)
        grant = self.hub.monitor.failure_followup.state(mid)[0]
        self.assertEqual(grant['status'], 'blocked', grant)
        self.assertIn('交付', grant['error'])
        self.assertEqual(len(registry.load('monitor_failure_claim')), 1)
        self.assertEqual(len(self.model.requests), 4)


def _issue_fixture():
    class Fixture(issue_execution_fixture.IssueExecutionTests):
        pass
    return Fixture()


class R2IssueSuccessorTests(unittest.TestCase):
    def setUp(self):
        self.case = _issue_fixture()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.followup = IssueFollowup(self.case.registry, self.case.repo,
                                      self.case.github, self.case.spec,
                                      self.case.rules, self.case.executor)

    def __getattr__(self, name):
        case = self.__dict__.get('case')
        if name == 'case' or case is None:
            raise AttributeError(name)
        return getattr(case, name)

    def deliver_execution(self, value):
        owner = self.executor.runs / value['run_id']
        review = prepare_integration(owner)
        self.assertEqual(review['status'], 'prepared', review)
        self.assertEqual(integrate(owner, review['review_sha256'])['status'], 'published')
        remote = FakePulls(self.github, review['review']['target_branch'],
                           review['review']['commit'])
        delivery = IssueDelivery(self.executor, remote)
        proposal = delivery.prepare(value['id'])
        created = delivery.create(proposal['id'], proposal['challenge'],
                                  approve_sha256=proposal['approval_sha256'], actor='fixture')
        self.assertEqual(created['status'], 'awaiting-review', created)
        return delivery, created

    def parent_delivered(self):
        value = self.run_issue()
        self.assertEqual(value['status'], 'awaiting-review', value)
        delivery, created = self.deliver_execution(value)
        return value, delivery, created

    def new_version(self, suffix='new regression'):
        self.github.issue['body'] += '\n\n' + suffix + ': adjusted_total(-2) now misbehaves.'
        self.github.issue['updated_at'] = '2026-10-21T05:00:00Z'
        return IssueReader(self.github, self.spec).snapshot(7)

    def environment_for(self, successor):
        job = self.registry.load('issue_diagnosis')[successor['investigation_id']]
        environment = discover_environment(self.repo, job['snapshot']['head_sha'], ['.'],
                                          investigation=job['report'])
        grant = make_grant(environment, allow_local_code=True)
        return environment, grant

    def prepare(self):
        parent, delivery, created = self.parent_delivered()
        self.new_version()
        receipt = self.followup.preview(7)
        self.assertNotEqual(receipt['preview']['parent']['execution_id'], '')
        successor = self.followup.configure({**receipt, 'confirmed': True})
        environment, grant = self.environment_for(successor)
        return parent, successor, environment, grant

    def test_issue_new_version_positive_uses_original_pi_and_preserves_parent(self):
        parent, successor, environment, grant = self.prepare()
        old_view = self.executor.view(parent['id'])
        old_run = self.executor.runs / parent['run_id'] / 'run.json'
        old_bytes = old_run.read_bytes()
        old_tasks = set(self.registry.load('pi_task'))
        old_budget = self.registry.budget()
        result = self.followup.start(successor['id'], successor['challenge'], environment,
                                     grant, approval=lambda _: 'approve')
        self.assertEqual(result['status'], 'awaiting-review', result)
        self.assertEqual(self.executor.view(parent['id']), old_view)
        self.assertEqual(old_run.read_bytes(), old_bytes)
        self.assertEqual(len(set(self.registry.load('pi_task')) - old_tasks), 1)
        self.assertEqual(self.registry.budget()['requests_used'],
                         old_budget['requests_used'] + 2)
        new_view = self.executor.view(result['id'])
        self.assertEqual(new_view['continuation_kind'], 'version_successor')
        self.assertEqual(new_view['parent_execution_id'], parent['id'])
        self.assertEqual(new_view['issue_token'], successor['new_token'])
        self.assertNotEqual(new_view['task_id'], parent['task_id'])
        successors = self.registry.load('issue_successor')
        self.assertEqual(len(successors), 1)
        self.assertEqual(next(iter(successors.values()))['status'],
                         'awaiting-review')

    def test_issue_delivered_chain_tail_continues_only_from_verified_delivery(self):
        parent, successor, environment, grant = self.prepare()
        old_budget = self.registry.budget()
        first = self.followup.start(successor['id'], successor['challenge'],
                                    environment, grant, approval=lambda _: 'approve')
        self.assertEqual(first['status'], 'awaiting-review', first)
        self.deliver_execution(first)
        self.new_version('second reported regression')
        receipt = self.followup.preview(7)
        self.assertEqual(receipt['preview']['parent']['execution_id'], first['id'])
        self.assertEqual(receipt['preview']['parent']['delivery']['delivery_status'],
                         'awaiting-review')
        second = self.followup.configure({**receipt, 'confirmed': True})
        environment, grant = self.environment_for(second)
        result = self.followup.start(second['id'], second['challenge'],
                                     environment, grant, approval=lambda _: 'approve')
        self.assertEqual(result['status'], 'awaiting-review', result)
        rows = self.registry.load('issue_successor')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[successor['id']]['status'], 'awaiting-review')
        self.assertEqual(rows[second['id']]['status'], 'awaiting-review')
        self.assertEqual(self.executor.view(first['id'])['status'], 'awaiting-review')
        self.assertEqual(self.registry.budget()['requests_used'],
                         old_budget['requests_used'] + 4)
        self.assertEqual(self.executor.view(result['id'])['parent_execution_id'],
                         first['id'])

    def test_issue_new_version_requires_new_snapshot_and_delivery_receipt(self):
        self.parent_delivered()
        with self.assertRaises(SafetyError):
            self.followup.preview(7)  # Same snapshot token, no version change.
        self.new_version()
        parent_id = self.executor.view(next(iter(self.registry.load(
            'issue_execution').values()))['id'])['id']
        row = self.registry.load('issue_delivery')[parent_id]
        with self.registry.transaction() as db:
            row['status'] = 'creation_unknown'
            self.registry.put('issue_delivery', parent_id, row, db)
        with self.assertRaises(SafetyError):
            self.followup.preview(7)

    def test_closed_issue_and_unknown_budget_block_new_version(self):
        self.parent_delivered()
        self.new_version()
        self.github.issue['state'] = 'closed'
        with self.assertRaises(SafetyError):
            self.followup.preview(7)
        self.github.issue['state'] = 'open'
        self.registry.reserve()
        with self.assertRaises(SafetyError):
            self.followup.preview(7)
        self.assertEqual(len(self.model.requests), 2)


if __name__ == '__main__':
    unittest.main()
