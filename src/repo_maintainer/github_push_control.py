"""Push receipts schedule the original monitor; they never grant execution authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import threading

from .github_connection import github_url
from .github_push import MAX_BODY_BYTES, PushInbox, PushInputError, normalize_push, validate_target
from .models import SafetyError, digest
from .service_readiness import _unlinked_local


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate configuration field')
        value[key] = item
    return value


def _read_local_file(path, limit):
    path = Path(path)
    # Reuse the original local-only, ancestor-first path boundary before stat.
    if not _unlinked_local(path):
        raise ValueError()
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError()
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError()
        return stream.read(limit + 1)


def load_configuration(path):
    """Read only the explicitly named local config and secret. Never echo their contents."""
    try:
        raw = _read_local_file(path, 16384)
        if len(raw) > 16384:
            raise ValueError()
        config = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(config, dict) or set(config) != {'schema_version', 'target', 'secret_file', 'port'}
                or type(config['schema_version']) is not int or config['schema_version'] != 1
                or type(config['port']) is not int or not 0 <= config['port'] <= 65535
                or not isinstance(config['secret_file'], str)):
            raise ValueError()
        target = validate_target(config['target'])
        secret = _read_local_file(config['secret_file'], 4096)
        if not 32 <= len(secret) <= 4096:
            raise ValueError()
        return target, secret, config['port']
    except (OSError, ValueError, TypeError, KeyError, SafetyError, RecursionError):
        raise SafetyError('推送配置无效；请核对明确的本地配置、原监控和独立密钥文件') from None


class GitHubPushControl:
    max_body_bytes = MAX_BODY_BYTES

    def __init__(self, hub, target, secret):
        self.hub, self.target = hub, validate_target(target)
        if not isinstance(secret, bytes) or not 32 <= len(secret) <= 4096:
            raise SafetyError('推送签名密钥必须来自明确的本地文件')
        self._secret = secret
        self.lock = threading.RLock()
        self.closed = False
        self.queued = False
        self.receiver = None
        self.error = None
        mid = self.target['monitor_id']
        with hub.monitor.lock:
            if mid not in hub.monitor.records or mid not in hub.monitor.registries:
                raise SafetyError('推送入口需要已有原监控；请先在工作台登记仓库及维护规则')
            self.registry = hub.monitor.registries[mid]
        # Open no new journal/ledger. FileLock ownership remains on the original worker.
        self.inbox = hub.executor.submit(PushInbox, self.registry, self.target).result()
        hub.executor.submit(self.inbox.recover).result()

    @classmethod
    def from_file(cls, hub, path):
        target, secret, port = load_configuration(path)
        instance = cls(hub, target, secret)
        try:
            instance.start(port)
        except BaseException:
            instance.close()
            raise
        return instance

    def start(self, port=0):
        from .github_push_http import PushReceiver
        with self.lock:
            if self.closed or self.receiver is not None:
                raise SafetyError('推送接收入口已启动或关闭')
            self.receiver = PushReceiver(self, port, max_body_bytes=MAX_BODY_BYTES)
            self.receiver.start()
        self._schedule()
        return self

    def receive(self, headers, body):
        # Durable acknowledgement must never wait for network/Git/tests/AgentSession.
        with self.lock:
            if self.closed or self.hub.closed:
                raise PushInputError('推送入口正在停止', http_status=503, code='receiver_unavailable')
            fact = normalize_push(body, headers, self.target, self._secret)
            receipt = self.inbox.accept(fact)
        if receipt.get('work_id'):
            self._schedule()
        return (200 if fact['event'] == 'ping' else 202), {'receipt': receipt}

    def _schedule(self):
        with self.lock:
            if self.closed or self.hub.closed or self.queued:
                return
            self.queued = True
        try:
            self.hub.executor.submit(self._drain)
        except RuntimeError:
            with self.lock:
                self.queued = False
                self.error = '通知已登记；原工作队列已停止，保留至下次启动核验'

    def _drain(self):
        try:
            with self.lock:
                if self.closed or self.hub.closed:
                    return
                pending = self.inbox.pending()
                work = self.inbox.begin(pending[0]['id']) if pending else None
            if work is not None:
                self._dispatch(work)
        finally:
            with self.lock:
                self.queued = False
                # One receipt per original worker turn: queued human approvals
                # and pause operations must not sit behind a whole push burst.
                again = not self.closed and not self.hub.closed and bool(self.inbox.pending())
            if again:
                self._schedule()

    def _spec(self):
        mid = self.target['monitor_id']
        with self.hub.monitor.lock:
            record = self.hub.monitor.records.get(mid)
            if (not record or self.hub.monitor.registries.get(mid) is not self.registry
                    or record['spec'].get('id') != mid):
                raise SafetyError('原推送监控身份已变化；保留通知与原记录')
            spec = dict(record['spec'])
            enabled = record['enabled'] is True
        slug, _ = github_url(spec.get('github_url'))
        if slug.casefold() != self.target['repository'].casefold() or spec['branch'] != self.target['branch']:
            raise SafetyError('原推送仓库或分支已变化；通知不能换绑执行')
        with self.registry.transaction() as db:
            row = db.execute('SELECT body FROM metadata').fetchone()
            original = json.loads(row[0], object_pairs_hook=_object) if row else {}
        if original.get('monitor') != spec:
            raise SafetyError('原监控完整登记已变化；不能借通知使用不同的检查范围')
        return spec, enabled

    def _dispatch(self, work):
        identity, mid = work['id'], self.target['monitor_id']
        try:
            self.hub.runtime.require_current()
            spec, enabled = self._spec()
            if self.closed or self.hub.closed or self.hub.monitor.stopping.is_set() or not enabled:
                self.inbox.finish(identity, 'blocked', message='原监控已暂停或服务正在停止；通知不恢复许可')
                return
            budget = self.registry.budget()
            if budget['requests_reserved_unknown'] or budget['requests_remaining'] <= 0:
                self.inbox.finish(identity, 'blocked', message='原账有未知消费或可用额度不足；不重放、不增加额度')
                return
            # The payload's repository/URL is never used as a remote destination.
            metadata = self.hub.github.api('/repos/' + self.target['repository'])
            if (not isinstance(metadata, dict) or type(metadata.get('id')) is not int
                    or metadata['id'] != self.target['repository_id'] or metadata.get('private') is not True
                    or not isinstance(metadata.get('full_name'), str)
                    or metadata['full_name'].casefold() != self.target['repository'].casefold()):
                raise SafetyError('推送仓库的远端身份或私有范围不匹配')
            sha = self.hub.monitor.current_head(spec)
            if sha != work['after']:
                self.inbox.finish(identity, 'superseded', message='通知版本已非当前分支；不把其他版本结果冒作本次通过')
                return
            _, enabled = self._spec()  # Recheck registration after network observation.
            if self.closed or self.hub.closed or self.hub.monitor.stopping.is_set() or not enabled:
                self.inbox.finish(identity, 'blocked', message='原监控在来源核验期间暂停；通知不恢复许可')
                return
            event_id = digest(sha.encode())[:16]
            existing = self.registry.load('event').get(event_id)
            if existing is not None:
                if existing.get('sha') != sha:
                    raise SafetyError('原事件身份冲突；保留现场')
                self.inbox.finish(identity, 'event_linked', event_id=event_id,
                                  message='轮询或通知已登记此版本；查看原事件，不重复检查或修复')
                return
            # Exact-version gate is in the original poll, after its own remote read.
            outcome = self.hub.monitor.poll(mid, expected_sha=sha)
            if isinstance(outcome, dict) and outcome.get('status') == 'superseded':
                self.inbox.finish(identity, 'superseded', message='派发前分支再次变化；原通知未执行新版本')
                return
            event = self.registry.load('event').get(event_id)
            if event is not None and event.get('sha') == sha:
                self.inbox.finish(identity, 'event_linked', event_id=event_id,
                                  message='已关联原检查事件；审批、候选、暂停和消费均以原控制器为准')
            else:
                self.inbox.finish(identity, 'blocked', message='原检查未形成可核验事件；保留通知，查看监控原因')
        except Exception:
            # A transport exception may include a URL/credential. Keep only a fixed local reason.
            self.inbox.finish(identity, 'failed', message='原来源核验或检查未完成；保留现场，不自动重放执行意图')

    def view(self):
        value = self.inbox.view()
        events = self.registry.load('event')
        for work in value.get('work', []):
            event = events.get(work.get('event_id'))
            if event is not None and event.get('sha') == work.get('after'):
                work['monitor_status'] = event.get('status')
                work['monitor_message'] = event.get('message', '')
            work['next_step'] = ('查看原监控事件的检查、审批和修复结果'
                                 if work['status'] == 'event_linked' else
                                 '核对原监控、版本、许可与消费；不会自动重放未知执行')
        with self.lock:
            value.update(configured=True, target=dict(self.target), accepting=not self.closed,
                         active=self.queued, error=self.error,
                         local_url=self.receiver.url if self.receiver is not None else None)
        return value

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            receiver = self.receiver
        if receiver is not None:
            receiver.close()
