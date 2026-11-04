"""Behavior cases as a controller check in the original granted environment.

The marker is data in an approved Policy/Plan, never a shell command. Every
execution still uses the original process journal, environment and worktree.
"""
from __future__ import annotations

import time
import json
from pathlib import Path
import re

from .behavior_validation import (validate_contract, validate_receipt,
    compare_behavior_validation, run_behavior_validation)
from .issue_intake import fingerprint
from .models import Policy, SafetyError, digest
from .task_session import repository_identity

MARKER = 'repocare:behavior-v1'


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def command_for(contract):
    validate_contract(contract)
    return (MARKER, contract['contract_sha256'])


def check_id(contract):
    """Rules pin behavior meaning; each task command separately pins its version.

    A later commit may reuse the same approved cases, but never the previous
    command, requirement binding, grant, execution receipt or approval.
    """
    validate_contract(contract)
    from .behavior_validation import fingerprint as behavior_fingerprint
    return 'behavior-' + behavior_fingerprint({k: contract[k] for k in ('kind','requirement','cases')})


def is_behavior(command):
    return isinstance(command, (tuple, list)) and bool(command) and command[0] == MARKER


def bind_policy(policy, contract):
    """Trusted controller prepares a new approval scope; never mutates old Policy."""
    check = command_for(contract)
    require(not any(is_behavior(c) for c in policy.allowed_tests), 'Behavior check already bound')
    value = Policy(policy.allowed_paths, (*policy.allowed_tests, check),
                   policy.timeout_seconds, policy.readonly_paths)
    value.validate_scope()
    return value


def validate_scope(contract, source, base_sha, *, issue=None, policy=None):
    validate_contract(contract)
    require(contract['repository_id'] == repository_identity(source.root, source.common_dir)
            and contract['base_sha'] == base_sha, 'Behavior contract repository/base differs')
    if issue is not None:
        require(contract['request_sha256'] == digest(issue.encode('utf-8')),
                'Behavior contract requirement differs from original issue')
    if policy is not None:
        selected = [tuple(c) for c in policy.allowed_tests if is_behavior(c)]
        require(selected == [command_for(contract)], 'Original policy must pin exactly this behavior check')


def run_environment_check(environment, command, *, timeout=None, on_start=None, phase='candidate'):
    """Called only by TargetEnvironment.run_check after original grant guards."""
    contract = environment.plan.get('behavior_contract')
    runtime = 'node' if contract and contract['kind'] == 'node-behavior-v1' else 'python'
    require(contract is not None and tuple(command) == command_for(contract)
            and environment.plan['runtime'] == runtime, 'Behavior check is not in this granted environment')
    require((environment.python is not None if runtime == 'python' else environment.grant.get('node')),
            'Prepared original runtime required for behavior cases')
    started = time.monotonic()
    receipt = run_behavior_validation(contract, environment.workspace.root, environment.owner,
        python=environment.python, node=environment.grant.get('node'), phase=phase,
        environment_sha256=fingerprint(environment.binding),
        timeout_seconds=environment.grant['timeout_seconds'] if timeout is None else timeout,
        on_start=on_start, process_runner=environment.process)
    lines = ['Pinned behavior cases: ' + str(receipt['counts'])]
    for expected, actual in zip(contract['cases'], receipt['cases']):
        if actual['category'] == 'passed':
            continue
        quoted = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False)[:300]
        lines.append('FAIL: ' + expected['id'] + ' (' + expected['module'] + ':' + expected['callable'] + ') '
                     + actual['category'] + ' args=' + quoted(expected['args'])
                     + ' expected=' + quoted(expected['expect']) + ' actual=' + quoted(actual['actual']))
    return {'command': list(command), 'command_kind': 'controller_behavior_contract',
            'returncode': 0 if receipt['category'] == 'passed' else 1,
            'timed_out': any(row['category'] == 'timeout' for row in receipt['cases']),
            'output_truncated': False,
            'stdout': '\n'.join(lines), 'stderr': '',
            'category': receipt['category'], 'tests_run': receipt['counts']['total'],
            'duration_seconds': time.monotonic() - started, 'behavior_receipt': receipt}


def _receipt(check, contract):
    value = check.get('behavior_receipt')
    validate_receipt(value)
    require(tuple(check.get('command', ())) == command_for(contract)
            and value['contract'] == contract and value['category'] == check.get('category')
            and check.get('returncode') == (0 if value['category'] == 'passed' else 1)
            and check.get('timed_out') is any(r['category'] == 'timeout' for r in value['cases'])
            and check.get('tests_run') == value['counts']['total'], 'Behavior command result differs from actual cases')
    return value


def _case_workspace(receipt, workspace):
    for case in receipt['cases']:
        path = Path(receipt['artifact_directory']) / (case['id'] + '.request.json')
        request = json.loads(path.read_text(encoding='utf-8'))
        require(request['workspace'] == str(workspace), 'Behavior result belongs to a different worktree')


def verify_pair(record, source, policy, candidate=None):
    """Reopen pinned case artifacts before accepting or sealing a candidate."""
    declaration = record.get('behavior_validation')
    selected = [c for c in policy.allowed_tests if is_behavior(c)]
    if declaration is None:
        require(not selected, 'Behavior declaration missing; cannot downgrade to legacy checks')
        return None
    require(isinstance(declaration, dict) and set(declaration) == {'contract', 'contract_sha256'}
            and declaration['contract_sha256'] == declaration['contract']['contract_sha256'],
            'Original behavior declaration changed')
    contract = declaration['contract']
    validate_scope(contract, source, record['base_sha'], issue=record['issue'], policy=policy)
    require(record.get('environment') is not None, 'Behavior checks require original granted environment')
    pairs = []
    for field in ('baseline_tests', 'candidate_tests'):
        checks = [c for c in record.get(field, []) if is_behavior(c.get('command', []))]
        require(len(checks) == 1, 'Original behavior check missing or duplicated')
        receipt = _receipt(checks[0], contract)
        require(receipt['phase'] == ('baseline' if field == 'baseline_tests' else 'candidate'),
                'Behavior receipt controller phase differs')
        _case_workspace(receipt, Path(record['workspace']))
        pairs.append(receipt)
    comparison = compare_behavior_validation(*pairs)
    require(comparison['candidate_verified'], 'No reproduced and repaired pinned behavior')
    if candidate is not None:
        for case, row in zip(contract['cases'], pairs[1]['cases']):
            path = candidate.root / case['module']
            require(path.is_file() and digest(candidate.read(case['module'])) == row['module_sha256'],
                    'Behavior implementation changed after verification')
    return comparison


def validate_sealed_checks(record, tests, *, source=None, commit=None):
    declaration = record.get('behavior_validation')
    if declaration is None:
        require(not any(is_behavior(c.get('command', [])) for c in tests), 'Unexpected sealed behavior check')
        return
    checks = [c for c in tests if is_behavior(c.get('command', []))]
    require(len(checks) == 1, 'Sealed behavior result missing or duplicated')
    receipt = _receipt(checks[0], declaration['contract'])
    require(receipt['category'] == 'passed', 'Sealed behavior cases did not all pass')
    _case_workspace(receipt, Path(record['workspace']).parent / 'integration-verification')
    original = [c for c in record['candidate_tests'] if is_behavior(c['command'])]
    require(len(original) == 1, 'Original behavior candidate check missing')
    before = _receipt(original[0], declaration['contract'])
    pin = 'node_sha256' if declaration['contract']['kind'] == 'node-behavior-v1' else 'python_sha256'
    require(all(receipt[k] == before[k] for k in ('environment_sha256','runner_sha256',pin)),
            'Sealed behavior runtime differs from original candidate')
    if source is not None:
        require(isinstance(commit, str) and re.fullmatch(r'[a-f0-9]{40}', commit), 'Invalid sealed behavior commit')
        for case, row in zip(declaration['contract']['cases'], receipt['cases']):
            require(digest(source.command('show', commit + ':' + case['module'])) == row['module_sha256'],
                    'Behavior result is not for the actual sealed implementation')
