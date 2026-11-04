"""Real Git publication to owned local bare fixtures; no GitHub/model writes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer.github_branch import GitHubBranchTransport, fields
from repo_maintainer.git import git_env
from repo_maintainer.issue_delivery import IssueDelivery
from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.issue_publication import IssuePublication
from repo_maintainer.models import SafetyError
from repo_maintainer.recovery import process_identity
from repo_maintainer.ui_registry import UIRegistry
import test_issue_delivery as fixtures


def bare_git(directory, *args):
    return subprocess.check_output(['git', '-c', 'core.longpaths=true', '-c', 'core.hooksPath='+os.devnull,
                                   '--git-dir='+str(directory), *args], env=git_env(), stderr=subprocess.PIPE)


def prepare_bare(repository, directory):
    subprocess.run(['git', '-c', 'core.longpaths=true', 'init', '--bare', '--template=', str(directory)],
                   check=True, capture_output=True, env=git_env())
    repository.command('-c', 'protocol.file.allow=always', 'push', '--', str(directory), repository.head+':refs/heads/main')


class LocalBranchTransport(GitHubBranchTransport):
    """Test-only URL substitution; production offers no file-transport switch."""
    def __init__(self, bare):
        self.bare, self.attempts, self.mode = Path(bare), 0, 'normal'
        self.gate = None

    def command(self, directory, *args):
        argv = super().command(directory, *args)
        argv[1:1] = ['-c', 'protocol.file.allow=always']
        return [str(self.bare) if a == 'https://github.com/example/project.git' else a for a in argv]

    def push(self, *args):
        self.attempts += 1
        if self.gate:
            self.gate()
        if self.mode == 'timeout_without_write':
            raise TimeoutError('unknown fixture transport result')
        result = super().push(*args)
        if self.mode == 'timeout_after_write':
            raise TimeoutError('unknown fixture transport result')
        return result


class IssuePublicationTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.IssueDeliveryTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.repo = self.case.root, self.case.repo
        self.bare = self.root/'remote.git'
        prepare_bare(self.repo, self.bare)
        self.transport = LocalBranchTransport(self.bare)
        self.service = IssuePublication(self.case.service, self.transport)
        self.config_before = (self.repo.common_dir/'config').read_bytes()
        self.refs_before = self.repo.command('for-each-ref', '--format=%(refname) %(objectname)')

    def prepared(self):
        return self.service.prepare(self.case.identity)

    def push(self, row=None):
        row = row or self.prepared()
        return self.service.push(row['id'], row['challenge'], approve_sha256=row['approval_sha256'], actor='fixture')

    def preserved(self):
        self.case.preserved()
        self.assertEqual(self.config_before, (self.repo.common_dir/'config').read_bytes())
        self.assertEqual(self.refs_before, self.repo.command('for-each-ref', '--format=%(refname) %(objectname)'))

    def remote_sha(self):
        result = subprocess.run(['git', '-c', 'core.longpaths=true', '--git-dir='+str(self.bare),
                                 'show-ref', '--verify', '--hash', 'refs/heads/'+self.case.head],
                                capture_output=True, env=git_env())
        return result.stdout.decode().strip() or None

    def test_exact_approval_native_create_only_push_and_then_independent_pr_approval(self):
        prepared = self.prepared()
        self.assertEqual(prepared, self.prepared())
        self.assertIsNone(self.remote_sha())
        result = self.push(prepared)
        self.assertEqual(result['status'], 'remote-ref-verified')
        self.assertEqual(self.remote_sha(), self.case.commit)
        self.assertEqual(self.transport.attempts, 1)
        self.assertEqual([e['stage'] for e in result['process_events']],
                         ['init_starting', 'init_running', 'init_done', 'push_starting', 'push_running', 'push_done'])
        self.assertTrue(all(e['detail']['returncode'] == 0 for e in result['process_events'] if e['stage'].endswith('_done')))
        self.assertEqual(self.case.remote.writes, 0, 'Push approval must not create a PR')
        proposal = self.case.service.prepare(self.case.identity)
        delivered = self.case.service.create(self.case.identity, proposal['challenge'],
                    approve_sha256=proposal['approval_sha256'], actor='fixture')
        self.assertEqual(delivered['status'], 'awaiting-review')
        with self.assertRaises(SafetyError):
            self.push(prepared)
        self.assertEqual(self.transport.attempts, 1)
        self.preserved()

    def test_existing_head_wrong_approval_and_closed_issue_do_not_push(self):
        prepared = self.prepared()
        with self.assertRaises(SafetyError):
            self.service.push(self.case.identity, prepared['challenge'], approve_sha256='f'*64, actor='fixture')
        self.case.github.issue['state'] = 'closed'
        with self.assertRaises(SafetyError):
            self.push(prepared)
        self.case.github.issue['state'] = 'open'
        self.repo.command('-c', 'protocol.file.allow=always', 'push', '--', str(self.bare), self.case.commit+':refs/heads/'+self.case.head)
        with self.assertRaises(SafetyError):
            self.push(prepared)
        self.assertEqual(self.transport.attempts, 0)

    def test_racing_existing_remote_head_is_not_overwritten_even_when_fast_forward_possible(self):
        prepared = self.prepared()
        def race():
            self.repo.command('-c', 'protocol.file.allow=always', 'push', '--', str(self.bare),
                              self.repo.head+':refs/heads/'+self.case.head)
        self.transport.gate = race
        with self.assertRaises(SafetyError):
            self.push(prepared)
        self.assertEqual(self.remote_sha(), self.repo.head)
        self.assertEqual(self.service.view(self.case.identity)['status'], 'push_unknown')
        with self.assertRaises(SafetyError):
            self.service.reconcile(self.case.identity)
        self.assertEqual(self.transport.attempts, 1)

    def test_unknown_after_native_push_reopens_registry_and_only_queries_exact_ref(self):
        known, _ = self.case.registry.reserve(3)
        self.case.registry.settle(known, {'requests': 3}, success=True)
        unknown, _ = self.case.registry.reserve(2)
        self.case.registry.settle(unknown, {'requests': None}, success=False)
        self.case.budget = self.case.registry.budget()
        self.assertEqual(self.case.budget['requests_used'], 3)
        self.assertEqual(self.case.budget['requests_reserved_unknown'], 2)
        prepared = self.prepared()
        self.transport.mode = 'timeout_after_write'
        with self.assertRaises(TimeoutError):
            self.push(prepared)
        self.assertEqual(self.service.view(self.case.identity)['status'], 'push_unknown')
        self.case.registry.close()
        self.case.registry = UIRegistry(self.root/'state', {'repository': str(self.repo.root), 'provider': {'request_budget': 50}})
        self.addCleanup(self.case.registry.close)
        self.case.execution.registry = self.case.registry
        self.case.service = IssueDelivery(self.case.execution, self.case.remote)
        restarted = LocalBranchTransport(self.bare)
        self.service = IssuePublication(self.case.service, restarted)
        result = self.service.reconcile(self.case.identity)
        self.assertEqual(result['status'], 'remote-ref-verified')
        self.assertEqual(restarted.attempts, 0)
        self.preserved()

    def test_absent_unknown_outcome_remains_held_and_never_retries_push(self):
        prepared = self.prepared()
        self.transport.mode = 'timeout_without_write'
        with self.assertRaises(TimeoutError):
            self.push(prepared)
        for _ in range(2):
            with self.assertRaises(SafetyError):
                self.service.reconcile(self.case.identity)
        with self.assertRaises(SafetyError):
            self.push(prepared)
        self.assertEqual(self.transport.attempts, 1)
        self.assertIsNone(self.remote_sha())

    def test_repository_metadata_and_local_candidate_drift_stop_before_push(self):
        prepared = self.prepared()
        with patch.object(self.case.remote, 'api', return_value={'id': 999, 'full_name': 'example/project', 'default_branch': 'main'}):
            with self.assertRaises(SafetyError):
                self.push(prepared)
        path = Path(self.case.record['workspace'])/'calculator.py'
        original = path.read_bytes()
        path.write_bytes(original+b'# foreign change\n')
        with self.assertRaises(SafetyError):
            self.push(prepared)
        path.write_bytes(original)
        self.assertEqual(self.transport.attempts, 0)

    def test_external_change_after_push_keeps_receipt_but_requires_review(self):
        original = self.transport.push
        def changed(*args):
            result = original(*args)
            self.case.github.issue['updated_at'] = '2026-10-21T06:00:00Z'
            return result
        self.transport.push = changed
        result = self.push()
        self.assertEqual(result['status'], 'remote-ref-review-required')
        self.assertEqual(result['receipt']['sha'], self.case.commit)
        self.preserved()

    def test_source_hooks_url_rewrites_and_global_git_overrides_are_not_loaded(self):
        marker = self.root/'hook-ran'
        hook = self.repo.common_dir/'hooks/pre-push'
        hook.write_text('#!/bin/sh\nprintf bad > "'+marker.as_posix()+'"\n', encoding='utf-8')
        self.repo.command('config', 'core.hooksPath', str(hook.parent))
        self.repo.command('config', 'url.'+str(self.root/'evil.git')+'.insteadOf', str(self.bare))
        self.config_before = (self.repo.common_dir/'config').read_bytes()
        global_config = self.root/'evil.config'
        global_config.write_text('[url "'+(self.root/'evil.git').as_posix()+'"]\n insteadOf = '+self.bare.as_posix()+'\n', encoding='utf-8')
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(global_config), 'GIT_TRACE': '1',
                                    'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.hooksPath',
                                    'GIT_CONFIG_VALUE_0': str(hook.parent)}):
            self.assertEqual(self.push()['status'], 'remote-ref-verified')
        self.assertFalse(marker.exists())
        carrier = self.case.run/'remote-publication/carrier.git'
        carrier_config = (carrier/'config').read_text(encoding='utf-8')
        self.assertNotIn('insteadOf', carrier_config)
        self.assertNotIn('hooksPath', carrier_config)
        self.preserved()

    def test_two_publishers_one_native_push_and_live_owner_cannot_reconcile(self):
        prepared = self.prepared()
        entered, release = threading.Event(), threading.Event()
        results = []
        def gate():
            entered.set()
            self.assertTrue(release.wait(40))
        self.transport.gate = gate
        worker = threading.Thread(target=lambda: results.append(self.push(prepared)))
        worker.start()
        try:
            self.assertTrue(entered.wait(45))
            with self.assertRaises(SafetyError):
                self.push(prepared)
            with self.assertRaises(SafetyError):
                self.service.reconcile(self.case.identity)
        finally:
            release.set()
            worker.join(45)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]['status'], 'remote-ref-verified')
        self.assertEqual(self.transport.attempts, 1)

    def crash_owner(self, phase):
        proposal = self.prepared()
        config = {'source': str(self.repo.root), 'state': str(self.root/'state'),
                  'spec': self.case.spec, 'rules': self.case.rules, 'identity': self.case.identity,
                  'head': self.case.head, 'commit': self.case.commit, 'bare': str(self.bare),
                  'phase': phase, 'proposal': proposal}
        path = self.root/'worker.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        self.case.registry.close()
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('issue_publication_crash_fixture.py')),
                                 str(path), str(self.root/'exit.json')], capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 48 if phase == 'push_done' else 49, result.stderr.decode(errors='replace'))
        self.case.registry = UIRegistry(self.root/'state', {'repository': str(self.repo.root), 'provider': {'request_budget': 50}})
        self.addCleanup(self.case.registry.close)
        self.case.execution.registry = self.case.registry
        self.case.service = IssueDelivery(self.case.execution, self.case.remote)
        self.service = IssuePublication(self.case.service, self.transport)
        self.worker_exit = {'returncode': result.returncode, **json.loads((self.root/'exit.json').read_text(encoding='utf-8'))}

    def test_real_owner_exit_after_completed_native_push_is_read_only_reconciled(self):
        self.crash_owner('push_done')
        self.assertEqual(self.service.view(self.case.identity)['status'], 'push_intent')
        self.assertEqual(self.remote_sha(), self.case.commit)
        self.assertEqual(self.service.reconcile(self.case.identity)['status'], 'remote-ref-verified')
        self.assertEqual(self.transport.attempts, 0)
        self.preserved()

    def test_real_exit_in_push_pid_launch_gap_stays_manual_without_replay(self):
        self.crash_owner('push_starting')
        row = self.service.view(self.case.identity)
        self.assertEqual(row['stage'], 'push_starting')
        with self.assertRaisesRegex(SafetyError, 'PID launch gap'):
            self.service.reconcile(self.case.identity)
        self.assertIsNone(self.remote_sha())
        self.assertEqual(self.transport.attempts, 0)

    def test_live_git_child_and_interrupted_transport_without_done_receipt_stay_manual(self):
        proposal = self.prepared()
        child = None
        def live(*args):
            nonlocal child
            event = args[-1]
            event('push_starting', None)
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            event('push_running', process_identity(child.pid))
            raise TimeoutError('fixture leaves a known live owned child')
        self.transport.push = live
        try:
            with self.assertRaises(TimeoutError):
                self.push(proposal)
            with self.assertRaisesRegex(SafetyError, 'child is still alive'):
                self.service.reconcile(self.case.identity)
            child.terminate()
            child.wait(timeout=10)
            with self.assertRaisesRegex(SafetyError, 'completion receipt'):
                self.service.reconcile(self.case.identity)
        finally:
            if child and child.poll() is None:
                child.terminate()
                child.wait(timeout=10)

    def tearDown(self):
        capture = os.environ.get('REPOCARE_PUBLICATION_EVIDENCE')
        if capture:
            directory = Path(capture)
            directory.mkdir(parents=True, exist_ok=True)
            data = {'test': self.id(), 'publication': self.case.registry.load('issue_publication'),
                    'delivery': self.case.registry.load('issue_delivery'),
                    'source_before': self.case.before,
                    'source_after': self.repo.snapshot(exclude_branches=(self.case.record['branch'], self.case.head)),
                    'budget_before': self.case.budget, 'budget_after': self.case.registry.budget(),
                    'remote_sha': self.remote_sha(), 'transport_attempts': self.transport.attempts,
                    'issue_state': self.case.github.issue['state']}
            if hasattr(self, 'worker_exit'):
                data['worker_exit'] = self.worker_exit
            (directory/(fingerprint(self.id())[:12]+'.json')).write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')


class BranchTransportContractTests(unittest.TestCase):
    def test_fast_exited_child_uses_its_handle_instead_of_fabricating_live_pid_identity(self):
        import repo_maintainer.github_branch as module
        original = module._capture
        def completed(*args, **kwargs):
            callback = kwargs['on_process']
            def after_exit(process):
                process.wait(timeout=10)
                callback(process)
            return original(*args, **{**kwargs, 'on_process': after_exit})
        events = []
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(module, '_capture', side_effect=completed), \
             patch.object(module, 'process_identity', return_value=None):
            GitHubBranchTransport()._initialize(Path(directory)/'new', event=lambda stage, data: events.append((stage, data)))
        self.assertEqual(events[1][0], 'init_running')
        self.assertTrue(events[1][1]['completed_before_identity'])
        self.assertEqual(events[1][1]['returncode'], 0)
        self.assertEqual(events[-1][0], 'init_done')

    def test_new_process_callback_failure_stops_child_and_preserves_old_capture_api(self):
        from repo_maintainer.git_investigation import _capture
        child = []
        def stopped(process):
            child.append(process)
            raise SafetyError('fixture callback failed')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SafetyError, 'callback failed'):
                _capture([sys.executable, '-c', 'import time; time.sleep(60)'], Path(directory), git_env(), on_process=stopped)
            self.assertIsNotNone(child[0].poll())

    def test_production_fixed_https_single_ref_absent_lease_no_hooks_or_tracking(self):
        transport = GitHubBranchTransport()
        for repository, base, head in [('https://evil.test/x', 'main', 'integration/'+'a'*32),
                                        ('example/project', 'main', 'main'),
                                        ('example/project', 'main', 'integration/../main')]:
            with self.assertRaises(SafetyError):
                fields(repository, base, head)
        argv = transport.command(Path('carrier'), 'push', '--porcelain')
        self.assertIn('protocol.allow=never', argv)
        self.assertIn('protocol.https.allow=always', argv)
        self.assertIn('http.followRedirects=false', argv)
        with patch.dict(os.environ, {'MIMO_API_KEY': 'unused-sentinel', 'GITHUB_TOKEN': 'unused-sentinel',
                                     'GCM_TRACE': '1', 'GIT_TRACE': '1'}):
            env = transport.environment()
        self.assertNotIn('MIMO_API_KEY', env)
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertNotIn('GCM_TRACE', env)
        self.assertNotIn('GIT_TRACE', env)
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
        self.assertEqual(env['GCM_INTERACTIVE'], 'never')


if __name__ == '__main__':
    unittest.main()
