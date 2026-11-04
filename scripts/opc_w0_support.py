"""Offline W0 preparation helpers; no provider, production policy or executor imports.

The public prompt is a projection, not an authorization. Selection can inspect only
patch headers/counts for stratification; neither patch nor test metadata is exported.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

SEED = 'repocare-w0-v1:'
CODE_SUFFIXES = ('.py', '.js', '.mjs', '.ts', '.tsx', '.jsx')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def public_prompt(record, family='live'):
    """Strict allowlist: operational metadata belongs in the controller, not this DTO."""
    field = 'problem_statement' if family == 'live' else 'instruction'
    if family not in {'live', 'pro'}:
        raise ValueError('Unknown benchmark family')
    text = record.get(field)
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Missing official task text')
    return {field: text}


def patch_statistics(patch):
    paths = re.findall(r'^diff --git a/(.*?) b/.*$', patch, re.M)
    code = {p for p in paths if p.endswith(CODE_SUFFIXES)
            and not any(s in p.lower() for s in ('test', 'spec', 'snapshot', 'lock'))}
    changed = sum(line.startswith(('+', '-')) and not line.startswith(('+++', '---'))
                  for line in patch.splitlines())
    # No paths or patch bodies leave this function.
    return {'implementation_files': len(code), 'changed_lines': changed}


def select_live(rows, count, excluded_repositories=()):
    """Fixed metadata rule, one repository per selection, no model-outcome inputs."""
    eligible = []
    for row in rows:
        stats = patch_statistics(row['patch'])
        if (stats['implementation_files'] >= 2 and 15 <= stats['changed_lines'] <= 600
                and 100 <= len(row['problem_statement']) <= 16000
                and row.get('FAIL_TO_PASS') and row.get('PASS_TO_PASS')
                and row['repo'] not in excluded_repositories):
            eligible.append(row)
    eligible.sort(key=lambda r: hashlib.sha256((SEED + r['instance_id']).encode()).hexdigest())
    seen, selected = set(), []
    for row in eligible:
        if row['repo'] not in seen:
            selected.append(row)
            seen.add(row['repo'])
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError('Insufficient eligible distinct repositories')
    return selected


def event_key(event):
    """Draft identity, deliberately excludes mutable UI state and timestamps.

    Issue source_version hashes effective title/body/labels/state; CI source_version
    identifies its run/attempt. Cross-source correlation is a separate operation.
    """
    if event.get('kind') not in {'commit', 'ci', 'issue'}:
        raise ValueError('Unsupported event kind')
    required = ('repository_id', 'kind', 'source_id', 'source_version', 'target_sha')
    if any(not isinstance(event.get(k), str) or not event[k] for k in required):
        raise ValueError('Incomplete event identity')
    if not re.fullmatch(r'[0-9a-f]{40}', event['target_sha']):
        raise ValueError('Invalid target SHA')
    return fingerprint({k: event[k] for k in required})


def check_outcome(returncode, timed_out, tests_run):
    """Control-fixture interpretation only; never a production test parser."""
    if timed_out:
        return 'inconclusive_timeout'
    if tests_run == 0:
        return 'inconclusive_no_tests'
    if tests_run is None:
        return 'unknown_environment_or_parser'
    return 'passed_selected_checks' if returncode == 0 else 'failed_selected_checks'


def audit_contract_examples(examples):
    if examples['schema_version'] != 1 or examples['runtime_authorized'] is not False:
        raise ValueError('W0 examples cannot be runtime authority')
    policy, diagnosis, scope = (examples[k] for k in ('maintenance_policy', 'diagnosis', 'execution_scope'))
    if scope['policy_version'] != policy['version'] or scope['budget_ref'] != policy['budget_ref']:
        raise ValueError('Scope must retain policy version and original budget reference')
    if scope['approval_receipt'] is not None:
        raise ValueError('Examples cannot contain an approval')
    if not scope['editable_paths'] or not scope['tests']:
        raise ValueError('Repair scope needs concrete files and checks')
    if diagnosis['authorization_effect'] != 'none' or diagnosis['next_action'] not in {
            'investigate', 'reproduce', 'plan', 'request_scope', 'needs_environment', 'stop'}:
        raise ValueError('Diagnosis cannot grant execution authority')
    if diagnosis['target_sha'] != examples['event']['target_sha']:
        raise ValueError('Diagnosis baseline differs')
    event_key(examples['event'])
    return {'contract_examples': 'passed', 'runtime_authorized': False}


def audit_selection(selection):
    tasks = selection['tasks']
    if selection['paid_authorized'] or selection['status'] != 'metadata_frozen_environment_pending':
        raise ValueError('W0 cannot start a paid or environment-verified run')
    if len(tasks) != 10 or len({r['instance_id'] for r in tasks}) != 10:
        raise ValueError('Expected eight real tasks and two separate challenges')
    if Counter(r['role'] for r in tasks) != {'development': 4, 'holdout': 4, 'challenge': 2}:
        raise ValueError('Role distribution changed')
    for row in tasks:
        if row['environment_verified'] or row['agent_rollouts'] != 0:
            raise ValueError('W0 selection cannot imply runtime results')
        if not re.fullmatch(r'[a-f0-9]{64}', row['public_prompt_sha256']):
            raise ValueError('Missing prompt pin')
        if not re.fullmatch(r'[a-f0-9]{40}', row['source_revision']):
            raise ValueError('Unpinned source')
        if row['family'] == 'live' and not re.fullmatch(r'[a-f0-9]{40}', row['base_commit']):
            raise ValueError('Unpinned base commit')
    return {'selection': 'passed', 'real_tasks': 8, 'challenges': 2, 'model_calls': 0}
