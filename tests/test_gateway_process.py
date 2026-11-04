"""Real loopback/process lifecycle tests; never kill a process to prove stop."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from repo_maintainer import gateway
from repo_maintainer.models import SafetyError


class GatewayProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='repocare-gateway-process-')
        self.home = Path(self.temporary.name)/'private'
        self.processes = []
        self.outputs = []
        self.markers = []
        self.addCleanup(self.cleanup_processes)
        self.assertEqual(self.command('setup', '--ui', 'legacy', '--request-budget', '37')[0], 0)

    def command(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = gateway.main([*arguments, '--home', str(self.home), '--json'])
        return code, json.loads(output.getvalue())

    def start(self, operation='run', marker=None):
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(gateway.__file__).parents[1])
        environment['PYTHONIOENCODING'] = 'utf-8'
        output = tempfile.TemporaryFile(mode='w+b')
        self.outputs.append(output)
        if marker is None:
            arguments = [sys.executable, '-B', '-m', 'repo_maintainer.gateway', 'gateway', operation,
                         '--home', str(self.home), '--json']
        else:
            self.markers.append(marker)
            source = (
                'import sys;from pathlib import Path;from repo_maintainer.repository_workbench import RepositoryWorkbench;'
                'from repo_maintainer import gateway;original=RepositoryWorkbench.service\n'
                'def service(self):\n'
                ' value=original(self)\n'
                ' if Path(sys.argv[1]).exists(): value["active"]="fixture_busy"\n'
                ' return value\n'
                'RepositoryWorkbench.service=service\n'
                'raise SystemExit(gateway.main(["gateway","run","--home",sys.argv[2],"--json"]))'
            )
            arguments = [sys.executable, '-B', '-c', source, str(marker), str(self.home)]
        process = subprocess.Popen(arguments, stdout=output, stderr=output, env=environment,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.processes.append(process)
        return process

    def await_verified(self, *, different_from=None, timeout=15):
        deadline = time.monotonic()+timeout
        last = None
        while time.monotonic() < deadline:
            last = gateway.gateway_status(self.home)
            if last.get('identity_verified'):
                receipt = gateway._receipt(self.home, gateway.load_configuration(self.home))
                if receipt['instance_id'] != different_from:
                    return receipt, last
            if self.processes[-1].poll() is not None:
                break
            time.sleep(.05)
        self.fail(f'Gateway not verified: {last}; child exit={self.processes[-1].poll()}')

    def cleanup_processes(self):
        for marker in self.markers:
            marker.unlink(missing_ok=True)
        # Restore a tampered test receipt before reaching this cleanup. Failures
        # retain live fixtures instead of turning force-kill into false proof.
        if any(p.poll() is None for p in self.processes):
            try:
                gateway.stop_gateway(self.home, wait_seconds=10)
            except (OSError, ValueError, SafetyError):
                pass
        alive = []
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                alive.append(process.pid)
        for output in self.outputs:
            output.close()
        if alive:
            self.temporary._finalizer.detach()
            raise AssertionError(f'Safe stop not confirmed; retained fixture processes: {alive}')
        self.temporary.cleanup()

    def test_real_run_open_readonly_status_and_safe_stop(self):
        process = self.start()
        receipt, status = self.await_verified()
        self.assertEqual(status['ready'], 'unknown')
        self.assertFalse(status['busy'])
        metadata = self.home/'workbench'/'workbench.json'
        before = metadata.read_bytes()
        control = (self.home/'gateway'/'control.json').read_bytes()
        lock_path = self.home/'workbench'/'workbench.lock'
        lock = (lock_path.stat().st_size, lock_path.stat().st_mtime_ns)
        with patch('repo_maintainer.gateway.webbrowser.open', return_value=True) as opening:
            self.assertEqual(self.command('gateway', 'open')[1]['status'], 'opened')
            self.assertIn('#session='+receipt['token'], opening.call_args.args[0])
        code, result = self.command('gateway', 'status')
        self.assertEqual(code, 0)
        self.assertTrue(result['identity_verified'])
        self.assertNotIn(receipt['token'], json.dumps(result))
        self.assertEqual(metadata.read_bytes(), before)
        self.assertEqual((self.home/'gateway'/'control.json').read_bytes(), control)
        self.assertEqual((lock_path.stat().st_size, lock_path.stat().st_mtime_ns), lock)
        stopped = gateway.stop_gateway(self.home, wait_seconds=10)
        self.assertEqual(stopped['status'], 'stopped')
        self.assertEqual(process.wait(timeout=2), 0)
        self.assertIn(gateway._lock_state(self.home/'workbench'), {'released', 'absent'})
        self.outputs[0].seek(0)
        self.assertNotIn(receipt['token'].encode(), self.outputs[0].read())
        self.assertEqual(metadata.read_bytes(), before)

    def test_restart_changes_instance_but_preserves_original_budget_and_state(self):
        original_process = self.start()
        old, _ = self.await_verified()
        metadata = self.home/'workbench'/'workbench.json'
        original = metadata.read_bytes()
        preserved = self.home/'workbench'/'native-preservation-fixture.bin'
        preserved.write_bytes(b'original-native-bytes\x00unchanged')
        replacement = self.start('restart')
        new, status = self.await_verified(different_from=old['instance_id'])
        self.assertEqual(original_process.wait(timeout=2), 0)
        self.assertNotEqual(old['token'], new['token'])
        self.assertEqual(new['request_budget'], 37)
        self.assertEqual(metadata.read_bytes(), original)
        self.assertEqual(preserved.read_bytes(), b'original-native-bytes\x00unchanged')
        self.assertEqual(gateway.stop_gateway(self.home, wait_seconds=10)['status'], 'stopped')
        self.assertEqual(replacement.wait(timeout=2), 0)

    def test_busy_refuses_stop_and_restart_without_posting(self):
        marker = Path(self.temporary.name)/'busy'
        marker.touch()
        process = self.start(marker=marker)
        self.await_verified()
        self.assertTrue(gateway.gateway_status(self.home)['busy'])
        with patch('repo_maintainer.gateway._request', wraps=gateway._request) as request:
            with self.assertRaises(SafetyError):
                gateway.stop_gateway(self.home)
            with self.assertRaises(SafetyError):
                gateway.restart_gateway(self.home)
            self.assertTrue(all(len(call.args) == 1 for call in request.call_args_list))
        self.assertIsNone(process.poll())
        marker.unlink()
        self.assertEqual(gateway.stop_gateway(self.home, wait_seconds=10)['status'], 'stopped')
        self.assertEqual(process.wait(timeout=2), 0)

    def test_invalid_token_and_identity_refuse_control_and_open(self):
        process = self.start()
        original, _ = self.await_verified()
        target = self.home/'gateway'/'control.json'
        for changes in ({'token': '0'*64}, {'instance_id': '0'*32}, {'process_created': original['process_created']-10}):
            with self.subTest(changes=list(changes)):
                gateway._write_private(target, dict(original, **changes), replace=True)
                try:
                    self.assertEqual(gateway.gateway_status(self.home)['status'], 'unknown')
                    with patch('repo_maintainer.gateway.webbrowser.open') as opening:
                        self.assertEqual(self.command('gateway', 'open')[0], 1)
                        opening.assert_not_called()
                    with self.assertRaises(SafetyError):
                        gateway.restart_gateway(self.home)
                    self.assertIsNone(process.poll())
                finally:
                    gateway._write_private(target, original, replace=True)

    def test_http_202_alone_does_not_mean_stopped_and_no_replay(self):
        process = self.start()
        self.await_verified()
        original_request = gateway._request
        sent = []

        def accepted_without_stopping(receipt, method='GET', payload=None, **kwargs):
            if method == 'POST':
                sent.append(payload)
                return {'id': payload['request_id'], 'action': 'stop_workbench', 'status': 'running'}
            return original_request(receipt, method, payload, **kwargs)

        with patch('repo_maintainer.gateway._request', side_effect=accepted_without_stopping), \
                patch('repo_maintainer.gateway.run_gateway') as run:
            result = gateway.restart_gateway(self.home, wait_seconds=.15)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(sent), 1)
        run.assert_not_called()
        self.assertIsNone(process.poll())

    def test_stop_distinguishes_read_transport_from_uncertain_post(self):
        process = self.start()
        self.await_verified()
        original_request = gateway._request
        for stage in ('GET', 'POST'):
            with self.subTest(stage=stage):
                def disconnected(receipt, method='GET', payload=None):
                    if method == stage:
                        raise TimeoutError('private fixture secret')
                    return original_request(receipt, method, payload)

                with patch('repo_maintainer.gateway._request', side_effect=disconnected) as request:
                    code, result = self.command('gateway', 'stop')
                self.assertEqual(code, 1)
                self.assertNotIn('private fixture secret', json.dumps(result))
                self.assertEqual(request.call_count, 1 if stage == 'GET' else 2)
                if stage == 'GET':
                    self.assertEqual(result['status'], 'blocked')
                    self.assertIn('未能读取本机服务状态', result['detail'])
                else:
                    self.assertEqual(result['status'], 'unknown')
                    self.assertTrue(result['request_sent'])
                    self.assertFalse(result['automatic_retry'])
                    self.assertIn('未收到可核对的停止回执', result['detail'])
                self.assertIsNone(process.poll())

    def test_stop_reports_already_stopping_and_explicit_rejection(self):
        process = self.start()
        receipt, _ = self.await_verified()
        service = gateway._request(receipt)
        with patch('repo_maintainer.gateway._request', return_value=dict(service, stopping=True)) as request:
            self.assertEqual(self.command('gateway', 'status')[1]['status'], 'stopping')
            code, result = self.command('gateway', 'stop')
            self.assertEqual(code, 1)
            self.assertIn('原服务正在停止', result['detail'])
            self.assertTrue(all(len(call.args) == 1 for call in request.call_args_list))
        rejected = gateway.GatewayStopRejected('fixture stop rejection')
        with patch('repo_maintainer.gateway._request', side_effect=[service, rejected]) as request:
            code, result = self.command('gateway', 'restart')
            self.assertEqual(code, 1)
            self.assertEqual(result['status'], 'blocked')
            self.assertTrue(result['request_sent'])
            self.assertFalse(result['automatic_retry'])
            self.assertEqual(request.call_count, 2)
        self.assertIsNone(process.poll())

    def test_duplicate_run_keeps_original_live(self):
        process = self.start()
        original, _ = self.await_verified()
        with self.assertRaises(SafetyError):
            gateway.run_gateway(self.home)
        current, _ = self.await_verified()
        self.assertEqual(current['instance_id'], original['instance_id'])
        self.assertIsNone(process.poll())

    def test_status_reports_drift_and_readiness_without_running_checks(self):
        self.start()
        receipt, _ = self.await_verified()
        original = gateway._request(receipt)
        for changes, expected, readiness in (
                ({'runtime': dict(original['runtime'], code_changed=True)}, 'code_changed', 'unknown'),
                ({'readiness': {'status': 'ready', 'stale': False}}, 'ready', 'ready'),
                ({'readiness': {'status': 'ready', 'stale': True}}, 'running', 'unknown'),
                ({'history_active': 'history-operation'}, 'busy', 'unknown')):
            with self.subTest(expected=expected), patch('repo_maintainer.gateway._request', return_value=dict(original, **changes)) as reading:
                status = gateway.gateway_status(self.home)
                self.assertEqual(status['status'], expected)
                self.assertEqual(status['ready'], readiness)
                self.assertTrue(all(len(call.args) == 1 for call in reading.call_args_list))

    def test_http_redirect_is_not_followed_and_environment_proxy_is_ignored(self):
        self.start()
        receipt, _ = self.await_verified()
        requests = []

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(302)
                self.send_header('Location', 'https://example.invalid/never-send-the-token')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}):
                self.assertTrue(gateway.gateway_status(self.home)['identity_verified'])
                with self.assertRaises(SafetyError):
                    gateway._request(dict(receipt, port=server.server_port))
            self.assertEqual(requests, ['/api/service'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
