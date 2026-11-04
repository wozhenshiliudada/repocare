"""Read-only repair comparability, never correlation approval or execution authority.

``fault_group`` is an exact server-created explicit-association group. It records
an operator's association, not a diagnosis or permission supplied by Issue text.
The caller must still validate its original controller authority before effects.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import os
from pathlib import Path
import re

from .behavior_validation import canonical
from .git_investigation import allowed_path, validate_roots
from .github_connection import branch_name
from .issue_intake import fingerprint
from .maintenance_limits import LIMITS
from .models import Policy, SafetyError, digest, file_digest
from .source_discovery import CommittedTree
from .target_environment import validate_grant
from .workflow_behavior import command_for, is_behavior, validate_scope
from .workflow_environment import GrantedEnvironment

_HASH = re.compile(r'[a-f0-9]{64}')
_BUDGET = {'request_budget', 'max_requests', 'requests_used', 'requests_remaining',
           'requests_reserved_unknown', 'budget', 'request_ceiling'}


def _require(value, message):
    if not value:
        raise SafetyError(message)


def _behavior(contract):
    # All behavior bytes remain meaningful; only the clone/version wrappers go.
    return {k: deepcopy(v) for k, v in contract.items()
            if k not in {'repository_id', 'base_sha', 'contract_sha256'}}


def _checks(rows, contract):
    result = deepcopy(rows)
    if contract is not None:
        semantic = fingerprint(_behavior(contract))
        for row in result:
            if is_behavior(row['argv']):
                _require(tuple(row['argv']) == command_for(contract), 'Behavior command differs')
                row['argv'] = [row['argv'][0], semantic]
                row['id'] = 'behavior-comparison-' + semantic
    return result


def _environment_semantics(plan):
    value = deepcopy(plan)
    for name in ('repository_id', 'plan_sha256'):
        value.pop(name, None)
    contract = value.get('behavior_contract')
    value['checks'] = _checks(value['checks'], contract)
    if contract is not None:
        value['behavior_contract'] = _behavior(contract)
    if 'selection' in value:
        # The controller keeps the original report/hash; comparison verifies the
        # complete selected/required pins and exact generated commands instead.
        value['selection'].pop('investigation_sha256', None)
    return value


def _grant_semantics(grant):
    value = deepcopy(grant)
    for name in ('plan_sha256', 'grant_sha256', 'wheelhouse'):
        value.pop(name, None)
    # Runtime paths are deliberately conservative. Also pin the actual local
    # executable/CLI bytes without launching them; a same-path replacement differs.
    for name in ('python', 'node', 'npm_cli'):
        path = grant.get(name)
        if path is not None:
            _require(isinstance(path, str) and Path(path).is_file(), 'Original runtime missing')
            value[name] = {'path': path, 'sha256': file_digest(Path(path))}
    wheelhouse = grant.get('wheelhouse')
    if wheelhouse is not None:
        root = Path(wheelhouse)
        _require(root.is_dir() and not root.is_symlink(), 'Original wheelhouse missing')
        files = list(root.iterdir())
        _require(len(files) <= 100 and all(p.is_file() and not p.is_symlink()
                 and p.suffix == '.whl' and p.stat().st_size <= 64 * 1024 * 1024 for p in files),
                 'Original wheelhouse changed')
        _require({p.name: file_digest(p) for p in files} == grant['wheel_pins'],
                 'Original wheel bytes changed')
    else:
        _require(grant.get('wheel_pins') == {}, 'Wheel pins have no original wheelhouse')
    return value


def repair_comparison(repository, *, repository_id, slug, branch, head_sha, policy,
                      environment, grant, read_roots, write_roots, mode,
                      model_identity, max_attempts, fault_group):
    """Return existing MaintenanceCorrelation comparison fields, without writes.

    Local Git identity hashes cannot substitute for a verified GitHub numeric ID.
    This function reads Git blobs, runtime and wheel bytes; it never executes
    target checks, imports target modules, installs packages or invokes a model.
    The original controller must first rebuild its report/plan/policy binding;
    this projection does not reconstruct investigations or confer freshness.
    """
    _require(type(repository_id) is int and 0 < repository_id <= 2**53 - 1,
             'Verified numeric GitHub repository ID required')
    _require(isinstance(slug, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', slug)
             and len(slug) <= 201, 'Invalid GitHub repository slug')
    branch = branch_name(branch)
    _require(isinstance(head_sha, str) and re.fullmatch(r'[a-f0-9]{40}', head_sha)
             and repository.head == head_sha, 'Original source baseline differs')
    _require(isinstance(fault_group, str) and _HASH.fullmatch(fault_group),
             'Explicit server association group required')
    _require(mode in {'approval', 'auto'} and type(max_attempts) is int
             and 1 <= max_attempts <= LIMITS['candidate_max_rounds'], 'Invalid original repair authority')
    _require(isinstance(policy, Policy), 'Original Policy required')
    policy.validate_scope()
    reads, writes = validate_roots(read_roots), validate_roots(write_roots)
    _require(reads and writes and all(allowed_path(p, reads) for p in writes if p != '.')
             and ('.' not in writes or '.' in reads), 'Original read/write roots differ')
    _require(all(allowed_path(p, writes) for p in policy.allowed_paths)
             and all(allowed_path(p, reads) for p in policy.readonly_paths),
             'Original Policy exceeds maintainer roots')
    _require(isinstance(model_identity, dict) and model_identity
             and isinstance(model_identity.get('provider_config_sha256'), str)
             and _HASH.fullmatch(model_identity['provider_config_sha256'])
             and 'credential_source' in model_identity
             and not _BUDGET.intersection(model_identity), 'Original model/config identity required without budget')
    canonical(model_identity)
    before = repository.snapshot()
    repository.require_clean(before)
    validate_grant(environment, grant)
    GrantedEnvironment(environment, grant)  # No target workspace or effects.
    _require(environment['head_sha'] == head_sha
             and environment['read_roots'] == list(reads)
             and environment['repository_id'] == digest(os.path.normcase(str(repository.common_dir)).encode()),
             'Original environment belongs to another source')
    tree = CommittedTree(repository, head_sha, reads)
    _require(not tree.tree_truncated, 'Original source catalog incomplete')
    source_pins = []
    check_sources = []
    for check in environment['checks']:
        _require(isinstance(check, dict) and isinstance(check.get('sources'), list)
                 and all(isinstance(p, str) for p in check['sources']), 'Original check sources missing')
        check_sources.extend(check['sources'])
    for name in sorted(set((*policy.allowed_paths, *policy.readonly_paths, *check_sources))):
        tree.read(name)
        pin = tree.pin(name)
        _require(digest(repository.read(name)) == pin['sha256'], 'Original source bytes changed')
        source_pins.append(pin)
    for pin in [*environment['inputs'], *environment['decision_inputs']]:
        _require(isinstance(pin, dict) and isinstance(pin.get('path'), str), 'Invalid environment input pin')
        tree.read(pin['path'])
        _require(pin == tree.pin(pin['path'])
                 and digest(repository.read(pin['path'])) == pin['sha256'], 'Original environment input pin changed')
    contract = environment.get('behavior_contract')
    if contract is not None:
        validate_scope(contract, repository, head_sha, policy=policy)
    available = [tuple(row['argv']) for row in environment['checks']]
    _require(all(command in available for command in policy.allowed_tests), 'Policy check is outside original environment')
    checks = _checks(environment['checks'], contract)
    policy_value = asdict(policy)
    policy_value['allowed_tests'] = _checks([
        {'argv': list(c), 'id': str(i)} for i, c in enumerate(policy.allowed_tests)], contract)
    grant_value = _grant_semantics(grant)
    authority = {'schema_version': 1, 'read_roots': list(reads), 'write_roots': list(writes),
                 'policy': policy_value, 'source_pins': source_pins, 'mode': mode,
                 'model_identity': deepcopy(model_identity), 'max_attempts': max_attempts,
                 'environment_grant': grant_value}
    _require(repository.head == head_sha and repository.snapshot() == before, 'Original source changed during comparison')
    return {'repository_id': repository_id, 'repository': slug.casefold(), 'branch': branch,
            'head_sha': head_sha, 'checks_sha256': fingerprint({'checks': checks, 'inputs': environment['inputs'],
                'selection': _environment_semantics(environment).get('selection')}),
            'environment_sha256': fingerprint({'plan': _environment_semantics(environment), 'grant': grant_value}),
            'fault_sha256': fingerprint({'kind': 'explicit-local-association-v1', 'group': fault_group}),
            'authority_sha256': fingerprint(authority)}
