"""Independent assistant acceptance with the real pinned Pi and local synthetic SSE."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from repo_maintainer.assistant import AssistantControl
from repo_maintainer.assistant_limits import MAX_ASSISTANT_REQUESTS_PER_TURN
from repo_maintainer.model_profiles import ModelProfiles
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.ui_registry import UIRegistry
from test_assistant_forms import snapshot


class AssistantModel:
    def __init__(self):
        self.requests = []
        self.failure = False
        self.failure_status = 500
        self.failure_message = 'synthetic provider failure'
        self.failure_after = 0
        self.echo = None
        self.proposal_value = '检查加法需求'
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                if owner.failure and len(owner.requests)>owner.failure_after:
                    self.send_response(owner.failure_status); self.send_header('Content-Type', 'application/json'); self.end_headers()
                    message='synthetic provider timeout' if owner.failure=='timeout' else owner.failure_message
                    self.wfile.write(json.dumps({'error':{'message':message}}).encode()); return
                indices = [n for n, row in enumerate(body['messages']) if row['role'] == 'user']
                content = body['messages'][indices[-1]]['content']
                packet = json.loads(content if isinstance(content, str) else content[0]['text'])
                current = body['messages'][indices[-1]+1:]
                user = packet['user_message']
                tools = [row for row in current if row['role'] == 'tool']
                if '教程' in user and not tools:
                    name, arguments = 'get_help', {'query': '模型'}
                elif '草稿' in user and not tools:
                    name, arguments = 'propose_form_values', {'changes': [{'field_id': '1'*32, 'value': owner.proposal_value}],
                        'explanation': '这是未应用草稿，用户还须核对。'}
                else:
                    name = None
                if name:
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_' + str(len(owner.requests)),
                        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                else:
                    delta = {'role': 'assistant', 'content': owner.echo or ('仅供讨论，未执行。此前用户消息：' + str(len(indices)-1))}
                frame = {'id': 'assistant-fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': body['model'],
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': 'tool_calls' if name else 'stop'}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 12, 'total_tokens': 112}}
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                self.wfile.write(('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)


@unittest.skipUnless(shutil.which('node'), 'Node required for pinned Pi assistant acceptance')
class AssistantTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parents[1] / 'runtime' / 'pi'
        if not (runtime / 'node_modules').exists():
            self.skipTest('Pinned Pi dependencies unavailable')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = SimpleNamespace(root=self.root, provider_config=self.root/'legacy.env', request_budget=24,
            lock=threading.RLock(), closed=False, _operate=Mock(), register=Mock(), approve=Mock(), monitor=Mock())
        self.hub.model_profiles = ModelProfiles(self.hub)
        self.secret = 'synthetic-assistant-profile-key'
        self.profile = self.hub.model_profiles.create({'revision': self.hub.model_profiles.view()['revision'],
            'action': 'create', 'name': 'assistant fixture', 'api_key': self.secret,
            'base_url': 'https://provider.example.invalid/v1', 'model': 'fixture-assistant',
            'context_window_tokens': 65536, 'max_output_tokens': 2048})['created_profile_id']
        self.model = AssistantModel(); self.addCleanup(self.model.close)
        self.control = AssistantControl(self.hub, mock_url=self.model.url)
        self.addCleanup(self._close)
        self.repair_root = self.root/'existing-repair'
        registry = UIRegistry(self.repair_root, {'provider': {'request_budget': 10}})
        identity, _ = registry.reserve(maximum=2); registry.settle(identity, {'requests': 2}, success=True)
        registry.reserve(maximum=3); registry.close()
        (self.repair_root/'native.jsonl').write_bytes(b'{"original":"native bytes retained"}\n')
        self.original_repair = self._snapshot(self.repair_root)

    @staticmethod
    def _snapshot(root):
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file() and path.name!='owner.lock'}

    def _close(self):
        if self.control.closed:
            return
        limit = time.monotonic()+15
        while self.control.active and time.monotonic()<limit:
            time.sleep(.02)
        self.control.close()

    def wait(self, operation):
        limit = time.monotonic()+20
        while time.monotonic()<limit:
            result = next((job for job in self.control.view()['jobs'] if job['id']==operation['id']), None)
            if result and result['status'] in {'finished', 'failed'} and not self.control.active:
                return result
            time.sleep(.02)
        self.fail('Synthetic assistant job did not finish within bounded test wait')

    def create(self):
        payload = {'request_id': uuid.uuid4().hex, 'profile_id': self.profile}
        result = self.wait(self.control.submit('create', payload))
        self.assertEqual('finished', result['status'], result)
        return result['session_id']

    def send(self, session, text='你好', form=None, request_id=None):
        payload = {'request_id': request_id or uuid.uuid4().hex, 'session_id': session,
                   'text': text, 'form_context': form, 'language': 'zh-CN'}
        return self.wait(self.control.submit('send', payload)), payload

    def session(self, identity):
        return next(record for record in self.control.view()['sessions'] if record['id']==identity)

    def record(self, identity):
        return self.control.executor.submit(lambda: self.control._head().load('assistant_session')[identity]).result()

    def mutate_record(self, identity, update):
        record = self.record(identity); update(record)
        self.control.executor.submit(lambda: self.control._head().put('assistant_session', identity, record)).result()

    def assert_no_business_calls(self):
        for name in ('_operate', 'register', 'approve', 'monitor'):
            getattr(self.hub, name).assert_not_called()
        self.assertEqual(self.original_repair, self._snapshot(self.repair_root))

    def restart(self):
        self.control.close(); self.control = AssistantControl(self.hub, mock_url=self.model.url)

    def move_owned_directory(self, source, target):
        # Fault injection preserves bytes by moving only our checked temporary fixture.
        base = self.root.resolve()
        self.assertNotEqual(base, source.resolve())
        self.assertTrue(source.resolve().is_relative_to(base))
        self.assertTrue(target.parent.resolve().is_relative_to(base))
        self.assertFalse(target.exists())
        source.rename(target)

    def test_get_create_and_duplicate_create_are_repository_free_and_zero_model_calls(self):
        self.assertFalse((self.root/'assistant').exists())
        before = self._snapshot(self.root)
        self.assertEqual([], self.control.view()['sessions'])
        self.assertEqual(before, self._snapshot(self.root))
        payload = {'request_id': uuid.uuid4().hex, 'profile_id': self.profile}
        first = self.wait(self.control.submit('create', payload))
        second = self.wait(self.control.submit('create', payload))
        self.assertEqual(first['session_id'], second['session_id'])
        self.assertEqual(1, len(self.control.view()['sessions']))
        self.assertEqual([], self.model.requests)
        self.assert_no_business_calls()

    @unittest.skipUnless(os.name == 'nt', 'Windows short-path alias regression')
    def test_short_path_catalog_reopens_under_canonical_path_without_reset(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetShortPathNameW.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD]
        buffer=ctypes.create_unicode_buffer(32768)
        self.assertTrue(kernel.GetShortPathNameW(str(self.root),buffer,len(buffer)))
        short=Path(buffer.value)
        if str(short).lower()==str(self.root.resolve()).lower():
            self.skipTest('This volume does not expose an 8.3 alias')
        self.control.close()
        self.hub.root=short
        self.control=AssistantControl(self.hub,mock_url=self.model.url)
        identity=self.create()
        record=self.record(identity)
        self.control.close()
        marker=self.root/'assistant-binding.json'
        before=marker.read_bytes()
        self.hub.root=self.root.resolve()
        self.control=AssistantControl(self.hub,mock_url=self.model.url)
        self.assertEqual(self.record(identity),record)
        self.assertEqual(marker.read_bytes(),before)
        self.assertEqual(len(self.control.view()['sessions']),1)
        self.assertEqual(self.model.requests,[])
        self.assert_no_business_calls()

    def test_real_pi_multiturn_help_draft_restart_and_exact_operation_dedup(self):
        identity = self.create()
        first, payload = self.send(identity, '普通问题')
        self.assertEqual('finished', first['status'], first)
        count = len(self.model.requests)
        duplicate = self.wait(self.control.submit('send', payload))
        self.assertEqual(first['result'], duplicate['result']); self.assertEqual(count, len(self.model.requests))
        with self.assertRaises(SafetyError):
            self.control.submit('send', {**payload, 'text': '同一标识不能改成新内容'})
        self.assertEqual(count, len(self.model.requests))
        first_record = self.record(identity)
        native = self.root/'assistant/conversations'/identity/'sessions'/first_record['native_file']
        prefix = native.read_bytes()
        second, _ = self.send(identity, '请读教程说明模型设置')
        self.assertEqual('finished', second['status'], second)
        third, _ = self.send(identity, '请给当前表单一个草稿', snapshot())
        self.assertEqual('finished', third['status'], third)
        public = self.session(identity)
        self.assertEqual(6, len(public['messages']))
        draft = public['messages'][-1]['proposal']; self.assertTrue(draft['draft_only'])
        self.assertEqual(snapshot()['revision'], draft['revision'])
        self.assertEqual(prefix, native.read_bytes()[:len(prefix)])
        before = self.control.view(); count = len(self.model.requests)
        self.restart()
        self.hub.request_budget = 48  # A new UI default cannot replenish the selected old model ledger.
        self.assertEqual(before['sessions'], self.control.view()['sessions'])
        self.assertEqual(before['budgets'], self.control.view()['budgets'])
        self.assertEqual(count, len(self.model.requests))
        fourth, _ = self.send(identity, '继续普通讨论')
        self.assertEqual('finished', fourth['status'], fourth)
        self.assertIn('此前用户消息：3', self.session(identity)['messages'][-1]['content'])
        for body in self.model.requests:
            self.assertEqual(2048, body['max_tokens'])
            self.assertEqual(['get_help', 'propose_form_values'], sorted(tool['function']['name'] for tool in body['tools']))
        self.assertTrue(any(any(row['role']=='tool' and '模型' in row.get('content','') for row in body['messages']) for body in self.model.requests))
        self.assert_no_business_calls()

    def test_profile_tamper_rejects_send_before_creating_a_new_model_reservation(self):
        identity = self.create()
        selected = self.hub.model_profiles.resolve(self.profile)
        selected['path'].write_bytes(selected['path'].read_bytes()+b'# changed pinned config\n')
        before = self.control.view()['budgets']
        failed, _ = self.send(identity)
        self.assertEqual('failed', failed['status'])
        self.assertEqual([], self.model.requests)
        self.assertEqual(before, self.control.view()['budgets'])
        self.assert_no_business_calls()

    def test_unsupported_actions_payloads_and_bad_types_do_not_create_an_intent(self):
        base = {'request_id': uuid.uuid4().hex, 'session_id': 'a'*32, 'text': '你好', 'form_context': None, 'language': 'zh-CN'}
        cases = [('approve', base), ('send', {**base, 'action': 'submit'}),
                 ('send', {**base, 'api_key': self.secret}), ('send', {**base, 'language': []}),
                 ('send', {**base, 'text': ''}), ('send', {**base, 'session_id': '../outside'}),
                 ('send', {**base, 'approved': True}), ('send', {**base, 'form_context': {'action': 'next_step'}})]
        for action, value in cases:
            with self.subTest(action=action, keys=set(value)), self.assertRaises(SafetyError):
                self.control.submit(action, value)
        with self.hub.lock:
            self.hub.stopping = True
        with self.assertRaises(SafetyError):
            self.control.submit('create', {'request_id': uuid.uuid4().hex, 'profile_id': self.profile})
        self.hub.stopping = False
        self.assertFalse((self.root/'assistant').exists())
        self.assertEqual([], self.model.requests)
        self.assert_no_business_calls()

    def test_failure_unknown_ledger_and_same_request_cannot_replay_after_restart(self):
        identity = self.create(); self.model.failure = 'timeout'
        first, payload = self.send(identity)
        self.assertEqual('failed', first['status'])
        self.assertEqual('timeout', first['error_category'])
        budget = self.control.view()['budgets'][self.profile]
        self.assertEqual(MAX_ASSISTANT_REQUESTS_PER_TURN, budget['requests_reserved_unknown'])
        self.assertEqual(0, budget['requests_used'])
        count = len(self.model.requests); self.restart()
        repeated = self.wait(self.control.submit('send', payload))
        self.assertEqual('failed', repeated['status']); self.assertEqual(count, len(self.model.requests))
        self.assertEqual(budget, self.control.view()['budgets'][self.profile])
        self.model.failure = False
        different, _ = self.send(identity, '发送一条新消息')
        self.assertEqual('finished', different['status'])
        self.assertEqual(count+1, len(self.model.requests))
        after = self.control.view()['budgets'][self.profile]
        self.assertEqual(budget['requests_reserved_unknown'], after['requests_reserved_unknown'])
        self.assertEqual(1, after['requests_used'])
        self.assert_no_business_calls()

    def test_native_tamper_is_rejected_before_request_reservation(self):
        identity = self.create(); first, _ = self.send(identity); self.assertEqual('finished', first['status'])
        record = self.record(identity); native = self.root/'assistant/conversations'/identity/'sessions'/record['native_file']
        native.write_bytes(native.read_bytes()+b'{"tampered":true}\n')
        before = copy.deepcopy(self.control.view()['budgets']); count = len(self.model.requests)
        failed, _ = self.send(identity)
        self.assertEqual('failed', failed['status']); self.assertEqual(count, len(self.model.requests))
        self.assertEqual(before, self.control.view()['budgets']); self.assert_no_business_calls()

    def test_corrupt_record_id_or_unknown_action_is_rejected_before_any_model_work(self):
        for update in (lambda record: record.update(id='f'*32), lambda record: record.update(action='approve')):
            identity = self.create(); original = self.record(identity); self.mutate_record(identity, update)
            with self.subTest(update=update), patch.object(self.control, '_converse', side_effect=RuntimeError('must not launch')) as converse:
                payload = {'request_id': uuid.uuid4().hex, 'session_id': identity, 'text': '普通问题', 'form_context': None, 'language': 'zh-CN'}
                operation = self.control.submit('send', payload)
                failed = self.control.executor.submit(lambda: self.control._head().load('job')[operation['id']]).result(timeout=20)
                self.assertEqual('failed', failed['status']); converse.assert_not_called()
                with self.assertRaises(SafetyError): self.control.view()
            self.control.executor.submit(lambda: self.control._head().put('assistant_session', identity, original)).result()
        self.assert_no_business_calls()

    def test_failed_final_job_write_cannot_replay_a_completed_model_call_in_same_process(self):
        identity = self.create(); head = self.control.executor.submit(self.control._head).result(); original = head.put
        failed_once = [False]
        def put(kind, identity, value, *args, **kwargs):
            if kind=='job' and value['status']=='finished' and not failed_once[0]:
                failed_once[0] = True
                raise OSError('synthetic terminal persistence failure')
            return original(kind, identity, value, *args, **kwargs)
        payload = {'request_id': uuid.uuid4().hex, 'session_id': identity, 'text': '普通问题', 'form_context': None, 'language': 'zh-CN'}
        with patch.object(head, 'put', side_effect=put):
            self.control.submit('send', payload)
            limit = time.monotonic()+20
            while self.control.active and time.monotonic()<limit: time.sleep(.02)
        self.assertTrue(failed_once[0]); count = len(self.model.requests)
        self.control.submit('send', payload)
        limit = time.monotonic()+20
        while self.control.active and time.monotonic()<limit: time.sleep(.02)
        self.assertEqual(count, len(self.model.requests), 'A durable existing intent must never be dispatched again')
        self.assert_no_business_calls()

    def test_provider_credential_echo_is_not_exposed_as_public_assistant_text(self):
        identity = self.create(); self.model.echo = self.secret
        self.send(identity)
        public = json.dumps(self.control.view(), ensure_ascii=False)
        self.assertNotIn(self.secret, public)
        self.assert_no_business_calls()

    def test_orphan_model_ledger_is_not_adopted_or_given_new_allowance(self):
        identity = self.create(); selected = self.hub.model_profiles.resolve(self.profile)
        directory = self.root/'assistant/models'/self.profile
        ledger = UIRegistry(directory, {'schema_version': 1, 'purpose': 'conversation-model-v1',
            'profile_id': self.profile, 'profile_sha256': selected['config_sha256'],
            'provider': {'type': 'PiAssistant', 'config_file': str(selected['path']),
                         'mock_endpoint': self.model.url, 'request_budget': 24}})
        ledger.reserve(maximum=3); ledger.close()
        before = self._snapshot(directory)
        failed, _ = self.send(identity)
        self.assertEqual('failed', failed['status'])
        self.assertEqual([], self.model.requests)
        self.assertEqual(before, self._snapshot(directory))
        self.assertEqual(3, self.control.view()['budgets'][self.profile]['requests_reserved_unknown'])
        self.assert_no_business_calls()

    def test_missing_entire_model_ledger_directory_cannot_reset_unknown_budget(self):
        identity = self.create(); self.model.failure = 'timeout'
        failed, _ = self.send(identity)
        self.assertEqual('failed', failed['status'])
        self.assertEqual(MAX_ASSISTANT_REQUESTS_PER_TURN, self.control.view()['budgets'][self.profile]['requests_reserved_unknown'])
        self.control.close()
        directory = self.root/'assistant/models'/self.profile
        preserved = self._snapshot(directory); quarantine = self.root/'preserved-model-ledger'
        self.move_owned_directory(directory, quarantine)
        self.control = AssistantControl(self.hub, mock_url=self.model.url)
        self.model.failure = False; count = len(self.model.requests)
        rejected, _ = self.send(identity, '缺失原账不能获得新额度')
        self.assertEqual('failed', rejected['status'])
        self.assertEqual(count, len(self.model.requests))
        self.assertFalse(directory.exists())
        self.assertEqual(preserved, self._snapshot(quarantine))
        self.assert_no_business_calls()

    def test_missing_entire_catalog_directory_refuses_get_and_new_create(self):
        self.create(); marker = self.root/'assistant-binding.json'
        self.assertTrue(marker.is_file()); marker_bytes = marker.read_bytes()
        self.control.close()
        catalog = self.root/'assistant/catalog'; preserved = self._snapshot(catalog)
        quarantine = self.root/'preserved-assistant-catalog'
        self.move_owned_directory(catalog, quarantine)
        self.control = AssistantControl(self.hub, mock_url=self.model.url)
        with self.assertRaises(SafetyError): self.control.view()
        with self.assertRaises(SafetyError):
            self.control.submit('create', {'request_id': uuid.uuid4().hex, 'profile_id': self.profile})
        self.assertFalse(catalog.exists())
        self.assertEqual(marker_bytes, marker.read_bytes())
        self.assertEqual(preserved, self._snapshot(quarantine))
        self.assertEqual([], self.model.requests)
        self.assert_no_business_calls()

    def test_corrupt_catalog_marker_cannot_be_refreshed_by_new_create_in_same_process(self):
        self.create(); marker = self.root/'assistant-binding.json'
        self.assertTrue(marker.is_file())
        corrupt = json.dumps({'schema_version': 1, 'root': str(self.root/'different-assistant')}).encode()
        marker.write_bytes(corrupt)
        catalog_before = self._snapshot(self.root/'assistant/catalog')
        with self.assertRaises(SafetyError): self.control.view()
        with self.assertRaises(SafetyError):
            self.control.submit('create', {'request_id': uuid.uuid4().hex, 'profile_id': self.profile})
        self.assertEqual(corrupt, marker.read_bytes())
        self.assertEqual(catalog_before, self._snapshot(self.root/'assistant/catalog'))
        self.assertEqual([], self.model.requests)
        self.assert_no_business_calls()

    def test_verified_provider_terminal_errors_count_only_actual_invocations_and_keep_raw_private(self):
        cases=[(400,'Model Not Exist '+self.secret,'invalid_model'),
               (404,'UnsupportedModel synthetic-private-provider-body','invalid_model'),
               (401,'Invalid API key '+self.secret,'auth'),(429,'rate limit','rate_limit'),
               (400,'unsupported parameter reasoning_effort','unsupported_parameters'),
               (500,'synthetic provider failure','provider_error')]
        identity=self.create()
        for count,(status,message,category) in enumerate(cases,1):
            self.model.failure=True;self.model.failure_status=status;self.model.failure_message=message
            result,payload=self.send(identity,'明确的新请求 '+str(count))
            self.assertEqual('failed',result['status'],result)
            self.assertEqual(category,result.get('error_category'),result)
            self.assertEqual(1,result.get('requests'));self.assertTrue(result.get('requests_counted'))
            budget=self.control.view()['budgets'][self.profile]
            self.assertEqual(count,budget['requests_used']);self.assertEqual(0,budget['requests_reserved_unknown'])
            self.assertEqual(24-count,budget['requests_remaining'])
            public=json.dumps(self.control.view(),ensure_ascii=False)
            self.assertNotIn(self.secret,public);self.assertNotIn('synthetic-private-provider-body',public)
            before=len(self.model.requests)
            self.assertEqual(result,self.wait(self.control.submit('send',payload)))
            self.assertEqual(before,len(self.model.requests))
        # Known errors may resume only after a new explicit send, using the original ledger.
        self.model.failure=False;self.restart()
        finished,_=self.send(identity,'现在重新发送')
        self.assertEqual('finished',finished['status'],finished)
        self.assertEqual(7,self.control.view()['budgets'][self.profile]['requests_used'])
        self.assert_no_business_calls()

    def test_switch_model_links_only_successful_text_without_model_calls_or_old_state_changes(self):
        identity=self.create();first,_=self.send(identity,'成功讨论')
        self.assertEqual('finished',first['status'],first)
        second,_=self.send(identity,'请给当前表单一个草稿',snapshot())
        self.assertEqual('finished',second['status'],second)
        source_record=copy.deepcopy(self.record(identity));source_native=(self.root/'assistant/conversations'/identity/'sessions'/source_record['native_file']).read_bytes()
        source_budget=copy.deepcopy(self.control.view()['budgets'][self.profile])
        profile=self.hub.model_profiles.create({'revision':self.hub.model_profiles.view()['revision'],
            'action':'create','name':'second reasoning profile','api_key':'synthetic-second-key',
            'base_url':'https://provider.example.invalid/v1','model':'other-model',
            'context_window_tokens':65536,'max_output_tokens':2048,'reasoning_protocol':'openai','reasoning_effort':'high'})['created_profile_id']
        calls=len(self.model.requests)
        payload={'request_id':uuid.uuid4().hex,'session_id':identity,'profile_id':profile}
        switched=self.wait(self.control.submit('switch_model',payload))
        self.assertEqual('finished',switched['status'],switched)
        target=switched['result']['session_id'];self.assertNotEqual(identity,target)
        self.assertEqual(calls,len(self.model.requests));self.assertEqual(source_record,self.record(identity))
        self.assertEqual(source_budget,self.control.view()['budgets'][self.profile])
        new=self.session(target);self.assertEqual([],new['messages']);self.assertEqual(profile,new['profile_id'])
        self.assertEqual(4,new['continuation']['source_message_count'])
        self.assertTrue(all(set(message)=={'role','content'} for message in new['history_messages']))
        self.assertEqual(source_native,(self.root/'assistant/conversations'/identity/'sessions'/source_record['native_file']).read_bytes())
        self.assertIsNone(self.record(target)['native_file'])
        repeated=self.wait(self.control.submit('switch_model',payload));self.assertEqual(target,repeated['session_id'])
        self.restart();self.assertEqual(calls,len(self.model.requests));self.assertEqual(new,self.session(target))
        sent,_=self.send(target,'请沿只读历史继续讨论')
        self.assertEqual('finished',sent['status'],sent)
        content=self.model.requests[calls]['messages'][-1]['content']
        packet=json.loads(content if isinstance(content,str) else content[0]['text'])
        self.assertEqual(new['history_messages'],packet['history_context'])
        self.assertNotIn('proposal',json.dumps(packet['history_context']))
        self.assertEqual('high',self.model.requests[calls]['reasoning_effort'])
        self.assertEqual(source_record,self.record(identity));self.assertEqual(source_budget,self.control.view()['budgets'][self.profile])
        self.assert_no_business_calls()

    def test_failure_receipt_native_or_count_mismatch_keeps_full_unknown_and_never_replays(self):
        from repo_maintainer.assistant_receipts import verify_terminal_native
        identity=self.create();self.model.failure=True;self.model.failure_status=400;self.model.failure_message='Model Not Exist'
        def reject_count(value,*args,**kwargs):
            changed={**value,'requests':value['requests']+1}
            return verify_terminal_native(changed,*args,**kwargs)
        with patch('repo_maintainer.assistant.verify_terminal_native',side_effect=reject_count):
            failed,payload=self.send(identity)
        self.assertEqual('failed',failed['status']);self.assertNotIn('requests_counted',failed)
        before=self.control.view()['budgets'][self.profile]
        self.assertEqual(MAX_ASSISTANT_REQUESTS_PER_TURN,before['requests_reserved_unknown']);self.assertEqual(0,before['requests_used'])
        calls=len(self.model.requests);self.restart()
        self.wait(self.control.submit('send',payload));self.assertEqual(calls,len(self.model.requests))
        self.assertEqual(before,self.control.view()['budgets'][self.profile]);self.assert_no_business_calls()

    def test_terminal_error_after_help_counts_both_completed_native_invocations(self):
        identity=self.create();self.model.failure=True;self.model.failure_after=1
        self.model.failure_status=400;self.model.failure_message='Model Not Exist'
        result,_=self.send(identity,'请读教程说明模型设置')
        self.assertEqual('invalid_model',result.get('error_category'),result)
        self.assertEqual(2,result.get('requests'));self.assertEqual(2,len(self.model.requests))
        budget=self.control.view()['budgets'][self.profile]
        self.assertEqual((2,0,22),(budget['requests_used'],budget['requests_reserved_unknown'],budget['requests_remaining']))
        self.assert_no_business_calls()

    def test_switch_keeps_a_previous_500_unknown_ledger_unchanged_and_does_not_send(self):
        self.hub.request_budget=500
        identity=self.create();self.model.failure='timeout'
        with patch('repo_maintainer.assistant.MAX_ASSISTANT_REQUESTS_PER_TURN',500):
            failed,_=self.send(identity,'保留这个未知结果')
        self.assertEqual('failed',failed['status'])
        before=copy.deepcopy(self.control.view()['budgets'][self.profile])
        self.assertEqual(500,before['requests_reserved_unknown'])
        blocked,_=self.send(identity,'额度占用时说明原因')
        self.assertEqual('budget_blocked',blocked['error_category'])
        self.assertEqual(1,len(self.model.requests))
        directory=self.root/'assistant/models'/self.profile
        original_ledger=self._snapshot(directory)
        original_native=self._snapshot(self.root/'assistant/conversations'/identity)
        profile=self.hub.model_profiles.create({'revision':self.hub.model_profiles.view()['revision'],
            'action':'create','name':'other profile','api_key':'synthetic-other-key',
            'base_url':'https://provider.example.invalid/v1','model':'other-model'})['created_profile_id']
        calls=len(self.model.requests)
        switched=self.wait(self.control.submit('switch_model',{'request_id':uuid.uuid4().hex,'session_id':identity,'profile_id':profile}))
        self.assertEqual('finished',switched['status'],switched)
        self.assertEqual(calls,len(self.model.requests));self.assertEqual(before,self.control.view()['budgets'][self.profile])
        self.assertEqual(original_ledger,self._snapshot(directory));self.assertEqual(original_native,self._snapshot(self.root/'assistant/conversations'/identity))
        self.assertEqual([],self.session(switched['result']['session_id'])['history_messages'])
        self.assert_no_business_calls()

    def test_terminal_network_error_has_a_safe_count_without_a_provider_response(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        self.control.mock_url=f'http://127.0.0.1:{port}/v1'
        identity=self.create();result,_=self.send(identity)
        self.assertEqual('network',result.get('error_category'),result)
        self.assertEqual(1,result.get('requests'));self.assertTrue(result.get('requests_counted'))
        budget=self.control.view()['budgets'][self.profile]
        self.assertEqual((1,0,23),(budget['requests_used'],budget['requests_reserved_unknown'],budget['requests_remaining']))
        self.assertEqual([],self.model.requests);self.assert_no_business_calls()

    def test_million_token_declaration_accepts_three_megabyte_reply_and_explicitly_blocks_oversize_history(self):
        self.profile=self.hub.model_profiles.create({'revision':self.hub.model_profiles.view()['revision'],
            'action':'create','name':'large declared output','api_key':self.secret,
            'base_url':'https://provider.example.invalid/v1','model':'large-model',
            'context_window_tokens':4000000,'max_output_tokens':1500000})['created_profile_id']
        identity=self.create();self.model.echo='完整原始回复'+('a'*(3*1024**2))
        sent,_=self.send(identity,'大回复边界')
        self.assertEqual('finished',sent['status'],sent)
        self.assertEqual(self.model.echo,self.session(identity)['messages'][-1]['content'])
        self.assertEqual(1500000,self.model.requests[-1]['max_tokens'])
        record=copy.deepcopy(self.record(identity));native=self.root/'assistant/conversations'/identity/'sessions'/record['native_file']
        native_before=native.read_bytes();budget=copy.deepcopy(self.control.view()['budgets'][self.profile]);calls=len(self.model.requests)
        self.assertGreater(len(native_before),2*1024**2)
        self.restart();self.assertEqual(self.model.echo,self.session(identity)['messages'][-1]['content'])
        switched=self.wait(self.control.submit('switch_model',{'request_id':uuid.uuid4().hex,'session_id':identity,'profile_id':self.profile}))
        self.assertEqual('failed',switched['status']);self.assertIn('超过续接容量',switched['error'])
        self.assertEqual(calls,len(self.model.requests));self.assertEqual(record,self.record(identity))
        self.assertEqual(budget,self.control.view()['budgets'][self.profile]);self.assertEqual(native_before,native.read_bytes())
        self.assert_no_business_calls()

    def test_response_byte_rejection_preserves_complete_native_and_unknown_reservation(self):
        identity=self.create();self.model.echo='complete synthetic output beyond injected bound'
        with patch('repo_maintainer.assistant.MAX_ASSISTANT_TEXT_BYTES',16):
            failed,_=self.send(identity)
        self.assertEqual('failed',failed['status']);self.assertNotIn('requests_counted',failed)
        budget=self.control.view()['budgets'][self.profile]
        self.assertEqual((0,MAX_ASSISTANT_REQUESTS_PER_TURN),(budget['requests_used'],budget['requests_reserved_unknown']))
        files=list((self.root/'assistant/conversations'/identity/'sessions').glob('*.jsonl'))
        self.assertEqual(1,len(files));self.assertIn(self.model.echo,files[0].read_text(encoding='utf-8'))
        self.assertEqual([],self.session(identity)['messages']);self.assert_no_business_calls()


if __name__ == '__main__':
    unittest.main()
