import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.model_connectivity import ModelConnectivity
from repo_maintainer.models import SafetyError
from repo_maintainer.job_control import OperationStopped
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.ui_registry import UIRegistry, DurableProvider, provider_binding


def receipt(revision):
    return dict(schema_version=1, purpose='model_connectivity', status='completed', pi_version='1.0.4',
                requests=1, requests_started=1, consumption='reported', count_scope='adapter_stream_invocations',
                active_tools=[], tool_calls=0, response_received=True, preview_revision=revision,
                transport_retries=0, repair_verified=False, model_cost_verified=False, transport='loopback-mock')


class ConnectivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inner = PiProvider(('unused.py',), 'http://127.0.0.1:1/v1', max_requests=6)
        self.binding = {'repository': 'original', 'policy': {}, 'provider': provider_binding(self.inner)}
        self.registry = UIRegistry(self.root, self.binding)
        self.addCleanup(lambda: self.registry.close())
        self.session = SimpleNamespace(provider=DurableProvider(self.inner, self.registry),
                                       budget_registry=self.registry, token='current-service-session')
        self.guard_calls = 0
        self.check = ModelConnectivity(self.session, before_model=self.guard)

    def guard(self):
        self.guard_calls += 1

    def restart(self):
        self.registry.close()
        self.registry = UIRegistry(self.root, self.binding)
        self.session.budget_registry = self.registry
        self.session.provider = DurableProvider(self.inner, self.registry)
        self.check = ModelConnectivity(self.session, before_model=self.guard)

    def test_preview_and_get_do_not_call_or_write_and_view_does_not_read_config(self):
        with patch('repo_maintainer.model_connectivity._invoke') as transport:
            preview = self.check.preview()
            self.check.view()
            transport.assert_not_called()
        self.assertEqual(1, preview['request_count'])
        self.assertEqual({}, self.registry.load('attempt'))
        self.assertEqual({}, self.registry.load('model_connectivity'))
        with patch.object(self.check, '_configuration', side_effect=AssertionError('GET read config')):
            self.assertEqual([], self.check.view()['checks'])

    def test_shared_used_unknown_and_ceiling_preserved_only_one_debited(self):
        old, _ = self.registry.reserve(maximum=2)
        self.registry.settle(old, {'requests': None}, success=False)
        used, _ = self.registry.reserve(maximum=1)
        self.registry.settle(used, {'requests': 1}, success=True)
        old_evidence = self.registry.load('attempt')[old]
        preview = self.check.preview()
        with patch('repo_maintainer.model_connectivity._invoke', return_value=receipt(preview['revision'])) as transport:
            identity = uuid.uuid4().hex
            result = self.check.execute(identity, preview['revision'])
            again = self.check.execute(identity, preview['revision'])
        self.assertEqual(result, again)
        self.assertEqual(1, transport.call_count)
        self.assertEqual((6, 2, 2, 2), tuple(self.registry.budget().values()))
        self.assertEqual(old_evidence, self.registry.load('attempt')[old])
        self.restart()
        self.assertEqual(result, self.check.execute(identity, preview['revision']))
        extension = self.registry.preview_extension(8, 'same')
        self.assertEqual(2, extension['requests_used'])
        self.assertEqual(2, extension['requests_reserved_unknown'])

    def test_failure_unknown_keeps_exactly_one_and_never_replays_after_restart(self):
        preview, identity = self.check.preview(), uuid.uuid4().hex
        with patch('repo_maintainer.model_connectivity._invoke', side_effect=RuntimeError('synthetic failure')) as transport:
            with self.assertRaises(RuntimeError):
                self.check.execute(identity, preview['revision'])
            self.assertEqual(1, transport.call_count)
        self.assertEqual(1, self.registry.budget()['requests_reserved_unknown'])
        self.restart()
        with patch('repo_maintainer.model_connectivity._invoke') as transport:
            self.assertEqual('unknown', self.check.execute(identity, preview['revision'])['status'])
            transport.assert_not_called()
        self.assertEqual(1, self.registry.budget()['requests_reserved_unknown'])

    def test_crash_after_intent_preserves_unknown_without_replay(self):
        preview, identity = self.check.preview(), uuid.uuid4().hex
        # os._exit equivalent durable window: intercept settle, leaving the original attempt started.
        with patch('repo_maintainer.model_connectivity._invoke', side_effect=RuntimeError()), \
             patch.object(self.registry, 'settle', side_effect=SystemExit()):
            with self.assertRaises(SystemExit):
                self.check.execute(identity, preview['revision'])
        self.restart()
        with patch('repo_maintainer.model_connectivity._invoke') as transport:
            self.assertEqual('unknown', self.check.execute(identity, preview['revision'])['status'])
            transport.assert_not_called()
        self.assertEqual(1, self.registry.budget()['requests_reserved_unknown'])

    def test_config_key_rotation_budget_and_session_changes_invalidate_preview(self):
        self.inner.mock_url = None
        self.inner.config_file = self.root / 'provider.env'
        config = 'PI_MAINTENANCE_API_KEY=synthetic-one\nPI_MAINTENANCE_BASE_URL=https://example.invalid/v1\nPI_MAINTENANCE_MODEL=explicit-model\n'
        self.inner.config_file.write_text(config)
        self.binding['provider'] = provider_binding(self.inner)
        with self.registry.transaction() as db:
            db.execute('UPDATE metadata SET body=?', (json.dumps(self.binding),))
        preview = self.check.preview()
        self.assertNotIn('synthetic-one', json.dumps(preview))
        self.inner.config_file.write_text(config.replace('synthetic-one', 'synthetic-two'))
        with patch('repo_maintainer.model_connectivity._invoke') as transport:
            with self.assertRaisesRegex(SafetyError, 'changed'):
                self.check.execute(uuid.uuid4().hex, preview['revision'])
            transport.assert_not_called()
        self.assertEqual(0, self.registry.budget()['requests_reserved_unknown'])
        preview = self.check.preview()
        self.session.token = 'new-service-session'
        with self.assertRaisesRegex(SafetyError, 'changed'):
            self.check.execute(uuid.uuid4().hex, preview['revision'])
        self.session.token = 'current-service-session'
        preview = self.check.preview()
        self.registry.reserve(maximum=1)
        with self.assertRaisesRegex(SafetyError, 'changed'):
            self.check.execute(uuid.uuid4().hex, preview['revision'])

    def test_permission_missing_configuration_and_budget_fail_closed(self):
        with self.assertRaises(SafetyError):
            ModelConnectivity(self.session, before_model=None)
        self.inner.mock_url = None
        with self.assertRaisesRegex(SafetyError, 'explicitly configured'):
            self.check.preview()
        self.inner.mock_url = 'http://127.0.0.1:1/v1'
        self.session.provider.before_model = lambda: (_ for _ in ()).throw(SafetyError('permission revoked'))
        with self.assertRaisesRegex(SafetyError, 'revoked'):
            self.check.preview()
        self.session.provider.before_model = None
        self.registry.reserve()
        with self.assertRaisesRegex(SafetyError, 'exhausted'):
            self.check.preview()

    def test_cancel_before_reserve_and_during_transport_keep_original_semantics(self):
        preview = self.check.preview()
        self.inner._cancel_event = threading.Event()
        self.inner._cancel_event.set()
        with self.assertRaises(OperationStopped):
            self.check.execute(uuid.uuid4().hex, preview['revision'])
        self.assertEqual(0, self.registry.budget()['requests_reserved_unknown'])
        self.inner._cancel_event.clear()
        with patch('repo_maintainer.model_connectivity._invoke', side_effect=OperationStopped()):
            with self.assertRaises(OperationStopped):
                self.check.execute(uuid.uuid4().hex, preview['revision'])
        self.assertEqual(1, self.registry.budget()['requests_reserved_unknown'])

    def test_invalid_response_cannot_debit_or_store_raw_provider_text(self):
        for change in ({'requests': 0}, {'requests_started': True}, {'raw_message': 'SECRET'},
                       {'active_tools': ['bash']}, {'transport_retries': 1}, {'repair_verified': True}):
            preview = self.check.preview()
            with patch('repo_maintainer.model_connectivity._invoke', return_value={**receipt(preview['revision']), **change}):
                with self.assertRaises(SafetyError):
                    self.check.execute(uuid.uuid4().hex, preview['revision'])
        self.assertEqual(0, self.registry.budget()['requests_used'])
        self.assertEqual(6, self.registry.budget()['requests_reserved_unknown'])
        self.assertNotIn('SECRET', json.dumps(self.check.view()))

    def test_two_racing_duplicates_dispatch_only_once(self):
        preview, identity = self.check.preview(), uuid.uuid4().hex
        entered, release = threading.Event(), threading.Event()
        def invoke(*args):
            entered.set(); release.wait(5)
            return receipt(preview['revision'])
        errors=[]
        def execute():
            try:self.check.execute(identity, preview['revision'])
            except BaseException as error:errors.append(error)
        with patch('repo_maintainer.model_connectivity._invoke', side_effect=invoke) as transport:
            worker=threading.Thread(target=execute);worker.start()
            self.assertTrue(entered.wait(5))
            self.assertEqual('started', self.check.execute(identity, preview['revision'])['status'])
            release.set();worker.join(5)
            self.assertEqual([], errors)
            self.assertEqual(1, transport.call_count)
        self.assertEqual(1, self.registry.budget()['requests_used'])

    def _server(self, failure=False):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                if failure:
                    self.send_response(503);self.end_headers();self.wfile.write(b'not a model');return
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                frames=[{'id':'synthetic','object':'chat.completion.chunk','created':1,'model':'mock-maintenance',
                         'choices':[{'index':0,'delta':{'role':'assistant','content':'REPOCARE_CONNECTIVITY_OK'},'finish_reason':None}]},
                        {'id':'synthetic','object':'chat.completion.chunk','created':1,'model':'mock-maintenance',
                         'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}]
                for frame in frames:self.wfile.write(('data: '+json.dumps(frame)+'\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.inner.mock_url=f'http://127.0.0.1:{server.server_port}/v1'
        self.binding['provider']=provider_binding(self.inner)
        with self.registry.transaction() as db:
            db.execute('UPDATE metadata SET body=?',(json.dumps(self.binding),))
        return requests

    def test_real_pi_sdk_synthetic_response_original_ledger_no_tools(self):
        requests=self._server()
        preview=self.check.preview()
        result=self.check.execute(uuid.uuid4().hex,preview['revision'])
        self.assertEqual('completed',result['status'])
        self.assertFalse(result['repair_verified'])
        self.assertEqual(1,len(requests))
        self.assertFalse(requests[0].get('tools'))
        self.assertEqual('mock-maintenance',requests[0]['model'])
        self.assertEqual(1,self.registry.budget()['requests_used'])
        self.assertEqual(0,self.registry.budget()['requests_reserved_unknown'])
        self.assertNotIn('REPOCARE_CONNECTIVITY_OK',json.dumps(self.check.view()))

    def test_real_pi_sdk_synthetic_provider_failure_has_no_http_retry(self):
        requests=self._server(failure=True)
        preview, identity=self.check.preview(),uuid.uuid4().hex
        with self.assertRaises(SafetyError):self.check.execute(identity,preview['revision'])
        self.assertEqual(1,len(requests))
        self.assertEqual(1,self.registry.budget()['requests_reserved_unknown'])
        self.assertEqual('unknown',self.check.execute(identity,preview['revision'])['status'])
        self.assertEqual(1,len(requests))


if __name__=='__main__':unittest.main()
