"""Use real subprocesses, with synthetic Git transport and no network access."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import psutil

from repo_maintainer.git import git_env
from repo_maintainer.git_investigation import _capture
from repo_maintainer.github_connection import GitHubConnection, SnapshotError


# The child inherits both writer handles. Its own short deadline bounds a failed test.
CHILD = """
import os, sys, time
from pathlib import Path
print('descendant stdout', flush=True)
print('descendant stderr', file=sys.stderr, flush=True)
Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(8)
"""
PARENT = """
import subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], stdin=subprocess.DEVNULL)
deadline = time.monotonic() + 3
while not Path(sys.argv[2]).is_file() and time.monotonic() < deadline:
    time.sleep(.01)
if sys.argv[3] == 'wait':
    time.sleep(8)
"""


class SnapshotFailureTests(unittest.TestCase):
    def test_api_head_success_does_not_hide_git_fetch_failure(self):
        github = GitHubConnection()
        sha = 'a' * 40
        stages = []

        def capture(argv, *args, **kwargs):
            stages.append(argv)
            if 'fetch' in argv:
                self.assertIn('http.sslVerify=true', argv)
                self.assertIn('https://github.com/example/source.git', argv)
                argv = [sys.executable, '-c',
                        "import sys; sys.stderr.write('proxy secret-fixture'); sys.exit(128)"]
            return _capture(argv, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(github, 'api', return_value={'commit': {'sha': sha}}), \
                patch('repo_maintainer.github_connection._capture', side_effect=capture):
            url = 'https://github.com/example/source'
            observed = github.branch_head(url, 'main')
            self.assertEqual(observed, sha)
            dest = Path(directory) / 'snapshot'
            with self.assertRaisesRegex(SnapshotError, r'Git fetch 失败（退出码 128）') as failure:
                github.snapshot(url, 'main', observed, dest)
            self.assertTrue((dest / '.git').is_dir())
        self.assertEqual(len(stages), 2)
        message = str(failure.exception)
        self.assertIn('GitHub API', message)
        self.assertIn('git ls-remote', message)
        self.assertIn('不会自动重放', message)
        self.assertNotIn('secret-fixture', message)


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object regression')
class WindowsSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pid_file = self.root / 'descendant.pid'

    def command(self, mode):
        return [sys.executable, '-c', PARENT, CHILD, str(self.pid_file), mode]

    def assert_descendant_exited(self):
        pid = int(self.pid_file.read_text())
        try:
            psutil.Process(pid).wait(timeout=2)
        except psutil.NoSuchProcess:
            pass

    def test_snapshot_timeout_releases_inherited_pipes_and_leaves_other_process_alive(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
        try:
            def capture(argv, *args, **kwargs):
                if 'fetch' in argv:
                    self.assertEqual(kwargs['timeout'], 120)
                    argv = self.command('wait')
                    kwargs['timeout'] = 2
                return _capture(argv, *args, **kwargs)

            dest = self.root / 'snapshot'
            started = time.monotonic()
            with patch('repo_maintainer.github_connection._capture', side_effect=capture):
                with self.assertRaisesRegex(SnapshotError, r'Git fetch 在 120 秒内未完成'):
                    GitHubConnection().snapshot('https://github.com/example/source', 'main', 'a'*40, dest)
            self.assertLess(time.monotonic() - started, 6)
            self.assertTrue((dest / '.git').is_dir())
            self.assert_descendant_exited()
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.kill()
            unrelated.wait(timeout=5)

    def test_root_exit_also_releases_descendant_pipe_handles(self):
        started = time.monotonic()
        result = _capture(self.command('exit'), self.root, git_env(), timeout=4, own_windows_job=True)
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(result['timed_out'])
        self.assertEqual(result['returncode'], 0)
        self.assertIn(b'descendant stdout', result['stdout'])
        self.assertIn(b'descendant stderr', result['stderr'])
        self.assert_descendant_exited()

    def test_snapshot_stops_before_checkout_when_fetch_output_exceeds_the_bound(self):
        def capture(argv, *args, **kwargs):
            if 'fetch' in argv:
                argv = [sys.executable, '-c', "import sys; sys.stderr.buffer.write(b'x' * 70000)"]
            return _capture(argv, *args, **kwargs)

        with patch('repo_maintainer.github_connection._capture', side_effect=capture) as call:
            with self.assertRaisesRegex(SnapshotError, r'Git fetch 输出超过上限'):
                GitHubConnection().snapshot('https://github.com/example/source', 'main', 'a'*40, self.root / 'snapshot')
        self.assertEqual(call.call_count, 2)

    def test_rejected_job_assignment_stops_the_suspended_command(self):
        import ctypes
        from repo_maintainer.windows_job import windows_job_process

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        popen, started = subprocess.Popen, []

        def record(*args, **kwargs):
            process = popen(*args, **kwargs)
            started.append(process)
            return process

        with patch.object(kernel, 'AssignProcessToJobObject', return_value=0) as rejected, \
                patch('repo_maintainer.windows_job.ctypes.WinDLL', return_value=kernel), \
                patch('repo_maintainer.windows_job.subprocess.Popen', side_effect=record):
            with self.assertRaises(OSError):
                with windows_job_process(self.command('wait'), stdout=subprocess.PIPE, stderr=subprocess.PIPE):
                    self.fail('Command ran without its Windows job')
        rejected.assert_called_once()
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].poll())
        self.assertFalse(self.pid_file.exists())
