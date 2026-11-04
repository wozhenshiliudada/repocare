"""Explicit Issue-version successors on the original execution ledger.

This module only creates a *durable successor intent* and validates the original
parent. Execution itself stays in :class:`IssueExecution`, so the original Pi,
Native, policy, approval, environment and budget controllers remain the only
ones that can edit or test code. No Issue text grants permission here.
"""
from __future__ import annotations

import json
import re
import threading
import uuid

from .dashboard import read_json, unlinked
from .git import RepositoryGateway
from .integration import verified_publication
from .issue_intake import IssueIntake, IssueReader, fingerprint
from .maintenance_rules import require
from .models import SafetyError
from .store import now, redact

KIND = 'issue_successor'
HEAD = 'issue_successor_head'
MAX_CHAIN = 3
TERMINAL_PARENTS = {'awaiting-review', 'delivered', 'failed_no_replay'}
ACTIVE_PARENTS = {'claimed', 'running'}
DELIVERY_STATUSES = {'awaiting-review', 'remote-closed-review'}


def _identity(value, length=32):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{%s}' % length, value):
        raise SafetyError('Issue 后继编号无效')
    return value


class IssueFollowup:
    """Production adapter used by IssueWorkbench; tests may inject the service."""

    def __init__(self, registry, source, connection, spec, rules=None, service=None):
        self.registry = registry
        self.source = source if isinstance(source, RepositoryGateway) else RepositoryGateway(source)
        self.connection = connection
        self.spec = spec
        self.rules = rules
        self.service = service
        self.runs = service.runs if service is not None else unlinked(registry.root.parent)
        self.lock = threading.RLock()

    def _load(self, kind, key):
        return self.registry.load(kind).get(key)

    @staticmethod
    def _row(db, kind, key):
        row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?',
                         (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def _metadata(self):
        with self.registry.transaction() as db:
            value = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        require(value.get('repository') == str(self.source.root),
                '原账本仓库身份不同；不能另建 Issue 版本账')
        return value

    def _budget(self):
        budget = self.registry.budget()
        require(budget['requests_reserved_unknown'] == 0 and budget['requests_remaining'] > 0,
                '原预算存在未知消费或已耗尽；保留旧账，不新建额度')
        return budget

    def view(self, identity):
        _identity(identity)
        row = self._load(KIND, identity)
        require(row is not None, '未知 Issue 后继')
        return {**row, 'challenge': fingerprint(row)}

    def _run_receipt(self, parent):
        run_id = _identity(parent.get('run_id'))
        directory = unlinked(self.runs / run_id)
        record, record_sha = read_json(directory / 'run.json')
        expected = {'execution_id': parent['id'], 'issue_number': parent['issue_number'],
                    'issue_token': parent['issue_token'], 'task_id': parent.get('task_id'),
                    'rules_sha256': parent['rules_sha256'],
                    'policy_binding_sha256': parent['policy_binding']['binding_sha256']}
        require(record.get('run_id') == run_id
                and record.get('source_repo') == str(self.source.root)
                and record.get('issue_execution') == expected
                and record.get('finished_at')
                and record.get('source_unchanged') is True
                and record.get('status') in {'candidate_ready', 'integrated',
                                             'rolled_back', 'failed', 'rejected',
                                             'discarded', 'repair_aborted'},
                '原 Issue 运行报告未完整结束或反向绑定不同')
        return record, record_sha, expected

    def _delivery_receipt(self, parent):
        record, record_sha, expected = self._run_receipt(parent)
        with self.registry.transaction() as db:
            delivery = self._row(db, 'issue_delivery', parent['id'])
            closure = self._row(db, 'issue_closure',
                                parent.get('issue_closure_id') or parent['id'])
            diagnosis = self._row(db, 'issue_diagnosis', parent['investigation_id'])
        require(isinstance(delivery, dict), '前一 Issue 候选没有原交付回执；不能接续')
        require(delivery.get('status') in DELIVERY_STATUSES,
                '前一 Issue 交付状态不是已核验回执；不能接续')
        core = delivery.get('core')
        receipt = delivery.get('receipt')
        approval = delivery.get('authorization') or {}
        require(isinstance(core, dict) and isinstance(core.get('binding'), dict)
                and isinstance(core.get('payload'), dict) and isinstance(receipt, dict)
                and delivery.get('approval_sha256') == fingerprint(core)
                and approval.get('approval_sha256') == delivery['approval_sha256'],
                '原 Issue 交付缺少独立批准或批准绑定变化')
        proof = verified_publication(unlinked(self.runs / parent['run_id']))
        review = proof['review']
        binding = {**expected, 'repository': self.spec['repository'],
                   'repository_id': parent['repository_id'], 'run_id': parent['run_id'],
                   'base_sha': record['base_sha'], 'commit': review['commit'],
                   'head': review['target_branch'], 'base': self.spec['branch'],
                   'review_sha256': proof['review_sha256'],
                   'diff_sha256': review['diff_sha256'],
                   'policy_sha256': fingerprint(record['policy']),
                   'candidate_tests_sha256': fingerprint(record['candidate_tests']),
                   'sealed_tests_sha256': fingerprint(review['tests']),
                   'test_count': len(review['tests']),
                   'environment_sha256': fingerprint(record.get('environment'))}
        require(core['binding'] == binding,
                '原 Issue 交付的 Issue/运行/版本/封存范围不匹配')
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
                '原 Issue 交付回执的 PR/提交/载荷不匹配')
        if receipt.get('merged_at') is not None:
            require(receipt.get('state') == 'closed', '已合并回执必须是已关闭状态')
        closure_sha = None
        if parent['status'] == 'delivered' or parent.get('issue_closure_id'):
            require(isinstance(closure, dict) and closure.get('status') == 'closed-verified'
                    and isinstance(closure.get('receipt'), dict)
                    and isinstance(closure.get('core'), dict)
                    and isinstance(closure['core'].get('proof'), dict)
                    and isinstance(closure['core']['proof'].get('binding'), dict)
                    and isinstance(diagnosis, dict)
                    and diagnosis.get('snapshot', {}).get('token') == parent['issue_token']
                    and isinstance(diagnosis['snapshot'].get('issue'), dict),
                    '已交付 Issue 缺少原已核验关闭回执')
            issue = diagnosis['snapshot']['issue']
            closure_receipt = closure['receipt']
            closure_binding = closure['core']['proof']['binding']
            require(closure_receipt.get('state') == 'closed'
                    and closure_receipt.get('number') == parent['issue_number']
                    and closure_receipt.get('issue_id') == issue.get('id')
                    and closure_binding.get('issue_number') == parent['issue_number']
                    and closure_binding.get('repository', '').casefold()
                        == self.spec['repository'].casefold(),
                    '原关闭回执属于另一个 Issue/仓库；不能当作本版本交付')
            closure_sha = fingerprint(closure)
        return {'kind': 'delivery', 'delivery_id': parent['id'],
                'delivery_status': delivery['status'],
                'delivery_sha256': fingerprint(delivery),
                'receipt_sha256': fingerprint(receipt),
                'closure_sha256': closure_sha, 'review_sha256': proof['review_sha256'],
                'record_sha256': record_sha, 'run_id': parent['run_id']}

    def _failure_receipt(self, parent):
        record, record_sha, _ = self._run_receipt(parent)
        require(parent['status'] == 'failed_no_replay', '父执行不是安全结束的失败结果')
        require(record.get('status') in {'rolled_back', 'failed', 'rejected',
                                         'discarded', 'repair_aborted'}
                and not unlinked(self.runs / parent['run_id'] / 'workspace').exists()
                and not self.source.command('show-ref', '--verify', '--hash',
                    'refs/heads/maintenance/' + parent['run_id'], check=False),
                '原失败候选仍有工作区或分支；先按原恢复入口处置')
        with self.registry.transaction() as db:
            delivery = self._row(db, 'issue_delivery', parent['id'])
        require(delivery is None, '失败父执行却有交付登记；须先人工核对')
        return {'kind': 'failure', 'record_sha256': record_sha,
                'run_id': parent['run_id'], 'delivery_sha256': None,
                'receipt_sha256': None, 'closure_sha256': None}

    def _successors(self, issue_key):
        return [row for row in self.registry.load(KIND).values()
                if row.get('issue_key') == issue_key]

    def _preview(self, number, existing=None):
        require(type(number) is int and 1 <= number <= 9999999999, 'Issue 编号无效')
        snapshot = IssueReader(self.connection, self.spec, registry=self.registry).snapshot(number)
        require(snapshot.get('status') == 'ready' and snapshot['issue'].get('state') == 'open',
                'Issue 不是当前可信 ready 版本；已关闭 Issue 不会自动重开')
        require(self.source.head == snapshot['head_sha'], '本地目标版本与可信分支 HEAD 不同')
        key = fingerprint([snapshot['repository_id'], number])
        with self.registry.transaction() as db:
            head = self._row(db, 'issue_execution_head', key)
            issue_head = self._row(db, 'issue_head', key)
            parent = self._row(db, 'issue_execution', head.get('execution_id')) if head else None
        require(parent is not None, '没有可接续的原 Issue 执行')
        require(parent['issue_key'] == key and parent['issue_number'] == number
                and parent['repository_id'] == snapshot['repository_id'],
                '父执行与当前 Issue/仓库身份不同')
        require(parent['status'] in TERMINAL_PARENTS,
                '父执行仍活跃、已暂停或中断；先按原入口处置，不能另开新版本')
        require(parent['issue_token'] != snapshot['token'],
                'Issue 快照 token 未变化；没有新版本，不能重复接续')
        require(issue_head and issue_head.get('token') in {parent['issue_token'], snapshot['token']},
                'Issue 当前观察头已变化；先核对原账，不能跳过版本')
        manager = self._metadata()
        require(manager.get('issue_spec_sha256') in {None, fingerprint(self.spec)},
                '原账 Issue 信任配置不同')
        budget = self._budget()
        successors = self._successors(key)
        require(len(successors) <= MAX_CHAIN if existing is not None else len(successors) < MAX_CHAIN,
                'Issue 版本后继次数已用尽；保留历史')
        matches = [row for row in successors
                   if row.get('parent_execution_id') == parent['id']
                   and row.get('new_token') == snapshot['token']]
        if existing is not None:
            require(len(matches) == 1 and matches[0]['id'] == existing['id'],
                    '此 Issue 版本已有另一个后继意图')
        else:
            require(not matches, '此 Issue 版本已有后继意图；保留原记录，不重复创建')
        receipt = (self._failure_receipt(parent) if parent['status'] == 'failed_no_replay'
                   else self._delivery_receipt(parent))
        # The investigation is read-only for source/model/budget but creates the
        # original diagnosis record needed by the normal execution controller.
        result = IssueIntake(self.connection, self.registry, self.spec).investigate(
            number, self.source)
        require(result.get('status') in {'investigated', 'duplicate_no_replay'},
                '新的 Issue 版本未能形成可执行调查：' + str(result.get('status')))
        with self.registry.transaction() as db:
            diagnosis = self._row(db, 'issue_diagnosis', snapshot['token'])
            current_head = self._row(db, 'issue_head', key)
        require(isinstance(diagnosis, dict) and diagnosis.get('status') == 'investigated'
                and diagnosis.get('snapshot', {}).get('token') == snapshot['token'],
                '新 Issue 版本的调查记录缺失或已陈旧')
        require(current_head and current_head.get('token') == snapshot['token'],
                'Issue 在调查期间再次变化；保留旧版，重新开始')
        fresh = IssueReader(self.connection, self.spec, registry=self.registry).snapshot(number)
        require(fresh.get('token') == snapshot['token'] and fresh.get('status') == 'ready',
                'Issue 在确认前变化；旧预览不能授权')
        with self.registry.transaction() as db:
            now_head = self._row(db, 'issue_execution_head', key)
            now_parent = self._row(db, 'issue_execution', parent['id'])
        require(now_head and now_head.get('execution_id') == parent['id']
                and now_parent == parent,
                '父执行/执行头在预览期间变化；不能跳过旧任务')
        value = {
            'schema_version': 1, 'kind': 'issue_version_successor',
            'issue_key': key, 'issue_number': number,
            'repository': self.spec['repository'],
            'repository_id': snapshot['repository_id'],
            'parent': {'execution_id': parent['id'], 'challenge': fingerprint(parent),
                       'status': parent['status'], 'token': parent['issue_token'],
                       'run_id': parent['run_id'], 'task_id': parent.get('task_id'),
                       'report_sha256': receipt['record_sha256'],
                       'delivery': receipt},
            'snapshot': {'token': snapshot['token'], 'issue_id': snapshot['issue']['id'],
                         'head_sha': snapshot['head_sha'],
                         'updated_at': snapshot['issue']['updated_at'],
                         'raw_text_sha256': snapshot['issue']['raw_text_sha256']},
            'investigation_id': snapshot['token'],
            'diagnosis_sha256': fingerprint(diagnosis),
            'spec_sha256': fingerprint(self.spec),
            # Rules are compiled by the original environment/execution step.
            # Keeping this field constant avoids a preview/configure mismatch
            # between a read-only preview and an execute adapter with rules.
            'rules_sha256': None,
            'budget': budget, 'chain_index': len(successors) + (0 if existing is not None else 1),
            'max_chain': MAX_CHAIN, 'new_approval_required': True,
            'will_call_model': True, 'may_charge': True, 'push_or_merge': False,
            'old_task_preserved': True,
        }
        require(len(json.dumps(value, ensure_ascii=False).encode()) <= 32768,
                'Issue 后继预览超过记录上限')
        if existing is not None:
            require(existing.get('core') == value, '既有 Issue 后继意图已变化')
        return {'preview': value, 'revision': fingerprint(value)}

    def preview(self, number):
        return self._preview(number)

    def configure(self, payload):
        require(isinstance(payload, dict)
                and set(payload) == {'preview', 'revision', 'confirmed'}
                and payload['confirmed'] is True, '需明确确认 Issue 新版本后继')
        value = payload['preview']
        require(isinstance(value, dict)
                and len(json.dumps(value, ensure_ascii=False).encode()) <= 32768
                and payload['revision'] == fingerprint(value), 'Issue 后继预览已失效')
        key = value.get('issue_key')
        parent_id = (value.get('parent') or {}).get('execution_id')
        token = (value.get('snapshot') or {}).get('token')
        require(isinstance(key, str) and isinstance(parent_id, str)
                and isinstance(token, str), 'Issue 后继预览身份不完整')
        with self.registry.transaction() as db:
            old = [json.loads(body) for (body,) in db.execute(
                'SELECT body FROM objects WHERE kind=?', (KIND,)).fetchall()]
        old = [row for row in old if row.get('issue_key') == key
               and row.get('parent_execution_id') == parent_id
               and row.get('new_token') == token]
        if old:
            require(len(old) == 1 and old[0].get('core') == value,
                    '既有 Issue 后继意图不同；保留旧记录')
            return self.view(old[0]['id'])
        fresh = self._preview(value['issue_number'])
        require(fresh['preview'] == value and fresh['revision'] == payload['revision'],
                'Issue/父任务/预算/交付回执已变化，请重新预览')
        successor_id = uuid.uuid4().hex
        row = {'id': successor_id, 'revision': 0, 'status': 'prepared',
               'issue_key': key, 'issue_number': value['issue_number'],
               'repository_id': value['repository_id'],
               'parent_execution_id': parent_id,
               'parent_challenge': value['parent']['challenge'],
               'parent_token': value['parent']['token'],
               'parent_run_id': value['parent']['run_id'],
               'parent_report_sha256': value['parent']['report_sha256'],
               'delivery_sha256': value['parent']['delivery'].get('delivery_sha256'),
               'closure_sha256': value['parent']['delivery'].get('closure_sha256'),
               'new_token': token, 'new_head_sha': value['snapshot']['head_sha'],
               'investigation_id': value['investigation_id'],
               'diagnosis_sha256': value['diagnosis_sha256'],
               'chain_index': value['chain_index'], 'budget_at_consent': value['budget'],
               'core': value, 'preview_sha256': fingerprint(value),
               'created_at': now()}
        with self.registry.transaction() as db:
            issue_head = self._row(db, 'issue_head', key)
            execution_head = self._row(db, 'issue_execution_head', key)
            parent = self._row(db, 'issue_execution', parent_id)
            diagnosis = self._row(db, 'issue_diagnosis', token)
            require(issue_head and issue_head.get('token') == token
                    and execution_head and execution_head.get('execution_id') == parent_id
                    and fingerprint(parent) == value['parent']['challenge'],
                    'Issue 版本或父执行在确认时变化；保留旧意图')
            require(isinstance(diagnosis, dict) and diagnosis.get('status') == 'investigated'
                    and diagnosis.get('snapshot', {}).get('token') == token,
                    '新版本调查在确认时不可用')
            require(db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone() ==
                    tuple(value['budget'][k] for k in
                          ('request_budget', 'requests_used', 'requests_reserved_unknown')),
                    '原预算在确认时变化；请重新预览')
            require(db.execute('SELECT count(*) FROM objects WHERE kind=?', (KIND,)).fetchone()[0] < 100,
                    'Issue 后继记录达到容量')
            current = [json.loads(body) for (body,) in db.execute(
                'SELECT body FROM objects WHERE kind=?', (KIND,)).fetchall()]
            require(not any(r.get('issue_key') == key
                            and r.get('parent_execution_id') == parent_id
                            and r.get('new_token') == token for r in current),
                    '此 Issue 版本已有后继意图')
            require(sum(1 for r in current if r.get('issue_key') == key) < MAX_CHAIN,
                    'Issue 版本后继次数已用尽')
            self.registry.put(KIND, successor_id, row, db)
            self.registry.put(HEAD, key, {'id': successor_id}, db)
        return self.view(successor_id)

    def start(self, successor_id, revision, environment_plan, grant, *, approval=None,
              max_attempts=3):
        require(self.service is not None, 'Issue 后继缺少原执行控制器')
        row = self.view(successor_id)
        require(row['status'] == 'prepared' and revision == row['challenge'],
                'Issue 后继状态已变化，不能重复启动')
        fresh = self._preview(row['issue_number'], existing={**row, 'core': row['core']})
        require(fresh['preview'] == row['core'], 'Issue 后继确认后现场变化；保留旧意图')
        try:
            result = self.service.start(row['investigation_id'], environment_plan, grant,
                                        approval=approval, max_attempts=max_attempts,
                                        _successor=row)
        except BaseException as error:
            with self.registry.transaction() as db:
                current = self._row(db, KIND, successor_id)
                if current and current['status'] in {'prepared', 'claimed', 'running'}:
                    current.update(status='blocked_no_replay', error_type=type(error).__name__,
                                   error=redact(str(error))[:1000], finished_at=now(),
                                   revision=current['revision'] + 1)
                    self.registry.put(KIND, successor_id, current, db)
            raise
        status = result.get('status')
        with self.registry.transaction() as db:
            current = self._row(db, KIND, successor_id)
            require(current and current.get('core') == row['core']
                    and current.get('execution_id') == result.get('id')
                    and current.get('status') in {'claimed', 'running'},
                    'Issue 后继在启动期间变化')
            current.update(status=status if status in
                           {'awaiting-review', 'failed_no_replay', 'delivered',
                            'interrupted', 'stale'} else 'started',
                           result_status=status,
                           revision=current['revision'] + 1,
                           finished_at=now() if status in
                           {'awaiting-review', 'failed_no_replay', 'delivered',
                            'interrupted', 'stale'} else None)
            self.registry.put(KIND, successor_id, current, db)
        return {**result, 'successor': self.view(successor_id)}
