"""Original Hub/Issue journals and genuine Pi SDK; loopback synthetic model only."""
import copy
import json
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient

from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app, inspect_repository
from repo_maintainer.workflow import cleanup_run
from repo_maintainer.workbench_correlation import PAIRS, ROUTES, RECEIPTS
from repo_maintainer.maintenance_correlation import CLAIMS
from test_issue_intake import FakeGitHub, BASE
from test_task_planning import SyntheticModel


class IndependentIssues(FakeGitHub):
    """Number selects immutable, independently versioned remote objects."""
    def __init__(self, head):
        super().__init__(head)
        self.issues = {}
        self.issue_events = {}
        for number in (7, 8):
            self.issues[number] = {**copy.deepcopy(self.issue), 'number': number, 'id': 100 + number,
                'html_url': 'https://github.com/example/project/issues/' + str(number),
                'title': 'Fix calculator add subtraction bug',
                'body': 'add(2,3) must be 5; preserve negative and mixed operands.'}
            self.issue_events[number] = [self.event(number, 'labeled', 'maintainer')]

    def api(self, path):
        match = re.fullmatch(re.escape(BASE) + r'/issues/(7|8)(/events\?per_page=30&page=\d+)?', path)
        if match:
            self.calls.append(path)
            if self.hook:
                self.hook(path)
            number = int(match[1])
            return copy.deepcopy(self.issue_events[number] if match[2] else self.issues[number])
        return super().api(path)


class WorkbenchCorrelationTests(unittest.TestCase):
    def setUp(self):
        # Genuine candidate Git worktrees and venvs need short Windows paths.
        base = Path.home() / '.codex' / 'tmp'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='r3-', dir=base)
        self.root = Path(self.temp.name)
        self.repo = RepositoryGateway(create_fixture(self.root / 's'))
        self.repo.command('config', 'core.longpaths', 'true')
        self.repo.command('remote', 'add', 'origin', 'https://github.com/example/project.git')
        self.github = IndependentIssues(self.repo.head)
        self.config = self.root / '.env.local'
        self.config.write_text('synthetic model fixture; no credentials\n', encoding='utf-8')
        self.model = SyntheticModel()
        self.start_hub()
        self.addCleanup(self.finish)

    def start_hub(self):
        self.hub = RepositoryWorkbench(self.root / 'h', self.config, request_budget=12, allow_auto=True,
            github=self.github, provider_factory=lambda p: PiProvider(p.allowed_paths, self.model.url,
                readonly_paths=p.readonly_paths, max_requests=12))
        self.hub.monitor.stop()
        self.hub.url = 'http://127.0.0.1:12345/#session=' + self.hub.token
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.headers = {'Authorization': 'Bearer ' + self.hub.token}

    def finish(self):
        self.client.close()
        self.hub.close()
        for run in self.root.rglob('run.json'):
            if (run.parent / 'workspace').exists():
                cleanup_run(run.parent)
        self.model.close()
        self.temp.cleanup()

    def wait(self, response, prefix='/api/', headers=None):
        self.assertEqual(response.status_code, 202, response.text)
        identity = response.json()['id']
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = self.client.get(prefix + 'state', headers=headers or self.headers)
            self.assertEqual(state.status_code, 200, state.text)
            job = next(j for j in state.json()['jobs'] if j['id'] == identity)
            if job['status'] != 'running':
                return job
            time.sleep(.02)
        self.fail('Original Hub/Issue operation timed out')

    def job(self, action, payload, *, workspace=None):
        prefix = workspace['api_prefix'] if workspace else '/api/'
        headers = {'Authorization': 'Bearer ' + workspace['token']} if workspace else self.headers
        response = self.client.post(prefix + 'actions/' + action, headers=headers,
            json={'request_id': uuid.uuid4().hex, **payload})
        return self.wait(response, prefix, headers)

    def execute(self, action, payload, *, workspace=None):
        job = self.job(action, payload, workspace=workspace)
        self.assertEqual(job['status'], 'finished', job)
        return job['result']

    def connect(self, mode='auto'):
        preview = inspect_repository(self.repo.root)
        self.execute('connect', {'path': str(self.repo.root), 'revision': preview['revision'],
            'allowed_paths': ['calculator.py'], 'readonly_paths': ['tests/test_calculator.py'],
            'allowed_tests': [list(DEMO_TEST)], 'timeout_seconds': 30,
            'allow_auto': True, 'confirmed': True})
        workspace = next(w for w in self.client.get('/api/state', headers=self.headers).json()['workspaces']
                         if w['repository'] == str(self.repo.root))
        self.workspace = workspace
        self.session = self.hub.workspaces[workspace['id']].session
        self.registry = self.session.registry
        configuration = self.execute('issue_config_preview', {'read_roots': ['.'], 'write_roots': ['.'],
            'trusted_actors': ['maintainer'], 'mode': mode, 'allow_install': False,
            'allow_network': False, 'workflow_ids': [1001]}, workspace=workspace)
        self.execute('issue_configure', {'config': configuration['configuration'],
            'revision': configuration['revision']}, workspace=workspace)
        return workspace

    def prepare(self, number):
        investigation = self.execute('issue_investigate', {'number': number}, workspace=self.workspace)
        self.assertEqual(investigation['status'], 'investigated', investigation)
        prepared = self.execute('issue_environment', {'investigation_id': investigation['id']}, workspace=self.workspace)
        self.assertIn('environment', prepared)
        return prepared

    def selector(self, prepared):
        return {'kind': 'issue', 'workspace_id': self.workspace['id'], 'investigation_id': prepared['id']}

    def pair(self):
        self.connect()
        left, right = self.prepare(7), self.prepare(8)
        self.assertNotEqual(left['id'], right['id'])
        return left, right

    def preview(self, left, right):
        return self.execute('maintenance_link_preview', {'left': self.selector(left), 'right': self.selector(right)})

    def confirm(self, preview):
        return self.execute('maintenance_link_confirm', {**preview, 'confirmed': True})

    def run_issue(self, prepared):
        return self.execute('issue_execute', {'investigation_id': prepared['id'],
            'revision': prepared['revision']}, workspace=self.workspace)

    def native(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*.jsonl') if 'task-sessions' in p.parts}

    def candidates(self):
        return {str(p.relative_to(self.root)) for p in self.root.rglob('run.json')}

    def record(self, label, **extra):
        target = os.environ.get('R3_EVIDENCE_DIR')
        if not target:
            return
        value = {'case': self._testMethodName, 'stage': label, 'model_requests': len(self.model.requests),
                 'budgets': {str(r.path.relative_to(self.root)): r.budget()
                             for r in self.hub.correlation._registries().values()},
                 'native_sha256': {p: hashlib.sha256(b).hexdigest() for p, b in self.native().items()},
                 'candidates': sorted(self.candidates()), **extra}
        path = Path(target); path.mkdir(parents=True, exist_ok=True)
        (path / (self._testMethodName + '-' + label + '.json')).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def independent_pair(self, mode='auto'):
        self.connect()
        left, workspace, registry, original = self.prepare(7), self.workspace, self.registry, self.repo
        clone = self.root / 's2'
        original.command('clone', '--no-hardlinks', str(original.root), str(clone))
        self.repo = RepositoryGateway(clone)
        self.repo.command('remote', 'set-url', 'origin', 'https://github.com/example/project.git')
        self.connect(mode=mode)
        right = self.prepare(8)
        selectors = {'left': {'kind': 'issue', 'workspace_id': workspace['id'], 'investigation_id': left['id']},
                     'right': self.selector(right)}
        return left, right, workspace, registry, original, selectors

    def test_two_original_issues_one_real_pi_candidate_second_remains_visible_without_dispatch(self):
        left, right = self.pair()
        original_budget = self.registry.budget()
        journals = {str(p) for p in self.root.rglob('registry.sqlite3')}
        preview = self.preview(left, right)
        self.assertTrue(preview['preview']['comparison']['comparable'], preview)
        self.assertEqual(self.model.requests, [])
        self.assertEqual(self.registry.budget(), original_budget)
        self.assertEqual(self.confirm(preview)['status'], 'linked')
        self.assertEqual(journals, {str(p) for p in self.root.rglob('registry.sqlite3')})
        self.assertEqual(self.registry.budget(), original_budget)
        first = self.run_issue(left)
        self.assertEqual(first['status'], 'awaiting-review', first)
        self.assertTrue(first['correlation']['dispatch_allowed'])
        self.assertEqual(len(self.model.requests), 2)
        self.assertTrue(self.native(), 'Actual Pi Native transcript required')
        budget, native, candidates = self.registry.budget(), self.native(), self.candidates()
        self.assertEqual(budget['requests_used'], 2)
        second = self.run_issue(right)
        self.assertEqual(second['status'], 'related_no_dispatch', second)
        self.assertFalse(second['correlation']['dispatch_allowed'])
        self.assertEqual(self.registry.budget(), budget)
        self.assertEqual(self.native(), native)
        self.assertEqual(self.candidates(), candidates)
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(len(self.registry.load('issue_execution')), 2)
        view = self.session.issue_workbench.view()
        self.assertIn(second['id'], json.dumps(view))
        self.assertEqual(self.registry.load('issue_execution')[second['id']]['status'], 'related_no_dispatch')
        self.assertEqual(self.repo.command('status', '--porcelain'), b'')
        self.record('finished', owner=first['id'], non_owner=second['id'])

    def test_independent_original_ledgers_keep_one_candidate_and_restart_requires_both_original_handles(self):
        left, right, workspace, registry, original, selectors = self.independent_pair()
        preview = self.execute('maintenance_link_preview', selectors)
        self.assertTrue(preview['preview']['comparison']['comparable'], preview)
        self.assertEqual(self.confirm(preview)['status'], 'linked')
        self.assertNotEqual(registry.path, self.registry.path)
        first = self.execute('issue_execute', {'investigation_id': left['id'], 'revision': left['revision']}, workspace=workspace)
        self.assertEqual(first['status'], 'awaiting-review', first)
        self.assertEqual(registry.budget()['requests_used'], 2)
        owner_budget, other_budget = registry.budget(), self.registry.budget()
        before_native, before_candidates = self.native(), self.candidates()
        second = self.run_issue(right)
        self.assertEqual(second['status'], 'related_no_dispatch', second)
        self.assertEqual(self.registry.budget(), other_budget)
        self.assertEqual(registry.budget(), owner_budget)
        self.assertEqual(self.native(), before_native)
        self.assertEqual(self.candidates(), before_candidates)
        self.assertEqual(len(self.model.requests), 2)
        self.record('before-restart', owner=first['id'], non_owner=second['id'])
        self.client.close(); self.hub.close(); self.start_hub()
        self.connect()  # Only the non-owner's original journal is explicitly reconnected.
        origin = self.registry.load(RECEIPTS)[second['id']]['origin']
        with self.assertRaisesRegex(SafetyError, '协调账未打开|对方原账未打开'):
            self.hub.correlation._route(self.registry, origin)
        other = self.registry
        self.repo = original; self.connect()
        self.assertEqual(self.registry.budget(), owner_budget)
        self.assertEqual(other.budget(), other_budget)
        pair, anchor = self.hub.correlation._route(other, origin)
        self.assertEqual(pair['status'], 'linked')
        self.assertEqual(len(anchor.load(CLAIMS)), 1)
        self.assertEqual(self.native(), before_native)
        self.assertEqual(self.candidates(), before_candidates)
        self.assertEqual(len(self.model.requests), 2)
        self.record('reconnected-no-replay')

    def test_authentication_origin_explicit_confirmation_and_stale_source_cannot_create_links(self):
        left, right = self.pair()
        selectors = {'left': self.selector(left), 'right': self.selector(right)}
        path = '/api/actions/maintenance_link_preview'
        body = {'request_id': uuid.uuid4().hex, **selectors}
        self.assertEqual(self.client.post(path, json=body).status_code, 401)
        self.assertEqual(self.client.post(path, headers={**self.headers, 'Origin': 'https://outside.invalid'}, json=body).status_code, 403)
        preview = self.preview(left, right)
        for confirmed in (False, 1, 'true'):
            job = self.job('maintenance_link_confirm', {**preview, 'confirmed': confirmed})
            self.assertEqual(job['status'], 'failed', job)
        self.github.issues[8]['body'] += ' New business requirement.'
        self.github.issues[8]['updated_at'] = '2026-10-29T01:00:00Z'
        job = self.job('maintenance_link_confirm', {**preview, 'confirmed': True})
        self.assertEqual(job['status'], 'failed', job)
        self.assertFalse(self.registry.load(PAIRS))
        self.assertFalse(self.registry.load(ROUTES))
        self.assertEqual(self.model.requests, []); self.assertFalse(self.native())
        self.record('rejected')

    def test_different_original_permission_mode_remains_incomparable_and_both_sources_preserved(self):
        left, right, workspace, registry, _, selectors = self.independent_pair(mode='approval')
        preview = self.execute('maintenance_link_preview', selectors)
        self.assertFalse(preview['preview']['comparison']['comparable'], preview)
        self.assertIn('authority_sha256', preview['preview']['comparison']['differences'])
        self.assertEqual(self.job('maintenance_link_confirm', {**preview, 'confirmed': True})['status'], 'failed')
        self.assertFalse(registry.load(PAIRS)); self.assertFalse(self.registry.load(PAIRS))
        self.assertFalse(self.native()); self.assertFalse(self.candidates()); self.assertEqual(self.model.requests, [])
        self.record('kept-separate')

    def test_unknown_consumption_blocks_correlated_owner_before_native_or_candidate(self):
        left, right = self.pair()
        self.confirm(self.preview(left, right))
        self.registry.reserve(maximum=1)  # Own synthetic journal, never a real account.
        before = self.registry.budget()
        self.assertGreater(before['requests_remaining'], 0)
        result = self.run_issue(left)
        self.assertEqual(result['status'], 'failed_no_replay', result)
        self.assertFalse(self.registry.load(CLAIMS))
        self.assertEqual(self.registry.budget(), before)
        self.assertFalse(self.native()); self.assertFalse(self.candidates()); self.assertEqual(self.model.requests, [])
        self.record('unknown-preserved')

    def test_torn_confirmation_does_not_fall_back_to_original_unfenced_execution(self):
        left, right = self.pair()
        self.confirm(self.preview(left, right))
        oid = self.hub.correlation.origin_id(self.hub.correlation._resolve(self.selector(left))['origin'])
        with self.registry.transaction() as db:
            db.execute('DELETE FROM objects WHERE kind=? AND id=?', (ROUTES, oid))
        before = self.registry.budget()
        result = self.run_issue(left)
        self.assertEqual(result['status'], 'failed_no_replay', result)
        self.assertEqual(self.registry.budget(), before)
        self.assertFalse(self.native()); self.assertFalse(self.candidates()); self.assertEqual(self.model.requests, [])
        self.record('torn-kept')

    def test_budget_metadata_extension_keeps_old_association_blocked_instead_of_reanchoring(self):
        left, right = self.pair()
        self.confirm(self.preview(left, right))
        origin = self.hub.correlation._resolve(self.selector(left))['origin']
        extension = self.registry.preview_extension(13, 'own-fixture')
        self.registry.extend_budget(13, extension['revision'], 'own-fixture', None)
        with self.assertRaisesRegex(SafetyError, '身份/额度元数据变化'):
            self.hub.correlation._route(self.registry, origin)
        self.assertEqual(self.hub.correlation.view()['links'][0]['status'], 'blocked')
        self.assertEqual(self.registry.budget()['request_budget'], 13)
        self.assertFalse(self.native()); self.assertEqual(self.model.requests, [])
        self.record('old-anchor-preserved')

    def test_associated_manual_owner_still_requires_its_new_original_approval(self):
        self.connect(mode='approval')
        left, right = self.prepare(7), self.prepare(8)
        self.confirm(self.preview(left, right))
        prefix = self.workspace['api_prefix']
        headers = {'Authorization': 'Bearer ' + self.workspace['token']}
        response = self.client.post(prefix + 'actions/issue_execute', headers=headers,
            json={'request_id': uuid.uuid4().hex, 'investigation_id': left['id'], 'revision': left['revision']})
        self.assertEqual(response.status_code, 202, response.text)
        deadline = time.monotonic() + 90
        pending = None
        while time.monotonic() < deadline:
            state = self.client.get(prefix + 'state', headers=headers).json()
            pending = next((r for r in state['issue_workbench']['approvals'] if r['status'] == 'pending'), None)
            if pending:
                break
            if not state['active']:
                self.fail(state['jobs'])
            time.sleep(.02)
        self.assertIsNotNone(pending, 'Must wait for a new original approval, not inherit association consent')
        self.assertEqual(len(self.model.requests), 2)
        # Original workflow persists run.json before waiting for approval; that
        # intent report is not a created candidate worktree or an applied edit.
        for report in self.root.rglob('run.json'):
            self.assertFalse((report.parent / 'workspace').exists())
            self.assertFalse(json.loads(report.read_text(encoding='utf-8'))['candidate_tests'])
        self.assertEqual(self.repo.command('status', '--porcelain'), b'')
        self.assertTrue(self.native())
        self.record('waiting-new-approval')
        # Immediate controls return an approval receipt, not a queued job ID.
        decision = self.client.post(prefix + 'actions/issue_decide', headers=headers,
            json={'request_id': uuid.uuid4().hex, 'approval_id': pending['id'],
                  'revision': pending['revision'], 'decision': 'approve'})
        self.assertEqual(decision.status_code, 202, decision.text)
        self.assertEqual(decision.json()['status'], 'decision_recorded')
        job = self.wait(response, prefix, headers)
        self.assertEqual(job['status'], 'finished', job)
        self.assertEqual(job['result']['status'], 'awaiting-review', job)
        before, native, candidates = self.registry.budget(), self.native(), self.candidates()
        second = self.run_issue(right)
        self.assertEqual(second['status'], 'related_no_dispatch', second)
        self.assertEqual(self.registry.budget(), before)
        self.assertEqual(self.native(), native); self.assertEqual(self.candidates(), candidates)
        self.assertEqual(len(self.model.requests), 2)
        self.record('approved-one-chain')

    def test_original_monitor_and_issue_with_different_checks_and_timeout_are_not_forced_equivalent(self):
        self.connect()
        issue = self.prepare(7)
        source = inspect_repository(self.repo.root)
        payload = {'path': str(self.repo.root), 'revision': source['revision'], 'branch': source['branch'],
            'github_url': '', 'interval_seconds': 60, 'timeout_seconds': 30, 'read_roots': ['.'],
            'write_roots': ['.'], 'check_families': ['unittest'], 'mode': 'auto',
            'allow_local_code': True, 'allow_install': False, 'allow_network': False}
        value = self.execute('monitor_rules_preview', payload)
        monitor = self.execute('monitor_rules_configure', {**value, 'confirmed': True})['monitor_id']
        self.hub.executor.submit(self.hub.monitor.poll, monitor).result(timeout=180)
        event = self.hub.monitor.registries[monitor].load('event')
        event = next(iter(event.values()))
        self.assertEqual(event['root_preparation']['status'], 'reproduced', event)
        self.hub.correlation._monitor(monitor, event['id'])  # Preserve a direct stack on an adapter failure.
        preview = self.execute('maintenance_link_preview', {'left': self.selector(issue),
            'right': {'kind': 'monitor', 'monitor_id': monitor, 'event_id': event['id']}})
        self.assertFalse(preview['preview']['comparison']['comparable'], preview)
        self.assertIn('environment_sha256', preview['preview']['comparison']['differences'])
        self.assertIn('authority_sha256', preview['preview']['comparison']['differences'])
        self.assertEqual(self.job('maintenance_link_confirm', {**preview, 'confirmed': True})['status'], 'failed')
        for registry in self.hub.correlation._registries().values():
            self.assertFalse(registry.load(PAIRS)); self.assertFalse(registry.load(ROUTES))
            self.assertEqual(registry.budget()['requests_used'], 0)
        self.assertFalse(self.native()); self.assertEqual(self.model.requests, [])
        self.record('monitor-issue-kept-separate')

    def test_manual_issue_round_limit_matches_original_controller_and_rejects_changed_dispatch(self):
        self.connect(mode='approval')
        left, right = self.prepare(7), self.prepare(8)
        context = self.hub.correlation._resolve(self.selector(left))
        # IssueWorkbench.start passes three rounds, each requiring its own
        # approval. Monitor approval is separately capped at one by its controller.
        self.assertEqual(context['max_attempts'], 3)
        self.confirm(self.preview(left, right))
        service = self.session.issue_workbench._service(left)
        result = self.hub.executor.submit(service.start, left['id'], left['environment'], left['grant'],
            approval=lambda _: 'reject', max_attempts=2).result(timeout=180)
        self.assertEqual(result['status'], 'failed_no_replay', result)
        self.assertFalse(self.registry.load(CLAIMS))
        self.assertFalse(self.native()); self.assertFalse(self.candidates()); self.assertEqual(self.model.requests, [])
        self.record('changed-rounds-rejected')


if __name__ == '__main__':
    unittest.main()
