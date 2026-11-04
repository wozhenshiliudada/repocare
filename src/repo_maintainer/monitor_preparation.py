"""Root-rule discovery and explicitly granted baseline through the existing environment."""
from dataclasses import asdict

from .issue_intake import fingerprint
from .maintenance_rules import compile_investigation_policy, maintenance_rules, require
from .monitor_rules import family
from .models import SafetyError
from .repository_diagnosis import build_report
from .target_environment import discover_environment, make_grant, collect_environment_baseline


REPAIR_SCOPE_BLOCKED = '已复现测试失败；当前调查未形成可执行的修复范围。请核对失败检查和维护规则，后续提交会重新调查。'


def prepare_monitor(repository, spec, config, event, owner, guard, save, reader=None, *, ci_diagnosis=None,
                    behavior_contract=None,maintenance_request=None,behavior_guard=None,case_report=None,
                    preserved_checks=(),baseline_reader=None):
    """The monitor durably owns this intent before calling; no Pi/provider here."""
    settings, head = config['settings'], event['sha']
    guard()
    require(repository.head == head, '目录调查快照与提交不符')
    result = {'schema_version': 1, 'status': 'started', 'base_sha': head,
        'root_rules_sha256': config['sha256'], 'model_requests': 0,
        'candidate_execution_connected': False, 'tests_executed': False,
        'repair_authorized': False, 'preserved_registered_tests': spec['allowed_tests']}
    if behavior_contract is not None:
        from .workflow_behavior import validate_scope
        require(callable(behavior_guard) and isinstance(maintenance_request,str),
                '行为检查需要原案例审阅和固定需求')
        behavior_guard()
        validate_scope(behavior_contract,repository,head,issue=maintenance_request)
        result.update(maintenance_request=maintenance_request,
                      behavior_contract_sha256=behavior_contract['contract_sha256'])
    if event.get('ci_failures'):
        from .ci_investigation import investigate_ci
        effective = {**spec, 'maintenance_roots': settings, 'root_rules_sha256': config['sha256']}
        diagnosis = ci_diagnosis if ci_diagnosis is not None else investigate_ci(
            repository, effective, event, reader, guard,baseline_reader=baseline_reader)
        result['ci_diagnosis'] = diagnosis
        report = diagnosis['report']
        if not diagnosis['ci_evidence']['failed_steps']:
            result.update(status='evidence_review', message='尚未取得失败步骤，先核查 CI 环境或补充证据')
            save(result)
            return result
        from .ci_repair import failure_binding
        result['ci_failure_binding']=failure_binding(diagnosis,effective,event)
    else:
        report = build_report(repository, head_sha=head, read_roots=settings['read_roots'], search=True)
    # Case-oriented investigation selects repair files, never a smaller test
    # suite. Keep the original CI investigation (or the full ordinary monitor
    # discovery) as the check-selection input.
    check_report = report if result.get('ci_failure_binding') else None
    if case_report is not None:
        require(behavior_contract is not None,'补充案例调查不能替换普通原调查')
        behavior_guard();report=case_report
    result['report'] = report
    plan = discover_environment(repository, head, settings['read_roots'],
        investigation=check_report if check_report and check_report['file_candidates'] else None,
        behavior_contract=behavior_contract)
    result['environment'] = plan
    if not report['file_candidates'] and plan['checks']:
        # The comparison can be empty or unavailable. Follow discovered test-file
        # names/imports as bounded static hints; the user supplies no filenames.
        query = '\n'.join('`'+n+'`' for c in plan['checks'] for n in c['sources'])
        query = query.encode('utf-8')[:4096].decode('utf-8', errors='ignore')
        report = build_report(repository, head_sha=head, read_roots=settings['read_roots'], search=True, query=query)
        result['report'] = report
    def permitted(check):
        from .workflow_behavior import is_behavior,command_for,check_id
        if is_behavior(check['argv']):
            if behavior_contract is None:return False
            behavior_guard()
            return check['id']==check_id(behavior_contract) and tuple(check['argv'])==command_for(behavior_contract)
        return family(check) in settings['check_families']
    if not plan['checks'] and plan['blockers']==['No reliable functional check discovered']:
        result.update(status='validation_needed',message='没有现成检查；先说明业务期望，让Agent提出验收案例再核对')
    elif plan['status'] != 'proposed':
        result.update(status='environment_blocked', message='环境配置受阻；没有调用模型或修改代码')
    elif any(not permitted(c) for c in plan['checks']):
        result.update(status='scope_review', message='发现未授权的检查方式，需要调整维护规则；不会丢掉该检查')
    elif any(c not in plan['checks'] for c in preserved_checks):
        result.update(status='scope_review', message='案例准备未保留全部原回归检查及来源，停止执行')
    elif not all(c in [r['argv'] for r in plan['checks']] for c in spec['allowed_tests']):
        result.update(status='scope_review', message='新环境未包含全部原授权回归检查，保留原检查并停止执行')
    else:
        rules = maintenance_rules(repository, read_roots=settings['read_roots'],
            write_roots=settings['write_roots'], check_ids=[c['id'] for c in plan['checks']],
            mode=settings['mode'], allow_auto=settings['mode']=='auto',
            timeout_seconds=settings['timeout_seconds'], actor='local-workbench')
        # No implementation match may still allow a baseline investigation. It
        # cannot manufacture a repair scope, nor authorize editing test inputs.
        try:
            policy, binding = compile_investigation_policy(repository, head, report, plan, rules)
            result.update(proposed_policy=asdict(policy), compiled_scope=binding)
        except SafetyError as error:
            result['scope_compilation'] = {'status':'needs_investigation', 'error_type':type(error).__name__}
        if not settings['allow_local_code']:
            result.update(status='execution_consent_required', message='检查已自动选择；尚未允许执行本仓库代码，未运行测试')
        else:
            grant = make_grant(plan, allow_local_code=True, allow_install=settings['allow_install'],
                allow_network=settings['allow_network'], timeout=settings['timeout_seconds'], actor='local-workbench')
            result.update(status='baseline_started', grant=grant)
            save(result)  # Intent before workspace, installation or tests.
            guard()
            baseline = collect_environment_baseline(repository, plan, grant, owner/'baselines')
            # Original RunStore keeps full environment/check evidence. The event
            # stores a bounded projection, not a second run or budget journal.
            result['baseline'] = {k:baseline.get(k) for k in ('run_id','status','checks',
                'source_unchanged','cleanup','base_sha','plan_sha256','grant_sha256')}
            result['tests_executed'] = bool(baseline.get('checks'))
            if baseline['status']=='baseline_ready':
                message = REPAIR_SCOPE_BLOCKED if 'scope_compilation' in result else '已复现测试失败，已准备修复范围'
                result.update(status='reproduced', message=message)
            elif baseline['status']=='baseline_passes':
                result.update(status='baseline_passes', message='所选检查通过；不能证明整个仓库没有缺陷')
            else:
                result.update(status='environment_blocked', message='环境或基线检查未完成，保留证据；不统计为修复失败')
    guard()
    require(repository.head == head, '准备期间目录调查快照已变化')
    result['result_sha256'] = fingerprint(result)
    save(result)
    return result
