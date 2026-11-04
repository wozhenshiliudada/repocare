"""R4 acceptance: real Pi SDK search/read inside the original read roots.

The SSE model is local and deterministic; no provider credentials or network
leave 127.0.0.1. The candidate repository is created fresh under a short path
so Windows Git does not hit MAX_PATH.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_investigation import InvestigationBridge
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.task_investigation import TaskInvestigation
from repo_maintainer.task_session import KIND, repository_identity, validate_transcript
from repo_maintainer.ui_registry import UIRegistry

ISSUE = 'Restore calibrated addition using the approved read-only dependency.'


def temporary_root():
    base = Path(os.environ.get('R4_TEST_ROOT') or tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix='r4-', dir=str(base))


class InvestigationModel:
    """Deterministic local SSE model for the real Pi AgentSession tool loop."""

    def __init__(self, constant, value, marker):
        self.constant, self.value, self.marker = constant, value, marker
        self.requests = []
        self.phase = 'normal'
        self.extra_search = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _text(self, message):
                content = message.get('content')
                if isinstance(content, str):
                    return content
                return ''.join(part.get('text', '') for part in content
                               if isinstance(part, dict))

            def _reply(self, body, name=None, args=None, content=None):
                if name is None:
                    delta = {'role': 'assistant', 'content': content or 'done'}
                    finish = 'stop'
                else:
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0,
                        'id': 'call_%d' % len(owner.requests), 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(args)}}]}
                    finish = 'tool_calls'
                frame = {'id': 'synthetic-r4', 'object': 'chat.completion.chunk', 'created': 1,
                         'model': body['model'], 'choices': [{'index': 0, 'delta': delta,
                         'finish_reason': finish}],
                         'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11}}
                raw = ('data: ' + json.dumps(frame, ensure_ascii=False) +
                       '\n\ndata: [DONE]\n\n').encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                tool_names = [tool['function']['name'] for tool in body.get('tools', [])]
                users = [index for index, message in enumerate(body['messages'])
                         if message.get('role') == 'user']
                current = body['messages'][users[-1] + 1:]
                seen = [message for message in current if message.get('role') == 'tool']
                last = self._text(seen[-1]) if seen else ''
                if not seen:
                    if owner.phase == 'readonly':
                        self._reply(body, 'propose_repair', {'diagnosis': 'edit dependency',
                            'risk': 'synthetic', 'edits': [{'path': 'logic/hidden.py',
                            'replacements': [{'oldText': str(owner.value),
                                              'newText': str(owner.value + 1)}]}]})
                        return
                    self._reply(body, 'search_approved_sources', {'query': owner.constant})
                    return
                if '"operation":"search_sources"' in last or '"operation": "search_sources"' in last:
                    found = json.loads(last)
                    self._reply(body, 'read_approved_source',
                                {'path': found['hits'][0]['source']['path']})
                    return
                if '"operation":"read_source"' in last or '"operation": "read_source"' in last:
                    found = json.loads(last)
                    lines='\n'.join(line['text'] for line in found['lines'])
                    match=re.search(r'^OFFSET_[a-f0-9]+ = (\d+)$',lines,re.MULTILINE)
                    if not match:
                        self._reply(body,content='Missing actual calibration evidence')
                        return
                    owner.observed_value=int(match.group(1))
                    if owner.phase == 'budget' and not owner.extra_search:
                        owner.extra_search = True
                        self._reply(body, 'search_approved_sources', {'query': owner.constant})
                        return
                    self._reply(body, 'propose_repair', {'diagnosis':
                        'Use the constant read from the approved dependency', 'risk': 'low',
                        'edits': [{'path': 'calculator.py', 'replacements': [{
                            'oldText': 'return a - b',
                            'newText': 'return a + b + %s' % owner.observed_value}]}]})
                    return
                if 'Investigation budget exhausted' in last:
                    self._reply(body, 'read_source', {'path': 'calculator.py'})
                    return
                if 'before_sha256' in last and owner.phase != 'readonly':
                    self._reply(body, 'propose_repair', {'diagnosis':
                        'Use the constant read from the approved dependency', 'risk': 'low',
                        'edits': [{'path': 'calculator.py', 'replacements': [{
                            'oldText': 'return a - b',
                            'newText': 'return a + b + %s' % owner.observed_value}]}]})
                    return
                if owner.phase == 'readonly':
                    self._reply(body, content='The dependency is read-only; no plan.')
                    return
                self._reply(body, content='submitted')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:%d/v1' % self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class PiInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_root()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = create_fixture(self.root / 'source')
        self.constant = 'OFFSET_' + secrets.token_hex(3)
        self.value = secrets.randbelow(13) + 7
        self.marker = 'r4-marker-' + secrets.token_hex(6)
        (self.source / 'logic').mkdir()
        (self.source / 'logic/__init__.py').write_text('', encoding='utf-8')
        (self.source / 'logic/hidden.py').write_text(
            '%s = %d\nMARKER = %r\n' % (self.constant, self.value, self.marker), encoding='utf-8')
        (self.source / 'logic/.env').write_text('PRIVATE_KEY=must-not-enter-context\n',
                                                encoding='utf-8')
        (self.source / 'calculator.py').write_text(
            'from logic.hidden import %s\n\n\ndef add(a, b):\n    return a - b\n' % self.constant,
            encoding='utf-8')
        (self.source / 'tests/test_calculator.py').write_text(
            'import unittest\n\nfrom calculator import add\nfrom logic.hidden import %s\n\n\n'
            'class CalculatorTests(unittest.TestCase):\n'
            '    def test_positive(self):\n        self.assertEqual(add(2, 3), 5 + %s)\n'
            '    def test_negative_regression(self):\n        self.assertEqual(add(-2, -3), -5 + %s)\n'
            '    def test_zero(self):\n        self.assertEqual(add(0, 0), %s)\n'
            '    def test_mixed(self):\n        self.assertEqual(add(5, -2), 3 + %s)\n'
            % ((self.constant,) * 5), encoding='utf-8')
        self.repo = RepositoryGateway(self.source)
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=R4', '-c', 'user.email=r4@example.invalid',
                          'commit', '-m', 'fixture: hidden calibration dependency')
        self.model = InvestigationModel(self.constant, self.value, self.marker)
        self.addCleanup(self.model.close)
        self.session = None
        self.open()
        self.addCleanup(lambda: self.session and self.session.close())

    def open(self):
        # Only calculator.py enters the initial packet. The hidden dependency
        # is reachable solely through the approved investigation root.
        self.inner = PiProvider(('calculator.py',), self.model.url, max_requests=12)
        self.policy = Policy(('calculator.py',), (DEMO_TEST,))
        self.session = UISession(self.source, self.policy, self.inner, self.root / 'state')
        self.session.task_planner.use_controller_read_roots(('logic',), max_calls=2,
                                                            max_output_bytes=16384)

    def task(self):
        return self.session.registry.load(KIND)[self.session.task_planner.identity]

    def test_controller_rules_apply_to_new_tasks_without_upgrading_a_legacy_context(self):
        planner=self.session.task_planner
        scope,context=planner.current_scope(ISSUE)
        old=planner.store.allocate(scope,context)
        before=self.session.registry.budget()
        planner.restore_controller_read_scope(old['task_id'])
        self.assertIsNone(planner.investigation_config)
        self.assertNotIn('purpose',planner.current_scope(ISSUE)[1])
        self.assertEqual(self.session.registry.load('task_investigation'),{})
        fresh=planner.begin(ISSUE)
        self.assertNotEqual(fresh,old['task_id'])
        self.assertEqual(planner.scope['context_sha256'],
                         planner.current_scope(ISSUE)[0]['context_sha256'])
        self.assertEqual(self.task()['context']['purpose'],'repair-investigation-v1')
        self.assertEqual(self.session.registry.budget(),before)
        self.assertEqual(self.model.requests,[])

    def plan(self):
        return self.session._operate('plan', {'issue': ISSUE})

    def decision(self, plan, choice='reject'):
        return self.session._operate('decision', {**plan, 'decision': choice})

    def test_hidden_dependency_is_found_read_and_the_original_candidate_turns_green(self):
        red = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'],
                             cwd=self.source, capture_output=True, text=True)
        self.assertNotEqual(0, red.returncode, red.stdout + red.stderr)
        plan = self.plan()
        first = json.dumps(self.model.requests[0], ensure_ascii=False)
        user = [message for message in self.model.requests[0]['messages']
                if message.get('role') == 'user'][-1]
        content = user['content']
        packet = json.loads(content if isinstance(content, str) else content[0]['text'])
        self.assertEqual(['calculator.py'], packet['authorized_paths'])
        self.assertEqual(['logic'], packet['investigation_scope']['read_roots'])
        self.assertNotIn(self.marker, first)
        self.assertNotIn('%s = %d' % (self.constant, self.value), first)
        self.assertNotIn('tests/test_calculator.py', first)

        task = self.task()
        self.assertEqual('repair-investigation-v1', task['context']['purpose'])
        self.assertEqual(['logic'], task['context']['investigation']['read_roots'])
        journal = self.session.registry.load('task_investigation')[task['task_id']]
        self.assertEqual(2, journal['calls'])
        self.assertEqual(2, len(journal['receipts']))
        self.assertIn('logic/hidden.py', journal['read_paths'])
        self.assertTrue(any('logic/hidden.py' in json.dumps(request, ensure_ascii=False)
                            for request in self.model.requests[1:]))

        item = self.session.plans[plan['plan_id']]
        evidence = item['view']['planning_evidence']
        self.assertEqual(journal['scope_sha256'], evidence['investigation_scope_sha256'])
        self.assertEqual(['propose_repair', 'read_approved_source', 'read_source',
                          'search_approved_sources'], evidence['active_tools'])
        saved = json.loads(Path(item['path']).read_text(encoding='utf-8'))
        edit = saved['plan']['edits'][0]
        self.assertEqual('calculator.py', edit['path'])
        self.assertIn('return a + b + %s' % self.value, edit['content'])
        accepted = self.decision(plan, 'approve')
        self.assertEqual('candidate_ready', accepted['workflow_status'])
        record = json.loads((self.root / 'state' / accepted['run_id'] / 'run.json')
                            .read_text(encoding='utf-8'))
        self.assertEqual('passed', record['rounds'][-1]['verification'])
        self.assertTrue(record['source_unchanged'])
        self.assertGreaterEqual(len(self.model.requests), 3)

    def test_exhausted_capacity_is_reported_and_not_refilled_from_packaged_reads(self):
        self.model.phase = 'budget'
        plan = self.plan()
        task = self.task()
        journal = self.session.registry.load('task_investigation')[task['task_id']]
        self.assertEqual(2, journal['calls'])
        self.assertIn('Investigation budget exhausted',
                      json.dumps(self.model.requests, ensure_ascii=False))
        self.assertTrue(any('before_sha256' in json.dumps(request, ensure_ascii=False)
                            for request in self.model.requests))
        saved = json.loads(Path(self.session.plans[plan['plan_id']]['path'])
                           .read_text(encoding='utf-8'))
        self.assertIn('return a + b + %s' % self.value,
                      saved['plan']['edits'][0]['content'])

    def test_restart_keeps_capacity_and_rejects_a_tampered_result(self):
        plan = self.plan()
        rejected = self.decision(plan)
        task = self.task()
        task_id = task['task_id']
        session_file = (self.session.task_planner.store.directory(task_id) /
                        'sessions' / task['checkpoint']['file'])
        original = session_file.read_bytes()
        journal = self.session.registry.load('task_investigation')[task_id]
        self.assertEqual(2, journal['calls'])

        self.session.close()
        self.open()
        preview = self.session._resume_preview(rejected['run_id'])
        self.assertEqual(task_id, preview['task_session']['task_id'])
        self.assertEqual(2, self.session.registry.load('task_investigation')[task_id]['calls'])
        self.session.task_planner.begin(ISSUE, preview['task_session'])
        with self.assertRaisesRegex(SafetyError, 'budget exhausted'):
            self.session.task_planner.investigation.search_sources(self.constant)

        changed = original.replace(
            ('%s = %d' % (self.constant, self.value)).encode(),
            ('%s = %d' % (self.constant, self.value + 1)).encode())
        self.assertNotEqual(original, changed)
        session_file.write_bytes(changed)
        with self.assertRaisesRegex(SafetyError, 'investigation result changed|receipt'):
            self.session._resume_preview(rejected['run_id'])
        session_file.write_bytes(original)
        self.session._resume_preview(rejected['run_id'])

    def test_new_commit_blocks_the_next_turn_before_model_reservation(self):
        self.session.task_planner.begin(ISSUE)
        before = len(self.model.requests)
        (self.source / 'calculator.py').write_text(
            (self.source / 'calculator.py').read_text(encoding='utf-8') + '# changed\n',
            encoding='utf-8')
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=R4', '-c', 'user.email=r4@example.invalid',
                          'commit', '-m', 'source moved')
        with self.assertRaises(SafetyError):
            with self.session.task_planner.turn(RepositoryGateway(self.source), ISSUE,
                                                (DEMO_TEST,)):
                pass
        self.assertEqual(before, len(self.model.requests))

    def test_read_only_dependency_cannot_be_edited_by_the_model(self):
        self.model.phase = 'readonly'
        with self.assertRaises(SafetyError):
            self.plan()
        self.assertIn('return a - b', (self.source / 'calculator.py').read_text(encoding='utf-8'))
        journal = self.session.registry.load('task_investigation')[self.session.task_planner.identity]
        self.assertEqual(0, journal['calls'])


class InvestigationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_root()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = create_fixture(self.root / 'source')
        (self.source / 'logic').mkdir()
        (self.source / 'logic/hidden.py').write_text(
            'OFFSET = 7\n', encoding='utf-8')
        (self.source / 'logic/.env').write_text('PRIVATE_KEY=secret\n', encoding='utf-8')
        self.repo = RepositoryGateway(self.source)
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=R4', '-c', 'user.email=r4@example.invalid',
                          'commit', '-m', 'bridge fixture')
        self.registry = UIRegistry(self.root / 'state', {'provider': {'request_budget': 8}})
        self.addCleanup(lambda: self.registry.close())
        self.broker = TaskInvestigation(self.registry, self.repo, 'a' * 32)
        self.scope = dict(repository_id=repository_identity(self.repo.root, self.repo.common_dir),
                          base_sha=self.repo.head, read_roots=['logic'],
                          policy_sha256='b' * 64,
                          expires_at=(datetime.now(timezone.utc) +
                                      timedelta(minutes=10)).isoformat())
        self.sha = self.broker.register(**self.scope, max_calls=1, max_output_bytes=4096)
        self.bridge = InvestigationBridge(self.broker, scope_sha256=self.sha,
                                          base_sha=self.scope['base_sha'], read_roots=['logic'])
        self.addCleanup(lambda: self.bridge.close())

    def post(self, tool, args, token=None):
        request = Request(self.bridge.url + '/v1/tool',
                          data=json.dumps({'tool': tool, 'args': args}).encode('utf-8'),
                          headers={'Content-Type': 'application/json',
                                   'Authorization': 'Bearer ' + (token or self.bridge.token)},
                          method='POST')
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_allowed_read_then_budget_is_durable_across_restart(self):
        status, value = self.post('read_approved_source', {'path': 'logic/hidden.py'})
        self.assertEqual(200, status)
        self.assertEqual('read_source', value['operation'])
        self.assertFalse(value['runtime_authorized'])
        self.assertFalse(value['write_authorized'])
        status, value = self.post('read_approved_source', {'path': 'logic/hidden.py'})
        self.assertEqual(409, status)
        self.assertEqual({'error': 'budget_exhausted'}, value)
        self.assertEqual(1, self.registry.load('task_investigation')['a' * 32]['calls'])

        self.bridge.close()
        self.registry.close()
        self.registry = UIRegistry(self.root / 'state', {'provider': {'request_budget': 8}})
        self.broker = TaskInvestigation(self.registry, self.repo, 'a' * 32)
        self.assertEqual(['logic'], self.broker.existing_scope()['read_roots'])
        self.assertEqual(self.sha, self.broker.register(**self.scope, max_calls=1,
                                                        max_output_bytes=4096))
        self.bridge = InvestigationBridge(self.broker, scope_sha256=self.sha,
                                          base_sha=self.scope['base_sha'], read_roots=['logic'])
        status, value = self.post('read_approved_source', {'path': 'logic/hidden.py'})
        self.assertEqual(409, status)
        self.assertEqual({'error': 'budget_exhausted'}, value)

    def test_sensitive_outside_and_unauthorized_requests_return_fixed_errors(self):
        for path in ('logic/.env', 'calculator.py', '../outside.py'):
            with self.subTest(path=path):
                status, value = self.post('read_approved_source', {'path': path})
                self.assertEqual(403, status)
                self.assertEqual({'error': 'denied'}, value)
                self.assertNotIn('secret', json.dumps(value))
                self.assertNotIn(path, json.dumps(value))
        status, value = self.post('read_approved_source', {'path': 'logic/hidden.py'},
                                  token='0' * 64)
        self.assertEqual(401, status)
        self.assertEqual({'error': 'unauthorized'}, value)


class NativeInvestigationScopeTests(unittest.TestCase):
    SESSION = '22222222-2222-7222-a222-222222222222'

    def entries(self, cwd, context, name, arguments):
        entries = [{'type': 'session', 'version': 3, 'id': self.SESSION,
                    'timestamp': '2026-10-29T00:00:00Z', 'cwd': str(cwd)}]
        def add(kind, **fields):
            identity = '%08x' % len(entries)
            entries.append({'type': kind, 'id': identity,
                            'parentId': entries[-1].get('id') if len(entries) > 1 else None,
                            'timestamp': '2026-10-29T00:00:00Z', **fields})
        add('model_change', provider=context['provider'], modelId=context['model'])
        add('thinking_level_change', thinkingLevel='off')
        add('message', message={'role': 'user', 'content': [{'type': 'text', 'text': 'fixture'}],
                                'timestamp': 1})
        add('message', message={'role': 'assistant', 'provider': context['provider'],
                                'model': context['model'], 'timestamp': 2,
                                'stopReason': 'toolUse', 'content': [{'type': 'toolCall',
                                'id': 'call_fixture', 'name': name, 'arguments': arguments}]})
        return entries

    def raw(self, entries):
        return b''.join(json.dumps(entry, ensure_ascii=False).encode('utf-8') + b'\n'
                        for entry in entries)

    def test_legacy_context_cannot_call_a_new_investigation_tool(self):
        with temporary_root() as root:
            cwd = Path(root)
            context = {'provider': 'pi-loopback', 'model': 'mock-maintenance',
                       'paths': ['calculator.py']}
            entries = self.entries(cwd, context, 'search_approved_sources',
                                   {'query': 'OFFSET'})
            with self.assertRaisesRegex(SafetyError, 'Unauthorized Pi tool call'):
                validate_transcript(self.raw(entries), cwd=cwd, context=context)

    def test_investigation_context_cannot_propose_an_edit_outside_packaged_paths(self):
        with temporary_root() as root:
            cwd = Path(root)
            context = {'schema_version': 2, 'purpose': 'repair-investigation-v1',
                       'provider': 'pi-loopback', 'model': 'mock-maintenance',
                       'paths': ['calculator.py'],
                       'investigation': {'schema_version': 1, 'read_roots': ['logic'],
                                         'max_calls': 4, 'max_output_bytes': 32768}}
            entries = self.entries(cwd, context, 'propose_repair', {
                'diagnosis': 'edit read-only dependency', 'risk': 'synthetic',
                'edits': [{'path': 'logic/hidden.py',
                           'replacements': [{'oldText': '17', 'newText': '19'}]}]})
            with self.assertRaisesRegex(SafetyError, 'outside task scope'):
                validate_transcript(self.raw(entries), cwd=cwd, context=context,
                                    base_sha='b' * 40)


if __name__ == '__main__':
    unittest.main()
