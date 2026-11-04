"""Pinned data-only behavior checks; authority remains with the original controller.

Contracts are data, never Python/Shell source. The caller must authorize project
execution and bind the workspace, scope and environment to its existing task.
Project code runs with the interpreter's normal privileges, not an OS sandbox.
"""
from __future__ import annotations

import copy
import json
import math
import re
import tempfile
import time
import uuid
from pathlib import Path

from .models import SafetyError, digest, safe_context_path
from .maintenance_limits import LIMITS
from .recovery import unlinked
from .runner import run_test

MAX_CASES = 24
MAX_VALUE_BYTES = 32768
MAX_CONTRACT_BYTES = 262144
MAX_RESULT_BYTES = 65536
SHA256 = re.compile(r'[0-9a-f]{64}')
SHA = re.compile(r'[0-9a-f]{40}(?:[0-9a-f]{24})?')
IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,79}')
EXCEPTION = re.compile(r'(?:[A-Za-z][A-Za-z0-9_]*\.)*[A-Za-z][A-Za-z0-9_]{0,79}')
FAILURES = {'mismatch', 'missing_behavior', 'expected_exception_missing', 'wrong_exception'}
INCONCLUSIVE = {'import_error', 'execution_error', 'invalid_result', 'protocol_error', 'timeout'}
NODE_SUFFIXES = {'.js', '.mjs', '.cjs', '.ts', '.mts', '.cts'}
PYTHON_KIND = 'python-behavior-v1'
NODE_KIND = 'node-behavior-v1'


def require(value, message):
    if not value:
        raise SafetyError(message)


def canonical(value):
    """Only finite, bounded, exact JSON types; no coercion or user callbacks."""
    nodes = 0

    def visit(item, depth=0):
        nonlocal nodes
        nodes += 1
        require(depth <= 12 and nodes <= 4096, 'Behavior JSON nesting/size exceeds bound')
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            require(abs(item) <= 9007199254740991, 'Behavior integer exceeds canonical range')
        elif type(item) is float:
            require(math.isfinite(item), 'Behavior numbers must be finite')
        elif type(item) is str:
            require('\x00' not in item, 'Behavior JSON cannot contain NUL')
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif type(item) is dict:
            require(all(type(k) is str and '\x00' not in k for k in item), 'Behavior object keys must be strings')
            for child in item.values():
                visit(child, depth + 1)
        else:
            raise SafetyError('Behavior values require exact JSON types')

    visit(value)
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False).encode('utf-8')
    except (UnicodeError, ValueError, RecursionError) as error:
        raise SafetyError('Behavior JSON encoding failed') from error
    require(len(raw) <= MAX_VALUE_BYTES, 'Behavior JSON value exceeds byte bound')
    return raw


def fingerprint(value):
    try:
        return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                 allow_nan=False).encode('utf-8'))
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise SafetyError('Behavior fingerprint requires finite JSON data') from error


def is_behavior_module(path):
    return (type(path) is str and Path(path).suffix in NODE_SUFFIXES | {'.py'}
            and not path.endswith(('.d.ts', '.d.mts', '.d.cts')))


def contract_kind(cases):
    """Infer one runtime from data paths, never from executable/model commands."""
    require(type(cases) is list, 'Behavior cases must be a list')
    kinds = set()
    for case in cases:
        require(type(case) is dict and type(case.get('module')) is str, 'Invalid behavior module')
        suffix = Path(case['module']).suffix
        require(is_behavior_module(case['module']), 'Unsupported behavior module runtime')
        kinds.add(PYTHON_KIND if suffix == '.py' else NODE_KIND)
    require(len(kinds) <= 1, 'Mixed behavior runtimes require a separately approved combined environment')
    return next(iter(kinds), PYTHON_KIND)


def _node_json(value):
    canonical(value)
    if type(value) in (int, float):
        require(not float(value).is_integer() or abs(value) <= 9007199254740991,
                'Node behavior numbers exceed safe integer range')
    elif type(value) is list:
        for child in value:
            _node_json(child)
    elif type(value) is dict:
        for child in value.values():
            _node_json(child)


def _case(case, kind=None):
    require(type(case) is dict and set(case) ==
            {'id', 'module', 'callable', 'args', 'kwargs', 'expect', 'missing_behavior'},
            'Unexpected behavior case structure')
    require(type(case['id']) is str and IDENTIFIER.fullmatch(case['id']), 'Invalid behavior case ID')
    inferred = contract_kind([case])
    require(kind is None or kind == inferred, 'Behavior module differs from contract runtime')
    safe_context_path(case['module'])
    require(len(case['module']) <= 256, 'Behavior module path exceeds bound')
    require(type(case['callable']) is str and IDENTIFIER.fullmatch(case['callable']),
            'Behavior callable must be one public module identifier')
    require(type(case['args']) is list and type(case['kwargs']) is dict and
            all(IDENTIFIER.fullmatch(k) for k in case['kwargs'] if type(k) is str) and
            all(type(k) is str for k in case['kwargs']), 'Invalid behavior arguments')
    canonical(case['args'])
    canonical(case['kwargs'])
    if inferred == NODE_KIND:
        require(not case['kwargs'], 'Node behavior cases require positional JSON arguments and empty kwargs')
        _node_json(case['args'])
    expected = case['expect']
    require(type(expected) is dict and expected.get('kind') in {'json', 'exception'}, 'Invalid behavior expectation')
    if expected['kind'] == 'json':
        require(set(expected) == {'kind', 'value'}, 'Unexpected JSON expectation structure')
        canonical(expected['value'])
        if inferred == NODE_KIND:
            _node_json(expected['value'])
    else:
        require(set(expected) == {'kind', 'name'} and type(expected['name']) is str and
                len(expected['name']) <= 160 and EXCEPTION.fullmatch(expected['name']), 'Invalid expected exception name')
    require(type(case['missing_behavior']) is bool, 'Missing behavior must be explicit')


def make_contract(*, repository_id, base_sha, request_sha256, requirement, cases):
    value = {'schema_version': 1, 'kind': contract_kind(cases),
             'repository_id': repository_id, 'base_sha': base_sha, 'request_sha256': request_sha256,
             'requirement': requirement, 'cases': copy.deepcopy(cases), 'runtime_authorized': False}
    value['contract_sha256'] = fingerprint(value)
    validate_contract(value)
    return value


def validate_contract(contract):
    require(type(contract) is dict and set(contract) == {'schema_version', 'kind', 'repository_id',
            'base_sha', 'request_sha256', 'requirement', 'cases', 'runtime_authorized', 'contract_sha256'},
            'Unexpected behavior contract structure')
    require(type(contract['schema_version']) is int and contract['schema_version'] == 1 and
            type(contract['kind']) is str and contract['kind'] in {PYTHON_KIND, NODE_KIND}
            and contract['runtime_authorized'] is False,
            'Unsupported behavior contract')
    for key in ('repository_id', 'request_sha256', 'contract_sha256'):
        require(type(contract[key]) is str and SHA256.fullmatch(contract[key]), 'Invalid behavior identity')
    require(type(contract['base_sha']) is str and SHA.fullmatch(contract['base_sha']), 'Invalid behavior base commit')
    require(type(contract['requirement']) is str and contract['requirement'].strip() and
            len(contract['requirement'].encode('utf-8')) <= 8192 and '\x00' not in contract['requirement'],
            'An explicit bounded behavior requirement is required')
    require(type(contract['cases']) is list and 1 <= len(contract['cases']) <= MAX_CASES,
            'Behavior contract requires 1-24 cases')
    for case in contract['cases']:
        _case(case, contract['kind'])
    require(len({c['id'] for c in contract['cases']}) == len(contract['cases']), 'Duplicate behavior case ID')
    core = {k: v for k, v in contract.items() if k != 'contract_sha256'}
    raw = json.dumps(core, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    require(len(raw) <= MAX_CONTRACT_BYTES and digest(raw) == contract['contract_sha256'],
            'Behavior contract changed or exceeds byte bound')
    return contract


def _load(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'Duplicate behavior result key')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise SafetyError('Invalid behavior result JSON') from error


def _node_equal(left, right):
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is list:
        return len(left) == len(right) and all(_node_equal(a, b) for a, b in zip(left, right))
    if type(left) is dict:
        return left.keys() == right.keys() and all(_node_equal(left[k], right[k]) for k in left)
    return left == right


def _outcome(case, actual, contract_type=PYTHON_KIND):
    require(type(actual) is dict and actual.get('kind') in
            {'json', 'exception', 'missing_module', 'missing_callable', 'import_error', 'execution_error', 'invalid_result'},
            'Unknown behavior result')
    kind = actual['kind']
    if kind == 'json':
        require(set(actual) == {'kind', 'value'}, 'Invalid behavior JSON result')
        canonical(actual['value'])
        if contract_type == NODE_KIND:
            _node_json(actual['value'])
        if case['expect']['kind'] == 'exception':
            return 'expected_exception_missing'
        equal = (_node_equal(actual['value'], case['expect']['value']) if contract_type == NODE_KIND else
                 canonical(actual['value']) == canonical(case['expect']['value']))
        return 'passed' if equal else 'mismatch'
    if kind == 'exception':
        require(set(actual) == {'kind', 'name'} and type(actual['name']) is str and
                len(actual['name']) <= 160 and EXCEPTION.fullmatch(actual['name']), 'Invalid behavior exception result')
        name = actual['name']
        if contract_type == NODE_KIND and name in {'SyntaxError', 'ERR_MODULE_NOT_FOUND', 'MODULE_NOT_FOUND',
                'ERR_UNKNOWN_FILE_EXTENSION', 'ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX'}:
            return 'import_error'
        expected = case['expect'].get('name')
        if expected is not None and (name == expected or
                contract_type == PYTHON_KIND and name == 'builtins.' + expected):
            return 'passed'
        if name in {'builtins.ImportError', 'builtins.ModuleNotFoundError'}:
            return 'import_error'
        if case['expect']['kind'] != 'exception':
            # A crash is inconclusive until its cause has been independently diagnosed.
            return 'execution_error'
        return 'wrong_exception'
    require(set(actual) == {'kind'}, 'Unexpected behavior error result fields')
    if kind in {'missing_module', 'missing_callable'}:
        return 'missing_behavior' if case['missing_behavior'] else 'import_error'
    return kind


def _summary(cases):
    counts = {'total': len(cases), 'passed': 0, 'failed': 0, 'inconclusive': 0}
    for row in cases:
        category = row['category']
        require(category == 'passed' or category in FAILURES | INCONCLUSIVE, 'Unknown behavior case category')
        counts['passed' if category == 'passed' else 'failed' if category in FAILURES else 'inconclusive'] += 1
    return counts


def _source_pin(path):
    path = unlinked(path)
    if not path.exists():
        return None
    require(path.is_file() and path.stat().st_size <= LIMITS['source_file_max_bytes'],
            'Behavior module is not a bounded regular file')
    return digest(path.read_bytes())


def run_behavior_validation(contract, workspace, artifact_root, *, python=None, node=None, phase,
                            environment_sha256, timeout_seconds=30, on_start=None, process_runner=None):
    """Execute a pinned contract in a caller-owned isolated worktree.

    process_runner, when supplied, has TargetEnvironment.process's signature:
    (argv, *, on_start, timeout) -> the existing bounded process receipt. It lets
    the original environment retain process journaling and dependency settings.
    No setup, dependency installation, grant or model call occurs here.
    """
    frozen = copy.deepcopy(validate_contract(contract))
    require(phase in {'baseline', 'candidate'}, 'Invalid behavior execution phase')
    require(type(environment_sha256) is str and SHA256.fullmatch(environment_sha256), 'Behavior environment pin required')
    require(type(timeout_seconds) in (int, float) and math.isfinite(timeout_seconds) and 0 < timeout_seconds <= 1800,
            'Invalid behavior deadline')
    root, artifacts = unlinked(Path(workspace)), unlinked(Path(artifact_root))
    require(root.is_dir() and artifacts.is_dir() and not artifacts.is_relative_to(root),
            'Behavior artifacts must be outside the target worktree')
    is_node = frozen['kind'] == NODE_KIND
    executable = node if is_node else python
    require(isinstance(executable, (str, Path)), 'Behavior interpreter must be explicit')
    interpreter = unlinked(Path(executable))
    require(interpreter.is_file(), 'Behavior interpreter must be explicit')
    if is_node:
        from .pi_distribution import runtime_script
        runner = unlinked(runtime_script('behavior-runner.mjs'))
    else:
        runner = unlinked(Path(__file__).with_name('behavior_validation_runner.py'))
    require(not runner.is_relative_to(root), 'Behavior runner cannot come from the target repository')
    runner_sha = digest(runner.read_bytes())
    directory = Path(tempfile.mkdtemp(prefix='behavior-', dir=artifacts))
    contract_file = directory / 'contract.json'
    contract_raw = json.dumps(frozen, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    contract_file.write_bytes(contract_raw)
    deadline = time.monotonic() + timeout_seconds
    results = []
    for case in frozen['cases']:
        # Reject links before the interpreter sees a target path; do not resolve a
        # missing function into a permission to load some unrelated module.
        target = unlinked(root / case['module'])
        require(target.is_relative_to(root), 'Behavior module escapes target worktree')
        module_pin = _source_pin(target)
        token = uuid.uuid4().hex
        request = {'schema_version': 1, 'case_id': case['id'], 'token': token,
                   'contract_sha256': frozen['contract_sha256'], 'workspace': str(root),
                   'module': case['module'], 'callable': case['callable'],
                   'args': case['args'], 'kwargs': case['kwargs']}
        request_raw = json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
        request_file, result_file = directory / (case['id'] + '.request.json'), directory / (case['id'] + '.actual.json')
        request_file.write_bytes(request_raw)
        argv = [str(interpreter), *([] if is_node else ['-I', '-B']), str(runner), str(request_file), str(result_file)]
        row = {'id': case['id'], 'category': 'timeout', 'actual': None,
               'module_sha256': module_pin,
               'request_sha256': digest(request_raw), 'result_sha256': None, 'process': None}
        remaining = deadline - time.monotonic()
        if remaining > 0:
            process = (process_runner(argv, on_start=on_start, timeout=remaining) if process_runner else
                       run_test(tuple(argv), root, remaining, on_start=on_start))
            row['process'] = process
            if process.get('timed_out') is True:
                row['category'] = 'timeout'
            else:
                row['category'] = 'protocol_error'
                try:
                    require(type(process.get('returncode')) is int and process['returncode'] == 0,
                            'Behavior child exited without a complete result')
                    result_path = unlinked(result_file)
                    require(result_path.is_file() and result_path.stat().st_size <= MAX_RESULT_BYTES,
                            'Missing or oversized behavior result artifact')
                    raw = result_path.read_bytes()
                    value = _load(raw)
                    require(type(value) is dict and set(value) == {'schema_version', 'case_id', 'token',
                            'contract_sha256', 'request_sha256', 'actual'} and type(value['schema_version']) is int and
                            value['schema_version'] == 1 and value['case_id'] == case['id'] and value['token'] == token and
                            value['contract_sha256'] == frozen['contract_sha256'] and value['request_sha256'] == digest(request_raw),
                            'Behavior child result binding differs')
                    row['category'] = _outcome(case, value['actual'], frozen['kind'])
                    row['actual'], row['result_sha256'] = value['actual'], digest(raw)
                except (SafetyError, OSError, ValueError, TypeError):
                    row['category'] = 'protocol_error'
        require(unlinked(contract_file).read_bytes() == contract_raw and unlinked(request_file).read_bytes() == request_raw
                and digest(unlinked(runner).read_bytes()) == runner_sha,
                'Behavior contract/request/controller runner changed during execution')
        require(_source_pin(target) == row['module_sha256'],
                'Behavior execution changed its target module')
        results.append(row)
    counts = _summary(results)
    category = 'inconclusive' if counts['inconclusive'] else 'test_failed' if counts['failed'] else 'passed'
    executable_pin = 'node_sha256' if is_node else 'python_sha256'
    receipt = {'schema_version': 1, 'kind': 'node-behavior-result-v1' if is_node else 'python-behavior-result-v1', 'phase': phase,
               'repository_id': frozen['repository_id'], 'base_sha': frozen['base_sha'],
               'request_sha256': frozen['request_sha256'], 'contract_sha256': frozen['contract_sha256'],
               'environment_sha256': environment_sha256, 'runner_sha256': runner_sha,
               executable_pin: digest(interpreter.read_bytes()), 'artifact_directory': str(directory),
               'contract': frozen, 'cases': results, 'counts': counts, 'category': category,
               'runtime_authorized': False,
               'scope': ('Declared Node behavior cases only; native TypeScript stripping is not type checking. '
                         'Caller owns execution authority. No OS sandbox.' if is_node else
                         'Declared Python behavior cases only; caller owns execution authority. No OS sandbox.')}
    receipt['receipt_sha256'] = fingerprint(receipt)
    return receipt


def validate_receipt(receipt):
    require(type(receipt) is dict, 'Unexpected behavior receipt structure')
    node = receipt.get('kind') == 'node-behavior-result-v1'
    executable_pin = 'node_sha256' if node else 'python_sha256'
    require(type(receipt) is dict and set(receipt) == {'schema_version', 'kind', 'phase', 'repository_id',
            'base_sha', 'request_sha256', 'contract_sha256', 'environment_sha256', 'runner_sha256', executable_pin,
            'artifact_directory', 'contract', 'cases', 'counts', 'category', 'runtime_authorized', 'scope', 'receipt_sha256'},
            'Unexpected behavior receipt structure')
    contract = validate_contract(receipt['contract'])
    require(type(receipt['schema_version']) is int and receipt['schema_version'] == 1 and
            receipt['kind'] == ('node-behavior-result-v1' if contract['kind'] == NODE_KIND else 'python-behavior-result-v1')
            and receipt['phase'] in {'baseline', 'candidate'} and
            receipt['runtime_authorized'] is False, 'Invalid behavior receipt identity')
    require(all(receipt[k] == contract[k] for k in ('repository_id', 'base_sha', 'request_sha256', 'contract_sha256')),
            'Behavior receipt and contract differ')
    for field in ('environment_sha256', 'runner_sha256', executable_pin, 'receipt_sha256'):
        require(type(receipt[field]) is str and SHA256.fullmatch(receipt[field]), 'Invalid behavior receipt pin')
    require(type(receipt['artifact_directory']) is str, 'Behavior artifact directory required')
    directory = unlinked(Path(receipt['artifact_directory']))
    require(directory.is_dir(), 'Behavior artifacts are unavailable')
    saved_contract = unlinked(directory / 'contract.json')
    require(saved_contract.is_file() and saved_contract.stat().st_size <= MAX_CONTRACT_BYTES + 256 and
            _load(saved_contract.read_bytes()) == contract, 'Saved behavior contract changed')
    require(type(receipt['cases']) is list and len(receipt['cases']) == len(contract['cases']), 'Incomplete behavior receipt')
    for expected, row in zip(contract['cases'], receipt['cases']):
        require(type(row) is dict and set(row) == {'id', 'category', 'actual', 'module_sha256', 'request_sha256',
                'result_sha256', 'process'} and row['id'] == expected['id'], 'Behavior case receipt order/structure differs')
        if row['category'] == 'timeout':
            require(row['actual'] is None and (row['process'] is None or row['process'].get('timed_out') is True),
                    'Invalid behavior timeout receipt')
        elif row['category'] == 'protocol_error':
            pass  # An explicitly inconclusive receipt can never become verification.
        else:
            require(_outcome(expected, row['actual'], contract['kind']) == row['category'] and type(row['process']) is dict and
                    type(row['process'].get('returncode')) is int and row['process']['returncode'] == 0 and
                    row['process'].get('timed_out') is False and
                    type(row['result_sha256']) is str and SHA256.fullmatch(row['result_sha256']),
                    'Behavior result/category/process differs')
            request_file = unlinked(directory / (row['id'] + '.request.json'))
            result_file = unlinked(directory / (row['id'] + '.actual.json'))
            require(request_file.is_file() and request_file.stat().st_size <= MAX_CONTRACT_BYTES and
                    result_file.is_file() and result_file.stat().st_size <= MAX_RESULT_BYTES,
                    'Behavior case artifacts are unavailable')
            request_raw, actual_raw = request_file.read_bytes(), result_file.read_bytes()
            request, actual = _load(request_raw), _load(actual_raw)
            require(type(request) is dict and set(request) == {'schema_version', 'case_id', 'token',
                    'contract_sha256', 'workspace', 'module', 'callable', 'args', 'kwargs'} and
                    type(request['schema_version']) is int and request['schema_version'] == 1 and
                    request['case_id'] == expected['id'] and request['contract_sha256'] == contract['contract_sha256'] and
                    all(request[k] == expected[k] for k in ('module', 'callable')) and
                    all(canonical(request[k]) == canonical(expected[k]) for k in ('args', 'kwargs')) and
                    type(request['workspace']) is str and type(request['token']) is str and
                    re.fullmatch('[0-9a-f]{32}', request['token']), 'Behavior invocation differs from pinned case')
            require(digest(request_raw) == row['request_sha256'] and digest(actual_raw) == row['result_sha256'] and
                    type(actual) is dict and set(actual) == {'schema_version', 'case_id', 'token', 'contract_sha256',
                        'request_sha256', 'actual'} and fingerprint(actual['actual']) == fingerprint(row['actual']) and
                    type(actual['schema_version']) is int and actual['schema_version'] == 1 and actual['case_id'] == row['id'] and
                    actual['token'] == request.get('token') and actual['contract_sha256'] == contract['contract_sha256'] and
                    actual['request_sha256'] == row['request_sha256'], 'Behavior case artifact changed')
    require(type(receipt['counts']) is dict and all(type(v) is int for v in receipt['counts'].values()) and
            receipt['counts'] == _summary(receipt['cases']), 'Behavior case counts differ')
    counts = receipt['counts']
    expected_category = 'inconclusive' if counts['inconclusive'] else 'test_failed' if counts['failed'] else 'passed'
    require(receipt['category'] == expected_category and
            receipt.get('receipt_sha256') == fingerprint({k: v for k, v in receipt.items() if k != 'receipt_sha256'}),
            'Behavior receipt changed')
    return receipt


def compare_behavior_validation(baseline, candidate):
    validate_receipt(baseline)
    validate_receipt(candidate)
    require(baseline['phase'] == 'baseline' and candidate['phase'] == 'candidate', 'Behavior comparison phases differ')
    require(baseline['contract']['kind'] == candidate['contract']['kind'], 'Behavior contract runtimes differ')
    fields = ('repository_id', 'base_sha', 'request_sha256', 'contract_sha256', 'environment_sha256',
              'runner_sha256', 'node_sha256' if baseline['contract']['kind'] == NODE_KIND else 'python_sha256', 'contract')
    require(all(baseline[k] == candidate[k] for k in fields), 'Behavior contract/runtime/environment differs')
    rows = []
    for old, new in zip(baseline['cases'], candidate['cases']):
        before, after = old['category'], new['category']
        transition = ('inconclusive' if before in INCONCLUSIVE or after in INCONCLUSIVE else
                      ('maintained' if before == 'passed' else 'repaired') if after == 'passed' else
                      'regressed' if before == 'passed' else 'still_failing')
        rows.append({'id': old['id'], 'transition': transition, 'baseline_category': before, 'candidate_category': after})
    counts = {k: sum(r['transition'] == k for r in rows) for k in
              ('maintained', 'repaired', 'regressed', 'still_failing', 'inconclusive')}
    return {'schema_version': 1, 'contract_sha256': baseline['contract_sha256'], 'cases': rows, 'counts': counts,
            'comparable': not counts['inconclusive'],
            'baseline_reproduced': baseline['category'] == 'test_failed',
            'candidate_verified': baseline['category'] == 'test_failed' and candidate['category'] == 'passed'
                                  and counts['repaired'] > 0 and not counts['inconclusive'],
            'scope': 'Same pinned declared behavior cases; existing regression suites remain separately required.'}
