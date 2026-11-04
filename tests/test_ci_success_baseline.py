import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from repo_maintainer.ci_success_baseline import (GitHubComparableHistory, Incomparable,
                                                compare_success, investigate_success)
from repo_maintainer.issue_intake import fingerprint


def seal(value):
    value['evidence'] = {'complete': True, **{k+'_sha256': fingerprint(value[k])
        for k in ('workflow', 'run', 'job', 'checks', 'environment', 'dependencies')}}
    return value


def observation(success, *, version=None, day=None):
    version = version or ('a'*40 if success else 'b'*40)
    day = day or ('01' if success else '02')
    conclusion = 'success' if success else 'failure'
    started, finished = f'2026-10-{day}T01:00:00Z', f'2026-10-{day}T01:01:00Z'
    return seal(dict(repository='example/project', branch='main', sha=version,
        workflow={'id': 1, 'path': '.github/workflows/ci.yml', 'content_sha256': 'c'*64},
        run=dict(id=10 if success else 20, attempt=1, repository='example/project', branch='main', sha=version,
                 workflow_id=1, event='push', status='completed', conclusion=conclusion,
                 started_at=started, completed_at=finished),
        job=dict(id=100 if success else 200, run_id=10 if success else 20, attempt=1,
                 name='Python (3.13, ubuntu)', runner_id=55, status='completed', conclusion=conclusion,
                 started_at=started, completed_at=finished),
        checks=[dict(name='unit', argv=['python', '-m', 'unittest', 'discover', '-s', 'tests'],
                     scope=['src/', 'tests/'], status='completed', conclusion=conclusion, executed_tests=5)],
        environment=dict(os='linux', arch='x64', runtime='python', runtime_version='3.13.9',
                         image='ubuntu-24.04', image_version='20261001.1'),
        dependencies=dict(manifest_sha256='d'*64, lock_sha256='e'*64, resolved_sha256='f'*64)))


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.current, self.history = observation(False), [observation(True)]
        self.calls = []
        self.raw = dict(base_commit={'sha': 'a'*40}, merge_base_commit={'sha': 'a'*40}, status='ahead',
                        total_commits=1, commits=[{'sha': 'b'*40}], files=[
                            {'filename': 'src/app.py', 'status': 'modified', 'patch': '-old\n+new'},
                            {'filename': 'README.md', 'status': 'modified', 'patch': 'unrelated'}])

    def compare(self, repository, base, head):
        self.calls.append((repository, base, head))
        raw = copy.deepcopy(self.raw)
        raw['base_commit']['sha'] = raw['merge_base_commit']['sha'] = base
        return raw

    def result(self):
        return compare_success(self.current, self.history, self.compare)

    def rejected(self, reason):
        result = self.result()
        self.assertEqual(result['status'], 'unconfirmed', result)
        self.assertEqual(result['message'], '无法确认可比较基线')
        self.assertIn(reason, [r['reason'] for r in result['rejections']])
        return result

    def test_correct_match_provenance_and_relevant_differences(self):
        result = self.result()
        self.assertEqual(result['status'], 'comparable', result)
        p = result['baseline']
        for key in ['repository', 'branch', 'sha', 'workflow', 'run_id', 'attempt', 'job_id']:
            self.assertIn(key, p)
        self.assertEqual(result['diff']['relevant_files'][0]['patch'], '-old\n+new')
        self.assertEqual(len(result['diff']['relevant_files']), 1)
        self.assertIn('不能仅凭差异', result['diff']['interpretation'])
        self.assertEqual(self.calls, [('example/project', 'a'*40, 'b'*40)])

    def test_latest_comparable_success_not_input_order(self):
        older = observation(True, version='9'*40, day='01')
        newer = observation(True, version='8'*40, day='02')
        self.current = observation(False, day='03')
        self.history = [older, newer]
        self.assertEqual(self.result()['baseline']['sha'], '8'*40)
        self.assertEqual(len(self.calls), 1)

    def test_different_branches_and_repositories_do_not_match(self):
        for key, new, reason in [('branch', 'feature', 'different_branch'),
                                 ('repository', 'other/project', 'different_repository')]:
            with self.subTest(key=key):
                self.history = [observation(True)]
                self.history[0][key] = self.history[0]['run'][key] = new
                seal(self.history[0])
                self.rejected(reason)
        self.assertEqual(self.calls, [])

    def test_environment_and_dependencies_must_match(self):
        for group, key, new in [('environment', 'image_version', 'new-image'),
                                ('environment', 'runtime_version', '3.11.1'),
                                ('dependencies', 'resolved_sha256', '1'*64),
                                ('dependencies', 'lock_sha256', '2'*64)]:
            with self.subTest(key=key):
                self.history = [observation(True)]
                self.history[0][group][key] = new
                seal(self.history[0])
                self.rejected('different_'+group)
        self.assertEqual(self.calls, [])

    def test_workflow_content_identity_or_path_change_rejected(self):
        for key, new in [('content_sha256', '1'*64), ('path', '.github/workflows/other.yml'), ('id', 2)]:
            with self.subTest(key=key):
                self.history = [observation(True)]
                self.history[0]['workflow'][key] = new
                if key == 'id':
                    self.history[0]['run']['workflow_id'] = new
                seal(self.history[0])
                self.rejected('different_workflow')

    def test_scope_command_and_missing_check_do_not_match(self):
        for key, new in [('scope', ['tests/unit/']), ('argv', ['python', '-m', 'pytest'])]:
            with self.subTest(key=key):
                self.history = [observation(True)]
                self.history[0]['checks'][0][key] = new
                seal(self.history[0])
                self.rejected('different_checks')
        self.history[0]['checks'] = []
        seal(self.history[0])
        self.rejected('missing_checks')

    def test_cancelled_queued_and_no_runner_are_not_code_baselines(self):
        for status, conclusion in [('completed', 'cancelled'), ('queued', None), ('completed', 'skipped')]:
            with self.subTest(status=status, conclusion=conclusion):
                self.history = [observation(True)]
                self.history[0]['run'].update(status=status, conclusion=conclusion)
                seal(self.history[0])
                self.rejected('historical_run_not_success')
        self.history = [observation(True)]
        self.history[0]['job']['runner_id'] = None
        seal(self.history[0])
        self.rejected('job_has_no_runner')

    def test_current_cancelled_or_no_runner_retains_reason(self):
        self.current['run']['conclusion'] = 'cancelled'
        seal(self.current)
        self.rejected('current_run_not_failure')
        self.current = observation(False)
        self.current['job']['runner_id'] = 0
        seal(self.current)
        self.rejected('job_has_no_runner')

    def test_zero_tests_or_missing_actual_environment_never_green(self):
        self.history[0]['checks'][0]['executed_tests'] = 0
        seal(self.history[0])
        self.rejected('missing_executed_checks')
        self.history = [observation(True)]
        self.history[0]['environment'] = {}
        seal(self.history[0])
        self.rejected('missing_actual_environment')

    def test_job_attempt_and_run_sha_membership_required(self):
        self.history[0]['job']['attempt'] = 2
        seal(self.history[0])
        self.rejected('run_job_membership_mismatch')
        self.history = [observation(True)]
        self.history[0]['run']['sha'] = '7'*40
        seal(self.history[0])
        self.rejected('run_job_membership_mismatch')

    def test_missing_workflow_content_and_incomplete_observation(self):
        self.history[0]['workflow'].pop('content_sha256')
        seal(self.history[0])
        self.rejected('missing_workflow_content')
        self.history = [observation(True)]
        self.history[0]['evidence']['complete'] = False
        self.rejected('incomplete_evidence')

    def test_observation_tamper_not_accepted(self):
        self.history[0]['environment']['runtime_version'] = '3.12.1'
        self.rejected('evidence_digest_mismatch_environment')

    def test_future_success_not_a_previous_success(self):
        self.history = [observation(True, day='03')]
        self.rejected('success_not_before_current_attempt')

    def test_missing_timezone_is_not_comparable(self):
        self.history[0]['run']['started_at'] = '2026-10-01T01:00:00'
        seal(self.history[0])
        self.rejected('timestamp_without_timezone')

    def test_empty_history_is_explicitly_unconfirmed(self):
        self.history = []
        self.rejected('no_historical_observations')

    def test_compare_failure_is_sanitized_and_not_retried(self):
        def unavailable(*args):
            self.calls.append(args)
            raise TimeoutError('token=DO_NOT_ECHO')
        result = compare_success(self.current, self.history, unavailable)
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertNotIn('DO_NOT_ECHO', json.dumps(result))
        self.assertEqual(result['rejections'][0]['reason'], 'compare_unavailable')
        self.assertEqual(len(self.calls), 1)

    def test_nonancestor_success_not_used_even_with_matching_environment(self):
        self.raw['status'] = 'diverged'
        self.rejected('history_not_confirmed_ancestor')

    def test_compare_missing_commits_and_truncated_files_rejected(self):
        self.raw['total_commits'] = 2
        self.rejected('commit_comparison_incomplete')
        self.raw['total_commits'] = 1
        self.raw['files'] = [{'filename': 'src/a.py'}]*300
        self.rejected('file_comparison_incomplete')

    def test_patch_redaction_and_unavailable_patch_preserve_link(self):
        self.raw['files'][0]['patch'] = '+token=TOPSECRET\n+opaque-secret'
        result = compare_success(self.current, self.history, self.compare, known_secrets=('opaque-secret',))
        self.assertNotIn('TOPSECRET', json.dumps(result))
        self.assertNotIn('opaque-secret', json.dumps(result))
        self.raw['files'][0]['patch'] = 'a'*17000
        result = self.result()
        item = result['diff']['relevant_files'][0]
        self.assertIsNone(item['patch'])
        self.assertIn('compare/', item['url'])

    def test_same_sha_previous_attempt_can_have_empty_diff(self):
        self.history = [observation(True, version='b'*40)]
        self.raw.update(status='identical', total_commits=0, commits=[], files=[])
        result = self.result()
        self.assertEqual(result['status'], 'comparable', result)
        self.assertEqual(result['diff']['total_commits'], 0)

    def test_inputs_are_not_mutated(self):
        original = copy.deepcopy((self.current, self.history))
        result = self.result()
        result['comparison']['environment']['os'] = 'mutated'
        self.assertEqual((self.current, self.history), original)

    def test_readonly_discovery_hydrates_exact_run_and_never_uses_api_green_alone(self):
        client = GitHubComparableHistory()
        raw_run = dict(id=10, run_attempt=1, head_sha='a'*40, head_branch='main', workflow_id=1,
                       status='completed', conclusion='success', repository={'full_name': 'example/project'})
        paths = []
        def request(p):
            paths.append(p)
            if '/compare/' in p:
                return copy.deepcopy(self.raw)
            return {'total_count': 1, 'workflow_runs': [raw_run]}
        client._request = request
        result = investigate_success(self.current, client, lambda run: copy.deepcopy(self.history))
        self.assertEqual(result['status'], 'comparable', result)
        self.assertEqual(paths[0], '/repos/example/project/actions/workflows/1/runs?branch=main&status=success&per_page=30&page=1')
        self.assertTrue(result['history_search']['complete'])
        result = investigate_success(self.current, client, lambda run: [])
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertIn('historical_job_details_missing', [r['reason'] for r in result['rejections']])

    def test_history_reader_rejects_wrong_scope(self):
        client = GitHubComparableHistory()
        client._request = lambda p: {'total_count': 1, 'workflow_runs': [dict(id=10, run_attempt=1, head_sha='a'*40,
            head_branch='other', workflow_id=1, status='completed', conclusion='success',
            repository={'full_name': 'example/project'})]}
        with self.assertRaises(Incomparable):
            client.successful_runs('example/project', 'main', 1)

    def history_rows(self, start, count):
        return [dict(id=i, run_attempt=1, head_sha='a'*40, head_branch='main', workflow_id=1,
                     status='completed', conclusion='success', repository={'full_name': 'example/project'})
                for i in range(start, start+count)]

    def test_history_reader_does_not_treat_short_incomplete_page_as_complete(self):
        client = GitHubComparableHistory()
        client._request = lambda p: {'total_count': 5, 'workflow_runs': self.history_rows(1, 1)}
        with self.assertRaisesRegex(Incomparable, 'workflow_run_pagination_incomplete'):
            client.successful_runs('example/project', 'main', 1)
        result = investigate_success(self.current, client, lambda run: self.history)
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertEqual(result['rejections'][0]['reason'], 'workflow_run_pagination_incomplete')

    def test_history_reader_rejects_duplicate_or_changed_pages(self):
        client = GitHubComparableHistory()
        client._request = lambda p: {'total_count': 2, 'workflow_runs': self.history_rows(1, 1)*2}
        with self.assertRaisesRegex(Incomparable, 'duplicate_workflow_run_page'):
            client.successful_runs('example/project', 'main', 1)
        pages = iter([{'total_count': 31, 'workflow_runs': self.history_rows(1, 30)},
                      {'total_count': 32, 'workflow_runs': self.history_rows(31, 2)}])
        client._request = lambda p: next(pages)
        with self.assertRaisesRegex(Incomparable, 'workflow_run_page_count_changed'):
            client.successful_runs('example/project', 'main', 1)

    def test_history_reader_preserves_bound_without_claiming_all_history(self):
        client = GitHubComparableHistory()
        pages = iter([{'total_count': 120, 'workflow_runs': self.history_rows(i, 30)} for i in (1, 31, 61)])
        client._request = lambda p: next(pages)
        result = client.successful_runs('example/project', 'main', 1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['reason'], 'history_window_bound')
        self.assertEqual(len(result['runs']), 90)

    def test_hydrated_job_cannot_belong_to_another_attempt(self):
        class Reader:
            compare = self.compare
            def successful_runs(inner, *args):
                return {'runs': [dict(id=10, run_attempt=2, head_sha='a'*40, head_branch='main', workflow_id=1)],
                        'complete': True}
        result = investigate_success(self.current, Reader(), lambda run: self.history)
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertIn('hydrated_run_scope_mismatch', [r['reason'] for r in result['rejections']])

    def test_discovery_error_retains_unconfirmed_reason(self):
        class Reader:
            compare = self.compare
            def successful_runs(inner, *args):
                raise TimeoutError('private failure')
        result = investigate_success(self.current, Reader(), lambda run: self.history)
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertEqual(result['rejections'][0]['reason'], 'history_discovery_unavailable')

    def test_real_git_graph_diff_not_code_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], text=True).strip()
            git('init', '-q')
            (root/'src').mkdir()
            (root/'src/app.py').write_text('def value(): return 1\n')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'baseline')
            base = git('rev-parse', 'HEAD')
            (root/'src/app.py').write_text('def value(): return 2\n')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'changed')
            head = git('rev-parse', 'HEAD')
            current, history = observation(False, version=head), [observation(True, version=base)]
            def actual_compare(repo, before, after):
                return dict(base_commit={'sha': before}, merge_base_commit={'sha': git('merge-base', before, after)},
                    status='ahead', total_commits=int(git('rev-list', '--count', before+'..'+after)),
                    commits=[{'sha': s} for s in git('rev-list', '--reverse', before+'..'+after).splitlines()],
                    files=[{'filename': name, 'status': 'modified', 'patch': git('diff', before, after, '--', name)}
                           for name in git('diff', '--name-only', before, after).splitlines()])
            result = compare_success(current, history, actual_compare)
            self.assertEqual(result['status'], 'comparable', result)
            self.assertIn('+def value(): return 2', result['diff']['relevant_files'][0]['patch'])
            self.assertEqual(git('rev-parse', 'HEAD'), head)
            self.assertEqual(git('status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()
