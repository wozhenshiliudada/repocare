"""Read-only CI environment/scope proposal from an original monitor registration.

Uses existing task-test discovery and Policy byte checks. A suggested command,
including a narrower command, does not inherit the registered command's grant.
No project process, provider, install, grant or second journal is created here.
"""
from dataclasses import asdict
import json

from .github_connection import github_url
from .issue_intake import fingerprint
from .maintenance_rules import compile_investigation_policy, maintenance_rules, require
from .target_environment import discover_environment


def prepare_ci_environment(repository, spec, event, diagnosis, guard):
    """Only the serialized monitor passes its registered spec and saved diagnosis."""
    guard()
    head = event['sha']
    slug, _ = github_url(spec['github_url'])
    selection = diagnosis.get('selection', {})
    require(diagnosis.get('status') == 'investigated'
            and diagnosis.get('base_sha') == head and repository.head == head
            and diagnosis.get('scope_sha256') == fingerprint(spec)
            and selection.get('repository', '').casefold() == slug.casefold()
            and any(row.get('id') == selection.get('run_id')
                    and row.get('attempt') == selection.get('attempt')
                    for row in event.get('ci_failures', []))
            and diagnosis.get('selection_sha256') == fingerprint(
                [slug.casefold(), head, selection.get('run_id'), selection.get('attempt')]),
            'CI proposal source, attempt or registered scope changed')
    # Registration and current source are the read boundary. CI text cannot add
    # roots, switch mode or authorize commands by attaching a rules-shaped object.
    roots = list(dict.fromkeys([*spec['allowed_paths'], *spec['readonly_paths']]))
    result = {'schema_version': 1, 'base_sha': head, 'selection': selection,
        'diagnosis_sha256': fingerprint(diagnosis), 'scope_sha256': fingerprint(spec),
        'read_roots': roots, 'preserved_registered_tests': spec['allowed_tests'],
        'runtime_authorized': False, 'repair_authorized': False, 'model_requests': 0,
        'tests_executed': False, 'environment_prepared': False,
        'requires_execution_grant': True, 'status': 'evidence_review',
        'blockers': [], 'additional_test_commands': [],
        'interpretation': 'Read-only proposal. Original checks are retained, not replaced. '
                          'A proposal is not a reproduction, install, model or repair grant.'}
    if not diagnosis.get('ci_evidence', {}).get('failed_steps'):
        result['blockers'] = ['尚未取得失败步骤证据；先检查 CI 基础设施或补充证据，再判断是否需要修复代码。']
    else:
        plan = discover_environment(repository, head, roots, investigation=diagnosis['report'])
        result['environment'] = plan
        if plan['status'] != 'proposed':
            result.update(status='environment_blocked', blockers=plan['blockers'])
        else:
            # check_ids describe the proposed fixed templates. They do not extend
            # the original monitor's independently registered command authority.
            rules = maintenance_rules(repository, read_roots=roots, write_roots=spec['allowed_paths'],
                check_ids=[c['id'] for c in plan['checks']], timeout_seconds=spec['timeout_seconds'])
            policy, binding = compile_investigation_policy(repository, head, diagnosis['report'], plan, rules)
            registered = [tuple(c) for c in spec['allowed_tests']]
            additional = [list(c) for c in policy.allowed_tests if c not in registered]
            result.update(status='scope_review' if additional else 'proposed',
                proposed_policy=asdict(policy),
                compiled_scope={**binding, 'binding_sha256': fingerprint(binding)},
                additional_test_commands=additional,
                blockers=['新发现的检查不在原命令授权内，需要先审阅检查范围；原回归检查仍须保留。']
                         if additional else [])
    guard()
    require(repository.head == head, 'Source changed during CI preparation')
    require(len(json.dumps(result, ensure_ascii=False).encode('utf-8')) <= 262144,
            'CI environment proposal exceeds original event capacity')
    result['proposal_sha256'] = fingerprint(result)
    return result
