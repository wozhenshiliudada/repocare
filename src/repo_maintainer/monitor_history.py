"""Bounded historical evidence, independent of providers and executable sessions."""
import json
import re
import sqlite3
from contextlib import contextmanager

from .dashboard import pick, project_run, read_json, unlinked
from .models import SafetyError, digest
from .store import redact


def identity(value, length):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{%s}' % length, value):
        raise SafetyError('监控历史编号无效')
    return value


@contextmanager
def journal(root):
    path = unlinked(root/'ui-state'/'registry.sqlite3')
    for suffix in ('-journal', '-wal', '-shm'):
        unlinked(path.with_name(path.name+suffix))
    if not path.is_file() or path.stat().st_size > 32*1024*1024:
        raise SafetyError('原事件登记缺失或超过读取上限；保留现场')
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def object_at(db, kind, key):
    row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, key)).fetchone()
    if row is None:
        raise SafetyError('原事件或证据关联缺失')
    if len(row[0].encode('utf-8')) > 8*1024*1024:
        raise SafetyError('历史记录超过读取上限')
    value = json.loads(row[0])
    if not isinstance(value, dict):
        raise SafetyError('原历史记录格式无效')
    return value


def budget_at(db):
    ceiling, used, held = db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
    if any(type(n) is not int or n < 0 for n in (ceiling, used, held)) or used+held > ceiling:
        raise SafetyError('原消费记录无效；不能重置')
    return dict(request_budget=ceiling, requests_used=used, requests_reserved_unknown=held,
                requests_remaining=ceiling-used-held)


def history(monitor, monitor_id, event_id):
    """Fixed roots and names only. No Git/network/provider, claim, or journal writes."""
    identity(monitor_id, 16); identity(event_id, 16)
    if monitor_id not in monitor.records:
        raise SafetyError('未知监控仓库')
    root = unlinked(monitor.root/monitor_id)
    with journal(root) as db:
        event = object_at(db, 'event', event_id)
        if event.get('id') != event_id or not re.fullmatch('[a-f0-9]{40}', event.get('sha', '')):
            raise SafetyError('原事件身份无效')
        budget = budget_at(db)
        attempts = [pick(object_at(db, 'attempt', row[0]), 'id status reserved started_at finished_at outcome')
                    for row in db.execute("SELECT id FROM objects WHERE kind='attempt' ORDER BY rowid DESC LIMIT 20").fetchall()]
        continuation_rows = [object_at(db,'monitor_continuation',r[0]) for r in db.execute(
            "SELECT id FROM objects WHERE kind='monitor_continuation' ORDER BY rowid LIMIT 101").fetchall()]
    source = str(unlinked(root/'events'/event_id/'source'))
    value = {'schema_version':1, 'monitor_id':monitor_id, 'event_id':event_id,
             'base_sha':event['sha'], 'read_only':True, 'runtime_authorized':False,
             'model_requests':0, 'budget':budget, 'plans':[], 'runs':[], 'errors':[],
             'recent_shared_attempts':attempts, 'attempt_scope':'shared_monitor_not_event_only',
             'continuations':[pick(r,'id kind status parent_run_id plan_id run_id created_at finished_at')
                              for r in continuation_rows if r.get('event_id')==event_id],
             'event':pick(event, 'status message created_at display_status'),
             'execution':pick(event.get('root_execution', {}),
                              'status plan_id run_id started_at finished_at budget_at_consent ci_result'),
             'preparation':pick(event.get('root_preparation', {}),
                                'status base_sha root_rules_sha256 message baseline ci_failure_binding'),
             'ci_followup':pick(event.get('root_ci_followup',{}),
                'status parent_preparation_sha256 ci_failure_binding result_status created_at finished_at'),
             'historical':True, 'truncated':False}
    # No session or evidence yet is a valid history, including interrupted setup.
    run_root = root/'r'/event_id if event.get('root_execution') else root/'events'/event_id/'runs'
    if not unlinked(run_root/'ui-state'/'registry.sqlite3').is_file():
        if event.get('plan_id') or event.get('root_execution', {}).get('plan_id'):
            value['errors'].append('原方案登记缺失，无法核验；不会创建替代登记')
    else:
        with journal(run_root) as db:
            plans = db.execute("SELECT id FROM objects WHERE kind='plan' ORDER BY rowid LIMIT 21").fetchall()
            runs = db.execute("SELECT id FROM objects WHERE kind='run' ORDER BY rowid LIMIT 21").fetchall()
            value['truncated'] = len(plans)>20 or len(runs)>20
            for (plan_id,) in plans[:20]:
                try:
                    identity(plan_id, 32)
                    item = object_at(db, 'plan', plan_id)
                    session_id = identity(item['session_id'], 32)
                    data, file_sha = read_json(run_root/'ui-sessions'/session_id/(plan_id+'.json'))
                    from .local_ui import decode_plan
                    plan = decode_plan(data)
                    if (file_sha != item['file_sha'] or plan.fingerprint != data['plan_sha256']
                            or plan.fingerprint != item['view']['fingerprint'] or plan.base_sha != event['sha']):
                        raise SafetyError('原方案字节或版本绑定变化')
                    value['plans'].append({**pick(item['view'], 'id status fingerprint diff'),
                        'plan':pick(data['plan'], 'issue diagnosis risk base_sha'), 'file_sha256':file_sha})
                except (SafetyError, OSError, ValueError, KeyError, TypeError):
                    value['errors'].append('一份原方案缺失或绑定失效；保留原记录，不允许据此审批')
            for (run_id,) in runs[:20]:
                try:
                    identity(run_id, 32)
                    entry = object_at(db, 'run', run_id)
                    if entry.get('run_id') != run_id:
                        raise SafetyError('原运行身份变化')
                    run = project_run(unlinked(run_root/run_id))
                    if run.get('base_sha') != event['sha'] or run.get('source_repo') != source:
                        raise SafetyError('原运行与事件版本不符')
                    value['runs'].append(pick(run, 'run_id status base_sha source_unchanged diff '
                        'baseline_tests candidate_tests record_sha256 recovery repair_finished'))
                except (SafetyError, OSError, ValueError, KeyError, TypeError):
                    value['errors'].append('一份原运行证据缺失或绑定失效；不能认定测试通过')
    # Bounded display of recorded data; hashes are receipts, not execution authority.
    encoded = redact(json.dumps(value, ensure_ascii=False))
    if len(encoded.encode('utf-8')) > 1024*1024:
        raise SafetyError('历史展示超过1MiB；请保留原证据并缩小到单个事件核查')
    result = json.loads(encoded)
    result['history_sha256'] = digest(encoded.encode('utf-8'))
    return result


def recovery_preview(monitor, payload):
    """Explain next steps without restoring old capabilities or replaying effects."""
    value = history(monitor, payload['monitor_id'], payload['event_id'])
    mid, eid = value['monitor_id'], value['event_id']
    event = monitor.registries[mid].load('event')[eid]
    execution = monitor.execution.current_execution(mid,event) if event.get('root_execution') else None
    record = monitor.records[mid]
    result = {'monitor_id':mid, 'event_id':eid, 'read_only':True, 'model_requests':0,
              'history_sha256':value['history_sha256'], 'budget':value['budget'],
              'can_open_original':False, 'can_check_latest':False,
              'message':'历史只供参考，旧审批不能用于新版本。'}
    if value['errors']:
        result.update(status='evidence_invalid', message='原证据缺失或失效；请先核查原现场，不能重新执行。')
    elif not record['enabled']:
        result.update(status='paused', message='监控已暂停。可继续查看历史；恢复监控后重新核验，旧批准不会恢复。')
    elif value['budget']['requests_reserved_unknown']:
        result.update(status='unknown_consumption', message='存在未核实消费。先核对原模型请求与检查点；不会清零或另建额度重试。')
    else:
        try:
            head = monitor.current_head(record['spec'])
            if not isinstance(head,str) or not re.fullmatch('[a-f0-9]{40}',head):
                raise SafetyError('当前提交无法核验')
            result['current_head'] = head
            if head != event['sha']:
                latest = monitor.registries[mid].load('event').get(digest(head.encode())[:16])
                result.update(status='new_commit', can_check_latest=True,
                    latest_event_id=latest['id'] if latest and latest['sha']==head else None,
                    message='分支已有新提交。旧方案保留为历史；检查最新提交将沿现有规则处理，不移用旧审批。')
            elif execution and execution.get('status') in {'started','interrupted_no_replay','failed_no_replay'}:
                result.update(status='needs_reconciliation', message='这次修复中断或失败。请核验原运行的进程、现场和消费；本入口不会重放请求。')
            elif event.get('root_execution'):
                monitor.execution.guard(mid,eid)
                result.update(status='original_available', can_open_original=True,
                    message='当前提交、规则与执行绑定仍匹配。进入原任务后，重启留下的方案或运行仍须重新认领和新审批。')
            elif event.get('plan_id'):
                monitor._legacy_guard(mid,event['sha'])
                result.update(status='original_available', can_open_original=True,
                    message='原固定范围任务仍匹配。进入原任务核验、认领；不继承旧批准。')
            else:
                prep = event.get('root_preparation', {})
                if prep.get('status') in {'started','baseline_started','interrupted_no_replay','blocked_no_replay'}:
                    result.update(status='needs_reconciliation', message='修改前检查中断或结果未知。请先核验原环境与执行现场；不自动重做安装或测试。')
                else:
                    config = monitor.root_rules.current(mid,required=True)
                    changed = config['sha256'] != prep.get('root_rules_sha256')
                    result.update(status='new_rules' if changed else 'current_event', can_check_latest=changed,
                        message='规则已有新版。检查当前提交将保留旧检查，按新规则重新调查；修复仍须单独确认。' if changed
                        else '当前事件已有检查记录。可在事件中预览修复；重复检查不会重放同一版本的检查或模型请求。')
        except (SafetyError, OSError, KeyError, ValueError, TypeError):
            result.update(status='binding_changed', message='当前源码、规则、模型配置或原绑定不再匹配。旧报告仍可看；请核对维护配置，不重用旧批准。')
    return result
