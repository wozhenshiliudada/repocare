"""Opt-in branch observation, bounded diagnosis and the existing approval UI.

Serial dispatch belongs to RepositoryWorkbench. Journal intent precedes effects;
interrupted events are visible and never replayed. Every commit shares one budget.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .dashboard import unlinked
from .git import RepositoryGateway
from .github_connection import SnapshotError, branch_name, fetch_snapshot, github_url
from .local_ui import UISession
from .models import Policy, SafetyError, digest
from .monitor_preparation import REPAIR_SCOPE_BLOCKED
from .pi_provider import PiProvider
from .runner import run_test
from .store import now, redact
from .ui_registry import UIRegistry, provider_binding


class MaintenanceMonitor:
    def __init__(self, hub):
        self.hub = hub
        self.root = unlinked(hub.root / "monitors")
        self.root.mkdir(exist_ok=True)
        self.records, self.registries, self.servers = {}, {}, {}
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.queued = False
        self.next_due = {}
        from .monitor_rules import MonitorRootRules
        self.root_rules = MonitorRootRules(self)
        from .monitor_execution import MonitorExecution
        self.execution = MonitorExecution(self)
        from .monitor_continuation import MonitorContinuation
        self.continuation = MonitorContinuation(self)
        from .monitor_model_authorization import MonitorModelAuthorization
        self.model_authorization = MonitorModelAuthorization(self)
        from .acceptance_watch import AcceptanceWatch
        self.acceptance_watch = AcceptanceWatch(self)
        from .monitor_failure_followup import MonitorFailureFollowup
        self.failure_followup = MonitorFailureFollowup(self)
        from .monitor_validation import MonitorValidation
        self.validation = MonitorValidation(self)
        try:
            self.hub.executor.submit(self._restore).result()
        except BaseException:
            self.hub.executor.submit(self.close).result()
            raise
        self.thread = threading.Thread(target=self._timer, name="maintenance-observer", daemon=True)
        self.thread.start()

    def _provider(self, policy):
        settings = getattr(self.hub, 'provider_settings', None)
        if settings is not None:
            settings.require_ready()
        provider = self.hub.provider_factory(policy) if self.hub.provider_factory else PiProvider(
            policy.allowed_paths, readonly_paths=policy.readonly_paths, max_requests=self.hub.request_budget)
        # Explicit test factories may use Pi's existing loopback replay transport.
        if not getattr(provider, "mock_url", None):
            provider.config_file = self.hub.provider_config
        return provider

    def _restore(self):
        for directory in self.root.iterdir():
            if not re.fullmatch(r"[a-f0-9]{16}", directory.name):
                continue
            directory = unlinked(directory)
            spec = unlinked(directory / "spec.json")
            if not spec.is_file() or spec.stat().st_size > 16384:
                raise SafetyError("监控授权记录缺失；不能新建额度")
            specification = json.loads(spec.read_text(encoding="utf-8"))
            if not unlinked(directory / "ui-state" / "registry.sqlite3").is_file():
                raise SafetyError("监控额度日志缺失；不能重建或清零，请保留现场")
            self._open(directory.name, specification)
            self.root_rules.current(directory.name, required=specification.get('schema_version') == 2)
        if len(self.records) > 10:
            raise SafetyError("首版最多10个监控仓库")

    def _open(self, identity, spec):
        if spec.get("schema_version") not in {1, 2} or spec.get("id") != identity:
            raise SafetyError("监控授权记录无效")
        policy = Policy(tuple(spec["allowed_paths"]), tuple(tuple(c) for c in spec["allowed_tests"]),
                        spec["timeout_seconds"], tuple(spec["readonly_paths"]))
        if spec['schema_version'] == 1:
            policy.validate_scope()
        elif any((spec['allowed_paths'], spec['readonly_paths'], spec['allowed_tests'])) or spec.get('requires_root_rules') is not True:
            raise SafetyError('目录监控注册无效；空Policy只用于原账本提供方绑定，不是执行权限')
        provider = self._provider(policy)
        registry = UIRegistry(self.root / identity, {"monitor": spec, "provider": provider_binding(provider)})
        self.registries[identity] = registry
        record = registry.load("monitor").get(identity)
        if record is None:
            record = {"id": identity, "spec": spec, "enabled": False, "latest_sha": None,
                      "last_checked": None, "error": None, "revision": 0}
        with self.lock:
            self.records[identity] = record
        self.model_authorization.restore(identity)
        self.acceptance_watch.restore(identity)
        self.failure_followup.restore(identity)
        for key,row in registry.load('monitor_continuation').items():
            if row['status']=='started':
                row.update(status='interrupted_no_replay',interrupted_at=now())
                registry.put('monitor_continuation',key,row)
        for event_id, event in registry.load("event").items():
            intake=registry.load('monitor_validation_request').get(event_id)
            if intake:
                from .validation_proposal import clarification_leaf
                proposal_id,_=clarification_leaf(registry,intake['proposal_id'])
                proposal=registry.load('validation_proposal').get(proposal_id)
                if proposal and proposal.get('status')=='started':
                    proposal.update(status='interrupted_no_replay',interrupted_at=now())
                    registry.put('validation_proposal',proposal_id,proposal)
                    event.update(status='blocked',message='上次案例提议中断；核对原结果与消费，不重放模型')
                    registry.put('event',event_id,event)
                resume=registry.load('monitor_validation_resume').get(event_id)
                if resume and resume.get('status')=='started':
                    resume.update(status='interrupted_no_replay',interrupted_at=now())
                    registry.put('monitor_validation_resume',event_id,resume)
                    event.update(status='blocked',message='上次案例准备中断；保留现场，不重跑原基线')
                    registry.put('event',event_id,event)
            if event.get('root_ci_followup',{}).get('status')=='started':
                event['root_ci_followup']['status']='interrupted_no_replay'
                event.update(status='blocked',message='CI后续复现中断；保留原基线和意图，不自动重放')
                registry.put('event',event_id,event)
            if event.get('root_execution', {}).get('status') == 'started':
                event['root_execution']['status'] = 'interrupted_no_replay'
                event.update(status='blocked',message='上次修复中断；保留原任务与消费，不重放')
                registry.put('event',event_id,event)
            if event.get('root_late_ci', {}).get('status') == 'started':
                event['root_late_ci']['status'] = 'interrupted_no_replay'
                registry.put('event', event_id, event)
            if event.get('root_preparation', {}).get('status') in {'started','baseline_started'}:
                event['root_preparation']['status'] = 'interrupted_no_replay'
                event.update(status='blocked', message='上次目录维护检查中断；保留基线现场，不自动重放')
                registry.put('event', event_id, event)
            if event.get('ci_preparation', {}).get('status') == 'started':
                event['ci_preparation']['status'] = 'interrupted_no_replay'
                registry.put('event', event_id, event)
            if event.get('ci_diagnosis', {}).get('status') == 'started':
                event['ci_diagnosis']['status'] = 'interrupted_no_replay'
                registry.put('event', event_id, event)
            if event["status"] in {"checking", "diagnosing"}:
                event.update(status="interrupted", message="上次操作中断；不会重放测试或模型请求", notification=uuid.uuid4().hex)
                registry.put("event", event_id, event)
        # Previous approval sessions/capabilities are never reconstructed implicitly.
        self.next_due[identity] = time.monotonic() + spec["interval_seconds"]
        return record

    def _save(self, record):
        with self.lock:
            record["revision"] += 1
            self.registries[record["id"]].put("monitor", record["id"], record)

    def _event(self, identity, event):
        with self.lock:
            self.registries[identity].put("event", event["id"], event)

    def view(self):
        with self.lock:
            records = [(identity,dict(record),self.registries[identity]) for identity,record in self.records.items()]
            servers = dict(self.servers)
            watches = [((mid,wid),dict(row)) for (mid,wid),row in self.acceptance_watch.records.items()]
        result = []
        for identity, record, registry in records:
            events = list(registry.load("event").values())
            roots = self.root_rules.current(identity)
            heads = registry.load('monitor_continuation_head')
            continuations = registry.load('monitor_continuation')
            for event in events:
                validation=self.validation.projection(identity,event)
                if validation:event['validation']=validation
                event['can_propose_validation']=bool(roots and not event.get('root_execution')
                    and not event.get('model_dispatch') and not event.get('root_ci_followup')
                    and not event.get('failure_successor') and not event.get('root_preparation_historical')
                    and event.get('root_preparation',{}).get('status') in
                    {'validation_needed','baseline_passes','reproduced','execution_consent_required'})
                head = continuations.get(heads.get(event['id'],{}).get('id'))
                if head:
                    event['continuation'] = {k:head[k] for k in ('id','kind','status','parent_run_id','plan_id','run_id') if k in head}
                plan_id = head.get('plan_id') if head else event.get('plan_id')
                server = servers.get((identity, 'root-'+event['id'] if event.get('root_execution') else event['id']))
                if server:
                    event["launch_url"] = server.url
                    if 'failure_successor' in event:
                        # A child state guard may hold child.session.lock
                        # before requesting parent verification. No monitor
                        # lock may be held while entering child state.
                        state = server.session.state()
                    else:
                        # Original parent guards never request a child
                        # session; keep their Native read serialized with
                        # the successor's exact parent verification.
                        with self.lock:state = server.session.state()
                    event["runs"] = state["runs"]
                    plan = next((p for p in state["plans"] if p["id"] == plan_id), None)
                    if plan and plan["status"] == "consumed":
                        event['failure_parent_run_id'] = plan.get('run_id')
                        event["display_status"] = event["runs"][-1]["status"] if event["runs"] else "starting"
                elif plan_id:
                    # Read the fixed event journal without constructing a provider,
                    # restoring a capability or turning historical consumption into approval.
                    run_root = self.root/identity/'r'/event['id'] if event.get('root_execution') else self.root/identity/'events'/event['id']/'runs'
                    path = unlinked(run_root/'ui-state'/'registry.sqlite3')
                    try:
                        if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
                            raise SafetyError("事件登记缺失")
                        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
                        try:
                            row = db.execute("SELECT body FROM objects WHERE kind='plan' AND id=?", (plan_id,)).fetchone()
                        finally:
                            db.close()
                        if not row:
                            raise SafetyError("事件方案登记缺失")
                        if json.loads(row[0])["view"]["status"] == "consumed":
                            event['failure_parent_run_id'] = json.loads(row[0])['view'].get('run_id')
                            event["display_status"] = "consumed"
                    except (SafetyError, sqlite3.Error, ValueError, KeyError, OSError):
                        event["display_status"] = "evidence_invalid"
                root_execution = event.get('root_execution')
                prep = event.get('root_preparation', {})
                scope_blocked = prep.get('scope_compilation', {}).get('status')=='needs_investigation'
                if prep.get('status')=='reproduced' and scope_blocked and root_execution is None:
                    # Project old blocked events without changing their frozen evidence or retry state.
                    event['message'] = REPAIR_SCOPE_BLOCKED
                elif event.get('model_dispatch', {}).get('status')=='blocked_no_replay':
                    event['message'] = event['model_dispatch'].get('message') or event.get('message')
                if head:
                    event.setdefault('display_status',head['status'])
                event['can_prepare_repair'] = bool(roots and prep.get('status')=='reproduced' and not scope_blocked
                    and not event.get('root_preparation_historical') and root_execution is None
                    and event.get('root_ci_followup',{}).get('status','finished')=='finished'
                    and (not validation or validation.get('resume',{}).get('result_status')=='reproduced'
                         and validation.get('resume',{}).get('status')=='finished'))
                event["can_open"] = bool(root_execution and root_execution['status']!='related_no_dispatch' and roots and root_execution['binding']['root_rules_sha256']==roots['sha256']) or bool(event.get("plan_id")) and roots is None
            from .maintenance_memory import monitor_projection
            try:memory=monitor_projection(record,events)
            except (SafetyError,KeyError,TypeError,ValueError):memory={'status':'blocked','runtime_authorized':False}
            result.append({**record, **registry.budget(include_usage=True), "events": events,
                'root_rules':roots, 'maintenance_memory':memory,
                'acceptance_watches':[v for (mid,_),v in watches if mid==identity],
                'failure_followups':self.failure_followup.state(identity),
                'model_authorization':self.model_authorization.view(identity)})
        return json.loads(json.dumps(result))

    def start(self, payload):
        # An explicit preview and authorization, not an inferred scope from a Git URL.
        from .repository_workbench import inspect_repository
        source = inspect_repository(Path(payload["path"]))
        if source["revision"] != payload["revision"] or payload["confirmed"] is not True:
            raise SafetyError("仓库预览变化；请重新确认监控授权")
        policy = Policy(tuple(payload["allowed_paths"]), tuple(tuple(c) for c in payload["allowed_tests"]),
                        payload["timeout_seconds"], tuple(payload["readonly_paths"]))
        policy.validate_scope()
        for path in (*policy.allowed_paths, *policy.readonly_paths):
            if path not in source["files"]:
                raise SafetyError("监控只能读取明确选择的预览文件")
            RepositoryGateway(Path(source["path"])).read(path).decode("utf-8")
        if self.hub.root.is_relative_to(Path(source["path"])):
            raise SafetyError("工作台状态必须在目标仓库之外")
        branch = branch_name(payload["branch"])
        url = payload["github_url"]
        if url:
            _, url = github_url(url)
            actual = RepositoryGateway(Path(source["path"])).command("config", "--get", "remote.origin.url").decode().strip()
            _, actual = github_url(actual)
            if url != actual:
                raise SafetyError("GitHub 监控地址须与已预览仓库的 origin 一致")
        key = url.casefold() if url else os.path.normcase(source["path"])
        identity = digest(key.encode())[:16]
        spec = {"schema_version": 1, "id": identity, "path": source["path"], "github_url": url,
                "branch": branch, "interval_seconds": payload["interval_seconds"],
                "allowed_paths": list(policy.allowed_paths), "readonly_paths": list(policy.readonly_paths),
                "allowed_tests": [list(c) for c in policy.allowed_tests], "timeout_seconds": float(policy.timeout_seconds)}
        if identity in self.records:
            if self.records[identity]["spec"] != spec:
                raise SafetyError("该仓库已有固定监控范围、分支和额度；不能换配置重置")
            record = self.records[identity]
            first_enable = False
        else:
            if len(self.records) >= 10:
                raise SafetyError("首版最多10个监控仓库")
            directory = unlinked(self.root / identity)
            directory.mkdir()
            with (directory / "spec.json").open("x", encoding="utf-8") as stream:
                json.dump(spec, stream, ensure_ascii=False)
            record = self._open(identity, spec)
            first_enable = True
        record.update(enabled=True, error=None)
        self._save(record)
        if first_enable:
            self.next_due[identity] = time.monotonic()  # First check is automatic.
        return {"monitor_id": identity, "enabled": True}

    def current_head(self, spec):
        if spec["github_url"]:
            return self.hub.github.branch_head(spec["github_url"], spec["branch"])
        return RepositoryGateway(unlinked(Path(spec["path"]))).command(
            "rev-parse", "--verify", "refs/heads/" + spec["branch"]).decode().strip()

    def guard(self, identity, sha):
        record = self.records[identity]
        if not record["enabled"] or self.current_head(record["spec"]) != sha:
            raise SafetyError("监控已暂停或分支有新提交；旧方案不能授权修改，请检查最新待办")

    def operate(self, action, payload):
        if action.startswith('monitor_validation_'):
            from .task_session import require
            method=action.removeprefix('monitor_validation_')
            require(method in {'preview','propose','review','prepare','reconcile','clarification_preview','clarify'},'未知案例操作')
            return getattr(self.validation,method)(payload)
        if action == 'monitor_failure_preview':
            return self.failure_followup.preview(payload)
        if action == 'monitor_failure_configure':
            return self.failure_followup.configure(payload)
        if action in {'monitor_failure_pause','monitor_failure_resume'}:
            return self.failure_followup.control(action,payload)
        if action.startswith('monitor_failure_'):
            raise SafetyError('未知新失败后继操作')
        if action.startswith('monitor_delivery_'):
            from .monitor_delivery import MonitorDelivery
            return MonitorDelivery(self,payload['monitor_id'],payload['event_id'],
                getattr(self.hub,'monitor_delivery_remote',None)).operate(action,payload,
                getattr(self.hub,'monitor_publication_transport',None))
        if action=='monitor_model_preview':return self.model_authorization.preview(payload)
        if action=='monitor_model_configure':return self.model_authorization.configure(payload)
        if action == 'monitor_continuation_inspect':
            return self.continuation.inspect(payload)
        if action == 'monitor_continuation_recover':
            return self.continuation.recover(payload)
        if action == 'monitor_continuation_start':
            return self.continuation.start(payload)
        if action == 'monitor_recovery_preview':
            from .monitor_history import recovery_preview
            return recovery_preview(self, payload)
        if action == 'monitor_repair_preview':
            return self.execution.preview(payload)
        if action == 'monitor_repair_start':
            return self.execution.start(payload)
        if action == 'monitor_rules_preview':
            return self.root_rules.preview(payload)
        if action == 'monitor_rules_configure':
            return self.root_rules.configure(payload)
        if action == "monitor_start":
            return self.start(payload)
        identity = payload["monitor_id"]
        if identity not in self.records:
            raise SafetyError("未知监控仓库")
        record = self.records[identity]
        if action == "monitor_pause":
            grant=self.model_authorization.current(identity)
            if grant:self.model_authorization.control('monitor_model_pause',{'monitor_id':identity,'grant_id':grant['id']},uuid.uuid4().hex)
            record["enabled"] = False
            self._save(record)
            return {"monitor_id": identity, "enabled": False}
        if action == "monitor_resume":
            record.update(enabled=True, error=None)
            self._save(record)
            self.next_due[identity] = max(self.next_due.get(identity, 0), time.monotonic())
            return {"monitor_id": identity, "enabled": True}
        if action == "monitor_check":
            if time.monotonic() < self.next_due.get(identity, 0):
                raise SafetyError("请等待本次监控间隔；检查按钮不会绕过低频限制")
            self.poll(identity)
            return {"monitor_id": identity, "checked": True}
        if action == "monitor_open":
            event = self.registries[identity].load("event").get(payload["event_id"])
            if event and event.get('root_execution'):
                return {'launch_url':self.execution.session(identity,event).url}
            if not event or not event.get("plan_id"):
                raise SafetyError("该事件没有方案或运行报告")
            server = self._session(identity, event)
            return {"launch_url": server.url}
        raise SafetyError("未知监控操作")

    def _session(self, identity, event):
        self._legacy_guard(identity, event['sha'])
        if self.root_rules.current(identity) is not None:
            raise SafetyError('目录维护规则已变化；旧固定范围方案只作历史，不能继续授权')
        key = (identity, event["id"])
        if key in self.servers:
            return self.servers[key]
        spec = self.records[identity]["spec"]
        directory = unlinked(self.root / identity / "events" / event["id"])
        source = unlinked(directory / "source")
        if RepositoryGateway(source).head != event["sha"]:
            raise SafetyError("事件快照提交变化；保留现场")
        policy = Policy(tuple(spec["allowed_paths"]), tuple(tuple(c) for c in spec["allowed_tests"]),
                        spec["timeout_seconds"], tuple(spec["readonly_paths"]))
        session = UISession(source, policy, self._provider(policy), directory / "runs", allow_auto=False,
                            label="Pi · 监控待审批（同仓库共享额度）", budget_registry=self.registries[identity],
                            source_guard=lambda: self._legacy_guard(identity, event["sha"]))
        session.repository_picker_url = self.hub.url
        session.focus_plan_id = event.get("plan_id")
        try:
            server = self.hub.server_factory(session)
        except BaseException:
            session.close()
            raise
        self.servers[key] = server
        return server

    def _legacy_guard(self, identity, sha):
        self.guard(identity, sha)
        if self.root_rules.current(identity) is not None:
            raise SafetyError('目录维护规则已变化；旧固定范围审批不再有效')
        # Legacy scopes have no comparable target-environment grant. They may
        # continue independently, but must not bypass a durable association that
        # was registered for this exact source version by a newer controller.
        from .issue_intake import fingerprint
        from .workbench_correlation import ROUTES
        registry=self.registries[identity]
        routes=registry.load(ROUTES)
        for event in registry.load('event').values():
            if event['sha']!=sha:
                continue
            origin={'kind':'monitor','monitor_id':identity,'event_id':event['id'],
                    'registry_path':str(registry.path)}
            if fingerprint(origin) in routes:
                raise SafetyError('此版本已有来源关联；旧固定范围入口不能独立规划，'
                                  '请核对原关联和目录维护规则，保留原记录与额度')

    def _root_poll(self, identity, sha, failures, ci_error, config, *, event_id=None, ci_diagnosis=None):
        """One intent per commit/rule version, same registry and original budget."""
        registry = self.registries[identity]
        spec = self.records[identity]['spec']
        child_id = event_id
        event_id = event_id or digest(sha.encode())[:16]
        events = registry.load('event')
        event = events.get(event_id)
        if child_id is not None:
            if event is None or event.get('sha') != sha:
                raise SafetyError('内部后继事件缺失或来源变化')
            self.failure_followup.validate_event(identity,event)
        if event is not None and event['sha'] != sha:
            raise SafetyError('目录事件身份冲突；保留原记录')
        if event is None:
            if len(events) >= 100:
                raise SafetyError('监控事件达到100项；保留历史，不重置额度')
            event = {'id':event_id, 'sha':sha, 'created_at':now(), 'tests':[], 'plan_id':None}
            self.model_authorization.observe(identity,event)
        event.update(ci_failures=failures, ci_error=ci_error)
        previous = event.get('root_preparation')
        if previous and previous.get('root_rules_sha256') == config['sha256']:
            old_selection = previous.get('ci_diagnosis', {}).get('selection')
            event['root_preparation_historical'] = bool(old_selection and not any(
                r.get('id') == old_selection['run_id'] and r.get('attempt') == old_selection['attempt'] for r in failures))
            if failures and event.get('status') == 'healthy':
                event.update(status='ci_attention', message='本地目录检查已通过，后来出现 CI 失败；保留原结果，不重放检查')
            if failures and not previous.get('ci_diagnosis'):
                self._root_late_ci(identity, event, config)
            self._event(identity, event)
            return
        if previous:
            event.setdefault('root_preparation_history', []).append(previous)
        elif event.get('status'):
            event['legacy_monitor_status'] = event['status']
        event['root_preparation'] = {'status':'started','root_rules_sha256':config['sha256'], 'model_requests':0}
        event.update(status='checking', message='按已确认目录规则调查并选择检查', notification=None)
        self._event(identity, event)

        directory = unlinked(self.root / identity / 'events' / event_id)
        source = unlinked(directory / 'source')

        def guard():
            self.hub.runtime.require_current()
            self.guard(identity, sha)
            if child_id is not None:self.failure_followup.validate_event(identity,event)
            if self.root_rules.current(identity)['sha256'] != config['sha256']:
                raise SafetyError('目录规则版本变化，旧准备不能继续')

        def save(value):
            if len(json.dumps(value, ensure_ascii=False).encode()) > 524288:
                raise SafetyError('目录调查/环境证据超出事件容量；保留现场')
            event['root_preparation'] = value
            self._event(identity, event)

        try:
            guard()
            if not source.exists():
                if spec['github_url']:
                    self.hub.github.snapshot(spec['github_url'], spec['branch'], sha, source)
                else:
                    fetch_snapshot(unlinked(Path(spec['path'])), spec['branch'], sha, source)
            reader = None
            if failures:
                from .ci_evidence import CiEvidenceReader
                from .ci_http import CiHttpTransport
                reader = CiEvidenceReader(CiHttpTransport(self.hub.github, policy=self.hub.ci_download_policy), synthetic=False)
            from .monitor_preparation import prepare_monitor
            result = prepare_monitor(RepositoryGateway(source), spec, config, event,
                self.root/identity, guard, save, reader, ci_diagnosis=ci_diagnosis,
                baseline_reader=getattr(self.hub,'ci_baseline',None))
            event.update(status='healthy' if result['status']=='baseline_passes' and not failures else
                'ci_attention' if result['status']=='baseline_passes' else 'blocked',
                message=result['message'], notification=None if result['status']=='baseline_passes' and not failures else uuid.uuid4().hex)
        except Exception as error:
            prior = event['root_preparation']
            event['root_preparation'] = {**prior, 'status':'blocked_no_replay', 'error_type':type(error).__name__}
            message = str(error) if type(error) is SnapshotError else '目录维护检查未完成；原证据保留，不重放或自动扩权'
            event.update(status='blocked', message=message, notification=uuid.uuid4().hex)
        self._event(identity, event)

    def _root_late_ci(self, identity, event, config):
        old = event.get('root_late_ci')
        if old is not None:
            selection = old.get('result', {}).get('selection')
            event['root_late_ci_historical'] = bool(old.get('root_rules_sha256') != config['sha256'] or
                (selection and not any(r.get('id') == selection['run_id'] and
                    r.get('attempt') == selection['attempt'] for r in event['ci_failures'])))
            self._root_ci_followup(identity,event,config)
            return
        event['root_late_ci'] = {'status':'started', 'root_rules_sha256':config['sha256']}
        self._event(identity, event)
        try:
            from .ci_evidence import CiEvidenceReader
            from .ci_http import CiHttpTransport
            from .ci_investigation import investigate_ci
            spec = self.records[identity]['spec']
            effective = {**spec, 'maintenance_roots':config['settings'], 'root_rules_sha256':config['sha256']}
            source = unlinked(self.root / identity / 'events' / event['id'] / 'source')
            reader = CiEvidenceReader(CiHttpTransport(self.hub.github, policy=self.hub.ci_download_policy), synthetic=False)
            result = investigate_ci(RepositoryGateway(source), effective, event, reader,
                lambda: self.guard(identity, event['sha']), baseline_reader=getattr(self.hub,'ci_baseline',None))
            event['root_late_ci'].update(status='investigated', result=result)
        except Exception as error:
            event['root_late_ci'].update(status='blocked_no_replay', error_type=type(error).__name__)
        self._event(identity, event)
        self._root_ci_followup(identity,event,config)

    def _root_ci_followup(self, identity, event, config):
        """One late-CI reproduction under an original future-event authorization.

        No previous repair/model intent can be replaced. Original local baseline
        evidence remains in history; no new task, ledger or authorization exists.
        """
        # Legacy CI re-preparation has no authority to replace reviewed cases.
        if self.registries[identity].load('monitor_validation_request').get(event['id']):return
        late=event.get('root_late_ci',{})
        if (event.get('root_ci_followup') or event.get('root_execution') or event.get('model_dispatch')
                or event.get('root_late_ci_historical') or late.get('status')!='investigated'
                or late.get('root_rules_sha256')!=config['sha256']):return
        pin=event.get('model_observation')
        if not pin:return  # Legacy/no future-model authorization stays read-only.
        try:self.model_authorization.authorize(identity,event,pin)
        except SafetyError:return
        previous=event['root_preparation']
        if previous.get('status') not in {'baseline_passes','reproduced','execution_consent_required'}:return
        from .ci_repair import failure_binding,verify_current_failure
        from .issue_intake import fingerprint
        spec=self.records[identity]['spec']
        effective={**spec,'maintenance_roots':config['settings'],'root_rules_sha256':config['sha256']}
        row={'status':'started','root_rules_sha256':config['sha256'],
             'parent_preparation_sha256':fingerprint(previous),'authorization':pin,'created_at':now()}
        event['root_ci_followup']=row
        self._event(identity,event)  # Intent before any further test or model effect.
        try:
            ci_pin=failure_binding(late['result'],effective,event)
            row['ci_failure_binding']=ci_pin
            def guard():
                self.hub.runtime.require_current();self.guard(identity,event['sha'])
                self.model_authorization.authorize(identity,event,pin)
                if self.root_rules.current(identity)['sha256']!=config['sha256']:
                    raise SafetyError('CI后续复现的维护规则已变化')
                verify_current_failure(self.hub.github,spec,ci_pin)
            guard()
            event.setdefault('root_preparation_history',[]).append(previous)
            def save(value):
                if len(json.dumps(value,ensure_ascii=False).encode())>524288:
                    raise SafetyError('CI后续准备超出原事件容量')
                event['root_preparation']=value;self._event(identity,event)
            source=unlinked(self.root/identity/'events'/event['id']/'source')
            from .monitor_preparation import prepare_monitor
            result=prepare_monitor(RepositoryGateway(source),spec,config,event,self.root/identity,
                guard,save,ci_diagnosis=late['result'])
            row.update(status='finished',result_status=result['status'],finished_at=now())
            event['root_preparation_historical']=False
            event.update(status='ci_attention' if result['status']=='baseline_passes' else 'blocked',
                         message=result['message'],notification=uuid.uuid4().hex)
        except Exception as error:
            row.update(status='blocked_no_replay',error_type=type(error).__name__,finished_at=now())
            event.update(status='blocked',message='CI后续复现未完成；原基线、意图与消费保留，不重放',notification=uuid.uuid4().hex)
        self._event(identity,event)

    def _investigate_ci(self, identity, event):
        """Once per original event; durable intent also survives a restart."""
        if event.get('ci_diagnosis') is not None:
            selection = event['ci_diagnosis'].get('selection')
            event['ci_diagnosis_historical'] = bool(selection and not any(
                row.get('id') == selection['run_id'] and row.get('attempt') == selection['attempt']
                for row in event.get('ci_failures', [])))
            return
        if not event.get('ci_failures'):
            return
        event['ci_diagnosis'] = {'status': 'started', 'runtime_authorized': False}
        self._event(identity, event)
        try:
            from .ci_evidence import CiEvidenceReader
            from .ci_http import CiHttpTransport
            from .ci_investigation import investigate_ci
            spec = self.records[identity]['spec']
            source = unlinked(self.root / identity / 'events' / event['id'] / 'source')
            reader = CiEvidenceReader(CiHttpTransport(self.hub.github,
                policy=self.hub.ci_download_policy), synthetic=False)
            event['ci_diagnosis'] = investigate_ci(RepositoryGateway(source), spec, event,
                reader, lambda: self.guard(identity, event['sha']), baseline_reader=getattr(self.hub,'ci_baseline',None))
        except Exception as error:
            event['ci_diagnosis'] = {'status': 'blocked_no_replay',
                'error_type': type(error).__name__, 'runtime_authorized': False,
                'message': 'CI调查未完成；保留事件，不重放或自动扩权'}
        self._event(identity, event)

    def _prepare_ci(self, identity, event):
        """One read-only proposal in the original event, never a runtime grant."""
        diagnosis = event.get('ci_diagnosis', {})
        if (diagnosis.get('status') != 'investigated'
                or event.get('ci_diagnosis_historical') or event.get('ci_preparation') is not None):
            return
        event['ci_preparation'] = {'status': 'started', 'runtime_authorized': False}
        self._event(identity, event)
        try:
            from .ci_preparation import prepare_ci_environment
            spec = self.records[identity]['spec']
            source = unlinked(self.root / identity / 'events' / event['id'] / 'source')
            event['ci_preparation'] = prepare_ci_environment(RepositoryGateway(source), spec,
                event, diagnosis, lambda: self.guard(identity, event['sha']))
        except Exception as error:
            event['ci_preparation'] = {'status': 'blocked_no_replay',
                'runtime_authorized': False, 'error_type': type(error).__name__,
                'message': '环境与检查建议未完成；保留现场，请检查授权范围、来源和测试配置，不自动重放'}
        self._event(identity, event)

    def poll(self, identity, *, expected_sha=None):
        if expected_sha is not None and (not isinstance(expected_sha, str)
                or not re.fullmatch(r'[a-f0-9]{40}', expected_sha)):
            raise SafetyError('推送检查需要准确提交，不接受替代版本')
        record, registry = self.records[identity], self.registries[identity]
        spec = record["spec"]
        previous_error = record.get("error")
        if not record["enabled"]:
            return
        self.next_due[identity] = time.monotonic() + spec["interval_seconds"]
        try:
            sha = self.current_head(spec)
            if not re.fullmatch(r"[a-f0-9]{40}", sha):
                raise SafetyError("分支提交无效")
            if expected_sha is not None and sha != expected_sha:
                return {'status':'superseded','expected_sha':expected_sha,'current_sha':sha}
            events = registry.load("event")
            for event in events.values():
                if event["sha"] != sha and event["status"] == "awaiting_approval":
                    event.update(status="stale", message="分支有新提交，旧方案已失效", notification=uuid.uuid4().hex)
                    self._event(identity, event)
            record.update(latest_sha=sha, last_checked=now(), error=None)
            self._save(record)
            ci_error = None
            try:
                failures = self.hub.github.workflow_runs(spec["github_url"], spec["branch"], sha) if spec["github_url"] else []
            except SafetyError as error:
                failures, ci_error = [], str(error)
            config = self.root_rules.current(identity, required=spec['schema_version'] == 2)
            if config is not None:
                self._root_poll(identity, sha, failures, ci_error, config)
                self.model_authorization.dispatch(identity,sha)
                self.failure_followup.dispatch(identity,sha)
                return
            # Commit is the repair identity; CI attempts update evidence, never rerun paid diagnosis.
            event_id = digest(sha.encode())[:16]
            if event_id in events:
                event = events[event_id]
                if event["sha"] != sha:
                    raise SafetyError("事件目录身份冲突")
                previous = event.get("ci_failures", [])
                event.update(ci_failures=failures, ci_error=ci_error)
                if failures and not previous and event["status"] == "healthy":
                    event.update(status="ci_attention", message="GitHub CI 失败，本地授权测试通过；请检查 CI 环境或补充复现", notification=uuid.uuid4().hex)
                elif not failures and not ci_error and event["status"] == "ci_attention":
                    event.update(status="healthy", message="本地授权测试通过，当前CI结果中未发现失败", notification=None)
                if event['status'] in {'healthy', 'ci_attention', 'awaiting_approval'}:
                    self._investigate_ci(identity, event)
                    self._prepare_ci(identity, event)
                self._event(identity, event)
                return
            if len(events) >= 100:
                raise SafetyError("监控事件达到100项上限；保留历史，不自动清空额度")
            event = {"id": event_id, "sha": sha, "created_at": now(), "status": "checking",
                     "ci_failures": failures, "ci_error": ci_error, "tests": [], "notification": None,
                     "message": "检查新提交", "plan_id": None}
            self._event(identity, event)  # Intent durable BEFORE fetching or running code.
            directory = unlinked(self.root / identity / "events" / event_id)
            source = unlinked(directory / "source")
            if spec["github_url"]:
                self.hub.github.snapshot(spec["github_url"], spec["branch"], sha, source)
            else:
                fetch_snapshot(unlinked(Path(spec["path"])), spec["branch"], sha, source)
            from .repository_workbench import inspect_repository
            preview = inspect_repository(source)
            if not set(spec["allowed_paths"] + spec["readonly_paths"]).issubset(preview["files"]):
                raise SafetyError("新提交中的授权文件缺失或不适合读取；不能自动扩大范围")
            repository = RepositoryGateway(source)
            for path in spec["allowed_paths"] + spec["readonly_paths"]:
                repository.read(path).decode("utf-8")
            baseline = repository.snapshot()
            check_root = unlinked(directory / "check")
            repository.command("worktree", "add", "--detach", repository.worktree_path_argument(check_root), sha)
            check = RepositoryGateway(check_root)
            before = check.snapshot()
            for command in spec["allowed_tests"]:
                result = run_test(tuple(command), check_root, spec["timeout_seconds"])
                event["tests"].append(result)
                self._event(identity, event)
            if check.snapshot() != before or repository.snapshot() != baseline:
                raise SafetyError("检测测试修改了文件或 Git 元数据；现场保留，不自动清理")
            repository.command("worktree", "remove", str(check_root))
            self.guard(identity, sha)
            self._investigate_ci(identity, event)
            self._prepare_ci(identity, event)
            if any(test["timed_out"] for test in event["tests"]):
                raise SafetyError("检测测试超时；请检查测试环境，不自动调用模型")
            if all(test["returncode"] == 0 for test in event["tests"]):
                event.update(status="ci_attention" if failures else "healthy",
                             message="GitHub CI 失败，但本地授权测试通过；请补充复现" if failures else "本地授权测试通过（不能证明没有其他缺陷）")
                if failures:
                    event["notification"] = uuid.uuid4().hex
                self._event(identity, event)
                return
            event.update(status="diagnosing", message="发现失败，正在生成待审批方案")
            self._event(identity, event)
            server = self._session(identity, event)
            detail = json.dumps({"commit": sha, "branch": spec["branch"], "ci_failures": failures,
                                 "tests": [{**t, "stdout": t["stdout"][-3500:], "stderr": t["stderr"][-3500:]} for t in event["tests"]]}, ensure_ascii=False)
            diagnosis = event.get('ci_diagnosis', {})
            if diagnosis.get('status') == 'investigated':
                detail += '\n只读CI调查（不是权限或已复现证明）：' + json.dumps({
                    'selection': diagnosis['selection'], 'scope_sha256': diagnosis['scope_sha256'],
                    'packet_sha256': diagnosis['ci_evidence']['packet_sha256'],
                    'file_candidates': diagnosis['report']['file_candidates'],
                    'requires_reproduction': True}, ensure_ascii=False)
            issue = redact("自动维护检测发现授权测试失败。请诊断并提出最小修复，保持全部授权回归测试。证据：\n" + detail)[:12000]
            result = server.session._plan(issue)
            server.session.focus_plan_id = result["plan_id"]
            self.guard(identity, sha)
            event.update(status="awaiting_approval", plan_id=result["plan_id"],
                         message="发现可复现失败，修复方案已生成；请审阅后批准修改", notification=uuid.uuid4().hex)
            self._event(identity, event)
        except Exception as error:
            message = str(error) if isinstance(error, SafetyError) else "监控操作未完成，请检查环境；不会重放本次操作"
            record["error"] = message
            if message != previous_error and "event" not in locals():
                record["notification"] = uuid.uuid4().hex
            elif "event" in locals():
                record["notification"] = None
            self._save(record)
            if "event" in locals():
                event.update(status="blocked", message=message, notification=uuid.uuid4().hex)
                self._event(identity, event)
        finally:
            self.next_due[identity] = time.monotonic() + spec["interval_seconds"]

    def _timer(self):
        while not self.stopping.wait(1):
            with self.lock:
                if self.queued:
                    continue
                due = [i for i, r in self.records.items() if r["enabled"] and time.monotonic() >= self.next_due.get(i, 0)]
                watches = self.acceptance_watch.due()
                if not due and not watches:
                    continue
                self.queued = True
            self.hub.executor.submit(self._dispatch, due, watches)

    def _dispatch(self, identities, watches=()):
        try:
            for identity in identities:
                if not self.stopping.is_set():
                    self.poll(identity)
            for mid,wid in watches:
                if not self.stopping.is_set():self.acceptance_watch.poll(mid,wid)
        finally:
            with self.lock:
                self.queued = False

    def stop(self):
        self.stopping.set()
        self.thread.join()

    def close(self):
        for server in self.servers.values():
            server.close()
        for registry in self.registries.values():
            registry.close()
