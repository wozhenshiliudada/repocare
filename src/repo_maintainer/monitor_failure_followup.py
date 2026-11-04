"""Explicit bounded same-commit CI successors on the original monitor ledger."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
import re
import threading
import uuid
from urllib.parse import quote

from .ci_repair import FAILURES, failure_binding, verify_current_failure
from .dashboard import read_json, unlinked
from .git import RepositoryGateway
from .integration import verified_publication
from .issue_intake import fingerprint
from .maintenance_rules import require
from .models import SafetyError, digest
from .monitor_history import identity, journal, object_at
from .store import now

KIND = 'monitor_failure_followup'
CLAIMS = 'monitor_failure_claim'


def _utc_stamp(value, message):
    require(isinstance(value, str) and 0 < len(value) <= 40, message)
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise SafetyError(message)
    require(stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0), message)
    return stamp


def _run_identity(row, pin):
    """A listed run must still be the exact trusted repository/workflow/SHA."""
    repo = row.get('repository') if isinstance(row, dict) else None
    require(isinstance(repo, dict) and type(repo.get('id')) is int
            and repo.get('id') == pin['repository_id']
            and isinstance(repo.get('full_name'), str)
            and repo['full_name'].casefold() == pin['repository'].casefold()
            and type(row.get('workflow_id')) is int
            and row.get('workflow_id') == pin['workflow_id']
            and row.get('head_sha') == pin['head_sha'],
            '新CI列表中的来源、工作流或提交与原绑定不同')


def select_run_candidate(pin, runs):
    """Choose one provably newer failed run, or no action.

    GitHub's list order is not used as chronology. Both the original run and
    every possible new run need an exact run number and UTC creation time; a
    tie, reversal, missing field, foreign source or non-failed latest run stops
    the successor instead of guessing.
    """
    selection_key(pin)
    require(isinstance(runs, list) and len(runs) <= 30
            and all(isinstance(row, dict) for row in runs),
            '新CI运行列表不可核验')
    originals = [row for row in runs if row.get('id') == pin['run_id']]
    require(len(originals) == 1, '原CI run不在有界列表内；无法证明新run的先后次序')
    original = originals[0]
    _run_identity(original, pin)
    attempt = original.get('run_attempt')
    require(type(attempt) is int and 0 < attempt <= 2**53 - 1 and attempt >= pin['attempt'],
            '原CI run轮次无效或倒退')
    # The repository endpoint includes independent workflows. They never
    # authorize this repair, and must not make an otherwise current run stale.
    newer = [row for row in runs if row.get('id') != pin['run_id']
             and type(row.get('workflow_id')) is int and row['workflow_id']==pin['workflow_id']]
    if not newer:
        if attempt == pin['attempt']:
            return None
        return {'kind': 'same_run', 'pin': {**pin, 'attempt': attempt},
                'run': original, 'parent_run': original}

    branch = original.get('head_branch')
    require(isinstance(branch, str) and 1 <= len(branch) <= 255,
            '原CI run缺少固定分支；不能选择新run')
    parent_number = original.get('run_number')
    require(type(parent_number) is int and 0 < parent_number <= 2**53 - 1,
            '原CI run缺少run_number；不能证明新run更晚')
    parent_stamp = _utc_stamp(original.get('created_at'),
                              '原CI run缺少可比较的UTC创建时间')
    candidates=[]
    for row in newer:
        _run_identity(row, pin)
        require(row.get('head_branch') == branch, '新CI run分支与原run不同')
        require(type(row.get('run_number')) is int and 0 < row.get('run_number') <= 2**53 - 1
                and type(row.get('id')) is int and row['id'] > 0
                and type(row.get('run_attempt')) is int and 0 < row['run_attempt'] <= 2**53 - 1,
                '新CI run缺少run_number/id/attempt；不能猜先后次序')
        stamp=_utc_stamp(row.get('created_at'), '新CI run缺少可比较的UTC创建时间')
        if row['id']<pin['run_id'] and row['run_number']<parent_number and stamp<parent_stamp:
            continue  # Proven older history, not a successor or an authority.
        require(row['id']>pin['run_id'] and row['run_number']>parent_number and stamp>parent_stamp,
                'CI run先后字段矛盾或并列；不能证明后续次序')
        candidates.append(row)
    newer=candidates
    if not newer:
        return ({'kind':'same_run','pin':{**pin,'attempt':attempt},'run':original,'parent_run':original}
                if attempt>pin['attempt'] else None)
    latest_number = max(row['run_number'] for row in newer)
    latest = [row for row in newer if row['run_number'] == latest_number]
    require(len(latest) == 1, '新CI run的run_number并列；不能猜最近一次')
    chosen = latest[0]
    require(chosen['run_number'] > parent_number,
            '新CI run的run_number不大于原run；不能证明它是后续失败')
    chosen_stamp = _utc_stamp(chosen.get('created_at'), '新CI run创建时间无效')
    require(chosen_stamp > parent_stamp,
            '新CI run创建时间不晚于原run；不能证明后续次序')
    if chosen.get('status') != 'completed':
        return None
    if chosen.get('conclusion') not in FAILURES:
        return None
    return {'kind': 'new_run', 'pin': {**pin, 'run_id': chosen['id'],
            'attempt': chosen['run_attempt']}, 'run': chosen, 'parent_run': original}



def selection_key(pin):
    fields = ('repository_id','head_sha','workflow_id','run_id','attempt')
    require(isinstance(pin,dict) and all(k in pin for k in fields)
            and isinstance(pin['head_sha'],str) and re.fullmatch('[a-f0-9]{40}',pin['head_sha'])
            and all(type(pin[k]) is int and 0 < pin[k] <= 2**53-1 for k in fields if k != 'head_sha'),
            '后继准确CI选择无效')
    return fingerprint({k:pin[k] for k in fields})


def child_event_id(mid, key):
    identity(mid,16)
    require(isinstance(key,str) and re.fullmatch('[a-f0-9]{64}',key), '后继失败选择指纹无效')
    return digest(('failure-successor:'+mid+':'+key).encode())[:16]


_ORDER_FIELDS=('id','run_attempt','workflow_id','head_sha','head_branch','run_number',
               'created_at','status','conclusion','repository')


def run_order_proof(selection):
    require(selection['kind']=='new_run','只有新run需要跨run顺序回执')
    def project(run):
        value={k:deepcopy(run[k]) for k in _ORDER_FIELDS}
        value['repository']={k:value['repository'][k] for k in ('id','full_name')}
        return value
    return {'schema_version':1,'parent':project(selection['parent_run']),
            'selected':project(selection['run'])}


def verify_run_order_proof(old,pin,proof):
    require(isinstance(proof,dict) and set(proof)=={'schema_version','parent','selected'}
            and type(proof['schema_version']) is int and proof['schema_version']==1
            and all(isinstance(proof[k],dict) and set(proof[k])==set(_ORDER_FIELDS)
                    and isinstance(proof[k]['repository'],dict)
                    and set(proof[k]['repository'])=={'id','full_name'} for k in ('parent','selected')),
            '新run缺少完整、类型化的先后回执')
    selected=select_run_candidate(old,[proof['selected'],proof['parent']])
    require(selected is not None and selected['kind']=='new_run'
            and selection_key(selected['pin'])==selection_key(pin),
            '新run顺序回执与原父任务或当前选择不符')


def successor_lineage(row, key, prior=None, order=None):
    core = row['core']
    value = {'grant_id':row['id'],'selection_key':key,'parent_event_id':core['event_id'],
        'parent_run_id':core['parent']['run_id'],'parent_sha256':fingerprint(core['parent'])}
    if prior is not None:
        value['chain_parent'] = prior
    if order is not None:value['run_order_sha256']=fingerprint(order)
    return value


def verified_delivery_receipt(monitor, mid, event):
    """Read the original monitor delivery receipt. Nothing here can create one."""
    identity(mid, 16)
    require(isinstance(event, dict) and event.get('id'), '前一后继事件缺失')
    eid = identity(event['id'], 16)
    execution = event.get('root_execution') or {}
    require(execution.get('status') == 'candidate_ready', '前一后继候选尚未完整结束')
    run_id = identity(execution.get('run_id'), 32)
    registry = monitor.registries[mid]
    row = registry.load('monitor_delivery').get(run_id)
    require(isinstance(row, dict), '前一后继没有原监控交付回执；不能自动接续')
    require(row.get('status') in {'awaiting-review', 'remote-closed-review'},
            '前一后继交付状态不是已核验回执；不能自动接续')
    core = row.get('core')
    require(isinstance(core, dict) and isinstance(core.get('binding'), dict)
            and isinstance(core.get('payload'), dict), '原交付回执内容不完整')
    binding = core['binding']
    receipt = row.get('receipt')
    approval = row.get('authorization') or {}
    require(isinstance(receipt, dict)
            and row.get('approval_sha256') == fingerprint(core)
            and approval.get('approval_sha256') == row['approval_sha256'],
            '原交付缺少独立批准或批准绑定变化')
    require(binding.get('origin_kind') == 'monitor'
            and binding.get('monitor_id') == mid
            and binding.get('event_id') == eid
            and binding.get('run_id') == run_id
            and binding.get('base_sha') == event.get('sha')
            and isinstance(binding.get('repository'), str)
            and isinstance(binding.get('commit'), str)
            and isinstance(binding.get('head'), str)
            and isinstance(binding.get('review_sha256'), str),
            '原交付回执不属于此监控事件/运行/来源')
    number = receipt.get('number')
    require(type(number) is int and 0 < number <= 9999999999
            and isinstance(receipt.get('url'), str)
            and receipt['url'].casefold() ==
                f"https://github.com/{binding['repository']}/pull/{number}".casefold()
            and receipt.get('draft') is True
            and receipt.get('state') in {'open', 'closed'}
            and receipt.get('head_sha') == binding['commit']
            and receipt.get('base_sha') == binding['base_sha']
            and receipt.get('payload_sha256') == fingerprint(core['payload'])
            and isinstance(receipt.get('response_sha256'), str)
            and re.fullmatch('[a-f0-9]{64}', receipt['response_sha256']),
            '原交付回执的PR/仓/提交/载荷不匹配')
    if receipt.get('merged_at') is not None:
        require(receipt.get('state') == 'closed', '已合并回执必须是已关闭状态')
    root = monitor.root / mid / 'r' / eid
    record, record_sha = read_json(unlinked(root / run_id / 'run.json'))
    require(record.get('run_id') == run_id
            and record.get('source_repo') == str(unlinked(monitor.root / mid / 'events' / eid / 'source'))
            and record.get('base_sha') == event.get('sha')
            and record.get('finished_at')
            and record.get('status') in {'candidate_ready','integrated'}
            and record.get('source_unchanged') is True,
            '原交付候选报告已变化；不能据此接续')
    proof = verified_publication(unlinked(root / run_id))
    review = proof['review']
    require(proof['review_sha256'] == binding['review_sha256']
            and review.get('commit') == binding['commit']
            and review.get('target_branch') == binding['head']
            and read_json(unlinked(root / run_id / 'run.json'))[1] == record_sha,
            '原封存发布已变化；不能把旧交付当新回执')
    return {'event_id':eid, 'run_id':run_id, 'delivery_id':run_id,
            'delivery_sha256':fingerprint(row), 'receipt_sha256':fingerprint(receipt),
            'review_sha256':proof['review_sha256'], 'record_sha256':record_sha}



def failure_signature(diagnosis):
    """Hash recorded diagnostic fields/bytes, excluding attempt and Job IDs."""
    evidence = diagnosis['ci_evidence']
    return fingerprint({'workflow_id': evidence['run']['workflow_id'],
        'steps': [{k:s.get(k) for k in ('number', 'name')} for s in evidence['failed_steps']],
        'error': re.sub(r'\s+', ' ', evidence.get('log_excerpt', '')).strip()})


class MonitorFailureFollowup:
    def __init__(self, monitor):
        self.monitor, self.hub = monitor, monitor.hub
        # Dispatch is serialized before touching the durable claim CAS. A poll
        # worker or two API-triggered checks must still produce at most one child.
        self.lock = threading.RLock()

    def _parent(self, mid, eid, *, own_child=None):
        """Verify historical facts without requiring the old CI to remain latest."""
        identity(mid,16); identity(eid,16)
        require(mid in self.monitor.records, '未知监控仓库')
        registry = self.monitor.registries[mid]
        event = registry.load('event').get(eid)
        require(event and event.get('id') == eid and 'failure_successor' not in event,
                '只允许原目录修复父事件，不将后继另开授权链')
        row = event.get('root_execution', {})
        require(row.get('finished_at') and row.get('status') in
                {'candidate_ready','failed_no_replay','awaiting_approval'}, '父修复尚未完整结束')
        require(not registry.load('monitor_continuation_head').get(eid)
                and not any(c.get('event_id') == eid for c in registry.load('monitor_continuation').values()),
                '父任务已有接续记录；须先核对原链路，不能另开后继')
        require(not any(s.session.active for (owner,key),s in self.monitor.servers.items()
                        if owner == mid and key != 'root-'+str(own_child)),
                '原监控任务仍在执行，不能启动后继')
        config = self.monitor.root_rules.current(mid, required=True)
        prep = event.get('root_preparation', {})
        require(prep.get('result_sha256') == fingerprint({k:v for k,v in prep.items() if k != 'result_sha256'})
                and prep.get('status') == 'reproduced'
                and prep.get('root_rules_sha256') == config['sha256']
                and row.get('binding',{}).get('root_rules_sha256') == config['sha256']
                and event.get('root_ci_followup',{}).get('status','finished') == 'finished',
                '父任务原准备、规则或CI后续意图不可核验')
        pin = row['binding'].get('ci_failure')
        require(pin and prep.get('ci_failure_binding') == pin, '父任务缺少准确CI来源绑定')
        effective = {**self.monitor.records[mid]['spec'],'maintenance_roots':config['settings'],
                     'root_rules_sha256':config['sha256']}
        historical_event = {**event,'ci_failures':[{'id':pin['run_id'],'attempt':pin['attempt']}], 'ci_error':None}
        require(failure_binding(prep['ci_diagnosis'],effective,historical_event) == pin,
                '父CI原诊断与固定绑定不同')
        source = RepositoryGateway(unlinked(self.monitor.root/mid/'events'/eid/'source'))
        source.require_clean(source.snapshot())
        require(source.head == event['sha'] == pin['head_sha'], '父任务源码或准确提交变化')
        root = unlinked(self.monitor.root/mid/'r'/eid)
        with journal(root) as db:
            meta = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
            require(meta.get('repository') == str(source.root)
                    and meta.get('shared_budget') == str(registry.path)
                    and meta.get('target_environment',{}).get('controller') == row['binding'],
                    '父运行原账、环境或控制绑定不同')
            rid = row.get('run_id')
            if not rid:
                plan = object_at(db,'plan',identity(row.get('plan_id'),32))
                require(plan['view']['status'] == 'consumed', '父方案尚未批准执行，不能新建后继')
                rid = plan['view']['run_id']
            identity(rid,32)
            registered = object_at(db,'run',rid)
            require(registered.get('run_id') == rid and not registered.get('resolution'), '父运行登记不可核验')
            native = object_at(db,'pi_task_link','run:'+rid)
        record, record_sha = read_json(unlinked(root/rid/'run.json'))
        execution, execution_sha = read_json(unlinked(root/rid/'repair-execution.json'))
        require(record.get('run_id') == rid and record.get('source_repo') == str(source.root)
                and record.get('base_sha') == event['sha'] and record.get('finished_at')
                and record.get('source_unchanged') is True and record.get('policy') == meta['policy']
                and execution.get('phase') in {'finished','aborted'} and execution.get('inflight') is None,
                '父报告尚未完整结束或来源不同；保留原现场')
        from .monitor_delivery import MonitorDelivery
        from .models import Policy
        p = record['policy']
        policy = Policy(tuple(p['allowed_paths']),tuple(tuple(c) for c in p['allowed_tests']),
                        p['timeout_seconds'],tuple(p['readonly_paths']))
        delivery = MonitorDelivery(self.monitor,mid,eid)
        # Child state may already hold its own session lock. Never take that
        # lock here: readers serialize monitor -> exact parent session -> Native.
        with self.monitor.lock:
            parent_server = self.monitor.servers.get((mid,'root-'+eid))
            parent_lock = parent_server.session.lock if parent_server else nullcontext()
            with parent_lock:
                native_pin = delivery._native(source,policy,record,native,meta)
        require(native_pin is not None, '父Pi Native关联缺失；不能另造旧结果')
        baseline_id = identity(prep['baseline']['run_id'],32)
        _baseline, baseline_sha = read_json(self.monitor.root/mid/'baselines'/baseline_id/'run.json')
        require(row['binding'].get('monitor_id') == mid and row['binding'].get('event_id') == eid
                and row['binding'].get('base_sha') == event['sha']
                and row['binding'].get('preparation_sha256') == prep['result_sha256']
                and row['binding'].get('baseline_sha256') == baseline_sha
                and row['binding'].get('policy_sha256') == fingerprint(meta['policy'])
                and _baseline.get('run_id') == baseline_id and _baseline.get('status') == 'baseline_ready'
                and _baseline.get('source_unchanged') is True and _baseline.get('cleanup') == 'candidate_removed',
                '父原基线、权限与执行绑定不一致')
        return {'run_id':rid,'head_sha':event['sha'],'workflow_id':pin['workflow_id'],
            'ci_failure':pin,'execution_sha256':fingerprint(row),'preparation_sha256':fingerprint(prep),
            'run_record_sha256':record_sha,'repair_execution_sha256':execution_sha,
            'baseline_sha256':baseline_sha,'native':native_pin,
            'failure_signature':failure_signature(prep['ci_diagnosis'])}

    def _checked(self, row):
        identity(row['id'],32);identity(row['core']['event_id'],16)
        require(row.get('core_sha256') == fingerprint(row['core'])
                and row['core']['monitor_id'] in self.monitor.registries
                and type(row.get('enabled')) is bool and isinstance(row.get('children'),list)
                and len(row['children']) <= row['core']['max_successors'], '后继授权或历史记录变化')
        return row

    def _chain_tail(self, mid, row):
        """Return the verified delivery receipt of the current chain tail."""
        claims = self._claims(mid, row)
        if not claims:
            return None
        require(len(claims) == len(row['children']),
                '后继授权与原claim数量不一致；保留历史，不自动接续')
        require(all(isinstance(c, dict) and c.get('status') == 'finished'
                    for c in claims), '已有未结清或中断的后继，不重放')
        events = self.monitor.registries[mid].load('event')
        prior = None
        for reference in row['children']:
            require(isinstance(reference, dict), '后继child引用无效')
            child = events.get(reference.get('event_id'))
            require(isinstance(child, dict)
                    and child.get('failure_successor', {}).get('selection_key')
                        == reference.get('selection_key'),
                    '前一后继child事件缺失或绑定变化')
            prior = verified_delivery_receipt(self.monitor, mid, child)
        return prior

    def _bound_claim(self, mid, row, event):
        """Reconstruct all derived links from the authorization and exact CI key."""
        self._checked(row);core = row['core'];link = event.get('failure_successor')
        require(core['monitor_id'] == mid and isinstance(link,dict), '后继授权或事件归属不同')
        claim = self.monitor.registries[mid].load(CLAIMS).get(link.get('selection_key'))
        require(isinstance(claim,dict) and isinstance(claim.get('ci_failure'),dict), '原后继选择意图缺失')
        pin = claim['ci_failure'];key = selection_key(pin);eid = child_event_id(mid,key)
        references = [c for c in row['children'] if isinstance(c,dict)
                      and (c.get('selection_key') == key or c.get('event_id') == eid)]
        require(len(references) == 1, '后继授权未唯一引用此child')
        index = row['children'].index(references[0])
        prior = None
        if index:
            previous = row['children'][index-1]
            child = self.monitor.registries[mid].load('event').get(previous['event_id'])
            require(isinstance(child, dict)
                    and child.get('failure_successor', {}).get('selection_key')
                        == previous.get('selection_key'),
                    '链尾前一child事件缺失或绑定变化')
            prior = verified_delivery_receipt(self.monitor, mid, child)
        parent=core['parent'];old=parent['ci_failure'];order=claim.get('run_order')
        if pin['run_id']!=old['run_id']:
            verify_run_order_proof(old,pin,order)
        else:
            require(order is None,'原同run后继不能借用跨run回执')
        expected = successor_lineage(row,key,prior,order)
        require(claim.get('id') == key == link.get('selection_key')
                and claim.get('event_id') == eid == event.get('id')
                and claim.get('grant_id') == row['id'] and claim.get('lineage') == expected == link
                and references == [{'event_id':eid,'selection_key':key}]
                and claim.get('status') in {'started','finished'}
                and event.get('sha') == parent['head_sha'] == old['head_sha'] == pin['head_sha']
                and all(pin.get(k) == old.get(k) for k in ('repository','repository_id','workflow_id'))
                and pin['workflow_id'] == parent['workflow_id']
                and (pin['run_id'] != old['run_id'] or pin['attempt'] > old['attempt'])
                and event.get('model_observation') == {k:core['model_authorization'][k] for k in ('id','core_sha256')},
                '后继准确选择、父来源、派生身份或唯一引用变化')

        prep_pin = event.get('root_preparation',{}).get('ci_failure_binding')
        execution_pin = event.get('root_execution',{}).get('binding',{}).get('ci_failure')
        require((prep_pin is None or prep_pin == pin) and (execution_pin is None or execution_pin == pin),
                '后继准备或执行与原准确选择不同')
        return claim

    def _view(self, row):
        row = deepcopy(self._checked(row))
        row['revision'] = fingerprint(row)
        row['active'] = row['enabled'] and row['status'] == 'active'
        row['effective_status'] = row['status']
        if row['active']:
            model = self.monitor.model_authorization.view(row['core']['monitor_id'])
            if self.monitor.model_authorization.clock() >= row['core']['expires_epoch']:
                row.update(active=False,effective_status='expired')
            elif not model or not model['active'] or model['id'] != row['core']['model_authorization']['id']:
                row.update(active=False,effective_status='model_authorization_inactive')
        return row

    def state(self, mid):
        identity(mid,16)
        require(mid in self.monitor.registries,'未知监控仓库')
        rows = self.monitor.registries[mid].load(KIND)
        require(len(rows) <= 20, '后继授权达到容量；保留历史')
        return [self._view(r) for r in rows.values()]

    def preview(self, payload):
        require(set(payload) == {'monitor_id','event_id','max_successors','expires_at'}, '后继预览字段不匹配')
        mid,eid = payload['monitor_id'],payload['event_id']
        self.hub.runtime.require_current()
        parent = self._parent(mid,eid)
        monitor = self.monitor
        monitor.guard(mid,parent['head_sha'])
        grant = monitor.model_authorization.view(mid)
        require(grant and grant['active'], '需要已有有效的原模型许可和原剩余额度')
        event = monitor.registries[mid].load('event')[eid]
        pin = {'id':grant['id'],'core_sha256':grant['core_sha256']}
        monitor.model_authorization.authorize(mid,event,pin)
        require(event['root_execution'].get('model_authorization') == pin, '父修复不属于当前原模型许可')
        count = payload['max_successors']
        require(type(count) is int and 1 <= count <= 3, '后继上限为1至3次')
        try:
            require(isinstance(payload['expires_at'],str) and len(payload['expires_at']) <= 32, '到期时间无效')
            expires = datetime.fromisoformat(payload['expires_at'].replace('Z','+00:00'))
            require(expires.tzinfo is not None and expires.utcoffset().total_seconds() == 0, '到期时间须为UTC')
            epoch = int(expires.timestamp())
        except (ValueError,TypeError):
            raise SafetyError('后继到期时间无效')
        require(monitor.model_authorization.clock()+60 <= epoch <= grant['core']['expires_epoch'],
                '后继须至少1分钟后到期，且不晚于原模型许可')
        budget = monitor.registries[mid].budget()
        require(not budget['requests_reserved_unknown'] and budget['requests_remaining'] > 0, '原消费未知或额度耗尽')
        require(not any(r['core']['event_id'] == eid for r in monitor.registries[mid].load(KIND).values()),
                '此父任务已有后继授权；保留历史，不重新开放次数')
        core = {'schema_version':1,'monitor_id':mid,'event_id':eid,'parent':parent,
            'binding':grant['core']['binding'],'model_authorization':{**pin,'expires_epoch':grant['core']['expires_epoch']},
            'max_successors':count,'max_attempts':grant['core']['max_attempts'],
            'expires_at':datetime.fromtimestamp(epoch,timezone.utc).isoformat(),'expires_epoch':epoch,
            'budget':budget,'same_sha_only':True,'will_call_model':True,'may_charge':True,'push_or_merge':False}
        return {'preview':core,'revision':monitor.root_rules.challenge(core)}

    def configure(self, payload):
        require(set(payload) == {'preview','revision','confirmed'} and payload['confirmed'] is True,
                '需明确确认新CI失败后继、原费用许可与次数')
        value = payload['preview']
        require(isinstance(value,dict) and len(json.dumps(value).encode()) <= 32768
                and payload['revision'] == self.monitor.root_rules.challenge(value), '后继预览已失效')
        fresh = self.preview({k:value[k] for k in ('monitor_id','event_id','max_successors','expires_at')})
        require(fresh['preview'] == value, '父任务、许可或消费已变化，请重新预览')
        row = {'id':uuid.uuid4().hex,'core':fresh['preview'],'core_sha256':fingerprint(fresh['preview']),
            'status':'active','enabled':True,'version':0,'children':[],'error':None,'created_at':now()}
        registry = self.monitor.registries[value['monitor_id']]
        with registry.transaction() as db:
            rows = db.execute('SELECT body FROM objects WHERE kind=?',(KIND,)).fetchall()
            require(len(rows) < 20 and not any(json.loads(r[0])['core']['event_id'] == value['event_id'] for r in rows),
                    '父任务已有后继授权或达到容量')
            require(db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone() ==
                    tuple(value['budget'][k] for k in ('request_budget','requests_used','requests_reserved_unknown')),
                    '原消费已变化，请重新预览')
            registry.put(KIND,row['id'],row,db)
        return self._view(row)

    def restore(self, mid):
        registry = self.monitor.registries[mid]
        for key,row in registry.load(KIND).items():
            self._checked(row)
            require(row['id'] == key and row['core']['monitor_id'] == mid, '后继授权归属不同')
            if row['enabled']:
                row.update(enabled=False,status='restart_paused',version=row['version']+1,error='服务重启，须明确恢复；中断意图不重放')
                registry.put(KIND,key,row)
        for key,claim in registry.load(CLAIMS).items():
            if claim['status'] == 'started':
                claim.update(status='interrupted_no_replay',finished_at=now())
                registry.put(CLAIMS,key,claim)

    def control(self, action, payload):
        require(action in {'monitor_failure_pause','monitor_failure_resume'}
                and set(payload) == {'monitor_id','grant_id','revision'}, '后继控制字段不匹配')
        mid = identity(payload['monitor_id'],16); registry = self.monitor.registries[mid]
        row = registry.load(KIND).get(payload['grant_id'])
        require(row and fingerprint(self._checked(row)) == payload['revision'], '后继状态已变化，请刷新')
        if action.endswith('resume'):
            require(not row['enabled'] and row['status'] in {'paused','restart_paused'}, '本授权已停止，不能重新开放后继次数')
            self._allowed(mid,row,enabled=False)
            require(all(c['status'] == 'finished' for c in self._claims(mid,row)), '存在未结清后继，不重放')
            row.update(enabled=True,status='active',error=None)
        else:
            require(row['status'] in {'active','paused','restart_paused'}, '已停止的后继授权不能通过暂停重新开放')
            row.update(enabled=False,status='paused')
        row.update(version=row['version']+1,changed_at=now())
        with registry.transaction() as db:
            old = object_at(db,KIND,row['id'])
            require(fingerprint(old) == payload['revision'], '后继控制版本变化')
            registry.put(KIND,row['id'],row,db)
        if action.endswith('pause'):
            for (owner,eid),server in list(self.monitor.servers.items()):
                if owner == mid and any(eid == 'root-'+c['event_id'] for c in self._claims(mid,row)):
                    self.monitor.model_authorization.stop_job(server.session)
        return self._view(row)

    def _claims(self, mid, row):
        claims = self.monitor.registries[mid].load(CLAIMS)
        return [c for c in claims.values() if c['grant_id'] == row['id']]

    def _allowed(self, mid, row, *, enabled=True, execution=False, child_id=None):
        self._checked(row); core = row['core']
        current = self.monitor.registries[mid].load(KIND).get(row['id'])
        require(current and current['core'] == core and current['core_sha256'] == row['core_sha256']
                and (not enabled or current['enabled'] and current['status'] == 'active'),
                '新失败后继控制已暂停或变化')
        require(core['monitor_id'] == mid and (not enabled or row['enabled'] and row['status'] == 'active'),
                '新失败后继已暂停或停止')
        self.hub.runtime.require_current()
        self.monitor.guard(mid,core['parent']['head_sha'])
        grant = self.monitor.model_authorization.view(mid)
        require(grant and (grant['active'] or execution and grant['effective_status'] in {'unknown_consumption','exhausted'})
                and grant['id'] == core['model_authorization']['id']
                and grant['core_sha256'] == core['model_authorization']['core_sha256']
                and grant['core']['binding'] == core['binding']
                and grant['issued_epoch'] <= self.monitor.model_authorization.clock() < core['expires_epoch'],
                '原模型许可已暂停、到期、变化或消费未知')
        parent = self._parent(mid,core['event_id'],own_child=child_id)
        require(parent == core['parent'], '父报告、Native、原规则或固定来源变化；保留原结果')
        # Use the narrower deadline in the original cooperative stop controller.
        return {**grant,'core':{**grant['core'],'expires_epoch':min(
            grant['core']['expires_epoch'],row['core']['expires_epoch'])}}

    def authorize_child(self, mid, event, pin, *, execution=False):
        link = event.get('failure_successor')
        require(isinstance(link,dict), '后继来源关联缺失')
        registry = self.monitor.registries[mid]
        row = registry.load(KIND).get(link.get('grant_id'))
        require(row and pin == {k:row['core']['model_authorization'][k] for k in ('id','core_sha256')}, '后继模型许可不同')
        claim = self._bound_claim(mid,row,event)
        grant = self._allowed(mid,row,execution=execution,child_id=event['id'])
        verify_current_failure(self.hub.github,self.monitor.records[mid]['spec'],claim['ci_failure'])
        return grant

    def validate_event(self, mid, event):
        link = event.get('failure_successor')
        require(link, '内部后继来源关联缺失')
        row = self.monitor.registries[mid].load(KIND).get(link.get('grant_id'))
        require(row, '原后继授权缺失')
        self._bound_claim(mid,row,event)
        execution = event.get('root_execution',{})
        if execution and execution.get('status') in {'candidate_ready','awaiting_approval'}:
            require(self._parent(mid,row['core']['event_id'],own_child=event['id']) == row['core']['parent'],
                    '已结束后继的原来源关联变化')
            return None
        pin = {k:row['core']['model_authorization'][k] for k in ('id','core_sha256')}
        return self.authorize_child(mid,event,pin,execution=bool(event.get('root_execution')))

    def dispatch(self, mid, sha):
        identity(mid, 16)
        with self.lock:
            return self._dispatch_one(mid, sha)

    def _dispatch_one(self, mid, sha):
        registry = self.monitor.registries[mid]
        for row in list(registry.load(KIND).values()):
            if not row['enabled'] or row['status'] != 'active' or row['core']['parent']['head_sha'] != sha:continue
            claim = None; claimed = False
            try:
                self._allowed(mid,row)
                claims = self._claims(mid,row)
                require(len(claims) < row['core']['max_successors'], '后继次数已用尽，保留历史')
                # A prior child is continued only when its original delivery
                # receipt is still verifiable. An unpublished candidate blocks.
                spec = self.monitor.records[mid]['spec']; old = row['core']['parent']['ci_failure']
                path = '/repos/'+old['repository']+'/actions/runs?branch='+quote(spec['branch'],safe='')+'&head_sha='+sha+'&per_page=30'
                data = self.hub.github.api(path); self._allowed(mid,row)
                runs = data.get('workflow_runs') if isinstance(data,dict) else None
                require(isinstance(runs,list) and len(runs) <= 30 and all(isinstance(r,dict) for r in runs), '新CI列表不可核验')
                selected = select_run_candidate(old, runs)
                if selected is None:continue
                candidate = selected['pin']
                verify_current_failure(self.hub.github,spec,candidate)
                key = selection_key(candidate)
                if key in registry.load(CLAIMS):continue
                # A poll of the already claimed run is a no-op, even while its
                # PR awaits review. Only a genuinely new selection needs a
                # verifiable previous handoff; never disable a grant on refresh.
                prior = self._chain_tail(mid,row)
                if claims:
                    require(prior is not None, '已有后继候选尚未交付；不能自动接续')
                eid = child_event_id(mid,key)
                order=run_order_proof(selected) if selected['kind']=='new_run' else None
                lineage = successor_lineage(row,key,prior,order)
                run = selected['run']
                claim = {'id':key,'grant_id':row['id'],'event_id':eid,'lineage':lineage,
                    'ci_failure':candidate,'status':'started','created_at':now()}
                if order is not None:claim['run_order']=order
                event = {'id':eid,'sha':sha,'created_at':now(),'tests':[],'plan_id':None,
                    'status':'checking','ci_failures':[{'id':candidate['run_id'],'attempt':candidate['attempt'],
                        'name':str(run.get('name','CI'))[:200],'conclusion':run['conclusion']}],
                    'ci_error':None,'failure_successor':lineage,
                    'model_observation':{k:row['core']['model_authorization'][k] for k in ('id','core_sha256')}}
                with registry.transaction() as db:
                    current = object_at(db,KIND,row['id'])
                    require(current == row and db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(CLAIMS,key)).fetchone() is None,
                            '后继已被认领或授权变化')
                    require(db.execute("SELECT count(*) FROM objects WHERE kind='event'").fetchone()[0] < 100, '事件容量已满')
                    require(db.execute("SELECT body FROM objects WHERE kind='event' AND id=?",(eid,)).fetchone() is None, '后继事件身份冲突')
                    registry.put(CLAIMS,key,claim,db);registry.put('event',eid,event,db)
                    row['children'].append({'event_id':eid,'selection_key':key});row['version'] += 1
                    registry.put(KIND,row['id'],row,db)
                claimed = True
                self.validate_event(mid,event)
                source = unlinked(self.monitor.root/mid/'events'/eid/'source')
                self.hub.github.snapshot(spec['github_url'],spec['branch'],sha,source)
                from .ci_evidence import CiEvidenceReader
                from .ci_http import CiHttpTransport
                from .ci_investigation import investigate_ci
                reader = CiEvidenceReader(CiHttpTransport(self.hub.github,policy=self.hub.ci_download_policy),synthetic=False)
                config = self.monitor.root_rules.current(mid,required=True)
                effective = {**spec,'maintenance_roots':config['settings'],'root_rules_sha256':config['sha256']}
                diagnosis = investigate_ci(RepositoryGateway(source),effective,event,reader,lambda:self.validate_event(mid,event),
                                           baseline_reader=getattr(self.hub,'ci_baseline',None))
                current_pin = failure_binding(diagnosis,effective,event)
                verify_current_failure(self.hub.github,spec,current_pin)
                signature = failure_signature(diagnosis)
                require(signature != row['core']['parent']['failure_signature'] and not any(c.get('failure_signature') == signature for c in claims),
                        '新轮次失败步骤和报错没有进展；停止自动接续')
                claim.update(ci_failure=current_pin,failure_signature=signature,diagnosis_sha256=fingerprint(diagnosis))
                registry.put(CLAIMS,key,claim)
                self.monitor._root_poll(mid,sha,event['ci_failures'],None,config,event_id=eid,ci_diagnosis=diagnosis)
                child = registry.load('event')[eid]
                require(child.get('root_preparation',{}).get('status') == 'reproduced', '新失败未形成真实红基线；停止后继')
                self.monitor.model_authorization.dispatch(mid,sha,event_id=eid)
                child = registry.load('event')[eid]
                require(child.get('root_execution',{}).get('status') in {'candidate_ready','awaiting_approval'}, '后继修复未完成；保留意图，不重放')
                claim.update(status='finished',finished_at=now());registry.put(CLAIMS,key,claim)
                with registry.transaction() as db:
                    current = object_at(db,KIND,row['id'])
                    if current['enabled'] and len(current['children']) >= current['core']['max_successors']:
                        current.update(enabled=False,status='exhausted',error='后继次数已用尽',version=current['version']+1)
                        registry.put(KIND,row['id'],current,db)
            except Exception as error:
                if claimed:
                    claim.update(status='blocked_no_replay',error_type=type(error).__name__,finished_at=now())
                    registry.put(CLAIMS,claim['id'],claim)
                    child = registry.load('event').get(claim['event_id'])
                    if child:
                        child.update(status='blocked',message=str(error) if isinstance(error,SafetyError) else '新失败后继受阻，保留意图，不重放')
                        self.monitor._event(mid,child)
                with registry.transaction() as db:
                    latest = object_at(db,KIND,row['id'])
                    if latest['enabled']:
                        latest.update(enabled=False,status='blocked',error=str(error) if isinstance(error,SafetyError) else '新失败后继受阻，保留历史',version=latest['version']+1)
                        registry.put(KIND,row['id'],latest,db)
