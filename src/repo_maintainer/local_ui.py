"""Loopback UI adapter. Existing workflows retain all Git/test/approval authority."""
from __future__ import annotations
from .maintenance_limits import LIMITS

import difflib
import json
import os
import re
import secrets
import socket
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .dashboard import ASSETS as DIFF_ASSETS, project_run, read_json, unlinked
from .git import RepositoryGateway
from .integration import integrate, prepare_integration, recover_integration
from .models import Policy, SafetyError, digest
from .pi_provider import SavedPlanProvider, decode_plan
from .store import now, redact
from .workflow import cleanup_run, plan_repair, repair
from .ui_registry import DurableProvider, UIRegistry, provider_binding
from .ui_reconciliation import inspect_gap
from .repair_recovery import inspect_repair, recover_repair
from .ui_memory import UIMemory
from .ui_structure import UIStructure
from .service_state import RuntimeInfo
from .runtime_limits import MAX_REQUESTS
from .job_control import JobControl, OperationStopped

ID = re.compile(r"[0-9a-f]{32}")
SHA = re.compile(r"[0-9a-f]{64}")
ASSETS = Path(__file__).with_name("local_ui_assets")
MAX_BODY = 16 * 1024
MAX_JOBS = 100
SECURITY_HEADERS = {
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}




def json_digest(value) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def identifier(value: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SafetyError("Invalid session identity")
    return value


def policy_digest(value: dict) -> str:
    # JSON integers and floats express the same duration; keep all other fields exact.
    normalized = json.loads(json.dumps(value))
    if not isinstance(normalized, dict) or type(normalized.get("timeout_seconds")) not in {int, float}:
        raise SafetyError("Invalid recorded policy duration")
    normalized["timeout_seconds"] = float(normalized["timeout_seconds"])
    return json_digest(normalized)


class UISession:
    """One configured repository/policy/provider; serialized, bounded, idempotent jobs."""

    def __init__(self, repo: Path, policy: Policy, provider, runs_dir: Path, *,
                 allow_auto: bool = False, max_attempts: int = 1, label: str = "Pi", structure_index: Path | None = None,
                 structure_context_query: str | None = None, budget_registry=None, source_guard=None,
                 semantic_model_manifest: Path | None = None, target_environment=None, controller_binding=None,
                 monitor_owned=False, controller_read_roots=None):
        policy.validate_scope()
        from .pi_provider import PiProvider
        if semantic_model_manifest is not None and not isinstance(provider,PiProvider):
            raise SafetyError('Local semantic registration requires the original Pi provider')
        from .semantic_retrieval import ModelPackage,SessionRetrieval
        semantic_package=ModelPackage(semantic_model_manifest) if semantic_model_manifest is not None else None
        self.repo = unlinked(repo)
        repository = RepositoryGateway(self.repo)
        repository.require_clean(repository.snapshot())
        self.runs_dir = unlinked(runs_dir)
        if (self.runs_dir.is_relative_to(self.repo)
                or self.runs_dir.is_relative_to(repository.common_dir)):
            raise SafetyError("UI state/runs must be outside source checkout and Git metadata")
        if type(max_attempts) is not int or not 1 <= max_attempts <= LIMITS["candidate_max_rounds"]:
            raise SafetyError(f"Repair attempts must be in [1, {LIMITS['candidate_max_rounds']}]")
        self.policy, self.provider = policy, provider
        self.target_environment = target_environment
        self.monitor_owned = monitor_owned
        self.monitor_model_job_id = None
        self.monitor_continuation_permit = None
        self.source_guard = source_guard
        self.allow_auto, self.max_attempts, self.label = allow_auto, max_attempts, label
        self.memory = UIMemory(provider, self.repo)
        self.structure = UIStructure(structure_index, self.repo, provider, self.runs_dir, structure_context_query)
        binding = {"repository": str(self.repo),
                                  "policy": json.loads(json.dumps(asdict(policy))),
                                  "provider": provider_binding(provider)}
        if budget_registry is not None:
            binding["shared_budget"] = str(budget_registry.path)
        if target_environment is not None:
            binding['target_environment'] = {'plan_sha256':target_environment.plan['plan_sha256'],
                'grant_sha256':target_environment.grant['grant_sha256'], 'controller':controller_binding}
        if self.memory.binding:
            binding["source_memory"] = self.memory.binding
            # New journals require valid sources. Existing, identically bound journals
            # may reopen to show stale sources; model/edit actions still revalidate.
            if not unlinked(self.runs_dir / "ui-state" / "registry.sqlite3").exists():
                self.memory.verify("verification")
        if self.structure.binding:
            binding['structure_index'] = self.structure.binding
            if not unlinked(self.runs_dir / 'ui-state' / 'registry.sqlite3').exists():
                self.structure.require_current()
        self.registry = UIRegistry(self.runs_dir, binding)
        self.budget_registry = budget_registry or self.registry
        self.provider = DurableProvider(provider, self.budget_registry)
        from .pi_provider import PiProvider
        self.task_planner = None
        if isinstance(provider, PiProvider) and (self.budget_registry.path == self.registry.path
                or target_environment is not None and controller_binding is not None):
            from .task_planning import TaskPlanner
            self.task_planner = TaskPlanner(self.budget_registry, self.repo, policy, provider,
                                           {'memory':self.memory.binding,'structure':self.structure.binding},
                                           sources_guard=self._task_sources, link_registry=self.registry)
            self.provider.task_planner = self.task_planner
            if controller_read_roots is not None:
                if not monitor_owned or target_environment is None or controller_binding is None:
                    raise SafetyError('Dynamic reads require the original maintenance controller')
                self.task_planner.use_controller_read_roots(controller_read_roots)
        self.directory = unlinked(self.runs_dir / "ui-sessions" / uuid.uuid4().hex)
        self.token = secrets.token_hex(32)
        self.jobs = self.registry.load("job")
        self.plans, self.runs = {}, {k:v for k,v in self.registry.load("run").items()
                                    if not v.get('issue_execution_id')}
        # Issue runs retain the SAME registry/run directories but are projected
        # by their controller. They cannot enter the fixed manual-policy actions.
        self.issue_workbench = None
        from .task_feedback import TaskFailureFeedback
        standalone_tasks = self.task_planner and self.budget_registry.path == self.registry.path
        self.failure_feedback = TaskFailureFeedback(self) if standalone_tasks else None
        from .feedback_cleanup import FeedbackCleanup
        self.feedback_cleanup = FeedbackCleanup(self) if self.task_planner else None
        self.claimed_plans, self.claimed_runs = set(), set()
        self.inspections = {}
        self.repair_inspections = {}
        from .source_view import verified_source_view
        try:
            with verified_source_view():
                for identity, item in self.registry.load("plan").items():
                    identifier(identity)
                    item["path"] = unlinked(self.runs_dir / "ui-sessions" / identifier(item["session_id"]) / (identity + ".json"))
                    self.plans[identity] = item
                for identity in self.runs:
                    identifier(identity)
                    try:
                        self.runs[identity] = self._run_view(identity)
                    except Exception:
                        self.runs[identity] = self._unavailable_run_view(identity)
        except BaseException:
            self.registry.close()
            raise
        self.active = None
        self.control = None
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="maintenance-ui")
        self.closed = False
        self.runtime = RuntimeInfo()
        from .experience_memory import ExperienceMemory
        self.experience = ExperienceMemory(self) if standalone_tasks else None
        if self.experience:
            self.task_planner.experience = self.experience
            self.provider.inner._experience_context = self.experience.context
        from .knowledge_memory import KnowledgeMemory
        self.knowledge = KnowledgeMemory(self) if self.experience else None
        if self.knowledge:
            self.task_planner.knowledge = self.knowledge
            self.provider.inner._knowledge_context = self.knowledge.context
        from .task_compaction import TaskCompaction
        self.compaction = TaskCompaction(self) if standalone_tasks else None
        if self.compaction:self.task_planner.compaction = self.compaction
        from .maintenance_memory import MaintenanceMemory
        self.maintenance = MaintenanceMemory(self) if self.experience else None
        if self.maintenance:
            self.task_planner.maintenance = self.maintenance
            self.provider.inner._maintenance_context = self.maintenance.context
        from .memory_catalog import MemoryCatalog
        self.catalog = MemoryCatalog(self) if isinstance(provider,PiProvider) else None
        if self.catalog:self.provider.inner._memory_catalog = self.catalog
        self.retrieval=SessionRetrieval(self,semantic_package) if self.catalog else None
        if standalone_tasks:
            from .controller_memory import attach_ui
            attach_ui(self)

    def _task_sources(self, issue):
        if self.source_guard:
            self.source_guard()
        self.structure.require_current()
        self.memory.require_current(issue)

    def _model_probe_guard(self):
        self.runtime.require_current()
        if self.closed:
            raise SafetyError('原会话已关闭，请重新连接原仓库后核对')
        if self.monitor_owned or self.budget_registry is not self.registry:
            raise SafetyError('监控或共享模型额度须通过原控制器授权，不能从服务检查入口调用')
        if self.source_guard:
            self.source_guard()

    def _model_connectivity(self):
        from .model_connectivity import ModelConnectivity
        return ModelConnectivity(self, before_model=self._model_probe_guard)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        # Do not claim cancellation: bounded existing tasks finish before shutdown.
        if self.issue_workbench:self.issue_workbench.stop()
        self.executor.shutdown(wait=True)
        if self.retrieval:self.retrieval.close()
        self.registry.close()

    def _save_plan(self, identity: str, db=None):
        self.registry.put("plan", identity, {k: v for k, v in self.plans[identity].items() if k != "path"}, db)

    def _plan_revision(self, item: dict) -> str:
        # A new server capability also invalidates previously displayed claim challenges.
        return json_digest({"view": item["view"], "file_sha": item["file_sha"], "session": self.token})

    def state(self) -> dict:
        from .source_view import verified_source_view
        with verified_source_view():
            return self._state()

    def _state(self) -> dict:
        with self.lock:
            result = json.loads(json.dumps({"repository": str(self.repo), "provider": self.label,
                    "policy": asdict(self.policy), "allow_auto": self.allow_auto,
                    "manual_attempts": 1, "auto_attempts": self.max_attempts,
                    "runtime": self.runtime.view(),
                    "service_control": {'can_stop': bool(getattr(self,'stop_callback',None)),
                                        'stop_revision':json_digest({'build':self.runtime.loaded,'session':self.token})},
                    "budget_changes": list(self.budget_registry.load('budget_change').values()),
                    "source_memory": self.memory.view(),
                    "structure_index": self.structure.view(),
                    **self.budget_registry.budget(include_usage=True),
                    "budget_monitor_id": (self.budget_registry.root.parent.name
                                          if self.budget_registry is not self.registry else None),
                    "planning_attempts": list(self.budget_registry.load("attempt").values()),
                    "active": self.active, "jobs": list(self.jobs.values()),
                    "plans": [{**p["view"], "claimed": i in self.claimed_plans,
                               "revision": self._plan_revision(p)} for i, p in self.plans.items()],
                    "runs": [{**r, "claimed": i in self.claimed_runs} for i, r in self.runs.items()]}, ensure_ascii=False))
            if getattr(self, "repository_picker_url", None):
                result["repository_picker_url"] = self.repository_picker_url
            if getattr(self, "focus_plan_id", None):
                result["focus_plan_id"] = self.focus_plan_id
            result['task_control'] = {'can_stop': self.control is not None and bool(self.active)
                and not self.control.cancel.is_set(), 'job_id': self.active,
                'revision': self._stop_revision() if self.control is not None else None}
            if not self.active:
                # Refresh evidence for display only. Never write the registry or replay an operation on GET.
                for index, entry in enumerate(result['runs']):
                    try:
                        value = self._run_view(entry['run_id'])
                    except (SafetyError, ValueError, OSError, KeyError, TypeError):
                        value = self._unavailable_run_view(entry['run_id'])
                    result['runs'][index] = {**value, 'claimed': entry['claimed']}
            from .task_recovery_view import recovery_cards
            result['recovery_cards'] = recovery_cards(self, result)
            result['experience_memory'] = self.experience.view() if self.experience else {'enabled': False, 'entries': []}
            result['knowledge_memory'] = self.knowledge.view() if self.knowledge else {'entries': []}
            result['task_compaction'] = self.compaction.view() if self.compaction else {'tasks':[]}
            result['maintenance_memory'] = self.maintenance.view() if self.maintenance else {'status':'disabled'}
            result['memory_catalog'] = self.catalog.view() if self.catalog else {'enabled':False}
            result['memory_retrieval'] = self.retrieval.view() if self.retrieval else {'available':False,'enabled':False}
            result['issue_workbench'] = self.issue_workbench.view() if self.issue_workbench else {'available':False}
            from .pi_provider import PiProvider
            inner = self.provider.inner if isinstance(self.provider, DurableProvider) else None
            probe_supported = (isinstance(inner, PiProvider)
                               and self.provider.registry is self.budget_registry)
            probe_available = (probe_supported and not self.monitor_owned
                               and inner.max_requests > 0
                               and self.budget_registry is self.registry and not self.closed)
            result['model_connectivity'] = ({**self._model_connectivity().view(), 'available':probe_available}
                if probe_supported else {'available':False,'checks':[],
                    'proves':'model_response_only','repair_verified':False})
            if self.experience:
                result['experience_memory']['settings_revision'] = json_digest({
                    'settings': self.experience.session.registry.load('experience_settings'), 'session': self.token})
            return result

    def _stop_revision(self):
        return json_digest({'session':self.token, 'job':self.active,
                            'signature':self.jobs[self.active]['signature']})

    def _stop_job(self, request_id, payload, signature):
        if request_id in self.jobs:
            raise SafetyError('Request identity already belongs to another operation')
        saved = self.registry.load('stop').get(request_id)
        if saved:
            if saved['signature'] != signature:
                raise SafetyError('Stop request identity reused with different content')
            return saved
        if (self.control is None or self.active != payload['job_id'] or self.control.cancel.is_set()
                or payload['revision'] != self._stop_revision()):
            raise SafetyError('Task or session changed; review the active task before stopping')
        receipt = {'id':request_id, 'signature':signature, 'status':'finished',
                   'result':{'job_id':self.active, 'stop_requested':True}, 'finished_at':now()}
        target = {**self.jobs[self.active], 'stop_requested_at':now(), 'stop_request_id':request_id}
        with self.registry.transaction() as db:
            self.registry.put('job', self.active, target, db)
            self.registry.put('stop', request_id, receipt, db)
        self.jobs[self.active] = target
        self.control.cancel.set()  # Durable intent precedes its effect; never replay on restart.
        return receipt

    def _progress(self, value):
        with self.lock:
            if not self.active or self.control is None:
                raise SafetyError('Progress has no active owned task')
            job = self.jobs[self.active]
            job['progress'] = {**job.get('progress', {}), **value, 'updated_at':now()}
            self.registry.put('job', self.active, job)

    def _pi_progress(self, value):
        from .pi_evidence import validate_evidence
        value = validate_evidence(value, self.provider.max_requests)
        self._progress({key:value[key] for key in
            ('requests_started','tool_calls','events','usage','events_dropped') if key in value})

    def _phase(self, value):
        if self.control:
            self.control.phase(value)

    def submit(self, request_id: str, action: str, payload: dict) -> dict:
        identifier(request_id)
        signature = json_digest({"action": action, "payload": payload})
        with self.lock:
            if action in {'issue_watch_pause','issue_watch_revoke'}:
                if self.closed or not self.issue_workbench:raise SafetyError('Issue workbench is unavailable')
                return self.issue_workbench.watch.control(action,payload,request_id)
            if action in {'issue_decide','issue_pause','issue_request_changes'}:
                if not self.issue_workbench:raise SafetyError('Issue workbench is unavailable')
                return self.issue_workbench.immediate(action,payload)
            if action == 'stop_job':
                return self._stop_job(request_id, payload, signature)
            if request_id in self.registry.load('stop'):
                raise SafetyError('Request identity already belongs to a stop operation')
            if request_id in self.jobs:
                job = self.jobs[request_id]
                if job["signature"] != signature:
                    raise SafetyError("Request identity reused with different content")
                return dict(job)
            if self.closed or self.active:
                raise SafetyError("Another operation is active; do not resubmit")
            if len(self.jobs) >= MAX_JOBS:
                raise SafetyError("State directory operation limit reached; preserve this history")
            job = {"id": request_id, "action": action, "status": "running",
                   "signature": signature, "started_at": now()}
            self.jobs[request_id] = job
            self.registry.put("job", request_id, job)
            self.active = request_id
            self.control = JobControl(self._progress) if action in {'plan','auto','resume_run','decision','knowledge_propose','knowledge_refresh_propose','compact_task','model_probe','issue_validation_propose','issue_validation_clarify','issue_watch_check','issue_watch_process'} else None
            self.executor.submit(self._execute, request_id, action, payload)
            return dict(job)

    def _execute(self, request_id: str, action: str, payload: dict):
        inner, bound = None, False
        try:
            inner = getattr(self.provider, 'inner', self.provider)
            if self.control and hasattr(inner, '_cancel_event'):
                inner._progress_callback = self._pi_progress
                inner._cancel_event = self.control.cancel
                bound = True
            self._phase('starting')
            result = self._operate(action, payload)
            outcome = {"status": "stopped" if result.get('stop_acknowledged') else "finished", "result": result}
        except OperationStopped:
            outcome = {'status':'stopped', 'error':'Task stopped; evidence and request consumption retained'}
        except Exception as error:
            outcome = {"status": "failed", "error": redact(f"{type(error).__name__}: {error}")}
        finally:
            if bound:
                inner._progress_callback = None
                inner._cancel_event = None
            # Refresh only registered runs. Never enumerate arbitrary directories or follow JSON paths.
            with self.lock:
                identities = list(self.runs)
            from .source_view import verified_source_view
            views = {}
            try:
                with verified_source_view():
                    for identity in identities:
                        try:
                            views[identity] = self._run_view(identity)
                        except Exception:
                            views[identity] = self._unavailable_run_view(identity)
            except Exception:
                # A display refresh cannot undo the recorded business outcome,
                # leave the job running, or publish partially validated views.
                views = {i:self._unavailable_run_view(i) for i in identities}
                outcome['refresh_warning'] = 'Historical display changed; revalidate before acting'
            with self.lock:
                self.runs.update(views)
        with self.lock:
            self.jobs[request_id].update(outcome, finished_at=now())
            self.registry.put("job", request_id, self.jobs[request_id])
            self.active = None
            self.control = None

    def _run_dir(self, identity: str) -> Path:
        identifier(identity)
        if identity not in self.runs:
            raise SafetyError("Run does not belong to this UI session")
        directory = unlinked(self.runs_dir / identity)
        record, _ = read_json(directory / "run.json")
        if (record.get("run_id") != identity or record.get("source_repo") != str(self.repo)
                or record.get("branch") != "maintenance/" + identity
                or record.get("workspace") != str(directory / "workspace")
                or policy_digest(record.get("policy")) != policy_digest(asdict(self.policy))):
            raise SafetyError("Run repository, policy or ownership changed")
        return directory

    def _run_view(self, identity: str) -> dict:
        view = json.loads(redact(json.dumps(project_run(self._run_dir(identity)), ensure_ascii=False)))
        entry = self.registry.load("run").get(identity, {})
        if entry.get("resolution"):
            view.update(record_status=view["status"], status="resolution_conflict", resolution=entry["resolution"])
        if identity in self.inspections:
            view["reconciliation"] = self.inspections[identity]
        if identity in self.repair_inspections:
            view["repair_inspection"] = self.repair_inspections[identity]
        link = self.registry.load('continuation').get(identity)
        if link:
            view['continued_from_run'] = link['parent_run_id']
        try:
            preview = self._resume_preview(identity)
            view['continuation'] = {'available': True, **preview}
        except SafetyError as error:
            view['continuation'] = {'available': False, 'reason': str(error)}
            if self.failure_feedback:
                try:
                    recovery = self.failure_feedback.recovery_preview(identity)
                    view['failure_recovery'] = {k:v for k,v in recovery.items() if k != 'receipt'}
                except SafetyError as recovery_error:
                    view['failure_recovery'] = {'blocked':True,'reason':str(recovery_error)}
                try:
                    cleanup=self.feedback_cleanup.preview(identity)
                    view['feedback_cleanup']={k:v for k,v in cleanup.items() if k not in {'intent','current'}}
                except SafetyError:
                    pass
        return view

    def _resume_preview(self, identity, *, _monitor=False):
        if self.monitor_owned and not _monitor:
            raise SafetyError('请从监控事件核查现场并明确确认接续；此处不直接重放模型请求')
        directory = self._run_dir(identity)
        record, file_sha = read_json(directory/'run.json')
        if (record.get('status') not in {'rolled_back', 'failed', 'rejected', 'discarded', 'repair_aborted'}
                or not record.get('finished_at') or not record.get('source_unchanged')
                or unlinked(directory/'workspace').exists()
                or self.registry.load('run').get(identity, {}).get('resolution')):
            raise SafetyError('Finish or resolve the previous candidate before continuing this issue')
        repository = RepositoryGateway(self.repo)
        current = repository.snapshot()
        repository.require_clean(current)
        if current != record.get('source_before'):
            raise SafetyError('Original source changed; start a separately scoped task')
        issue = record.get('issue')
        if not isinstance(issue, str) or not 0 < len(issue) <= 2000:
            raise SafetyError('Previous issue is invalid')
        self._task_sources(issue)
        task = self.task_planner.reference('run',identity,issue) if self.task_planner else None
        feedback = self.failure_feedback.prepare(identity) if task and self.failure_feedback else None
        return {'revision':json_digest({'file':file_sha,'source':current,'session':self.token,
                                      **({'task':task} if task else {}),
                                      **({'feedback':feedback['payload_sha256']} if feedback else {})}),
                **({'task_session':task} if task else {}),
                **({'failure_feedback':{'source_run_id':feedback['run_id'],
                    'payload_sha256':feedback['payload_sha256'],
                    'failed_round':feedback['payload']['failed_round'],
                    'test_exits':[t['returncode'] for t in feedback['payload']['tests']]}} if feedback else {}),
                'issue':issue, 'auto_allowed':self.allow_auto and record.get('mode')=='auto',
                'explanation':'Starts a new isolated attempt for the same issue; keeps the original run and shared request ledger.'}

    def _unavailable_run_view(self, identity: str) -> dict:
        entry = self.registry.load("run").get(identity, {})
        status = "registration_closed" if entry.get("resolution") else "evidence_invalid"
        if entry.get("resolution"):
            try:
                if unlinked(self.runs_dir / identity).exists():
                    status = "resolution_conflict"
            except SafetyError:
                status = "resolution_conflict"
        if self.inspections.get(identity, {}).get("status") == "resolution_conflict":
            status = "resolution_conflict"
        return {"run_id": identity, "status": status,
                "resolution": entry.get("resolution"), "reconciliation": self.inspections.get(identity)}

    def _inspect_interruption(self, identity: str) -> dict:
        identifier(identity)
        if identity not in self.runs:
            raise SafetyError("Run does not belong to this UI journal")
        entry = self.registry.load("run").get(identity)
        if not entry:
            raise SafetyError("Run journal entry is missing")
        preview = inspect_gap(self.repo, self.policy, self.runs_dir, identity, self.plans, entry)
        preview["revision"] = json_digest({"preview": preview, "session": self.token})
        return preview

    def _register(self, record: dict) -> dict:
        identity = identifier(record["run_id"])
        with self.lock:
            self.runs[identity] = {"run_id": identity}
        if self.failure_feedback and self.task_planner:
            self.failure_feedback.capture(identity)
        view = self._run_view(identity)
        with self.lock:
            self.runs[identity] = view
            self.claimed_runs.add(identity)
            self.registry.put("run", identity, {"run_id": identity})
        if self.experience:
            try:
                self.experience.capture(identity)
            except (SafetyError, OSError, ValueError, KeyError, TypeError) as error:
                self.experience.session.registry.put('experience_capture', identity, {'status': 'blocked', 'reason': redact(str(error))})
        return {"run_id": identity, "workflow_status": record["status"],
                **({'stop_acknowledged':True} if record.get('operator_stop') else {})}

    def _reserve_run(self, identity: str):
        # Journal the fixed destination before the first external workflow side effect.
        self.registry.put("run", identity, {"run_id": identity})
        self.runs[identity] = {"run_id": identity, "status": "starting"}

    def _checked_plan(self, identity: str) -> tuple[dict, object]:
        if self.source_guard:
            self.source_guard()
        item = self.plans.get(identity)
        if item is None or item["view"]["status"] != "awaiting_approval":
            raise SafetyError("Plan is unknown or already consumed")
        data, file_sha = read_json(item["path"])
        plan = decode_plan(data)
        if (file_sha != item["file_sha"] or data["plan_sha256"] != plan.fingerprint
                or plan.fingerprint != item["view"]["fingerprint"]):
            raise SafetyError("Reviewed plan fingerprint or file changed")
        if RepositoryGateway(self.repo).snapshot() != item["snapshot"]:
            raise SafetyError("Source changed after review; request a new plan")
        self.policy.validate(plan)
        if self.experience:
            self.experience.checked_plan(data, plan.issue)
        if self.knowledge:
            self.knowledge.checked_plan(data, plan.issue)
        if self.maintenance:
            self.maintenance.checked_plan(data, plan.issue)
        if self.catalog:
            self.catalog.checked_plan(data,item['view'].get('planning_evidence',{}))
        if self.task_planner and data.get('provider_evidence', {}).get('task_session'):
            task = self.task_planner.reference('plan', identity, plan.issue)
            if task is None or task['task_id'] != data['provider_evidence']['task_session']['task_id']:
                raise SafetyError('Saved plan original task binding changed')
        self.structure.require_current()
        if self.structure.binding and item['view'].get('structure_binding') != self.structure.binding:
            raise SafetyError('Saved plan structure binding changed')
        structure = self.structure.context(plan.issue, plan.base_sha)
        if structure and data.get('provider_evidence', {}).get('structure_context_sha256') != structure['context_sha256']:
            raise SafetyError('Saved plan structure context receipt changed')
        result = self.memory.require_current(plan.issue)
        if self.memory.binding:
            if (item["view"].get("memory_binding") != self.memory.binding
                    or data.get("provider_evidence", {}).get("memory_context_sha256")
                    != result["context"]["context_sha256"]):
                raise SafetyError("Saved plan source memory binding or receipt changed")
        return item, plan

    def _plan(self, issue: str) -> dict:
        self._phase('planning')
        if self.source_guard:
            self.source_guard()
        self.structure.require_current()
        self.memory.require_current(issue)  # Fail before reserving requests or reading credentials.
        repository = RepositoryGateway(self.repo)
        before = repository.snapshot()
        from .pi_behavior import bound_expectations
        contract=self.target_environment.plan.get('behavior_contract') if self.target_environment else None
        with bound_expectations(self.provider,contract,repository):
            plan = plan_repair(self.repo, issue, self.provider, self.policy)
        self._phase('reviewing_plan')
        if self.source_guard:
            self.source_guard()
        if before != repository.snapshot():
            raise SafetyError("Source changed during planning; request a new plan")
        self.structure.require_current()
        memory = self.memory.require_current(issue)
        structure = self.structure.context(issue, plan.base_sha)
        if structure and getattr(self.provider, 'evidence', {}).get('structure_context_sha256') != structure['context_sha256']:
            raise SafetyError('Planning structure context receipt does not match the current scope')
        if memory and getattr(self.provider, "evidence", {}).get("memory_context_sha256") != memory["context"]["context_sha256"]:
            raise SafetyError("Planning source memory receipt does not match the current scope")
        data = {"plan": asdict(plan), "plan_sha256": plan.fingerprint,
                "provider_evidence": getattr(self.provider, "evidence", {})}
        # Recognized credential redaction must never permit approval of unseen original content.
        serialized = redact(json.dumps(data, ensure_ascii=False, indent=2))
        persisted = json.loads(serialized)
        if decode_plan(persisted).fingerprint != plan.fingerprint:
            raise SafetyError("Plan contains redacted content; approval is disabled")
        identity = uuid.uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=True)
        path = unlinked(self.directory / (identity + ".json"))
        temporary = unlinked(path.with_suffix(".tmp"))
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        diff = "".join("".join(difflib.unified_diff(
            repository.read(edit.path).decode("utf-8").splitlines(keepends=True),
            edit.content.splitlines(keepends=True), fromfile="a/" + edit.path,
            tofile="b/" + edit.path)) for edit in plan.edits)
        view = {"id": identity, "plan": persisted["plan"], "fingerprint": plan.fingerprint,
                "diff": diff, "status": "awaiting_approval",
                "planning_evidence": persisted["provider_evidence"]}
        if persisted['provider_evidence'].get('repeated_failed_patch'):
            view['repeated_failed_patch'] = True
        if self.structure.binding:
            self.structure.require_current()
            view['structure_binding'] = self.structure.binding
        if self.memory.binding:
            self.memory.require_current(issue)
            view["memory_binding"] = self.memory.binding
            if self.memory.relations_path:
                view["decision_relations"] = self.memory.related(memory)
        with self.lock:
            if self.control:
                self.control.check()  # Stop and plan publication serialize on the same lock.
            self.plans[identity] = {"path": path, "file_sha": digest(path.read_bytes()),
                                    "snapshot": before, "view": view, "session_id": self.directory.name}
            with self.registry.transaction() as db:
                self._save_plan(identity, db)
                if self.task_planner:
                    self.task_planner.link('plan', identity, db=db)
            self.claimed_plans.add(identity)
        return {"plan_id": identity, "fingerprint": plan.fingerprint}

    def _operate(self, action: str, payload: dict) -> dict:
        if action in {'model_probe_preview','model_probe'}:
            self._model_probe_guard()
            checker = self._model_connectivity()
            if action == 'model_probe_preview':
                return {'model_probe_preview':checker.preview()}
            if not any(job.get('action') == 'model_probe_preview' and job.get('status') == 'finished'
                       and job.get('result', {}).get('model_probe_preview', {}).get('revision') == payload['revision']
                       for job in self.jobs.values()):
                raise SafetyError('请先预览当前配置与原额度，再单独确认一次模型请求')
            self._phase('model_connectivity')
            return {'model_connectivity':checker.execute(self.active, payload['revision'])}
        if action.startswith('issue_'):
            if not self.issue_workbench:raise SafetyError('Issue workbench is unavailable')
            return self.issue_workbench.operate(action,payload)
        if action=='retrieval_settings':
            self.runtime.require_current()
            if not self.retrieval:raise SafetyError('Local retrieval requires the original Pi session')
            return self.retrieval.configure(payload['enabled'],payload['revision'])
        if action=='memory_query':
            self.runtime.require_current()
            if not self.catalog:raise SafetyError('Unified memory query requires the original Pi provider')
            return {'memory_query':self.catalog.query(payload['query'],payload['limit'],owned=True)}
        if action in {'maintenance_settings','maintenance_note','maintenance_search'}:
            self.runtime.require_current()
            if not self.maintenance:raise SafetyError('Maintenance memory requires the original Pi journal')
            if action=='maintenance_settings':return self.maintenance.configure(payload['enabled'],payload['revision'])
            if action=='maintenance_search':return {'maintenance_search':self.maintenance.search(payload['issue'])}
            return self.maintenance.save_note(payload['note_id'],payload['revision'],payload['title'],payload['detail'],payload['status'],payload['priority'])
        if action in {'context_settings','compaction_preview','compact_task','resolve_compaction'}:
            self.runtime.require_current()
            if not self.compaction:raise SafetyError('Context operations require the original Pi journal')
            if action=='context_settings':return self.compaction.configure(payload['config'],payload['revision'])
            if action=='resolve_compaction':return self.compaction.resolve(identifier(payload['intent_id']),payload['revision'],payload['choice'])
            identity=identifier(payload['task_id']);issue=self.compaction.issue(identity)
            if action=='compaction_preview':return {'compaction_preview':self.compaction.detail(identity,issue)}
            return self.compaction.execute(identity,issue,RepositoryGateway(self.repo),payload['revision'])
        if action.startswith('knowledge_'):
            self.runtime.require_current()
            if not self.knowledge:
                raise SafetyError('Knowledge memory requires the original Pi journal')
            if action == 'knowledge_refresh_preview':
                return {'knowledge_refresh_preview':self.knowledge.preview(payload['experience_ids'],identifier(payload['knowledge_id']))}
            if action == 'knowledge_refresh_propose':
                return self.knowledge.propose(payload['experience_ids'],payload['revision'],identifier(payload['knowledge_id']))
            if action == 'knowledge_preview':
                return {'knowledge_preview':self.knowledge.preview(payload['experience_ids'])}
            if action == 'knowledge_propose':
                return self.knowledge.propose(payload['experience_ids'], payload['revision'])
            if action == 'knowledge_review':
                return self.knowledge.review(identifier(payload['knowledge_id']),payload['revision'],payload['review_status'])
            if action == 'knowledge_revise':
                return self.knowledge.revise(identifier(payload['knowledge_id']),payload['revision'],payload['candidate'])
            if action == 'knowledge_search':
                return {'knowledge_search':self.knowledge.search(payload['issue'])}
            raise SafetyError('Unknown knowledge operation')
        if action.startswith('experience_'):
            self.runtime.require_current()
            if not self.experience:
                raise SafetyError('Experience memory requires the original Pi task journal')
            if action == 'experience_capture':
                return self.experience.capture(identifier(payload['run_id']))
            if action == 'experience_review':
                return self.experience.review(identifier(payload['run_id']), payload['revision'], payload['review_status'])
            if action == 'experience_search':
                return {'experience_search': self.experience.search(payload['issue'])}
            if action == 'experience_settings':
                memory_registry = self.experience.session.registry
                expected = json_digest({'settings': memory_registry.load('experience_settings'), 'session': self.token})
                if payload['revision'] != expected:
                    raise SafetyError('Experience settings changed; refresh before confirming')
                memory_registry.put('experience_settings', 'default', {'schema_version': 1, 'enabled': payload['enabled']})
                return {'experience_enabled_for_new_tasks': payload['enabled']}
            raise SafetyError('Unknown experience operation')
        if action in {'plan','auto','decision','prepare','publish','resume_run','recover_feedback','reconcile_feedback_cleanup','budget_preview','extend_budget'}:
            self.runtime.require_current()
        if action in {'budget_preview','extend_budget'}:
            if self.budget_registry is not self.registry:
                raise SafetyError('Shared monitor budgets require an owner-level extension')
            if action == 'budget_preview':
                return {'budget_preview':self.registry.preview_extension(payload['new_total'],self.token,self.active)}
            result = self.registry.extend_budget(payload['new_total'],payload['revision'],self.token,self.active)
            self.provider.inner.max_requests = result['new_total']
            self.claimed_plans.clear()
            self.claimed_runs.clear()
            return {'budget_change':result}
        parent = None
        task_reference = None
        continuation_feedback = None
        if action == 'reconcile_feedback_cleanup':
            if not self.feedback_cleanup:
                raise SafetyError('This journal has no controlled failure cleanup')
            return self.feedback_cleanup.reconcile(identifier(payload['run_id']),payload['revision'])
        if action == 'recover_feedback':
            if not self.failure_feedback:
                raise SafetyError('This journal has no controlled Pi failure recovery')
            return self.failure_feedback.recover(identifier(payload['run_id']),payload['revision'])
        if action == 'stop_service':
            if (not getattr(self,'stop_callback',None) or payload['revision'] !=
                    json_digest({'build':self.runtime.loaded,'session':self.token})):
                raise SafetyError('Service stop requires a fresh owned-service review')
            self.stop_callback()
            return {'service_stopping':True,'request_ledger_preserved':True}
        if action == 'resume_run':
            if self.monitor_owned:
                permit = self.monitor_continuation_permit
                if not permit or permit['job_id']!=self.active or permit['payload']!=payload:
                    raise SafetyError('监控接续需要原控制器的新意图与本次确认')
            parent = identifier(payload['run_id'])
            preview = self._resume_preview(parent,_monitor=self.monitor_owned)
            if payload['revision'] != preview['revision']:
                raise SafetyError('Previous run, source or session changed; review the continuation again')
            if payload['mode'] == 'auto' and not preview['auto_allowed']:
                raise SafetyError('Continuation cannot grant autonomous mode or reuse a manual approval')
            payload_mode = payload['mode']
            task_reference = preview.get('task_session')
            if task_reference and self.failure_feedback:
                continuation_feedback = self.failure_feedback.prepare(parent)
            payload = {'issue':preview['issue']}
            action = 'auto' if payload_mode == 'auto' else 'plan'
        if self.source_guard and action in {"auto", "prepare", "publish", "claim_run"}:
            self.source_guard()
        if action in {'claim_run', 'prepare', 'publish'}:
            self.structure.require_current()
        if action == 'structure_preview':
            return {'structure_preview': self.structure.preview(payload['query'])}
        if action == "memory_preview":
            return {"memory_preview": self.memory.preview(payload["issue"])}
        if action in {"inspect_repair", "recover_repair"}:
            identity = identifier(payload["run_id"])
            if self.registry.load("run").get(identity, {}).get("resolution"):
                raise SafetyError("Closed registration cannot authorize repair recovery")
            directory = self._run_dir(identity)  # Fixed repo/policy/workspace ownership, no JSON-selected path.
            preview = inspect_repair(directory)
            challenge = json_digest({"preview":preview, "session":self.token})
            if action == "recover_repair":
                if payload["revision"] != challenge:
                    raise SafetyError("Repair evidence or session changed; inspect again before aborting")
                result = recover_repair(directory, preview["revision"], "local-browser-operator")
                with self.lock:
                    self.repair_inspections.pop(identity, None)
                return {"run_id":identity, "workflow_status":result["status"]}
            preview["session_revision"] = challenge
            with self.lock:
                self.repair_inspections[identity] = preview
            return {"run_id":identity, "workflow_status":preview["status"], "repair_inspection":preview}
        if action in {"inspect_interruption", "close_interruption"}:
            identity = identifier(payload["run_id"])
            preview = self._inspect_interruption(identity)
            if action == "close_interruption":
                if payload["revision"] != preview["revision"]:
                    raise SafetyError("Interruption evidence or session changed; inspect again before closing")
                if not preview["can_close_registration"]:
                    raise SafetyError("Interruption cannot be closed; preserve evidence and inspect manually")
                resolution = {"schema_version": 1, "status": "registration_closed", "execution_outcome": "unknown",
                              "actor": "local-browser-operator", "time": now(),
                              "inspection_sha256": preview["revision"], "inspection": preview}
                with self.registry.transaction() as db:
                    # Re-read ownership inside the transaction; closure never resets consumed plans or budgets.
                    row = db.execute("SELECT body FROM objects WHERE kind='run' AND id=?", (identity,)).fetchone()
                    entry = json.loads(row[0]) if row else {}
                    if entry.get("run_id") != identity or entry.get("resolution"):
                        raise SafetyError("Interruption ownership or resolution changed")
                    self.registry.put("run", identity, {**entry, "resolution": resolution}, db)
                preview = self._inspect_interruption(identity)
            with self.lock:
                self.inspections[identity] = preview
            return {"run_id": identity, "workflow_status": preview["status"], "reconciliation": preview}
        if action == "claim_plan":
            identity = identifier(payload["plan_id"])
            item, plan = self._checked_plan(identity)
            if payload["revision"] != self._plan_revision(item):
                raise SafetyError("Plan claim challenge changed; refresh and review")
            with self.lock:
                self.claimed_plans.add(identity)
            return {"plan_id": identity, "fingerprint": plan.fingerprint, "claimed": True}
        if action in {"plan", "auto"}:
            if self.monitor_owned and (self.active is None or self.active!=self.monitor_model_job_id):
                raise SafetyError('请在监控事件确认本次模型调用；旧方案页面不能创建新收费任务')
            issue = payload["issue"]
            if action == 'auto' and not self.allow_auto:
                raise SafetyError("Autonomous mode was not enabled at server startup")
            if self.task_planner:
                if self.source_guard:
                    self.source_guard()
                self.structure.require_current()
                self.memory.require_current(issue)
                guard = (lambda candidate:self.failure_feedback.guard(parent,continuation_feedback,candidate)) if continuation_feedback else None
                task_id = self.task_planner.begin(issue, task_reference, continuation_feedback, guard)
                self.provider.planning_origin = {'task_id': task_id}
            if action == "plan":
                result = self._plan(issue)
                if parent:
                    self.registry.put('continuation',result['plan_id'],{'parent_run_id':parent,
                                      'plan_id':result['plan_id'],'at':now()})
                    self.plans[result['plan_id']]['view']['continued_from_run'] = parent
                    self._save_plan(result['plan_id'])
                    result['continued_from_run'] = parent
                return result
            if not self.allow_auto:
                raise SafetyError("Autonomous mode was not enabled at server startup")
            self.structure.require_current()
            self.memory.require_current(issue)
            run_id = uuid.uuid4().hex
            with self.lock:
                self._reserve_run(run_id)
                if self.task_planner:
                    self.task_planner.link('run', run_id)
            record = repair(self.repo, issue, self.provider, self.policy, "auto",
                            self.runs_dir, actor="local-browser-operator",
                            max_attempts=self.max_attempts, run_id=run_id, operation_control=self._phase,
                            freshness_guard=self.source_guard, environment_factory=self.target_environment,
                            completion_hook=self.failure_feedback.seal if self.failure_feedback else None)
            if parent:
                self.registry.put('continuation',run_id,{'parent_run_id':parent,'run_id':run_id,'at':now()})
            return self._register(record)
        if action == "decision":
            identity = identifier(payload["plan_id"])
            with self.lock:
                if identity not in self.claimed_plans:
                    raise SafetyError("Historical plan requires explicit claim and a new approval")
            item, plan = self._checked_plan(identity)
            if plan.fingerprint != payload["fingerprint"]:
                raise SafetyError("Reviewed plan fingerprint or file changed")
            if payload['decision']=='approve' and item['view'].get('repeated_failed_patch'):
                raise SafetyError('This proposal repeats a verified failed patch; generate a revised plan before approval')
            run_id = uuid.uuid4().hex
            task_link = self.registry.load('pi_task_link').get('plan:' + identity) if self.task_planner else None
            with self.lock:
                item["view"]["status"] = "consumed"
                item["view"]["run_id"] = run_id
                # Consume + run identity in one transaction, before repair can touch Git.
                with self.registry.transaction() as db:
                    self.registry.put("plan", identity, {k: v for k, v in item.items() if k != "path"}, db)
                    self.registry.put("run", run_id, {"run_id": run_id}, db)
                    if task_link:
                        self.task_planner.link('run', run_id, task_id=task_link['task_id'], db=db)
                self.runs[run_id] = {"run_id": run_id, "status": "starting"}

            def approval(current_plan):
                if self.source_guard:
                    self.source_guard()
                self.structure.require_current()
                self.memory.require_current(current_plan.issue)
                # Bind the plan actually consumed by repair, not just our earlier file read.
                if (current_plan.fingerprint != payload["fingerprint"]
                        or digest(item["path"].read_bytes()) != item["file_sha"]
                        or RepositoryGateway(self.repo).snapshot() != item["snapshot"]):
                    raise SafetyError("Plan or source changed while entering repair; approval refused")
                return payload["decision"]

            saved = SavedPlanProvider(item["path"])
            saved.planning_origin.update(plan_id=identity, file_sha256=item["file_sha"])
            if task_link:
                saved.planning_origin['task_id'] = task_link['task_id']
            return self._register(repair(self.repo, plan.issue, saved,
                                         self.policy, "approval", self.runs_dir,
                                         approval=approval,
                                         actor="local-browser-operator", max_attempts=1, run_id=run_id,
                                         freshness_guard=self.source_guard, operation_control=self._phase,
                                         environment_factory=self.target_environment,
                                         completion_hook=self.failure_feedback.seal if self.failure_feedback else None))
        identity = identifier(payload["run_id"])
        if self.registry.load("run").get(identity, {}).get("resolution"):
            raise SafetyError("Closed registration cannot authorize run operations; inspect late evidence manually")
        directory = self._run_dir(identity)
        view = self._run_view(identity)
        # Bind the complete displayed run+review, including changes made through another CLI.
        if payload["revision"] != json_digest(view):
            raise SafetyError("Run evidence changed after review; refresh before deciding")
        if action == "claim_run":
            record, _ = read_json(directory / "run.json")
            if not record.get("finished_at"):
                raise SafetyError("Interrupted repair has no final process evidence; inspect with CLI before any cleanup")
            published = record.get("integration_branch", "")
            if published and published != "integration/" + identity:
                raise SafetyError("Historical run publication ownership changed")
            current = RepositoryGateway(self.repo).snapshot(exclude_branches=("maintenance/"+identity, published))
            if record.get("source_before") != current:
                raise SafetyError("Source changed since historical run; keep it read-only")
            with self.lock:
                self.claimed_runs.add(identity)
            return {"run_id": identity, "workflow_status": view["status"], "claimed": True}
        if identity not in self.claimed_runs:
            raise SafetyError("Historical run requires explicit claim before a new operation")
        if action == "prepare":
            result = prepare_integration(directory, payload["retry_aborted"])
        elif action in {"publish", "reject_publish"}:
            review = view.get("review")
            if (not review or review.get("hash_matches") is not True
                    or review.get("diff_hash_matches") is not True
                    or review.get("review_sha256") != payload["fingerprint"]):
                raise SafetyError("Publication requires the current independent review fingerprint")
            result = integrate(directory, payload["fingerprint"] if action == "publish" else None,
                               action == "reject_publish",
                               "local-browser-operator")
        elif action == "recover":
            result = recover_integration(directory)
        elif action == "cleanup":
            result = cleanup_run(directory,
                before_cleanup=(lambda:self.feedback_cleanup.begin(identity)) if self.feedback_cleanup else None,
                after_cleanup=(lambda:self.feedback_cleanup.finish(identity)) if self.feedback_cleanup else None)
        else:
            raise SafetyError("Unknown operation")
        return {"run_id": identity, "workflow_status": result["status"]}


def validate_payload(action: str, body: dict) -> dict:
    if action.startswith('issue_'):
        from .issue_workbench import validate_action
        return validate_action(action,body)
    expected = {
        'model_probe_preview': set(), 'model_probe': {'revision'},
        'stop_job': {'job_id','revision'},
        "plan": {"issue"}, "auto": {"issue"},
        "budget_preview": {"new_total"}, "extend_budget": {"new_total","revision"},
        "resume_run": {"run_id","revision","mode"},
        "recover_feedback": {"run_id","revision"},
        "reconcile_feedback_cleanup": {"run_id","revision"},
        "stop_service": {'revision'},
        "decision": {"plan_id", "decision", "fingerprint"},
        "memory_preview": {"issue"},
        'memory_query': {'query','limit'},
        'retrieval_settings': {'enabled','revision'},
        'experience_capture': {'run_id'}, 'experience_review': {'run_id','revision','review_status'},
        'experience_search': {'issue'}, 'experience_settings': {'enabled','revision'},
        'knowledge_preview': {'experience_ids'}, 'knowledge_propose': {'experience_ids','revision'},
        'knowledge_refresh_preview': {'knowledge_id','experience_ids'},
        'knowledge_refresh_propose': {'knowledge_id','experience_ids','revision'},
        'knowledge_review': {'knowledge_id','revision','review_status'},
        'knowledge_revise': {'knowledge_id','revision','candidate'}, 'knowledge_search': {'issue'},
        'context_settings': {'config','revision'}, 'compaction_preview': {'task_id'}, 'compact_task': {'task_id','revision'},
        'resolve_compaction': {'intent_id','revision','choice'},
        'maintenance_settings': {'enabled','revision'}, 'maintenance_search': {'issue'},
        'maintenance_note': {'note_id','revision','title','detail','status','priority'},
        "structure_preview": {"query"},
        "claim_plan": {"plan_id", "revision"}, "claim_run": {"run_id", "revision"},
        "inspect_interruption": {"run_id"}, "close_interruption": {"run_id", "revision"},
        "inspect_repair": {"run_id"}, "recover_repair": {"run_id", "revision"},
        "prepare": {"run_id", "revision", "retry_aborted"},
        "publish": {"run_id", "revision", "fingerprint"},
        "reject_publish": {"run_id", "revision", "fingerprint"},
        "recover": {"run_id", "revision"}, "cleanup": {"run_id", "revision"},
    }
    if action not in expected or set(body) != expected[action] | {"request_id"}:
        raise SafetyError("Unexpected operation fields")
    identifier(body["request_id"])
    if action=='maintenance_note':
        from .maintenance_memory import clean
        if body['note_id'] is not None:identifier(body['note_id'])
        clean(body['title'],200);clean(body['detail'],1200)
        if (not isinstance(body['status'],str) or body['status'] not in {'open','deferred','closed_by_user'}
                or type(body['priority']) is not int or not 0<=body['priority']<=3):raise SafetyError('Invalid maintenance declaration')
    if 'config' in body:
        from .compaction_config import validate_config
        validate_config(body['config'])
    if 'experience_ids' in body:
        identities = body['experience_ids']
        if not isinstance(identities,list) or not 1 <= len(identities) <= 3 or len(set(map(str,identities))) != len(identities):
            raise SafetyError('Select 1-3 distinct experience IDs')
        for identity in identities:identifier(identity)
    if 'candidate' in body:
        from .knowledge_schema import validate_note
        validate_note(body['candidate'])
    if 'enabled' in body and type(body['enabled']) is not bool:
        raise SafetyError('Experience enablement requires a boolean')
    if 'review_status' in body and body['review_status'] not in {'accepted','rejected'}:
        raise SafetyError('Experience review requires accepted or rejected')
    if 'new_total' in body and (type(body['new_total']) is not int or not 1 <= body['new_total'] <= MAX_REQUESTS):
        raise SafetyError('Invalid new total request budget')
    if 'mode' in body and body['mode'] not in {'approval','auto'}:
        raise SafetyError('Invalid continuation mode')
    if action=='memory_query':
        if (not isinstance(body['query'],str) or not 1<=len(body['query'].strip())<=2000 or '\x00' in body['query']
                or type(body['limit']) is not int or not 1<=body['limit']<=50):raise SafetyError('Invalid unified memory query')
    elif 'query' in body:
        from .structure_index import text
        if not text(body['query'], 128):
            raise SafetyError('Structure query requires 1-128 characters without controls')
    if 'choice' in body and body['choice'] not in {'reconcile','abandon'}:raise SafetyError('Invalid compaction recovery choice')
    for key in ("plan_id", "run_id", "job_id", "knowledge_id", "task_id", "intent_id"):
        if key in body:
            identifier(body[key])
    for key in ("fingerprint", "revision"):
        if key in body and (not isinstance(body[key], str) or not SHA.fullmatch(body[key])):
            raise SafetyError("Expected full SHA-256")
    if "issue" in body and (not isinstance(body["issue"], str) or not body["issue"].strip()
                            or len(body["issue"]) > 2000 or "\x00" in body["issue"]):
        raise SafetyError("Issue must contain 1-2000 characters")
    if "decision" in body and body["decision"] not in {"approve", "reject"}:
        raise SafetyError("Expected approve or reject")
    if "retry_aborted" in body and type(body["retry_aborted"]) is not bool:
        raise SafetyError("Retry must be an explicit boolean")
    return {key: value for key, value in body.items() if key != "request_id"}


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafetyError("Duplicate JSON fields")
        result[key] = value
    return result


class LocalBoundary:
    """No cookies/CORS: exact Host+Origin and an ephemeral bearer capability."""

    def __init__(self, app, session: UISession, port: int, *, page_csp=None):
        self.app, self.session = app, session
        self.host = f"127.0.0.1:{port}"
        self.origin = "http://" + self.host
        self.page_csp = page_csp or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {}
        duplicate = False
        for key, value in scope["headers"]:
            if key in headers and key in {b"host", b"origin", b"authorization", b"content-type"}:
                duplicate = True
            headers[key] = value.decode("latin-1")
        error = None
        if (duplicate or headers.get(b"host") != self.host
                or headers.get(b"origin", self.origin) != self.origin
                or headers.get(b"sec-fetch-site") == "cross-site"):
            error = (403, "Local origin required")
        elif scope["path"].startswith("/api/") and not secrets.compare_digest(
                headers.get(b"authorization", "").encode("latin-1"),
                ("Bearer " + self.session.token).encode()):
            error = (401, "会话访问已失效，请使用启动脚本输出的完整链接重新打开工作台。")

        async def secure_send(message):
            if message["type"] == "http.response.start":
                policy = self.page_csp.get(scope["path"]) if not error else None
                message["headers"] = list(message["headers"]) + [
                    (k.lower().encode(), (policy if policy and k == "Content-Security-Policy" else v).encode())
                    for k, v in SECURITY_HEADERS.items()]
            await send(message)

        if error:
            await JSONResponse({"error": error[1]}, status_code=error[0])(scope, receive, secure_send)
        else:
            await self.app(scope, receive, secure_send)


def create_app(session: UISession, port: int):
    async def service(request):
        with session.lock:
            return JSONResponse({'repository':str(session.repo),'runs_dir':str(session.runs_dir),
                'policy':asdict(session.policy),'provider_binding':provider_binding(session.provider.inner),
                'allow_auto':session.allow_auto,'auto_attempts':session.max_attempts,'active':session.active,
                'runtime':session.runtime.view(),**session.budget_registry.budget(),
                'stop_revision':json_digest({'build':session.runtime.loaded,'session':session.token})})
    async def state(request):
        try:
            result = session.state()
        except SafetyError as error:
            return JSONResponse({'error':redact(str(error))},status_code=409)
        for run in result["runs"]:
            run["revision"] = json_digest({k: v for k, v in run.items() if k not in {"revision", "claimed"}})
        return JSONResponse(result)

    async def operate(request):
        try:
            if request.headers.get("content-type") != "application/json":
                return JSONResponse({"error": "JSON required"}, status_code=415)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > MAX_BODY:
                    return JSONResponse({"error": "Request body too large"}, status_code=413)
            body = json.loads(content.decode("utf-8"), object_pairs_hook=strict_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(SafetyError("Nonfinite JSON")))
            if not isinstance(body, dict):
                raise SafetyError("JSON object required")
            action = request.path_params["action"]
            payload = validate_payload(action, body)
            if action in {'issue_decide','issue_pause','issue_request_changes','issue_watch_pause','issue_watch_revoke'}:
                from starlette.concurrency import run_in_threadpool
                job = await run_in_threadpool(session.submit, body['request_id'], action, payload)
            else:
                job = session.submit(body["request_id"], action, payload)
            return JSONResponse(job, status_code=202)
        except (SafetyError, ValueError, TypeError, KeyError) as error:
            return JSONResponse({"error": redact(str(error))}, status_code=409)

    files = {"/": ASSETS / "index.html", "/app.js": ASSETS / "app.js",
             "/session-access.js": ASSETS / "session-access.js",
             "/style.css": ASSETS / "style.css",
             "/diff2html.min.js": DIFF_ASSETS / "vendor" / "diff2html.min.js",
             "/diff2html.min.css": DIFF_ASSETS / "vendor" / "diff2html.min.css"}

    async def static(request):
        return FileResponse(files[request.url.path])

    routes = [Route(path, static, methods=["GET"]) for path in files]
    routes += [Route("/api/state", state, methods=["GET"]),
               Route("/api/actions/{action}", operate, methods=["POST"]), Route('/api/service',service)]
    return LocalBoundary(Starlette(routes=routes), session, port)


def serve(session: UISession, port: int = 0):
    import uvicorn
    if type(port) is not int or not 0 <= port <= 65535:
        raise SafetyError("Invalid loopback port")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # No reuse-address on Windows: do not permit another listener to share this capability origin.
        sock.bind(("127.0.0.1", port))
        actual_port = sock.getsockname()[1]
        app = create_app(session, actual_port)
        print(f"http://127.0.0.1:{actual_port}/#session={session.token}", flush=True)
        config = uvicorn.Config(app, host="127.0.0.1", port=actual_port, access_log=False,
                                proxy_headers=False, ws="none", log_level="warning")
        server = uvicorn.Server(config)
        session.stop_callback = lambda: setattr(server,'should_exit',True)
        metadata = {'schema_version':1,'pid':os.getpid(),'repository':str(session.repo),
                    'runs_dir':str(session.runs_dir),'policy':asdict(session.policy),
                    'provider':provider_binding(session.provider.inner),'allow_auto':session.allow_auto,
                    'max_attempts':session.max_attempts,'launch_url':f'http://127.0.0.1:{actual_port}/#session={session.token}'}
        temporary = unlinked(session.registry.root/'server-private.json.tmp')
        temporary.write_text(json.dumps(metadata),encoding='utf-8')
        temporary.replace(unlinked(session.registry.root/'server-private.json'))
        server.run(sockets=[sock])
    finally:
        sock.close()
        session.close()
