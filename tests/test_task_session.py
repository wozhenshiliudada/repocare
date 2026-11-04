import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.task_session import TaskSessionStore, fingerprint, validate_transcript, KIND, MAX_SESSION_BYTES, _verify_schema_rejections
from repo_maintainer.ui_registry import UIRegistry

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'runtime/pi/test/persistence-spike-fixture.mjs'
SESSION = '11111111-1111-7111-a111-111111111111'
CONTEXT = {'provider': 'pi-persistence-spike', 'model': 'synthetic', 'paths': ['calculator.py']}


def transcript(cwd):
    header = {'type': 'session', 'version': 3, 'id': SESSION, 'timestamp': '2026-10-13T00:00:00Z', 'cwd': str(cwd)}
    entries = [header]
    def add(kind, **fields):
        identity = f'{len(entries):08x}'
        entry = {'type': kind, 'id': identity, 'parentId': entries[-1].get('id') if len(entries) > 1 else None,
                 'timestamp': '2026-10-13T00:00:00Z', **fields}
        entries.append(entry)
    add('model_change', provider=CONTEXT['provider'], modelId=CONTEXT['model'])
    add('thinking_level_change', thinkingLevel='off')
    add('message', message={'role': 'user', 'content': [{'type': 'text', 'text': 'fixture'}], 'timestamp': 1})
    add('message', message={'role': 'assistant', 'provider': CONTEXT['provider'], 'model': CONTEXT['model'],
                           'timestamp': 2, 'stopReason': 'toolUse', 'content': [{'type': 'toolCall', 'id': 'call_fixture',
                           'name': 'propose_repair', 'arguments': {'edits': [{'path': 'calculator.py'}]}}]})
    add('message', message={'role': 'toolResult', 'toolCallId': 'call_fixture', 'toolName': 'propose_repair',
                           'timestamp': 3, 'isError': False, 'content': [{'type': 'text', 'text': '{"accepted":true}'}]})
    add('message', message={'role': 'assistant', 'provider': CONTEXT['provider'], 'model': CONTEXT['model'],
                           'timestamp': 4, 'stopReason': 'stop', 'content': [{'type': 'text', 'text': 'done'}]})
    return entries


def raw(entries):
    return b''.join(json.dumps(entry, ensure_ascii=False).encode() + b'\n' for entry in entries)


class TaskSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pi-task-store-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.common = self.source / '.git'
        self.common.mkdir()
        self.binding = {'repository': str(self.source), 'policy': {'fixture': True}, 'provider': {'request_budget': 50}}
        self.registry = UIRegistry(self.root / 'state', self.binding)
        self.addCleanup(lambda: self.registry.close())
        self.store = TaskSessionStore(self.registry, self.source, self.common)
        self.scope = {key: digest(key.encode()) for key in ('issue_sha256', 'policy_sha256', 'provider_sha256',
                    'source_sha256', 'tests_sha256')}
        self.scope.update(repository_sha256=self.store.repository, base_sha='a' * 40, context_sha256=fingerprint(CONTEXT))
        self.record = self.store.allocate(self.scope, CONTEXT)
        self.identity = self.record['task_id']
        self.directory = self.store.directory(self.identity)
        (self.directory / 'workspace').mkdir()
        (self.directory / 'sessions').mkdir()
        self.file = self.directory / 'sessions' / 'fixture.jsonl'
        self.entries = transcript(self.directory / 'workspace')
        self.file.write_bytes(raw(self.entries))

    def settled(self):
        identity, _allowance = self.registry.reserve()
        self.registry.settle(identity, {'requests': 3}, success=True)
        return identity

    def commit(self):
        attempt = self.settled()
        with self.store.lease(self.identity, self.scope, 0) as lease:
            return lease.commit(self.file.name, self.entries[-1]['id'], 'b' * 64, attempt)

    def validate(self, entries=None, payload=None, leaf=None):
        return validate_transcript(payload if payload is not None else raw(entries or self.entries),
                                   cwd=self.directory / 'workspace', context=CONTEXT, leaf=leaf)

    def test_checkpoint_reopens_from_same_journal_without_changing_budget(self):
        saved = self.commit()
        before = self.registry.budget()
        self.registry.close()
        self.registry = UIRegistry(self.root / 'state', self.binding)
        self.store = TaskSessionStore(self.registry, self.source, self.common)
        with self.store.lease(self.identity, self.scope, saved['revision']) as lease:
            receipt = lease.descriptor()
        self.assertEqual(SESSION, receipt['session_id'])
        self.assertEqual(self.entries[-1]['id'], receipt['leaf_id'])
        self.assertEqual(before, self.registry.budget())
        self.assertNotIn('requests_used', json.dumps(self.registry.load(KIND)))

    def test_real_pi_mapped_thinking_checkpoint_and_legacy_history_keep_original_ledger(self):
        node = shutil.which('node')
        if not node or not (ROOT/'runtime/pi/node_modules/@earendil-works/pi-coding-agent').is_dir():
            self.skipTest('Locked Pi runtime unavailable')
        legacy = self.commit()
        legacy_bytes = self.file.read_bytes()
        settings = {'PI_MAINTENANCE_REASONING_PROTOCOL':'deepseek', 'PI_MAINTENANCE_REASONING_EFFORT':'medium',
                    'PI_MAINTENANCE_REASONING_LEVELS':json.dumps([{'level':'medium','api_value':'high'}],separators=(',',':'))}
        config = self.root/'fixture-provider.env'
        config.write_text('\n'.join(f'{k}={v}' for k,v in {
            'PI_MAINTENANCE_BASE_URL':'https://example.invalid/v1', 'PI_MAINTENANCE_API_KEY':'synthetic-key',
            'PI_MAINTENANCE_MODEL':'fixture-model', **settings}.items()), encoding='utf-8')
        configured = PiProvider(('calculator.py',))
        configured.config_file = config
        self.assertEqual(configured.task_identity()[0]['thinking_level'], 'medium')
        context = {**configured.task_identity()[0], 'provider':'pi-loopback', 'model':'mock-maintenance'}
        scope = {**self.scope, 'context_sha256':fingerprint(context)}
        task = self.store.allocate(scope,context)
        directory = self.store.directory(task['task_id'])
        cwd, sessions = directory/'workspace', directory/'sessions'
        cwd.mkdir(); sessions.mkdir()
        content = 'def add(a, b):\n    return a - b\n'
        request = {'issue':'Fix addition', 'base_sha':scope['base_sha'], 'request_budget':3,
            'files':[{'path':'calculator.py','before_sha256':digest(content.encode()),'content':content}],
            'tests':[['{python}','-m','unittest']], 'task_session':{'mode':'create','context':context,
                'binding':{'schema_version':1,'task_id':task['task_id'],'revision':0,'cwd':str(cwd),'sessions_dir':str(sessions)}}}
        # This test calls the production Node planner with its explicit loopback transport.
        script = """
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {generatePlan} from './plan.mjs';
import {startMock} from './test/mock-server.mjs';
const input=JSON.parse(readFileSync(0,'utf8'));
const mock=await startMock();
try {
 const wrong=structuredClone(input);
 wrong.task_session.context.thinking_level='high';
 await assert.rejects(generatePlan(wrong,{mockUrl:mock.url}),e=>e.evidence.requests_started===0);
 assert.equal(mock.requests.length,0);
 const result=await generatePlan(input,{mockUrl:mock.url});
 assert.equal(mock.requests.length,2);
 assert.ok(mock.requests.every(r=>r.reasoning_effort==='high'));
 process.stdout.write(JSON.stringify(result));
} finally {await mock.close();}
"""
        env = {k:v for k,v in os.environ.items() if not k.startswith('PI_MAINTENANCE_')}
        result = subprocess.run([node,'--input-type=module','-e',script], input=json.dumps(request),
            cwd=ROOT/'runtime/pi', env={**env,**settings}, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['status'],'completed')
        self.assertEqual(plan['plan']['edits'][0]['content'],content.replace('a - b','a + b'))
        checkpoint = plan['task_checkpoint']
        attempt,_ = self.registry.reserve()
        self.registry.settle(attempt,plan['evidence'],success=True)
        native = sessions/checkpoint['file']
        original = native.read_bytes()
        with self.store.lease(task['task_id'],scope,0) as lease:
            saved = lease.commit(native.name,checkpoint['leaf_id'],checkpoint['snapshot_sha256'],attempt)
        self.registry.close()
        self.registry = UIRegistry(self.root/'state',self.binding)
        self.store = TaskSessionStore(self.registry,self.source,self.common)
        with self.store.lease(task['task_id'],scope,saved['revision']) as lease:
            self.assertEqual(lease.descriptor()['file_sha256'],digest(original))
        with self.store.lease(self.identity,self.scope,legacy['revision']) as lease:
            self.assertEqual(lease.descriptor()['file_sha256'],digest(legacy_bytes))
        self.assertEqual(self.registry.budget()['requests_used'],5)
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],0)
        entries = [json.loads(line) for line in original.splitlines()]
        level = next(e for e in entries if e['type']=='thinking_level_change')
        self.assertEqual(level['thinkingLevel'],'medium')
        for invalid in ('high','invented'):
            level['thinkingLevel'] = invalid
            with self.assertRaisesRegex(SafetyError,'Unexpected Pi thinking settings'):
                validate_transcript(raw(entries),cwd=cwd,context=context)
        level['thinkingLevel'] = 'medium'
        assistant = next(e['message'] for e in entries if e.get('message',{}).get('role')=='assistant')
        assistant['thinkingLevel'] = 'high'
        with self.assertRaisesRegex(SafetyError,'Unexpected Pi assistant thinking settings'):
            validate_transcript(raw(entries),cwd=cwd,context=context)
        assistant['thinkingLevel'] = 'medium'
        entries.remove(level)
        next(e for e in entries if e.get('parentId')==level['id'])['parentId'] = level['parentId']
        with self.assertRaisesRegex(SafetyError,'Unexpected Pi assistant thinking settings'):
            validate_transcript(raw(entries),cwd=cwd,context=context)
        with self.assertRaisesRegex(SafetyError,'Invalid bound Pi thinking level'):
            validate_transcript(original,cwd=cwd,context={**context,'thinking_level':'invented'})
        changed_scope = {**scope,'context_sha256':fingerprint({**context,'thinking_level':'high'})}
        with self.assertRaises(SafetyError):
            with self.store.lease(task['task_id'],changed_scope,saved['revision']):
                self.fail('A different profile effort opened the original checkpoint')
        self.assertEqual(native.read_bytes(),original)

    def with_denied_read(self):
        entries=copy.deepcopy(self.entries)
        assistant=copy.deepcopy(entries[4])
        assistant.update(id='aa000001',parentId=entries[3]['id'])
        assistant['message']['content']=[{'type':'toolCall','id':'denied_read',
            'name':'read_source','arguments':{'path':'outside.py'}}]
        result=copy.deepcopy(entries[5])
        result.update(id='aa000002',parentId=assistant['id'])
        denial={'error':'Path not authorized for read','requests_remaining':49}
        result['message'].update(toolCallId='denied_read',toolName='read_source',isError=True,
            content=[{'type':'text','text':json.dumps(denial)}],details=denial)
        entries[4]['parentId']=result['id']
        return entries[:4]+[assistant,result]+entries[4:]

    def test_exact_denied_read_is_history_not_authorized_access(self):
        self.assertEqual(self.validate(self.with_denied_read())['rounds'],1)

    def with_schema_rejection(self):
        entries=self.with_denied_read()
        entries[4]['message']['content'][0]['arguments']={'query':'def im('}
        entries[5]['message'].update(content=[{'type':'text','text':
            'Validation failed for tool "read_source":\n  - path: must have required properties path'
            '\n\nReceived arguments:\n{\n  "query": "def im("\n}'}],details={})
        return entries

    def test_sdk_schema_rejection_then_correct_proposal_is_complete_history(self):
        self.assertEqual(self.validate(self.with_schema_rejection())['rounds'],1)

    def test_schema_receipt_cannot_fake_success_arguments_or_error(self):
        for mutation in ('success','arguments','details','extra_text','error','valid_call','mismatch','missing'):
            entries=self.with_schema_rejection();result=entries[5]['message']
            if mutation=='success':result['isError']=False
            elif mutation=='arguments':entries[4]['message']['content'][0]['arguments']['query']='class im'
            elif mutation=='details':result['details']={'content':'not a validation receipt'}
            elif mutation=='extra_text':result['content'].append({'type':'text','text':'extra'})
            elif mutation=='error':result['content'][0]['text']=result['content'][0]['text'].replace('required properties','other error')
            elif mutation=='valid_call':entries[4]['message']['content'][0]['arguments']={'path':'calculator.py'}
            elif mutation=='mismatch':result['toolName']='propose_repair'
            else:
                entries[6]['parentId']=entries[4]['id'];del entries[5]
            with self.subTest(mutation=mutation),self.assertRaises(SafetyError):self.validate(entries)

    def test_unverified_schema_pair_on_unselected_branch_is_rejected(self):
        entries=self.with_schema_rejection()
        entries[5]['message']['content'][0]['text']+='tampered'
        entries[6]['parentId']=entries[3]['id']  # valid selected branch cannot hide the invalid pair
        with self.assertRaisesRegex(SafetyError,'schema rejection receipt'):self.validate(entries)

    def test_mixed_malformed_edit_cannot_hide_explicit_foreign_path(self):
        entries=self.with_schema_rejection()
        entries[4]['message']['content'][0].update(name='propose_repair',arguments={
            'edits':['not an object',{'path':'outside.py'}]})
        with self.assertRaisesRegex(SafetyError,'outside task scope'):self.validate(entries)

    def test_schema_verifier_unavailable_fails_closed_and_strips_hooks(self):
        pair={'call':{'name':'read_source','arguments':{}},'text':'untrusted'}
        with patch('repo_maintainer.task_session.shutil.which',return_value=None):
            with self.assertRaisesRegex(SafetyError,'requires Node.js'):_verify_schema_rejections([pair])
        for error in (OSError('unavailable'),subprocess.TimeoutExpired('node',20)):
            with patch('repo_maintainer.task_session.subprocess.run',side_effect=error):
                with self.assertRaisesRegex(SafetyError,'unavailable'):_verify_schema_rejections([pair])
        with patch.dict(os.environ,{'NODE_OPTIONS':'forbidden','NODE_PATH':'forbidden',
                                   'PI_MAINTENANCE_API_KEY':'not-a-real-key'}):
            with patch('repo_maintainer.task_session.subprocess.run',return_value=
                       subprocess.CompletedProcess([],0,'true','')) as called:
                _verify_schema_rejections([pair])
        env=called.call_args.kwargs['env']
        self.assertFalse({'NODE_OPTIONS','NODE_PATH','PI_MAINTENANCE_API_KEY'} & set(env))
        for response in (subprocess.CompletedProcess([],1,'true',''),
                         subprocess.CompletedProcess([],0,'false',''),
                         subprocess.CompletedProcess([],0,'true','warning')):
            with patch('repo_maintainer.task_session.subprocess.run',return_value=response):
                with self.assertRaisesRegex(SafetyError,'schema rejection receipt'):_verify_schema_rejections([pair])

    def test_outside_read_success_or_data_in_denial_rejected(self):
        for mutation in ['success','content','details','mismatch','wrong_error','bad_remaining','missing_details']:
            entries=self.with_denied_read()
            result=entries[5]['message']
            if mutation=='success':result['isError']=False
            elif mutation=='content':result['content'][0]['text']='{"content":"secret"}'
            elif mutation=='details':result['details']['content']='secret'
            elif mutation=='mismatch':result['toolCallId']='other'
            elif mutation=='wrong_error':
                result['details']['error']='Unknown failure'
                result['content'][0]['text']=json.dumps(result['details'])
            elif mutation=='bad_remaining':
                result['details']['requests_remaining']=True
                result['content'][0]['text']=json.dumps(result['details'])
            else:del result['details']
            with self.subTest(mutation=mutation),self.assertRaises(SafetyError):
                self.validate(entries)

    def test_outside_proposal_not_tolerated_even_when_denied(self):
        entries=self.with_denied_read()
        entries[4]['message']['content'][0].update(name='propose_repair',arguments={'edits':[{'path':'outside.py'}]})
        entries[5]['message']['toolName']='propose_repair'
        with self.assertRaises(SafetyError):self.validate(entries)

    def test_valid_uncommitted_tail_is_preserved_and_excluded(self):
        self.commit()
        tail = {'type': 'message', 'id': 'ffffff00', 'parentId': self.entries[-1]['id'],
                'timestamp': 'now', 'message': {'role': 'user', 'content': 'Unverified tail', 'timestamp': 5}}
        self.file.write_bytes(raw(self.entries + [tail]))
        before = self.file.read_bytes()
        with self.store.lease(self.identity, self.scope, 1) as lease:
            receipt = lease.descriptor()
        self.assertTrue(receipt['tail_uncommitted'])
        self.assertEqual(before, self.file.read_bytes())
        self.assertEqual(self.entries[-1]['id'], receipt['leaf_id'])

    def test_changed_committed_prefix_is_rejected_even_with_unchanged_identity(self):
        self.commit()
        self.entries[3]['message']['content'][0]['text'] = 'tampered'
        self.file.write_bytes(raw(self.entries))
        with self.store.lease(self.identity, self.scope, 1) as lease:
            with self.assertRaisesRegex(SafetyError, 'history changed'):
                lease.descriptor()

    def test_scope_changes_block_same_task(self):
        for field in self.scope:
            with self.subTest(field=field):
                changed = {**self.scope, field: 'c' * (40 if field == 'base_sha' else 64)}
                with self.assertRaises(SafetyError):
                    with self.store.lease(self.identity, changed, 0):
                        self.fail('changed scope accepted')

    def test_missing_other_task_and_invalid_id_are_rejected(self):
        for identity in ('../escape', 'c' * 32, True):
            with self.subTest(identity=identity), self.assertRaises(SafetyError):
                with self.store.lease(identity, self.scope, 0):
                    self.fail('foreign identity accepted')

    def test_single_writer_and_closed_lease(self):
        with self.store.lease(self.identity, self.scope, 0) as lease:
            with self.assertRaisesRegex(SafetyError, 'Another writer'):
                with self.store.lease(self.identity, self.scope, 0):
                    self.fail('second writer accepted')
        with self.assertRaisesRegex(SafetyError, 'closed'):
            lease.commit(self.file.name, self.entries[-1]['id'], 'b' * 64, self.settled())

    def test_stale_revision_and_compare_and_swap_are_rejected(self):
        with self.assertRaisesRegex(SafetyError, 'Stale'):
            with self.store.lease(self.identity, self.scope, 1):
                self.fail('stale revision accepted')
        attempt = self.settled()
        with self.store.lease(self.identity, self.scope, 0) as lease:
            actual = self.validate()
            checkpoint = {k: actual[k] for k in ('session_id', 'leaf_id', 'prefix_bytes', 'prefix_sha256', 'rounds')}
            checkpoint.update(file=self.file.name, snapshot_sha256='b' * 64, attempt_id=attempt)
            changed = {**self.record, 'revision': 1, 'checkpoint': checkpoint}
            self.registry.put(KIND, self.identity, changed)
            with self.assertRaisesRegex(SafetyError, 'compare-and-swap'):
                lease.commit(self.file.name, self.entries[-1]['id'], 'b' * 64, attempt)

    def test_missing_empty_and_escaped_file_do_not_initialize_new_history(self):
        attempt = self.settled()
        for name in ('missing.jsonl', '../fixture.jsonl', 'C:\\escape.jsonl', ''):
            with self.subTest(name=name), self.store.lease(self.identity, self.scope, 0) as lease:
                with self.assertRaises(SafetyError):
                    lease.commit(name, self.entries[-1]['id'], 'b' * 64, attempt)
        self.file.write_bytes(b'')
        with self.store.lease(self.identity, self.scope, 0) as lease:
            with self.assertRaises(SafetyError):
                lease.commit(self.file.name, self.entries[-1]['id'], 'b' * 64, attempt)
        self.assertEqual(b'', self.file.read_bytes())

    def test_oversized_file_is_rejected_before_loading(self):
        with self.file.open('wb') as stream:
            stream.truncate(MAX_SESSION_BYTES + 1)
        with self.store.lease(self.identity, self.scope, 0) as lease:
            with self.assertRaisesRegex(SafetyError, 'read limit'):
                lease.commit(self.file.name, self.entries[-1]['id'], 'b' * 64, self.settled())

    def test_bad_lines_duplicate_keys_utf8_and_nonfinite_numbers_are_rejected(self):
        for payload in (self.file.read_bytes() + b'{"unfinished":', self.file.read_bytes() + b'garbage\n',
                        b'\xff\n' + self.file.read_bytes(), b'\n' + self.file.read_bytes(),
                        self.file.read_bytes().replace(b'"version": 3', b'"version":3,"version":3'),
                        self.file.read_bytes().replace(b'"timestamp": 1', b'"timestamp": NaN')):
            with self.subTest(payload=payload[:30]), self.assertRaises(SafetyError):
                self.validate(payload=payload)

    def test_unsupported_entry_role_model_and_tool_definitions_are_rejected(self):
        for mutation in ('compaction', 'context_edit', 'custom_message', 'role', 'model', 'system_tool'):
            entries = copy.deepcopy(self.entries)
            if mutation in ('compaction', 'context_edit', 'custom_message'):
                entries[-1]['type'] = mutation
            elif mutation == 'role':
                entries[-1]['message']['role'] = 'bashExecution'
            elif mutation == 'model':
                entries[1]['modelId'] = 'other-model'
            else:
                entries[3]['message'] = {'role': 'system', 'content': '', 'timestamp': 1, 'toolsAdded': [{'name': 'bash'}]}
            with self.subTest(mutation=mutation), self.assertRaises(SafetyError):
                self.validate(entries)

    def test_broken_chain_duplicate_entry_and_wrong_session_are_rejected(self):
        for mutation in ('missing_parent', 'cycle', 'duplicate', 'session'):
            entries = copy.deepcopy(self.entries)
            if mutation == 'missing_parent':
                entries[-1]['parentId'] = 'deadbeef'
            elif mutation == 'cycle':
                entries[-1]['parentId'] = entries[-1]['id']
            elif mutation == 'duplicate':
                entries[-1]['id'] = entries[-2]['id']
            else:
                entries[0]['id'] = 'bad-session'
            with self.subTest(mutation=mutation), self.assertRaises(SafetyError):
                self.validate(entries)

    def test_orphan_mismatch_unauthorized_path_and_incomplete_leaf_are_rejected(self):
        for mutation in ('orphan', 'mismatch', 'path', 'tool', 'incomplete'):
            entries = copy.deepcopy(self.entries)
            if mutation == 'orphan':
                entries[5]['message']['toolCallId'] = 'other'
            elif mutation == 'mismatch':
                entries[5]['message']['toolName'] = 'read_source'
            elif mutation == 'path':
                entries[4]['message']['content'][0]['arguments']['edits'][0]['path'] = 'tests/test_bad.py'
            elif mutation == 'tool':
                entries[4]['message']['content'][0]['name'] = 'bash'
            else:
                entries = entries[:5]
            with self.subTest(mutation=mutation), self.assertRaises(SafetyError):
                self.validate(entries)

    def test_unknown_attempt_cannot_be_committed_and_preserves_original_budget(self):
        attempt, _ = self.registry.reserve()
        self.registry.settle(attempt, {'requests': None}, success=False)
        before = self.registry.budget()
        with self.store.lease(self.identity, self.scope, 0) as lease:
            with self.assertRaisesRegex(SafetyError, 'settled original'):
                lease.commit(self.file.name, self.entries[-1]['id'], 'b' * 64, attempt)
        self.assertEqual(before, self.registry.budget())

    def test_attempt_replay_and_cross_journal_are_rejected(self):
        attempt = self.settled()
        with self.store.lease(self.identity, self.scope, 0) as lease:
            lease.commit(self.file.name, self.entries[-1]['id'], 'b' * 64, attempt)
        record = self.store.allocate(self.scope, CONTEXT)
        other = self.store.directory(record['task_id'])
        (other / 'workspace').mkdir()
        (other / 'sessions').mkdir()
        data = transcript(other / 'workspace')
        (other / 'sessions' / 'fixture.jsonl').write_bytes(raw(data))
        with self.store.lease(record['task_id'], self.scope, 0) as lease:
            with self.assertRaisesRegex(SafetyError, 'already bound'):
                lease.commit('fixture.jsonl', data[-1]['id'], 'b' * 64, attempt)
        with patch.object(self.registry, 'path', self.root / 'other.sqlite3'):
            with self.assertRaisesRegex(SafetyError, 'budget binding'):
                with self.store.lease(self.identity, self.scope, 1):
                    self.fail('different budget path accepted')
        foreign = copy.copy(self.registry)
        foreign.path = self.root / 'foreign.sqlite3'
        with self.assertRaisesRegex(SafetyError, 'Cross-journal'):
            TaskSessionStore(self.registry, self.source, self.common, budget_registry=foreign)

    def test_state_directory_inside_source_is_rejected(self):
        with patch.object(self.registry, 'root', self.source / 'ui-state'):
            with self.assertRaisesRegex(SafetyError, 'outside source'):
                TaskSessionStore(self.registry, self.source, self.common)

    def test_new_checkpoint_must_descend_from_committed_leaf(self):
        self.commit()
        additions = copy.deepcopy(self.entries[3:])
        parent = self.entries[3]['id']  # branch before the first committed proposal
        for index, entry in enumerate(additions):
            entry['id'] = f'{1000 + index:08x}'
            entry['parentId'] = parent
            parent = entry['id']
        self.file.write_bytes(raw(self.entries + additions))
        attempt = self.settled()
        with self.store.lease(self.identity, self.scope, 1) as lease:
            with self.assertRaisesRegex(SafetyError, 'abandoned'):
                lease.commit(self.file.name, additions[-1]['id'], 'b' * 64, attempt)

    def test_junction_file_is_rejected_without_reading_target(self):
        original = getattr(Path, 'is_junction', lambda value: False)
        # Python 3.11 has no is_junction; exercise the rejection guard there too.
        with patch.object(Path, 'is_junction', lambda value: value == self.file or original(value), create=True):
            with self.store.lease(self.identity, self.scope, 0) as lease:
                with self.assertRaisesRegex(SafetyError, 'junction'):
                    lease.commit(self.file.name, self.entries[-1]['id'], 'b' * 64, self.settled())

    def test_real_pi_reopens_controller_checkpoint_and_rejects_changed_receipt_bytes(self):
        self.file.unlink()  # exact test-owned fixture file only; SDK creates its own file
        node = shutil.which('node')
        self.assertIsNotNone(node)
        env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
               'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'COMSPEC', 'PATHEXT'}}
        def probe(phase, checkpoint=None):
            payload = {'root': str(self.directory), 'phase': phase, 'request_budget': 50 if phase == 'create' else 0,
                       'content': 'def add(a, b):\n    return a - b\n'}
            if checkpoint:
                payload['checkpoint'] = checkpoint
            return subprocess.run([node, str(FIXTURE)], input=json.dumps(payload), encoding='utf-8',
                                  capture_output=True, env=env, timeout=40)
        first = probe('create')
        self.assertEqual(0, first.returncode, first.stderr)
        first = json.loads(first.stdout)
        attempt = self.settled()
        with self.store.lease(self.identity, self.scope, 0) as lease:
            lease.commit(Path(first['session_file']).name, first['leaf_id'], 'b' * 64, attempt)
            receipt = lease.descriptor()
            before = self.registry.budget()
            file = Path(first['session_file'])
            tail = {'type': 'message', 'id': 'fffffff0', 'parentId': first['leaf_id'], 'timestamp': 'now',
                    'message': {'role': 'user', 'content': 'uncommitted tail', 'timestamp': 10}}
            file.write_bytes(file.read_bytes() + raw([tail]))
            receipt = lease.descriptor()
            self.assertTrue(receipt['tail_uncommitted'])
            opened = probe('open', receipt)
            self.assertEqual(0, opened.returncode, opened.stderr)
            opened = json.loads(opened.stdout)
            self.assertEqual(first['session_id'], opened['session_id'])
            self.assertEqual(first['final_messages'], opened['final_messages'])
            self.assertEqual(0, opened['observed_http_requests'])
            self.assertEqual(before, self.registry.budget())
            file.write_bytes(file.read_bytes() + b'\n')
            failed = probe('open', receipt)
            self.assertNotEqual(0, failed.returncode)
            self.assertIn('size changed', failed.stderr)
            self.assertEqual(before, self.registry.budget())


if __name__ == '__main__':
    unittest.main()
