"""stdio MCP and original Gateway control-path acceptance; synthetic only."""
import contextlib
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from repo_maintainer import gateway, mcp_server
from repo_maintainer.models import SafetyError


def environment():
    return dict(os.environ, PYTHONPATH=str(Path(mcp_server.__file__).parents[1]), PYTHONIOENCODING='utf-8')


class StdioClient:
    def __init__(self, home, *, allow_plan=False):
        self.stderr = tempfile.TemporaryFile(mode='w+b')
        self.process = subprocess.Popen([sys.executable, '-B', '-m', 'repo_maintainer.mcp_server', '--home', str(home),
                        *(['--allow-plan'] if allow_plan else [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=self.stderr, env=environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.messages = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.sequence = 0

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(line)

    def notify(self, method, params=None):
        value = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            value['params'] = params
        self.process.stdin.write((json.dumps(value, ensure_ascii=False)+'\n').encode('utf-8'))
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.sequence += 1
        value = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method}
        if params is not None:
            value['params'] = params
        self.process.stdin.write((json.dumps(value, ensure_ascii=False)+'\n').encode('utf-8'))
        self.process.stdin.flush()
        response = json.loads(self.messages.get(timeout=15).decode('utf-8'))
        if response.get('id') != self.sequence:
            raise AssertionError('Unexpected protocol response identity')
        return response

    def initialize(self):
        response = self.request('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                               'clientInfo': {'name': 'stdio-fixture', 'version': '1'}})
        self.notify('notifications/initialized')
        return response

    def tool(self, name, arguments=None):
        response = self.request('tools/call', {'name': name, 'arguments': arguments or {}})
        if 'error' in response or response['result'].get('isError'):
            raise AssertionError(response)
        return response['result']['structuredContent']

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        self.process.stdout.close()
        self.reader.join(timeout=2)
        self.stderr.close()
        if self.process.returncode:
            raise AssertionError(f'MCP stdio exit {self.process.returncode}')


class McpProtocolTests(unittest.TestCase):
    def initialized(self, **options):
        server = mcp_server.Server(**options)
        first = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'fixture', 'version': '1'}}})
        self.assertEqual(first['result']['protocolVersion'], '2025-11-25')
        self.assertIsNone(server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        return server

    def test_initialize_list_and_tutorial_need_no_running_gateway(self):
        server = self.initialized()
        tools = server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
        self.assertEqual({t['name'] for t in tools}, {'repocare_status', 'repocare_list_workspaces',
                          'repocare_read_workspace', 'repocare_get_operation'})
        resources = server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'resources/list'})['result']['resources']
        reading = server.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'resources/read', 'params': {'uri': resources[0]['uri']}})
        self.assertIn('repocare gateway run', reading['result']['contents'][0]['text'])

    def test_permission_is_enforced_separately_from_annotations(self):
        client = Mock()
        server = self.initialized(client=client)
        response = server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'repocare_create_task', 'arguments': {'workspace_id': 'a'*32, 'request_id': 'b'*32, 'issue': 'fix'}}})
        self.assertEqual(response['error']['code'], -32602)
        client.create_task.assert_not_called()
        writable = self.initialized(allow_plan=True, client=client)
        names = [row['name'] for row in writable.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']]
        self.assertIn('repocare_create_task', names)
        self.assertFalse(any(word in name for name in names for word in ('approve', 'publish', 'merge', 'shell', 'push')))

    def test_invalid_ids_methods_arguments_and_initialization_fail_closed(self):
        server = mcp_server.Server()
        self.assertEqual(server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['error']['code'], -32002)
        self.assertEqual(server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})['result'], {})
        server = self.initialized()
        for identity in (None, True, [], 2**55, ''):
            with self.subTest(identity=identity):
                self.assertEqual(server.handle({'jsonrpc': '2.0', 'id': identity, 'method': 'ping'})['error']['code'], -32600)
        self.assertEqual(server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'execute'})['error']['code'], -32601)
        self.assertIsNone(server.handle({'jsonrpc': '2.0', 'method': 'unknown/notification'}))
        for arguments in ({'workspace_id': '../outside'}, {'workspace_id': 'a'*32, 'route': '/api/actions/publish'}):
            response = server.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
                'name': 'repocare_read_workspace', 'arguments': arguments}})
            self.assertEqual(response['error']['code'], -32602)

    def test_protocol_negotiates_supported_version(self):
        response = mcp_server.Server().handle({'jsonrpc': '2.0', 'id': 'init', 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'client', 'version': '1'}}})
        self.assertEqual(response['result']['protocolVersion'], '2025-11-25')

    def test_parse_errors_duplicate_json_batch_and_message_bound(self):
        for frame in (b'not json\n', b'\xff\n', b'[]\n', b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n',
                      b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}\n'):
            with self.subTest(frame=frame):
                output = io.BytesIO()
                self.assertEqual(mcp_server.serve(mcp_server.Server(), io.BytesIO(frame), output), 0)
                response = json.loads(output.getvalue())
                self.assertIn(response['error']['code'], {-32700, -32600})
        output = io.BytesIO()
        self.assertEqual(mcp_server.serve(mcp_server.Server(), io.BytesIO(b'x'*(mcp_server.MAX_LINE+1)), output), 1)
        self.assertEqual(json.loads(output.getvalue())['error']['code'], -32700)

    def test_capability_and_credential_sanitization(self):
        secret = 'e'*64
        value = {'token': secret, 'api_prefix': '/private', 'launch_url': 'http://127.0.0.1/#session='+secret,
                 'nested': {'api_key': 'private-key', 'authorization': 'Bearer hidden', 'content': 'accidental '+secret},
                 'service_control': {'stop_revision': 'private'}, 'note': 'Bearer abcdefghij'}
        result = mcp_server.sanitize(value, [secret])
        serialized = json.dumps(result)
        for private in (secret, 'api_key', 'authorization', 'api_prefix', 'launch_url', 'stop_revision', 'abcdefghij'):
            self.assertNotIn(private, serialized)

    def test_only_fixed_original_routes_are_available(self):
        with patch('repo_maintainer.mcp_server.http.client.HTTPConnection') as connecting:
            for path in ('https://remote.invalid', '/api/actions/stop_workbench', '/api/workspaces/'+'a'*32+'/actions/decision'):
                with self.subTest(path=path), self.assertRaises(SafetyError):
                    mcp_server._api({'port': 12345, 'token': 'f'*64}, path)
            connecting.assert_not_called()

    def test_unknown_acknowledgement_is_not_automatically_replayed(self):
        with tempfile.TemporaryDirectory(prefix='repocare-mcp-unknown-') as root:
            home = Path(root)/'private'
            gateway._control_directory(home, create=True)
            receipt = {'port': 12345, 'token': 'f'*64, 'workspace_root': str(home/'workbench')}
            selected = {'token': 'e'*64, 'repository_id': 'd'*64, 'model_profile_id': 'legacy'}
            identity = 'a'*32
            context = (receipt, selected, {'jobs': []}, ['e'*64, 'f'*64])
            client = mcp_server.Client(home)
            with patch.object(client, '_workspace', return_value=context), \
                    patch('repo_maintainer.mcp_server._api', side_effect=OSError('private network detail')) as sending:
                with self.assertRaises(OSError):
                    client.create_task(identity, 'addition issue', 'b'*32)
                result = client.create_task(identity, 'addition issue', 'b'*32)
                self.assertEqual(result['status'], 'unknown')
                self.assertFalse(result['replayed'])
                self.assertEqual(sending.call_count, 1)
                with self.assertRaises(mcp_server.ProtocolError):
                    client.create_task(identity, 'changed issue', 'b'*32)
            reopened = mcp_server.Client(home)
            with patch.object(reopened, '_workspace', return_value=context), \
                    patch('repo_maintainer.mcp_server._api') as sending:
                self.assertEqual(reopened.create_task('c'*32, 'addition issue', 'b'*32)['status'], 'unknown')
                sending.assert_not_called()
            stored = next((home/'gateway'/'mcp-intents').glob('*.json')).read_text()
            for private in ('e'*64, 'f'*64, 'addition issue', 'request_budget'):
                self.assertNotIn(private, stored)

    def test_unknown_dispatch_receipt_survives_an_actual_stdio_process_restart(self):
        with tempfile.TemporaryDirectory(prefix='repocare-mcp-cross-process-') as root:
            home = Path(root)/'private'
            gateway._control_directory(home, create=True)
            # Both independent MCP processes see the same original scope. The
            # first loses its ACK; the second must not reach the HTTP transport.
            source = (
                'import sys;from pathlib import Path;from repo_maintainer import mcp_server as m\n'
                'class Client(m.Client):\n'
                ' def _workspace(self,identity):\n'
                '  return ({"port":12345,"token":"f"*64,"workspace_root":str(self.home/"workbench")},'
                '{"token":"e"*64,"repository_id":"d"*64,"model_profile_id":"legacy"},{"jobs":[]},["e"*64,"f"*64])\n'
                'def transport(*args,**kwargs):\n'
                ' if sys.argv[2]=="first": raise OSError("synthetic lost acknowledgement")\n'
                ' raise AssertionError("Unknown dispatch was replayed")\n'
                'm._api=transport\n'
                'raise SystemExit(m.serve(m.Server(sys.argv[1],allow_plan=True,client=Client(sys.argv[1])),sys.stdin.buffer,sys.stdout.buffer))'
            )
            messages = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                    'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'fixture', 'version': '1'}}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'repocare_create_task',
                    'arguments': {'workspace_id': 'a'*32, 'request_id': 'b'*32, 'issue': 'addition issue'}}},
            ]
            incoming = ('\n'.join(json.dumps(message) for message in messages)+'\n').encode()
            results = []
            for phase in ('first', 'reopened'):
                process = subprocess.run([sys.executable, '-B', '-c', source, str(home), phase], input=incoming,
                                         capture_output=True, env=environment(), timeout=15,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertEqual(process.returncode, 0)
                frames = [json.loads(line) for line in process.stdout.splitlines()]
                self.assertEqual(len(frames), 2)
                results.append(frames[1]['result'])
            self.assertTrue(results[0]['isError'])
            self.assertFalse(results[1]['isError'])
            self.assertEqual(results[1]['structuredContent']['status'], 'unknown')
            self.assertFalse(results[1]['structuredContent']['replayed'])

    def test_dispatch_receipts_are_bounded_before_any_post(self):
        with tempfile.TemporaryDirectory(prefix='repocare-mcp-bounded-') as root:
            home = Path(root)/'private'
            parent = gateway._control_directory(home, create=True)
            directory = parent/'mcp-intents'
            directory.mkdir()
            gateway._protect(directory, directory=True)
            for number in range(1000):
                (directory/f'{number:064x}.json').write_text('{}')
            client = mcp_server.Client(home)
            receipt = {'workspace_root': str(home/'workbench'), 'port': 12345, 'token': 'f'*64}
            selected = {'repository_id': 'd'*64, 'model_profile_id': 'legacy', 'token': 'e'*64}
            with patch.object(client, '_workspace', return_value=(receipt, selected, {'jobs': []}, [])), \
                    patch('repo_maintainer.mcp_server._api') as sending:
                with self.assertRaises(SafetyError):
                    client.create_task('a'*32, 'new issue', 'b'*32)
                sending.assert_not_called()
            self.assertEqual(len(list(directory.glob('*.json'))), 1000)

    def test_real_stdio_initialize_list_tutorial_and_eof_shutdown(self):
        with tempfile.TemporaryDirectory(prefix='repocare-mcp-stdio-') as root:
            home = Path(root)/'never-configured'
            client = StdioClient(home)
            try:
                self.assertEqual(client.initialize()['result']['protocolVersion'], '2025-11-25')
                self.assertEqual(len(client.request('tools/list')['result']['tools']), 4)
                resources = client.request('resources/list')['result']['resources']
                guide = client.request('resources/read', {'uri': resources[0]['uri']})
                self.assertIn('repocare gateway run', guide['result']['contents'][0]['text'])
                self.assertEqual(client.tool('repocare_status')['status'], 'not_configured')
                self.assertFalse(home.exists())
            finally:
                client.close()


class McpOriginalGatewayIntegrationTests(unittest.TestCase):
    def test_stdio_plan_uses_actual_pi_original_controller_ledger_and_native(self):
        import test_pi_evidence as fixtures
        from repo_maintainer.demo import DEMO_TEST
        from repo_maintainer.repository_workbench import inspect_repository

        fixture = fixtures.PiFailureTests()
        fixture.setUp()
        fixture.mode = 'coalesce'
        home = fixture.root/'mcp-private-home'
        provider = fixture.root/'explicit-synthetic-provider.env'
        provider.write_text('explicit synthetic fixture; never a real credential', encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gateway.main(['setup', '--home', str(home), '--provider-config', str(provider),
                             '--ui', 'legacy', '--request-budget', '6']), 0)
        original_source = (fixture.repo.root/'calculator.py').read_bytes()
        processes, outputs = [], []
        native = {}

        def start_gateway():
            source = (
                'import sys;from repo_maintainer import gateway,repository_workbench as module;'
                'from repo_maintainer.pi_provider import PiProvider;original=module.RepositoryWorkbench\n'
                'def workbench(*args,**kwargs):\n'
                ' budget=kwargs["request_budget"]\n'
                ' kwargs["provider_factory"]=lambda policy:PiProvider(policy.allowed_paths,sys.argv[1],policy.readonly_paths,max_requests=budget)\n'
                ' return original(*args,**kwargs)\n'
                'module.RepositoryWorkbench=workbench\n'
                'raise SystemExit(gateway.main(["gateway","run","--home",sys.argv[2],"--json"]))'
            )
            output = tempfile.TemporaryFile(mode='w+b')
            outputs.append(output)
            process = subprocess.Popen([sys.executable, '-B', '-c', source, fixture.provider.mock_url, str(home)],
                                       stdout=output, stderr=output, env=environment(),
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            processes.append(process)
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                if gateway.gateway_status(home).get('identity_verified'):
                    return gateway.verified_gateway(home)[0]
                if process.poll() is not None:
                    self.fail('Synthetic Gateway exited during startup')
                time.sleep(.05)
            self.fail('Synthetic Gateway startup not verified')

        def connect(receipt):
            import http.client
            preview = inspect_repository(fixture.repo.root)
            payload = {'request_id': uuid.uuid4().hex, 'path': str(fixture.repo.root), 'revision': preview['revision'],
                       'allowed_paths': ['calculator.py'], 'readonly_paths': ['tests/test_calculator.py'],
                       'allowed_tests': [list(DEMO_TEST)], 'timeout_seconds': 30, 'allow_auto': False, 'confirmed': True}
            connection = http.client.HTTPConnection('127.0.0.1', receipt['port'], timeout=5)
            try:
                connection.request('POST', '/api/actions/connect', body=json.dumps(payload).encode(), headers={
                    'Authorization': 'Bearer '+receipt['token'], 'Origin': f"http://127.0.0.1:{receipt['port']}",
                    'Content-Type': 'application/json'})
                response = connection.getresponse()
                self.assertEqual(response.status, 202)
                response.read()
            finally:
                connection.close()
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                state = mcp_server._api(receipt, '/api/state')
                job = next(row for row in state['jobs'] if row['id'] == payload['request_id'])
                if job['status'] != 'running':
                    self.assertEqual(job['status'], 'finished', job)
                    return state['workspaces'][0]
                time.sleep(.05)
            self.fail('Original workspace connection timed out')

        client = None
        try:
            receipt = start_gateway()
            original_configuration = (home/'workbench'/'workbench.json').read_bytes()
            workspace = connect(receipt)
            client = StdioClient(home, allow_plan=True)
            client.initialize()
            listed = client.tool('repocare_list_workspaces')
            self.assertNotIn(workspace['token'], json.dumps(listed))
            self.assertNotIn(receipt['token'], json.dumps(listed))
            identity = workspace['id']
            before = client.tool('repocare_read_workspace', {'workspace_id': identity})['state']
            self.assertEqual((before['request_budget'], before['requests_used'], before['requests_reserved_unknown']), (6, 0, 0))
            request_id = uuid.uuid4().hex
            arguments = {'workspace_id': identity, 'request_id': request_id, 'issue': 'add(2, 3) must equal 5'}
            submitted = client.tool('repocare_create_task', arguments)
            self.assertEqual(submitted['operation']['id'], request_id)
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                operation = client.tool('repocare_get_operation', {'workspace_id': identity, 'request_id': request_id})
                if operation['status'] != 'running':
                    break
                time.sleep(.1)
            self.assertEqual(operation['status'], 'finished', operation)
            after = client.tool('repocare_read_workspace', {'workspace_id': identity})['state']
            self.assertEqual((after['request_budget'], after['requests_used'], after['requests_reserved_unknown'], after['requests_remaining']), (6, 1, 0, 5))
            self.assertEqual(fixture.requests, 1)
            self.assertEqual(len(after['plans']), 1)
            self.assertEqual(after['plans'][0]['status'], 'awaiting_approval')
            self.assertEqual(after['runs'], [])
            self.assertIn('a + b', after['plans'][0]['diff'])
            self.assertEqual((fixture.repo.root/'calculator.py').read_bytes(), original_source)
            self.assertEqual(client.tool('repocare_create_task', arguments)['operation']['id'], request_id)
            self.assertEqual(fixture.requests, 1)
            native = {path.relative_to(home): path.read_bytes() for path in (home/'workbench').rglob('*.jsonl')}
            self.assertTrue(native, 'Actual Pi Native session must be present')
            self.assertEqual(gateway.stop_gateway(home, wait_seconds=10)['status'], 'stopped')
            self.assertEqual(processes[0].wait(timeout=5), 0)
            receipt = start_gateway()
            reopened = connect(receipt)
            state = client.tool('repocare_read_workspace', {'workspace_id': reopened['id']})['state']
            self.assertEqual((state['request_budget'], state['requests_used'], state['requests_reserved_unknown'], state['requests_remaining']), (6, 1, 0, 5))
            restored = client.tool('repocare_get_operation', {'workspace_id': reopened['id'], 'request_id': request_id})
            self.assertEqual(restored['status'], 'finished')
            self.assertEqual(client.tool('repocare_create_task', dict(arguments, workspace_id=reopened['id']))['operation']['id'], request_id)
            self.assertEqual(fixture.requests, 1)
            self.assertEqual({path: (home/path).read_bytes() for path in native}, native)
            self.assertEqual((home/'workbench'/'workbench.json').read_bytes(), original_configuration)
        finally:
            if client is not None:
                client.close()
            if any(process.poll() is None for process in processes):
                deadline = time.monotonic()+15
                while time.monotonic() < deadline and gateway.gateway_status(home).get('busy'):
                    time.sleep(.1)
                try:
                    gateway.stop_gateway(home, wait_seconds=10)
                except (SafetyError, OSError):
                    pass
            alive = []
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    alive.append(process.pid)
            for output in outputs:
                output.close()
            if alive:
                fixture.temp._finalizer.detach()
                raise AssertionError(f'Original safe stop not confirmed; fixtures retained: {alive}')
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
