"""Private Pi checkpoints. No model calls, approvals, budget mutation or SDK recovery here."""
from __future__ import annotations
from .maintenance_limits import LIMITS

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from filelock import FileLock, Timeout

from .git_investigation import allowed_path
from .models import SafetyError, digest, safe_context_path
from .pi_investigation import (INVESTIGATION_PURPOSE, validate_investigation_context)
from .dashboard import unlinked
from .pi_distribution import runtime_script
from .provider_config import CANONICAL_REASONING_LEVELS
from .runtime_limits import MAX_REQUESTS

MAX_SESSION_BYTES = LIMITS["native_session_max_bytes"]
MAX_ENTRIES = 20000
TASK_ID = re.compile(r'[0-9a-f]{32}')
ENTRY_ID = re.compile(r'(?:[0-9a-f]{8}|[0-9a-f]{32}|[0-9a-f-]{36})')
SESSION_ID = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')
SHA = re.compile(r'[0-9a-f]{64}')
TOOLS = {'read_source', 'propose_repair'}
VALIDATION_PURPOSE = 'validation-proposal-v1'
VALIDATION_DATA_SCHEMA = 'data-behavior-v2'
VALIDATION_TOOLS = {'read_source', 'propose_validation'}
INVESTIGATION_TOOLS = {'read_source', 'propose_repair',
                       'search_approved_sources', 'read_approved_source'}
INVESTIGATION_SCOPE_TOOLS = {'search_approved_sources', 'read_approved_source'}
SCOPE_KEYS = {'repository_sha256', 'base_sha', 'issue_sha256', 'policy_sha256',
              'provider_sha256', 'source_sha256', 'tests_sha256', 'context_sha256'}
KIND = 'pi_task'




def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                             allow_nan=False).encode())


def repository_identity(root, common_dir):
    return fingerprint([os.path.normcase(str(unlinked(Path(root)))),
                        os.path.normcase(str(unlinked(Path(common_dir))))])


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate key in Pi session JSON')
        result[key] = value
    return result


def _constant(_value):
    raise SafetyError('Non-finite number in Pi session JSON')


def strict_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise SafetyError('Invalid Pi session JSON') from error


def _context(value):
    require(isinstance(value, dict), 'Invalid Pi task context')
    fields = set(value) - {'thinking_level'}
    if 'thinking_level' in value:
        require(value['thinking_level'] in CANONICAL_REASONING_LEVELS[1:], 'Invalid bound Pi thinking level')
    legacy = fields == {'provider', 'model', 'paths'}
    proposal = fields == {'schema_version', 'purpose', 'provider', 'model', 'paths'} \
        and type(value['schema_version']) is int and value['schema_version'] == 2 \
        and value['purpose'] == VALIDATION_PURPOSE
    proposal_data = fields == {
        'schema_version', 'purpose', 'provider', 'model', 'paths', 'validation_schema'} \
        and type(value['schema_version']) is int and value['schema_version'] == 3 \
        and value['purpose'] == VALIDATION_PURPOSE and value['validation_schema'] == VALIDATION_DATA_SCHEMA
    investigation = fields == {
        'schema_version', 'purpose', 'provider', 'model', 'paths', 'investigation'} \
        and value.get('purpose') == INVESTIGATION_PURPOSE
    if investigation:
        validate_investigation_context(value)
    require(legacy or proposal or proposal_data or investigation, 'Invalid Pi task context')
    require(all(isinstance(value[k], str) and 0 < len(value[k]) <= 128 for k in ('provider', 'model')),
            'Invalid Pi task model identity')
    paths = value['paths']
    require(isinstance(paths, list) and 1 <= len(paths) <= 20 and
            all(isinstance(p, str) for p in paths) and len({p.casefold() for p in paths}) == len(paths),
            'Invalid Pi task authorized paths')
    for p in paths:
        safe_context_path(p)
    return deepcopy(value)


def _scope(value, context, repository):
    require(isinstance(value, dict) and set(value) == SCOPE_KEYS, 'Invalid Pi task scope')
    require(all(isinstance(value[k], str) and SHA.fullmatch(value[k]) for k in SCOPE_KEYS - {'base_sha'}),
            'Invalid Pi task scope fingerprint')
    require(isinstance(value['base_sha'], str) and re.fullmatch(r'[0-9a-f]{40}', value['base_sha']),
            'Invalid Pi task Git baseline')
    require(value['context_sha256'] == fingerprint(context) and value['repository_sha256'] == repository,
            'Pi task context or repository changed')
    return deepcopy(value)


def _text_content(content, assistant=False, *, tools=TOOLS):
    if isinstance(content, str):
        require(not assistant, 'Invalid assistant content')
        return []
    require(isinstance(content, list), 'Invalid Pi message content')
    calls = []
    for part in content:
        require(isinstance(part, dict), 'Invalid Pi content part')
        kind = part.get('type')
        if kind == 'text':
            require(isinstance(part.get('text'), str), 'Invalid Pi text part')
        elif assistant and kind == 'thinking':
            require(isinstance(part.get('thinking'), str), 'Invalid Pi thinking part')
        elif assistant and kind == 'toolCall':
            require(isinstance(part.get('id'), str) and 0 < len(part['id']) <= 256 and
                    part.get('name') in tools and isinstance(part.get('arguments'), dict),
                    'Unauthorized Pi tool call')
            calls.append(part)
        else:
            raise SafetyError('Unsupported Pi content part')
    return calls


def _verify_schema_rejections(pairs, purpose='repair', *, proposals=None, paths=None, validation_schema=None):
    """Recompute exact SDK errors, once per transcript; never execute a tool or model."""
    require(purpose in {'repair', VALIDATION_PURPOSE}, 'Invalid Pi schema verification purpose')
    require(validation_schema is None or purpose == VALIDATION_PURPOSE and validation_schema == VALIDATION_DATA_SCHEMA,
            'Invalid controller validation schema')
    if not pairs and purpose == 'repair':
        return
    node = shutil.which('node')
    require(node is not None, 'Pi schema receipt verification requires Node.js')
    script = runtime_script('schema-rejection.mjs')
    # Strip provider keys, NODE_OPTIONS/NODE_PATH and other inherited code/config hooks.
    env = {k:v for k,v in os.environ.items() if k.upper() in {
        'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT'}}
    body = pairs if purpose == 'repair' else {'rejections': pairs, 'proposals': proposals or [], 'paths': paths}
    if validation_schema is not None:
        body['validation_schema'] = validation_schema
    payload = json.dumps(body, ensure_ascii=False, allow_nan=False)
    require(len(payload.encode()) <= MAX_SESSION_BYTES, 'Pi schema receipt batch exceeds read limit')
    try:
        result = subprocess.run([node,str(script),'--purpose',purpose], input=payload, encoding='utf-8',
                                capture_output=True, env=env, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SafetyError('Pi schema receipt verification unavailable') from error
    require(result.returncode == 0 and result.stdout == 'true' and not result.stderr,
            'Invalid Pi SDK schema rejection receipt')


def _validation_proposal(value, paths, validation_schema='python-behavior-v1'):
    """Check proposal data against fixed source pins; never construct execution authority."""
    from .behavior_validation import _case, contract_kind, MAX_CONTRACT_BYTES
    require(type(validation_schema) is str and validation_schema in {'python-behavior-v1', VALIDATION_DATA_SCHEMA},
            'Unsupported validation schema')
    def size(text):
        try:
            return len(text.encode('utf-8'))
        except UnicodeError as error:
            raise SafetyError('Invalid Pi validation proposal encoding') from error
    fields = {'requirement','rationale','limitations','clarification'}
    require(type(value) is dict and set(value) in (fields | {'cases'},fields | {'cases_json'}),
            'Invalid Pi validation proposal fields')
    for field in ('requirement','rationale','limitations'):
        require(type(value[field]) is str and '\x00' not in value[field] and
                size(value[field]) <= 8192, 'Invalid Pi validation proposal text')
    require(value['requirement'].strip() and value['rationale'].strip(), 'Missing Pi validation proposal basis')
    if 'cases_json' in value:
        require(type(value['cases_json']) is str and size(value['cases_json']) <= MAX_CONTRACT_BYTES,
                'Invalid Pi validation cases JSON')
        cases = strict_json(value['cases_json'])
    else:
        cases = value['cases']
    require(type(value['clarification']) is bool and type(cases) is list and
            (0 if value['clarification'] else 1) <= len(cases) <= 24,
            'Invalid Pi validation proposal case count')
    for case in cases:
        _case(case, None if validation_schema == VALIDATION_DATA_SCHEMA else 'python-behavior-v1')
        require(case['module'] in paths, 'Pi validation module outside task scope')
    contract_kind(cases)  # Native proposals cannot combine unrelated runtimes.
    require(len({case['id'] for case in cases}) == len(cases), 'Duplicate Pi validation case ID')
    require(size(json.dumps(value,ensure_ascii=False,allow_nan=False)) <= MAX_CONTRACT_BYTES,
            'Pi validation proposal exceeds byte bound')


INVESTIGATION_DENIED = {'Investigation denied by the original read scope',
                        'Investigation budget exhausted',
                        'Investigation bridge unavailable'}


def _investigation_call(call, roots):
    """Shape-check one approved-source call without granting a path."""
    name, args = call['name'], call.get('arguments')
    if not isinstance(args, dict):
        return {'valid': False, 'kind': None, 'in_scope': False}
    if name == 'search_approved_sources':
        valid = (set(args) <= {'query', 'max_hits'} and 'query' in args
                 and isinstance(args['query'], str) and '\x00' not in args['query']
                 and 1 <= len(args['query']) <= 512
                 and ('max_hits' not in args or (type(args['max_hits']) is int
                      and 1 <= args['max_hits'] <= 100)))
        return {'valid': valid, 'kind': 'search', 'in_scope': valid}
    if name == 'read_approved_source':
        valid = (set(args) <= {'path', 'start_line', 'max_lines'} and 'path' in args
                 and isinstance(args['path'], str) and 0 < len(args['path']) <= 4096
                 and '\x00' not in args['path']
                 and ('start_line' not in args or (type(args['start_line']) is int and args['start_line'] >= 1))
                 and ('max_lines' not in args or (type(args['max_lines']) is int
                      and 1 <= args['max_lines'] <= 200)))
        return {'valid': valid, 'kind': 'read',
                'in_scope': valid and allowed_path(args['path'], roots)}
    return {'valid': False, 'kind': None, 'in_scope': False}


def _pin(value, roots):
    require(isinstance(value, dict) and set(value) == {'path', 'side', 'commit_sha', 'blob', 'sha256'}
            and isinstance(value['path'], str) and allowed_path(value['path'], roots)
            and value['side'] == 'head'
            and isinstance(value['commit_sha'], str) and re.fullmatch(r'[a-f0-9]{40}', value['commit_sha'])
            and isinstance(value['blob'], str) and re.fullmatch(r'[a-f0-9]{40}', value['blob'])
            and isinstance(value['sha256'], str) and SHA.fullmatch(value['sha256']),
            'Invalid Pi investigation source pin')
    return value


def _investigation_result(tool, result, context, base_sha):
    """Validate the exact broker envelope returned by the loopback bridge."""
    require(isinstance(result, dict), 'Invalid Pi investigation receipt')
    investigation = context['investigation']
    roots = investigation['read_roots']
    common = {'operation', 'scope_sha256', 'base_sha', 'runtime_authorized',
              'write_authorized', 'call_index'}
    require(type(result.get('call_index')) is int and 0 <= result['call_index'] < investigation['max_calls']
            and isinstance(result.get('scope_sha256'), str) and SHA.fullmatch(result['scope_sha256'])
            and result.get('base_sha') == base_sha
            and result.get('runtime_authorized') is False and result.get('write_authorized') is False,
            'Invalid Pi investigation authority receipt')
    optional = {'output_truncated', 'omitted_rows'}
    require(set(result) in (common | {'operation', 'source', 'lines', 'total_lines', 'truncated', 'side'},
                            common | {'operation', 'source', 'lines', 'total_lines', 'truncated', 'side'} | optional,
                            common | {'operation', 'query_sha256', 'hits', 'scanned_files', 'skipped_nontext',
                                      'catalog_files', 'complete_catalog_scan', 'catalog_truncated',
                                      'interpretation'},
                            common | {'operation', 'query_sha256', 'hits', 'scanned_files', 'skipped_nontext',
                                      'catalog_files', 'complete_catalog_scan', 'catalog_truncated',
                                      'interpretation'} | optional),
            'Invalid Pi investigation receipt fields')
    if 'output_truncated' in result:
        require(result['output_truncated'] is True and type(result.get('omitted_rows')) is int
                and result['omitted_rows'] > 0, 'Invalid Pi investigation truncation receipt')
    if tool == 'read_approved_source':
        require(result['operation'] == 'read_source' and result.get('side') == 'original_fixed_commit'
                and _pin(result['source'], roots) and result['source']['commit_sha'] == base_sha,
                'Invalid Pi investigation read receipt')
        require(isinstance(result['lines'], list) and len(result['lines']) <= 200
                and isinstance(result['total_lines'], int) and result['total_lines'] >= 0
                and isinstance(result['truncated'], bool), 'Invalid Pi investigation line window')
        for row in result['lines']:
            require(isinstance(row, dict) and set(row) == {'line', 'text'}
                    and type(row['line']) is int and row['line'] >= 1
                    and isinstance(row['text'], str) and len(row['text']) <= 500,
                    'Invalid Pi investigation line')
    else:
        require(result['operation'] == 'search_sources'
                and isinstance(result['query_sha256'], str) and SHA.fullmatch(result['query_sha256'])
                and isinstance(result['hits'], list) and len(result['hits']) <= 100
                and isinstance(result['interpretation'], str) and len(result['interpretation']) <= 512
                and all(type(result[name]) is int and result[name] >= 0 for name in
                        ('scanned_files', 'skipped_nontext', 'catalog_files'))
                and type(result['complete_catalog_scan']) is bool
                and type(result['catalog_truncated']) is bool,
                'Invalid Pi investigation search receipt')
        for hit in result['hits']:
            require(isinstance(hit, dict) and set(hit) == {'source', 'line', 'text', 'excerpt_truncated'}
                    and _pin(hit['source'], roots) and hit['source']['commit_sha'] == base_sha
                    and type(hit['line']) is int and hit['line'] >= 1
                    and isinstance(hit['text'], str) and len(hit['text']) <= 500
                    and type(hit['excerpt_truncated']) is bool, 'Invalid Pi investigation hit')
    return result


def validate_transcript(raw: bytes, *, cwd: Path, context: dict, leaf=None, session_id=None, compactions=None,
                        base_sha=None):
    """Validate every raw line/tree edge, then select a complete branch; never skip bad tails."""
    context = _context(context)
    thinking_level = context.get('thinking_level', 'off')
    purpose = context.get('purpose', 'repair')
    require(purpose in {'repair', VALIDATION_PURPOSE, INVESTIGATION_PURPOSE}, 'Unsupported Pi planning purpose')
    tools = (TOOLS if purpose == 'repair' else VALIDATION_TOOLS if purpose == VALIDATION_PURPOSE
             else INVESTIGATION_TOOLS)
    if purpose == INVESTIGATION_PURPOSE:
        require(isinstance(base_sha, str) and re.fullmatch(r'[a-f0-9]{40}', base_sha),
                'Pi investigation checkpoint lacks its original Git baseline')
        investigation_roots = context['investigation']['read_roots']
    else:
        investigation_roots = ()
    require(0 < len(raw) <= MAX_SESSION_BYTES and raw.endswith(b'\n'), 'Missing, oversized or truncated Pi session')
    lines = raw.splitlines(keepends=True)
    require(2 <= len(lines) <= MAX_ENTRIES + 1 and all(line.strip() for line in lines), 'Invalid Pi session line count')
    try:
        entries = [strict_json(line.decode('utf-8')) for line in lines]
    except UnicodeError as error:
        raise SafetyError('Pi session is not valid UTF-8') from error
    header = entries[0]
    require(isinstance(header, dict) and set(header) == {'type', 'version', 'id', 'timestamp', 'cwd'} and
            header.get('type') == 'session' and type(header.get('version')) is int and header['version'] == 3 and
            isinstance(header.get('id'), str) and SESSION_ID.fullmatch(header['id']) and
            isinstance(header.get('timestamp'), str), 'Unsupported Pi session header')
    require(isinstance(header.get('cwd'), str) and
            os.path.normcase(str(unlinked(Path(header['cwd'])))) == os.path.normcase(str(unlinked(cwd))),
            'Pi session workspace identity changed')
    if session_id is not None:
        require(header['id'] == session_id, 'Pi session identity changed')
    by_id, states, offsets = {}, {}, {}
    schema_rejections = []
    proposal_receipts = []
    entry_investigation = {}
    offset, roots = len(lines[0]), 0
    for entry, line in zip(entries[1:], lines[1:]):
        require(isinstance(entry, dict) and isinstance(entry.get('id'), str) and ENTRY_ID.fullmatch(entry['id']) and
                entry['id'] not in by_id and isinstance(entry.get('timestamp'), str), 'Invalid or duplicate Pi entry')
        parent = entry.get('parentId')
        require('parentId' in entry and (parent is None or isinstance(parent, str) and parent in by_id),
                'Broken Pi parent chain')
        if parent is None:
            roots += 1
            state = {'pending': {}, 'accepted': False, 'done': False, 'rounds': 0,
                     'thinking_bound': thinking_level == 'off'}
        else:
            state = deepcopy(states[parent])
        require(roots == 1, 'Multiple Pi session roots')
        kind = entry.get('type')
        if kind == 'model_change':
            require(not state['pending'] and entry.get('provider') == context['provider'] and
                    entry.get('modelId') == context['model'], 'Pi session model changed')
        elif kind == 'thinking_level_change':
            require(not state['pending'] and entry.get('thinkingLevel') == thinking_level, 'Unexpected Pi thinking settings')
            state['thinking_bound'] = True
        elif kind == 'compaction':
            require(compactions is not None and not state['pending'] and state['accepted'] and state['done'],
                    'Unapproved Pi compaction or interrupted tool pair')
            fields = {'type','id','parentId','timestamp','summary','firstKeptEntryId','tokensBefore','details','usage','fromHook','systemMessage'}
            require(not set(entry)-fields and isinstance(entry.get('summary'),str) and entry['summary'].strip()
                    and len(entry['summary'].encode()) <= 48000 and '\x00' not in entry['summary']
                    and type(entry.get('tokensBefore')) is int and entry['tokensBefore'] > 0
                    and (entry.get('fromHook') is None or entry.get('fromHook') is False), 'Invalid Pi compaction summary')
            from .store import redact
            require(redact(entry['summary']) == entry['summary'], 'Sensitive Pi compaction summary')
            kept = entry.get('firstKeptEntryId')
            ancestors = set();cursor = parent
            while cursor is not None:
                ancestors.add(cursor);cursor=by_id[cursor]['parentId']
            require(kept in ancestors and by_id[kept].get('type')=='message'
                    and by_id[kept]['message'].get('role') in {'user','assistant'}
                    and not (states[by_id[kept]['parentId']]['pending'] if by_id[kept]['parentId'] else {}),
                    'Pi compaction split a tool pair or kept a foreign entry')
            details = entry.get('details',{})
            require(isinstance(details,dict) and set(details)=={'readFiles','modifiedFiles'} and
                    all(isinstance(details[k],list) and all(
                        p in context['paths'] or (purpose == INVESTIGATION_PURPOSE
                                                  and allowed_path(p, investigation_roots))
                        for p in details[k]) for k in details),
                    'Invalid Pi compaction file metadata')
            usage = entry.get('usage',{})
            import math
            require(isinstance(usage,dict) and not set(usage)-{'input','output','cacheRead','cacheWrite','reasoning','totalTokens','cost'}
                    and all(type(v) in {int,float} and math.isfinite(v) and v>=0 for k,v in usage.items() if k!='cost'),
                    'Invalid Pi compaction usage')
            if 'cost' in usage:
                require(isinstance(usage['cost'],dict) and not set(usage['cost'])-{'input','output','cacheRead','cacheWrite','total'}
                        and all(type(v) in {int,float} and math.isfinite(v) and v>=0 for v in usage['cost'].values()),
                        'Invalid Pi compaction cost metadata')
            if 'systemMessage' in entry:
                system = entry['systemMessage']
                require(isinstance(system,dict) and system.get('role')=='system' and not _text_content(system.get('content'))
                        and isinstance(system.get('sections',{}),dict) and all(isinstance(v,str) for v in system.get('sections',{}).values())
                        and isinstance(system.get('toolsAdded',[]),list) and all(isinstance(t,dict) and t.get('name') in tools
                            for t in system.get('toolsAdded',[])) and not system.get('toolsRemoved'),
                        'Compaction cannot introduce system tools or instructions')
                expected_system = None;cursor=parent
                while cursor is not None:
                    prior=by_id[cursor]
                    if prior['type']=='message' and prior['message'].get('role')=='system':
                        expected_system=prior['message'];break
                    if prior['type']=='compaction' and 'systemMessage' in prior:
                        expected_system=prior['systemMessage'];break
                    cursor=prior['parentId']
                require(expected_system is not None and
                        {k:v for k,v in system.items() if k!='timestamp'}==
                        {k:v for k,v in expected_system.items() if k!='timestamp'},
                        'Compaction changed the original system context')
        elif kind == 'message':
            message = entry.get('message')
            require(isinstance(message, dict), 'Invalid Pi message')
            role = message.get('role')
            require(type(message.get('timestamp')) is int and message['timestamp'] >= 0, 'Invalid Pi message timestamp')
            calls = _text_content(message.get('content'), role == 'assistant', tools=tools)
            if role == 'system':
                require(not state['pending'], 'System message interrupted Pi tool pair')
                require(isinstance(message.get('sections', {}), dict) and
                        all(isinstance(v, str) for v in message.get('sections', {}).values()), 'Invalid Pi system sections')
                require(isinstance(message.get('toolsAdded', []), list), 'Invalid Pi tool definitions')
                for tool in message.get('toolsAdded', []):
                    require(isinstance(tool, dict) and tool.get('name') in tools, 'Unauthorized Pi tool definition')
                require(not message.get('toolsRemoved'), 'Unexpected Pi tool removal')
            elif role == 'user':
                require(not state['pending'], 'User message interrupted Pi tool pair')
                state.update(accepted=False, done=False, rounds=state['rounds'] + 1)
            elif role == 'assistant':
                require(state['thinking_bound'] and message.get('thinkingLevel', 'off') == thinking_level,
                        'Unexpected Pi assistant thinking settings')
                require(not state['pending'] and state['rounds'] > 0 and
                        message.get('provider') == context['provider'] and message.get('model') == context['model'] and
                        message.get('stopReason') in {'stop', 'toolUse', 'error', 'aborted', 'length'},
                        'Invalid Pi assistant identity or stop state')
                require(not calls or message['stopReason'] == 'toolUse', 'Invalid Pi tool stop state')
                for call in calls:
                    require(call['id'] not in state['pending'], 'Duplicate Pi tool call')
                    args = call['arguments']
                    investigation_info = None
                    if call['name'] in INVESTIGATION_SCOPE_TOOLS:
                        require(purpose == INVESTIGATION_PURPOSE, 'Investigation tool outside its Native scope')
                        investigation_info = _investigation_call(call, investigation_roots)
                        in_scope = investigation_info['in_scope']
                        denied_read = (call['name'] == 'read_approved_source' and investigation_info['valid']
                                       and not in_scope and isinstance(args.get('path'), str)
                                       and 0 < len(args['path']) <= 4096)
                        schema_required = not investigation_info['valid'] and not denied_read
                    else:
                        edits = args.get('edits')
                        shaped_edits = isinstance(edits,list) and all(isinstance(e,dict) for e in edits)
                        paths = [args.get('path')] if call['name'] == 'read_source' else (
                            [e.get('path') for e in edits if isinstance(e,dict)] if isinstance(edits,list) else [])
                        if call['name'] == 'propose_repair':
                            # Explicit foreign edit paths remain forbidden, even in a rejected call.
                            require(not any(isinstance(p,str) and p not in context['paths'] for p in paths),
                                    'Pi tool path outside task scope')
                        in_scope = bool(paths) and (call['name']=='read_source' or shaped_edits) and \
                            all(isinstance(p, str) and p in context['paths'] for p in paths)
                        if call['name'] == 'propose_validation':
                            cases = args.get('cases')
                            raw_cases = isinstance(args.get('cases_json'),str)
                            if raw_cases:
                                try:
                                    cases = strict_json(args['cases_json'])
                                except SafetyError:
                                    # A schema-valid raw string may fail the tool's data parser.
                                    # Retain an error pair as history; success is strictly rechecked below.
                                    cases = None
                            if isinstance(cases,list):
                                require(not any(isinstance(c,dict) and isinstance(c.get('module'),str)
                                                and c['module'] not in context['paths'] for c in cases),
                                        'Pi validation module outside task scope')
                            in_scope = raw_cases or (isinstance(cases,list) and all(isinstance(c,dict) and
                                isinstance(c.get('module'),str) and c['module'] in context['paths'] for c in cases))
                        denied_read = not in_scope and call['name']=='read_source' and isinstance(args.get('path'),str) \
                            and 0 < len(args['path']) <= 4096
                        schema_required = not in_scope and not denied_read
                    # Missing/malformed paths have no access authority. Only a paired, independently
                    # reproduced SDK validation rejection can retain them as unexecuted history.
                    state['pending'][call['id']] = {'name':call['name'], 'call':call,
                        'denied_read':denied_read, 'schema_required':schema_required,
                        'investigation':investigation_info['kind'] if investigation_info else None}
                state['done'] = message['stopReason'] == 'stop' and state['accepted']
            elif role == 'toolResult':
                identity = message.get('toolCallId')
                require(isinstance(identity, str) and identity in state['pending'] and
                        state['pending'][identity]['name'] == message.get('toolName') and type(message.get('isError')) is bool,
                        'Orphaned or mismatched Pi tool result')
                pending = state['pending'].pop(identity)
                name = pending['name']
                content = message['content']
                text = content if isinstance(content,str) else ''.join(p['text'] for p in content)
                schema_error = text.startswith('Validation failed for tool "')
                if pending['schema_required'] or schema_error:
                    require(message['isError'] is True and message.get('details') == {} and
                            isinstance(content,list) and len(content)==1 and
                            set(content[0])=={'type','text'} and content[0]['type']=='text',
                            'Invalid Pi SDK schema rejection envelope')
                    schema_rejections.append({'call':pending['call'], 'text':text})
                elif pending['denied_read']:
                    require(message['isError'] is True, 'Pi tool path outside task scope')
                    result = strict_json(text)
                    if pending.get('investigation'):
                        require(isinstance(result, dict) and set(result) == {'error'}
                                and result['error'] in INVESTIGATION_DENIED and message.get('details') == result,
                                'Invalid Pi investigation denial receipt')
                    else:
                        require(isinstance(result, dict) and set(result) == {'error', 'requests_remaining'} and
                                result['error'] == 'Path not authorized for read' and
                                type(result['requests_remaining']) is int and 0 <= result['requests_remaining'] <= MAX_REQUESTS and
                                message.get('details') == result, 'Invalid Pi denied-read receipt')
                elif pending.get('investigation') and message['isError']:
                    result = strict_json(text)
                    require(isinstance(result, dict) and set(result) == {'error'}
                            and result['error'] in INVESTIGATION_DENIED and message.get('details') == result,
                            'Invalid Pi investigation error receipt')
                if name == 'propose_repair' and not message['isError']:
                    result = strict_json(text)
                    require(isinstance(result, dict) and result.get('accepted') is True, 'Invalid Pi proposal receipt')
                    state['accepted'] = True
                    state['done'] = not state['pending']  # production tool can terminate here
                if name == 'propose_validation' and not message['isError']:
                    arguments = pending['call']['arguments']
                    _validation_proposal(arguments, context['paths'],
                                         context.get('validation_schema', 'python-behavior-v1'))
                    result = strict_json(text)
                    require(isinstance(result,dict) and set(result) == {
                        'accepted','purpose','proposal_sha256','runtime_authorized'} and result['accepted'] is True
                        and result['purpose'] == purpose and result['runtime_authorized'] is False
                        and isinstance(result['proposal_sha256'],str) and SHA.fullmatch(result['proposal_sha256'])
                        and message.get('details') == result, 'Invalid Pi validation proposal receipt')
                    proposal_receipts.append({'call':pending['call'],'receipt':result})
                    state['accepted'] = True
                    state['done'] = not state['pending']  # submitted only; never executed or approved
                if pending.get('investigation') and not message['isError']:
                    result = strict_json(text)
                    _investigation_result(name, result, context, base_sha)
                    entry_investigation[entry['id']] = result
            else:
                raise SafetyError('Unsupported Pi message role')
        else:
            raise SafetyError('Unsupported Pi entry type')  # Extensions/context edits remain disabled.
        by_id[entry['id']], states[entry['id']] = entry, state
        offset += len(line)
        offsets[entry['id']] = offset
    selected = entries[-1]['id'] if leaf is None else leaf
    require(isinstance(selected, str) and selected in by_id, 'Missing Pi checkpoint leaf')
    state = states[selected]
    require(state['done'] and state['accepted'] and not state['pending'], 'Pi checkpoint is not a completed planning round')
    _verify_schema_rejections(schema_rejections, 'repair' if purpose in {'repair', INVESTIGATION_PURPOSE} else purpose,
                              proposals=proposal_receipts, paths=context['paths'],
                              validation_schema=context.get('validation_schema'))
    # Also validates rejected/accepted pairs on non-selected branches and tails.
    size = offsets[selected]
    branch = []
    cursor = selected
    while cursor is not None:
        branch.append(cursor)
        cursor = by_id[cursor]['parentId']
    selected_compactions = [{'entry_id':i,'entry_sha256':fingerprint(by_id[i])} for i in reversed(branch)
                            if by_id[i]['type']=='compaction']
    require(all(compactions is not None and compactions.get(c['entry_id'])==c['entry_sha256'] for c in selected_compactions),
            'Selected compaction has no original metered receipt')
    projected = list(reversed(branch))
    last_compaction = next((by_id[i] for i in branch if by_id[i]['type']=='compaction'),None)
    if last_compaction:
        kept_index = projected.index(last_compaction['firstKeptEntryId'])
        projected = projected[kept_index:]
    messages = [by_id[i]['message'] for i in projected if by_id[i]['type']=='message']
    if last_compaction:
        messages += [{'summary':last_compaction['summary']},last_compaction.get('systemMessage',{})]
    context_bytes = sum(len(json.dumps(m,ensure_ascii=False).encode())+512 for m in messages)+4096
    return {'session_id': header['id'], 'leaf_id': selected, 'prefix_bytes': size,
            'prefix_sha256': digest(raw[:size]), 'file_sha256': digest(raw), 'file_bytes': len(raw),
            'rounds': state['rounds'], 'tail_uncommitted': selected != entries[-1]['id'], 'branch_ids': branch,
            'compactions':selected_compactions,'context_bytes_estimate':context_bytes,
            'leaf_is_compaction':by_id[selected]['type']=='compaction',
            'investigation_results':[entry_investigation[i] for i in projected
                                     if i in entry_investigation]}


class TaskSessionStore:
    def __init__(self, registry, source_root: Path, common_dir: Path, *, budget_registry=None):
        self.registry = registry
        self.budget_registry = budget_registry or registry
        require(self.budget_registry.path == registry.path, 'Cross-journal Pi checkpoints require a separate commit protocol')
        self.root = unlinked(registry.root.parent / 'task-sessions')
        self.source_root, self.common_dir = unlinked(source_root), unlinked(common_dir)
        for protected in (self.source_root, self.common_dir):
            require(not self.root.is_relative_to(protected), 'Pi task sessions must stay outside source and Git metadata')
        self.repository = repository_identity(self.source_root, self.common_dir)
        self.budget_scope = fingerprint(os.path.normcase(str(unlinked(self.budget_registry.path))))

    def directory(self, identity):
        require(isinstance(identity, str) and TASK_ID.fullmatch(identity), 'Invalid Pi task identity')
        return unlinked(self.root / identity)

    def allocate(self, scope, context):
        context = _context(context)
        scope = _scope(scope, context, self.repository)
        identity = uuid.uuid4().hex
        directory = self.directory(identity)
        directory.mkdir(parents=True, exist_ok=False)
        record = {'schema_version': 1, 'task_id': identity, 'scope': scope, 'context': context,
                  'budget_scope_sha256': self.budget_scope, 'revision': 0, 'checkpoint': None}
        self.registry.put(KIND, identity, record)
        return deepcopy(record)

    def _load(self, identity, scope, *, db=None):
        self.directory(identity)
        require(fingerprint(os.path.normcase(str(unlinked(self.budget_registry.path)))) == self.budget_scope,
                'Pi task original budget binding changed')
        if db is None:
            record = self.registry.load(KIND).get(identity)
        else:
            row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (KIND, identity)).fetchone()
            record = strict_json(row[0]) if row else None
        require(isinstance(record, dict) and set(record) == {'schema_version', 'task_id', 'scope', 'context',
                'budget_scope_sha256', 'revision', 'checkpoint'} and type(record['schema_version']) is int and record['schema_version'] == 1 and
                record['task_id'] == identity and type(record['revision']) is int and record['revision'] >= 0,
                'Missing or invalid Pi task binding')
        _context(record['context'])
        _scope(scope, record['context'], self.repository)
        require(record['scope'] == scope and record['budget_scope_sha256'] == self.budget_scope,
                'Pi task scope or original budget binding changed')
        checkpoint = record['checkpoint']
        require((record['revision'] == 0 and checkpoint is None) or
                (record['revision'] > 0 and isinstance(checkpoint, dict) and set(checkpoint) == {
                    'session_id', 'leaf_id', 'prefix_bytes', 'prefix_sha256', 'rounds', 'file', 'snapshot_sha256', 'attempt_id'} and
                 type(checkpoint['prefix_bytes']) is int and 0 < checkpoint['prefix_bytes'] <= MAX_SESSION_BYTES and
                 type(checkpoint['rounds']) is int and checkpoint['rounds'] == record['revision'] and
                 all(isinstance(checkpoint[k], str) and SHA.fullmatch(checkpoint[k]) for k in ('prefix_sha256', 'snapshot_sha256'))),
                'Invalid Pi checkpoint binding')
        return record

    @contextmanager
    def lease(self, identity, scope, revision):
        directory = self.directory(identity)
        require(directory.is_dir(), 'Missing Pi task directory')
        lock = FileLock(str(unlinked(directory / 'session.lock')), timeout=0, blocking=False,
                        fallback_to_soft=False, preserve_lock_file=True)
        try:
            with lock:
                record = self._load(identity, scope)
                require(type(revision) is int and record['revision'] == revision, 'Stale Pi checkpoint revision')
                lease = TaskSessionLease(self, record)
                try:
                    yield lease
                finally:
                    lease.active = False
        except Timeout as error:
            raise SafetyError('Another writer owns this Pi task session') from error


class TaskSessionLease:
    def __init__(self, store, record):
        self.store, self.record = store, record
        self.active = True

    @property
    def directory(self):
        return self.store.directory(self.record['task_id'])

    def _file(self, name):
        require(self.active, 'Pi task lease is closed')
        require(isinstance(name, str) and len(name) <= 200 and Path(name).name == name and
                re.fullmatch(r'[A-Za-z0-9._-]+\.jsonl', name), 'Invalid Pi session filename')
        file = unlinked(self.directory / 'sessions' / name)
        require(file.is_file(), 'Missing Pi session file')
        return file

    def _read(self, name, leaf, session_id=None, *, staged_compactions=None):
        file = self._file(name)
        raw = self._bytes(file)
        known = {}
        records=self.store.registry.load('pi_compaction_receipt');attempts=self.store.registry.load('attempt')
        for identity,record in records.items():
            if record.get('task_id')!=self.record['task_id']:continue
            attempt=attempts.get(identity)
            require(attempt and attempt.get('status')=='finished' and attempt['evidence'].get('purpose')=='task_compaction'
                    and fingerprint(attempt['evidence'])==record['evidence_sha256']
                    and attempt['evidence'].get('compaction_entry_sha256')==record['entry_sha256'],
                    'Original metered compaction receipt changed')
            require(record['entry_id'] not in known,'Duplicate original compaction receipt')
            known[record['entry_id']]=record['entry_sha256']
        allow = bool(known or staged_compactions or any(r.get('task_id')==self.record['task_id']
                     for r in self.store.registry.load('pi_compaction_intent').values()))
        compactions = {**known,**(staged_compactions or {})} if allow else None
        receipt = validate_transcript(raw, cwd=self.directory / 'workspace', context=self.record['context'],
                                      leaf=leaf, session_id=session_id, compactions=compactions,
                                      base_sha=self.record['scope']['base_sha'])
        if self.record['context'].get('purpose') == INVESTIGATION_PURPOSE:
            self._verify_investigation(receipt['investigation_results'])
        return file, receipt

    def _verify_investigation(self, results):
        """Bind every transcript investigation result to the original broker journal."""
        from .git import RepositoryGateway
        from .task_investigation import TaskInvestigation, _hash
        broker = TaskInvestigation(self.store.registry, RepositoryGateway(self.store.source_root),
                                   self.record['task_id'])
        row = broker.journal()
        scope, investigation = row['scope'], self.record['context']['investigation']
        require(scope['task_id'] == self.record['task_id']
                and scope['repository_id'] == self.store.repository
                and scope['base_sha'] == self.record['scope']['base_sha']
                and scope['policy_sha256'] == self.record['scope']['policy_sha256']
                and scope['read_roots'] == investigation['read_roots']
                and scope['max_calls'] == investigation['max_calls']
                and scope['max_output_bytes'] == investigation['max_output_bytes'],
                'Pi investigation scope does not match the original task journal')
        receipts, previous = row['receipts'], -1
        for result in results:
            index = result['call_index']
            require(index > previous and index < len(receipts), 'Pi investigation call receipt is out of order')
            previous = index
            receipt = receipts[index]
            require(receipt.get('call', index) == index
                    and receipt['operation'] == result['operation']
                    and receipt['result_sha256'] == _hash(result)
                    and receipt['bytes'] == len(json.dumps(
                        result, ensure_ascii=False, allow_nan=False).encode()),
                    'Pi investigation result changed after the original tool call')
        return row

    @staticmethod
    def _bytes(file):
        file = unlinked(file)
        require(file.stat().st_size <= MAX_SESSION_BYTES, 'Pi session exceeds read limit')
        with file.open('rb') as stream:
            raw = stream.read(MAX_SESSION_BYTES + 1)
        require(len(raw) <= MAX_SESSION_BYTES, 'Pi session exceeds read limit')
        return raw

    def descriptor(self):
        saved = self.record['checkpoint']
        require(isinstance(saved, dict), 'Pi task has no completed checkpoint')
        file, actual = self._read(saved['file'], saved['leaf_id'], saved['session_id'])
        require(all(actual[k] == saved[k] for k in ('prefix_bytes', 'prefix_sha256', 'rounds')),
                'Committed Pi history changed')
        return {'schema_version': 1, 'task_id': self.record['task_id'], 'revision': self.record['revision'],
                'session_file': str(file), 'cwd': str(unlinked(self.directory / 'workspace')),
                'session_id': actual['session_id'], 'leaf_id': actual['leaf_id'],
                'file_sha256': actual['file_sha256'], 'file_bytes': actual['file_bytes'],
                'tail_uncommitted': actual['tail_uncommitted']}

    def commit(self, name, leaf, snapshot_sha256, attempt_id):
        require(isinstance(snapshot_sha256, str) and SHA.fullmatch(snapshot_sha256), 'Invalid candidate snapshot fingerprint')
        previous = self.record['checkpoint']
        if previous:
            self.descriptor()  # pin the committed prefix before advancing
            require(name == previous['file'], 'Cannot replace a task session file')
        file, actual = self._read(name, leaf, previous['session_id'] if previous else None)
        require(not actual['tail_uncommitted'] and actual['rounds'] == (previous['rounds'] if previous else 0) + 1,
                'Pi checkpoint did not complete exactly one new round')
        if previous:
            require(previous['leaf_id'] in actual['branch_ids'], 'Pi checkpoint abandoned committed history')
        attempt = self.store.budget_registry.load('attempt').get(attempt_id)
        require(isinstance(attempt, dict) and attempt.get('status') == 'finished' and
                type(attempt.get('evidence', {}).get('requests')) is int and attempt['evidence']['requests'] > 0,
                'Pi checkpoint requires a settled original model attempt')
        if previous:
            require(previous['attempt_id'] != attempt_id, 'Cannot replay a settled model attempt')
        with self.store.registry.transaction() as db:
            current = self.store._load(self.record['task_id'], self.record['scope'], db=db)
            require(current == self.record, 'Pi checkpoint compare-and-swap failed')
            require(digest(self._bytes(file)) == actual['file_sha256'], 'Pi session changed during checkpoint commit')
            require(not db.execute('SELECT 1 FROM objects WHERE kind=? AND id=?', ('pi_checkpoint_attempt', attempt_id)).fetchone(),
                    'Model attempt already bound to a Pi checkpoint')
            updated = deepcopy(current)
            updated['revision'] += 1
            updated['checkpoint'] = {k: actual[k] for k in ('session_id', 'leaf_id', 'prefix_bytes', 'prefix_sha256', 'rounds')}
            updated['checkpoint'].update(file=name, snapshot_sha256=snapshot_sha256, attempt_id=attempt_id)
            self.store.registry.put('pi_checkpoint_attempt', attempt_id, {'task_id': current['task_id'], 'revision': updated['revision']}, db)
            self.store.registry.put(KIND, current['task_id'], updated, db)
        self.record = updated
        return deepcopy(updated)

    def commit_compaction(self, receipt, before, attempt_id, evidence_sha256):
        previous=self.record['checkpoint'];require(previous is not None,'Compaction needs a completed checkpoint')
        self.descriptor()  # Still select the original leaf; an uncommitted tail is not context.
        require(receipt['task_id']==self.record['task_id'] and receipt['file']==previous['file']
                and receipt['session_id']==previous['session_id'] and receipt['previous_leaf_id']==previous['leaf_id']
                and receipt['previous_file_sha256']==before['file_sha256'],'Compaction original checkpoint differs')
        file,actual=self._read(receipt['file'],receipt['leaf_id'],previous['session_id'],
            staged_compactions={receipt['leaf_id']:receipt['entry_sha256']})
        require(actual['leaf_is_compaction'] and not actual['tail_uncommitted'] and actual['rounds']==self.record['revision']
                and actual['file_sha256']==receipt['file_sha256'] and previous['leaf_id'] in actual['branch_ids']
                and digest(self._bytes(file)[:before['file_bytes']])==before['file_sha256'],
                'Compaction changed original history or planning rounds')
        attempt=self.store.registry.load('attempt').get(attempt_id)
        require(attempt and attempt['status']=='finished' and fingerprint(attempt['evidence'])==evidence_sha256
                and attempt['evidence'].get('purpose')=='task_compaction' and attempt['evidence'].get('requests',0)>0
                and attempt['evidence'].get('compaction_entry_sha256')==receipt['entry_sha256'],
                'Compaction requires its original settled request receipt')
        with self.store.registry.transaction() as db:
            current=self.store._load(self.record['task_id'],self.record['scope'],db=db)
            require(current==self.record and digest(self._bytes(file))==actual['file_sha256'],'Compaction checkpoint CAS changed')
            require(not db.execute('SELECT 1 FROM objects WHERE kind=? AND id=?',('pi_checkpoint_attempt',attempt_id)).fetchone(),
                    'Compaction attempt already bound')
            updated=deepcopy(current)
            updated['checkpoint']={k:actual[k] for k in ('session_id','leaf_id','prefix_bytes','prefix_sha256','rounds')}
            updated['checkpoint'].update(file=receipt['file'],snapshot_sha256=previous['snapshot_sha256'],attempt_id=attempt_id)
            self.store.registry.put(KIND,current['task_id'],updated,db)
            self.store.registry.put('pi_checkpoint_attempt',attempt_id,{'task_id':current['task_id'],'revision':current['revision']},db)
            self.store.registry.put('pi_compaction_receipt',attempt_id,{'task_id':current['task_id'],
                'entry_id':receipt['leaf_id'],'entry_sha256':receipt['entry_sha256'],'evidence_sha256':evidence_sha256,
                'previous_checkpoint_sha256':fingerprint(previous),'receipt':receipt},db)
        self.record=updated
        return deepcopy(updated)
