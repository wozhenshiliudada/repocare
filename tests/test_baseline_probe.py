"""Test-only approval, immutable diagnostics, and no accidental edit/model authority."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer.baseline_probe import collect_baseline, BaselineEvidence, scope_for
from repo_maintainer.cli import main
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import Policy, SafetyError, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.runner import run_test as actual_test
from repo_maintainer.task_session import fingerprint
from repo_maintainer.workflow import cleanup_run


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / 'repo')
        self.git = RepositoryGateway(self.repo)
        self.before = self.git.snapshot()
        self.policy = Policy(('calculator.py',), (DEMO_TEST,))
        self.issue = 'Fix addition; preserve negative behavior'

    def collect(self, decision='approve', policy=None, approval=None):
        result = collect_baseline(self.repo, self.issue, policy or self.policy, self.root / 'probes',
                                  approval or (lambda scope: decision))
        self.directory = Path(result['workspace']).parent
        if Path(result['workspace']).exists() and result.get('finished_at'):
            self.addCleanup(cleanup_run, self.directory)
        return result

    def files(self, policy=None):
        policy = policy or self.policy
        return [{'path': name, 'before_sha256': digest(self.git.read(name)),
                 'content': self.git.read(name).decode(), 'editable': name in policy.allowed_paths}
                for name in (*policy.allowed_paths, *policy.readonly_paths)]

    def binding(self, policy=None):
        return BaselineEvidence(self.directory, self.git, self.issue, policy or self.policy)

    def test_pending_never_creates_workspace_or_starts_commands(self):
        with patch('repo_maintainer.baseline_probe.run_test') as run:
            result = self.collect('pending')
        self.assertEqual('awaiting_baseline_approval', result['status'])
        run.assert_not_called()
        self.assertFalse(Path(result['workspace']).exists())
        self.assertEqual(self.before, self.git.snapshot())

    def test_rejection_is_not_permission_to_run_tests(self):
        with patch('repo_maintainer.baseline_probe.run_test') as run:
            result = self.collect('reject')
        self.assertEqual('baseline_rejected', result['status'])
        run.assert_not_called()
        self.assertEqual([], result['baseline_tests'])

    def test_red_baseline_is_real_and_cleaned_before_context_is_available(self):
        result = self.collect()
        self.assertEqual('baseline_ready', result['status'], result)
        self.assertEqual([1], [row['returncode'] for row in result['baseline_tests']])
        self.assertEqual('candidate_removed', result['recovery']['result'])
        self.assertEqual(self.before, self.git.snapshot())
        context = self.binding()(self.git, self.issue, self.policy.allowed_tests, self.files())
        self.assertIn('AssertionError', context['test_focus']['tests'][0]['assertion_excerpt'])
        self.assertEqual(result['run_id'], context['probe_id'])
        self.assertEqual([], result['rounds'])
        self.assertEqual(0, result['model_requests'])

    def test_approval_callback_cannot_mutate_authorized_scope(self):
        def approve(scope):
            scope['policy']['allowed_tests'] = [['unapproved']]
            return 'approve'
        result = self.collect(approval=approve)
        self.assertEqual('baseline_ready', result['status'], result)
        self.assertEqual([list(DEMO_TEST)], json.loads((self.directory/'run.json').read_bytes())['policy']['allowed_tests'])

    def test_green_baseline_is_not_treated_as_reproduced_bug(self):
        policy = replace(self.policy, allowed_tests=(('{python}', '-c', 'pass'),))
        result = self.collect(policy=policy)
        self.assertEqual('baseline_passes', result['status'])
        with self.assertRaises(SafetyError):
            self.binding(policy)(self.git, self.issue, policy.allowed_tests, self.files(policy))

    def test_source_changed_while_approval_waits_blocks_tests(self):
        def approve(scope):
            (self.repo/'notes.txt').write_text('concurrent user work')
            return 'approve'
        with patch('repo_maintainer.baseline_probe.run_test') as run:
            result = self.collect(approval=approve)
        run.assert_not_called()
        self.assertEqual('source_changed', result['status'])
        self.assertFalse(Path(result['workspace']).exists())
        self.assertEqual('concurrent user work', (self.repo/'notes.txt').read_text())

    def test_source_changed_during_test_is_preserved_and_context_is_refused(self):
        def concurrent(command, workspace, timeout, on_start=None):
            result = actual_test(command, workspace, timeout, on_start)
            (self.repo/'notes.txt').write_text('external user work')
            return result
        with patch('repo_maintainer.baseline_probe.run_test', side_effect=concurrent):
            result = self.collect()
        self.assertEqual('source_changed', result['status'], result)
        self.assertFalse(Path(result['workspace']).exists())
        self.assertEqual('external user work', (self.repo/'notes.txt').read_text())

    def test_scope_limits_and_unknown_approval_never_launch_tests(self):
        with patch('repo_maintainer.baseline_probe.run_test') as run:
            with self.assertRaises(SafetyError):
                self.collect(policy=replace(self.policy, allowed_tests=(DEMO_TEST,)*6))
            result = self.collect('unknown')
        run.assert_not_called()
        self.assertEqual('baseline_failed', result['status'])

    def test_dirty_source_and_unsafe_destination_are_rejected_without_commands(self):
        with self.assertRaises(SafetyError):
            collect_baseline(self.repo, self.issue, self.policy, self.repo/'probes', lambda s:'approve')
        (self.repo/'calculator.py').write_text('user changes')
        with patch('repo_maintainer.baseline_probe.run_test') as run, self.assertRaises(SafetyError):
            self.collect()
        run.assert_not_called()

    def test_changed_candidate_and_ignored_side_effect_are_preserved(self):
        for name in ('calculator.py', '__pycache__/side-effect.txt'):
            def side_effect(command, workspace, timeout, on_start=None):
                result = actual_test(command, workspace, timeout, on_start)
                path = workspace/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('retain this unexpected edit')
                return result
            with self.subTest(name=name), patch('repo_maintainer.baseline_probe.run_test', side_effect=side_effect):
                result = self.collect()
            self.assertEqual('baseline_retained', result['status'], result)
            self.assertEqual('retain this unexpected edit', (Path(result['workspace'])/name).read_text())
            self.assertTrue(result['source_unchanged'])
            with self.assertRaises(SafetyError):
                self.binding()(self.git, self.issue, self.policy.allowed_tests, self.files())
            cleanup_run(self.directory)

    def test_timeout_is_not_a_reproduced_bug(self):
        policy = replace(self.policy, allowed_tests=(('{python}','-c','import time; time.sleep(5)'),), timeout_seconds=.2)
        result = self.collect(policy=policy)
        self.assertEqual('baseline_failed', result['status'], result)
        self.assertTrue(result['baseline_tests'][0]['timed_out'])
        self.assertFalse(Path(result['workspace']).exists())

    def test_unknown_launch_is_retained_and_not_marked_finished_or_replayable(self):
        with patch('repo_maintainer.baseline_probe.run_test', side_effect=OSError('synthetic launch failure')):
            result = self.collect()
        self.assertEqual('baseline_retained', result['status'])
        self.assertNotIn('finished_at', result)
        self.assertTrue(Path(result['workspace']).exists())
        with self.assertRaisesRegex(SafetyError, 'Interrupted repair'):
            cleanup_run(self.directory)

    def test_receipt_changes_are_rejected_before_credentials_or_node_launch(self):
        self.collect()
        provider = PiProvider(self.policy.allowed_paths)
        provider.baseline_context = self.binding()
        provider.config_file = self.root/'never-read-provider-config'
        path = self.directory/'run.json'
        path.write_bytes(path.read_bytes()+b'\n')
        with patch('repo_maintainer.pi_provider.subprocess.Popen') as spawn, \
                patch('repo_maintainer.provider_config.load_provider_config') as config, \
                self.assertRaisesRegex(SafetyError, 'receipt changed'):
            provider.plan(self.git, self.issue, self.policy.allowed_tests)
        config.assert_not_called()
        spawn.assert_not_called()
        self.assertEqual(0, provider.requests_used)

    def test_task_issue_test_order_and_source_projection_cannot_be_rebound(self):
        self.collect()
        evidence = self.binding()
        cases = [(self.issue+'changed', self.policy.allowed_tests, self.files()),
                 (self.issue, (('{python}','-c','pass'),), self.files()),
                 (self.issue, self.policy.allowed_tests, [{**self.files()[0], 'editable':False}])]
        for issue, tests, files in cases:
            with self.subTest(issue=issue, tests=tests), self.assertRaises(SafetyError):
                evidence(self.git, issue, tests, files)

    def test_source_change_after_probe_invalidates_first_planning_context(self):
        self.collect()
        evidence = self.binding()
        (self.repo/'calculator.py').write_text('concurrent user code')
        with self.assertRaisesRegex(SafetyError, 'current task'):
            evidence(self.git, self.issue, self.policy.allowed_tests, self.files())

    def test_fifth_failed_check_is_prioritized_and_readonly_scope_is_bound(self):
        commands = tuple(('{python}','-c','pass'+f' # {i}') for i in range(4)) + (DEMO_TEST,)
        policy = replace(self.policy, allowed_tests=commands, readonly_paths=('tests/test_calculator.py',))
        result = self.collect(policy=policy)
        self.assertEqual('baseline_ready', result['status'], result)
        context = self.binding(policy)(self.git, self.issue, commands, self.files(policy))
        self.assertEqual(5, context['test_focus']['tests'][0]['check_index'])
        self.assertEqual(1, context['test_focus']['omitted_tests'])

    def test_durable_session_cannot_silently_adopt_cli_preflight(self):
        self.collect()
        provider = PiProvider(self.policy.allowed_paths)
        provider.baseline_context = self.binding()
        provider._task_session_request = {'unrelated':'existing'}
        with patch('repo_maintainer.pi_provider.subprocess.Popen') as spawn, self.assertRaisesRegex(SafetyError, 'durable'):
            provider.plan(self.git, self.issue, self.policy.allowed_tests)
        spawn.assert_not_called()

    def test_cli_preview_does_not_read_config_or_launch_model(self):
        policy_file = self.root/'policy.json'
        policy_file.write_text(json.dumps({'allowed_paths':['calculator.py'],'allowed_tests':[list(DEMO_TEST)]}))
        output = io.StringIO()
        with redirect_stdout(output), patch('sys.stdin', SimpleNamespace(isatty=lambda:False)), \
                patch.object(PiProvider, '_generate') as generate, \
                patch('repo_maintainer.provider_config.load_provider_config') as config:
            code = main(['plan','--repo',str(self.repo),'--issue',self.issue,'--policy',str(policy_file),
                         '--provider-config',str(self.root/'do-not-read'),
                         '--preflight-runs-dir',str(self.root/'cli-probes')])
        self.assertEqual(2, code, output.getvalue())
        self.assertIn('awaiting_baseline_approval', output.getvalue())
        generate.assert_not_called()
        config.assert_not_called()

    def test_baseline_hash_is_optional_and_malformed_receipt_is_not_accepted(self):
        from repo_maintainer.pi_evidence import validate_evidence
        from test_pi_evidence import evidence
        value = evidence()
        self.assertNotIn('baseline_context_sha256', validate_evidence(value, 6))
        value['baseline_context_sha256'] = 'a'*64
        self.assertEqual('a'*64, validate_evidence(value, 6)['baseline_context_sha256'])
        value['baseline_context_sha256'] = 'not-a-hash'
        with self.assertRaises(SafetyError):
            validate_evidence(value, 6)


if __name__ == '__main__':
    unittest.main()
