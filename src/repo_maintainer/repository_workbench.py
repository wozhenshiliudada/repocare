"""Repository onboarding outside the immutable, single-repository approval session."""
from __future__ import annotations
from .maintenance_limits import LIMITS

import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import partial
from pathlib import Path

from filelock import FileLock, Timeout
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from .dashboard import unlinked
from .git import RepositoryGateway, git_env
from .github_connection import GitHubConnection, github_url
from .local_ui import LocalBoundary, UISession, create_app, strict_object
from .models import Policy, SafetyError, digest, safe_context_path
from .runtime_limits import MAX_REQUESTS
from .pi_provider import PiProvider
from .embedded_workspace import EmbeddedWorkspace, WorkspaceRouter
from .service_state import RuntimeInfo

ASSETS = Path(__file__).with_name("workbench_assets")
MAX_CATALOG_FILES = LIMITS["catalog_max_files"]
MAX_CATALOG_PATH_BYTES = LIMITS["catalog_max_path_bytes"]




def inspect_repository(path: Path):
    if not path.is_absolute() or not str(path) or len(str(path)) > 4096:
        raise SafetyError("请选择或输入仓库根目录的完整路径")
    path = unlinked(path)
    try:
        repository = RepositoryGateway(path)
    except SafetyError:
        raise SafetyError("请选择有效 Git 仓库的根目录；首版不支持子模块") from None
    status = repository.command("status", "--porcelain=v1", "-z", "--untracked-files=all")
    tracked = repository.command("ls-files", "-z").split(b"\0")
    if (len([raw for raw in tracked if raw]) > MAX_CATALOG_FILES
            or sum(map(len, tracked)) > MAX_CATALOG_PATH_BYTES):
        raise SafetyError("仓库文件目录超过当前配置容量；请检查维护限制设置")
    files = []
    for raw in tracked:
        if not raw:
            continue
        name = raw.decode("utf-8", errors="strict")
        try:
            safe_context_path(name)
            candidate = repository.safe_file(name)
            if candidate.stat().st_size <= LIMITS["source_file_max_bytes"]:
                files.append(name)
        except SafetyError:
            continue
    result = {"path": str(path), "head": repository.head,
              "branch": repository.command("symbolic-ref", "--short", "HEAD", check=False).decode().strip(),
              "clean": not status, "files": files}
    remote = repository.command("config", "--get", "remote.origin.url", check=False).decode().strip()
    try:
        result["github_url"] = github_url(remote)[1]
    except SafetyError:
        result["github_url"] = ""
    from .check_discovery import discover
    result['checks'] = discover(repository, files)
    result["revision"] = digest(json.dumps(result, sort_keys=True, ensure_ascii=False).encode())
    return result


def folder_picker():
    # A real native dialog, not an HTML upload pretending to supply a local absolute path.
    source = "import tkinter as t;from tkinter import filedialog;r=t.Tk();r.withdraw();r.attributes('-topmost',True);p=filedialog.askdirectory(title='选择本地 Git 仓库');r.destroy();print(p)"
    environment = git_env()
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run([sys.executable, "-c", source], capture_output=True,
                                timeout=120, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        raise SafetyError("文件夹选择未完成；也可以直接输入完整路径") from None
    if result.returncode or len(result.stdout) > 16384:
        raise SafetyError("本机文件夹选择器不可用；请直接输入完整路径")
    value = result.stdout.decode("utf-8").strip()
    return {"cancelled": True} if not value else inspect_repository(Path(value))


class SessionServer:
    def __init__(self, session):
        import uvicorn
        self.session = session
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(create_app(session, self.port),
            access_log=False, proxy_headers=False, ws="none", log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True)
        self.thread.start()
        import time
        deadline = time.monotonic() + 5
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        if not self.server.started:
            self.close()
            raise SafetyError("维护会话未能启动")

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/#session={self.session.token}"

    def close(self):
        self.server.should_exit = True
        self.thread.join()
        self.socket.close()
        self.session.close()


class ReactBuildError(SafetyError):
    """Fixed startup diagnostic for missing or incomplete React assets."""


class RepositoryWorkbench:
    def __init__(self, root: Path, provider_config: Path | None = None, *, request_budget=MAX_REQUESTS, allow_auto=False,
                 github=None, provider_factory=None, server_factory=None, picker=None,semantic_model_manifest=None,ui='legacy',
                 ci_download_hosts=(), ci_baseline_sources=None, ci_baseline_remote=None):
        from .ci_http import CiDownloadPolicy
        self.ci_download_policy = CiDownloadPolicy(ci_download_hosts)
        self.ci_baseline_sources = dict(ci_baseline_sources or {})
        self.ci_baseline_remote = ci_baseline_remote
        if ui not in {'legacy','react'}:
            raise SafetyError('Unknown workbench UI')
        self.ui = ui
        if ui == 'react' and not Path(__file__).with_name('workbench_ui').joinpath('index.html').is_file():
            raise ReactBuildError('缺少 React 工作台构建。源码检出请在 frontend 运行 npm ci 和 npm run build，再用原 --home 启动。')
        from .semantic_retrieval import ModelPackage
        if semantic_model_manifest is not None:ModelPackage(semantic_model_manifest)
        self.semantic_model_manifest=semantic_model_manifest
        self.root = unlinked(root)
        managed_provider = provider_config is None
        self.provider_config = unlinked(provider_config if provider_config is not None else self.root / 'provider.env')
        if not managed_provider and not self.provider_config.is_file():
            raise SafetyError("请在启动时明确指定已有本地模型配置")
        from .service_readiness import ServiceReadiness
        self.readiness = ServiceReadiness(self.provider_config)
        if type(request_budget) is not int or not 1 <= request_budget <= MAX_REQUESTS:
            raise SafetyError("请求额度无效")
        self.request_budget, self.allow_auto = request_budget, allow_auto
        self.github_push = None
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = FileLock(str(unlinked(self.root / "workbench.lock")), timeout=0)
        try:
            self.guard.acquire()
        except Timeout:
            raise SafetyError("此仓库工作台已经运行，请使用原页面") from None
        self.token, self.url = secrets.token_hex(32), None
        self.github = github or GitHubConnection()
        self.provider_factory, self.server_factory = provider_factory, server_factory or self._embedded_workspace
        self.workspaces = {}
        self.picker = picker or folder_picker
        self.jobs, self.sessions = {}, {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="repository-workbench")
        self.active = None
        self.closed = False
        self.stopping = False
        self.runtime = RuntimeInfo()
        self.stop_callback = None
        metadata = {"schema_version": 1, "provider_config": str(self.provider_config),
                    "request_budget": request_budget, "allow_auto": allow_auto, "layout": "compact-v1"}
        try:
            path = unlinked(self.root / "workbench.json")
            if path.exists():
                if path.stat().st_size > 16384 or json.loads(path.read_text(encoding="utf-8")) != metadata:
                    raise SafetyError("已有工作台配置不一致；保留原记录和额度")
            else:
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(metadata, stream)
        except BaseException:
            self.executor.shutdown(wait=True)
            self.guard.release()
            raise

        from .workbench_read import ReadPool, GitHubBrowser
        from .workbench_history import HistoryCache
        self.read_pool = ReadPool()
        self.github_browser = GitHubBrowser(self.github)
        self.history = HistoryCache(self)
        from .maintenance_monitor import MaintenanceMonitor
        try:
            self.monitor = MaintenanceMonitor(self)
        except BaseException:
            self.history.close()
            self.read_pool.close()
            self.executor.shutdown(wait=True)
            self.guard.release()
            raise

        from .workbench_correlation import WorkbenchCorrelation
        self.correlation=WorkbenchCorrelation(self)
        from .web_provider_settings import WebProviderSettings
        self.provider_settings = WebProviderSettings(self, managed_provider)
        from .model_profiles import ModelProfiles
        self.model_profiles = ModelProfiles(self)
        self.select_readiness_profile()
        from .workbench_tools import WorkbenchTools
        try:
            self.tools = WorkbenchTools(self)
        except BaseException:
            self.close()
            raise
        from .assistant import AssistantControl
        self.assistant = AssistantControl(self)

    def state(self):
        with self.lock:
            return {"request_budget": self.request_budget, "allow_auto": self.allow_auto,
                    "resource_limits": dict(LIMITS),
                    "active": self.active, "jobs": list(self.jobs.values()),
                    "monitors": self.monitor.view(),
                    "correlation":self.correlation.view(),
                    "github_push":self.github_push.view() if self.github_push else None,
                    "sessions": [{"repository": key, "launch_url": value.url} for key, value in self.sessions.items()],
                    "model_profiles": self._model_profile_view(),
                    "workspaces": [value.view() for value in self.workspaces.values()]}

    def _model_profile_view(self):
        try:
            return self.model_profiles.view()
        except (SafetyError, OSError, ValueError, TypeError):
            # An unavailable new catalog must not hide original task/approval views.
            return {'profiles': [], 'default_profile_id': None, 'revision': '',
                    'error': '模型列表暂不可读取；原任务和原账保留，请核对本机配置。'}

    def service(self):
        with self.lock:
            return {'workspace_root':str(self.root), 'provider_config':str(self.provider_config),
                    'gateway_identity':getattr(self,'gateway_identity',None),
                    'request_budget':self.request_budget, 'allow_auto':self.allow_auto,
                    'active':self.active, 'workspace_active':bool(any(w.session.active for w in self.workspaces.values())
                        or (getattr(self,'assistant',None) and self.assistant.active)),
                    'assistant_active':self.assistant.active if getattr(self,'assistant',None) else None,
                    'history_active':self.history.active,
                    'monitor_enabled':any(r['enabled'] for r in self.monitor.records.values()) or self.monitor.acceptance_watch.active(),
                    'acceptance_watch_active':self.monitor.acceptance_watch.active(),
                    'github_push_active':bool(self.github_push and self.github_push.queued),
                    'stopping':self.stopping, 'stop_revision':digest(self.token.encode()), 'runtime':self.runtime.view(),
                    'readiness':self.readiness.snapshot()}

    def _embedded_workspace(self, session):
        original_guard=session.source_guard
        def service_boundary():
            if self.stopping or self.closed:
                raise SafetyError('工作台正在停止，不接收新的模型或执行动作')
            if original_guard:original_guard()
        session.source_guard=service_boundary
        workspace = EmbeddedWorkspace(session, self.port)
        with self.lock:
            self.workspaces[workspace.identity] = workspace
        return workspace

    def submit(self, identity, action, payload):
        from .local_ui import identifier
        identifier(identity)
        signature = digest(json.dumps([action, payload], sort_keys=True, ensure_ascii=False).encode())
        with self.lock:
            if identity in self.jobs:
                if self.jobs[identity]["signature"] != signature:
                    raise SafetyError("同一请求编号不能更改内容")
                return dict(self.jobs[identity])
            if action in {'monitor_model_pause','monitor_model_revoke'}:
                if self.closed:raise SafetyError('工作台已关闭')
                result=self.monitor.model_authorization.control(action,payload,identity)
                job={'id':identity,'action':action,'signature':signature,'status':'finished','result':result}
                self.jobs[identity]=job
                return dict(job)
            if action=='monitor_validation_stop':
                if self.closed:raise SafetyError('工作台已关闭')
                result=self.monitor.validation.stop(payload,identity)
                job={'id':identity,'action':action,'signature':signature,'status':'finished','result':result}
                self.jobs[identity]=job
                return dict(job)
            if action in {'monitor_delivery_watch_pause','monitor_failure_pause'}:
                if self.closed:raise SafetyError('工作台已关闭')
                result=self.monitor.operate(action,payload)
                job={'id':identity,'action':action,'signature':signature,'status':'finished','result':result}
                self.jobs[identity]=job
                return dict(job)
            if self.closed or self.stopping or self.active or len(self.jobs) >= 100:
                raise SafetyError("请等待当前操作完成；每次启动最多100项操作")
            job = {"id": identity, "action": action, "signature": signature, "status": "running", "result": None}
            self.jobs[identity], self.active = job, identity
            self.executor.submit(self._execute, job, payload)
            return dict(job)

    def _execute(self, job, payload):
        try:
            action = job["action"]
            if action == 'monitor_start':
                self.provider_settings.require_ready()
            if action in {'connect','task_model_workspace','monitor_start','monitor_resume','monitor_check','monitor_open',
                          'monitor_rules_preview','monitor_rules_configure','monitor_repair_preview','monitor_repair_start',
                          'capability_preview','capability_execute'}:
                self.runtime.require_current()
            if action in {'maintenance_link_preview','maintenance_link_confirm'}:
                method='preview' if action=='maintenance_link_preview' else 'confirm'
                result=getattr(self.correlation,method)(payload)
            elif action == "local":
                result = inspect_repository(Path(payload["path"]))
            elif action == 'saved_projects':
                from .workbench_reports import saved_projects
                result = saved_projects(self.root, payload['page'])
            elif action in {'export_dashboard', 'export_comparison'}:
                from .workbench_reports import export_report
                result = export_report(self.root, job['id'], action.removeprefix('export_'), payload['inputs'])
            elif action == 'capability_preview':
                result = self.tools.preview(payload['capability'], payload['params'])
            elif action == 'capability_execute':
                result = self.tools.execute(payload, job['id'])
            elif action == "pick":
                result = self.picker()
            elif action == 'analyze_repo':
                source = inspect_repository(Path(payload['path']))
                if source['revision'] != payload['revision']:
                    raise SafetyError('仓库预览已变化，请重新检查后分析')
                from .check_discovery import analyze_readonly
                result = analyze_readonly(RepositoryGateway(Path(source['path'])), source)
                if inspect_repository(Path(source['path']))['revision'] != source['revision']:
                    raise SafetyError('分析期间仓库已变化，请重新检查')
            elif action == "account":
                result = self.github.account()
            elif action == "repositories":
                result = self.github.repositories(payload["page"])
            elif action == "login":
                def update(value):
                    with self.lock:
                        job["result"] = value
                result = self.github.login(update)
            elif action == "clone":
                slug, url = github_url(payload["url"])
                identity = digest(url.casefold().encode())[:16]
                destination = unlinked(self.root / "clones" / identity)
                completion = unlinked(self.root / "clones" / (identity + ".json"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                if completion.exists():
                    if completion.stat().st_size > 16384 or json.loads(completion.read_text()) != {"url": url, "path": str(destination)}:
                        raise SafetyError("克隆归属记录异常，请保留现场")
                    repository = RepositoryGateway(destination)
                    remote = repository.command("config", "--get", "remote.origin.url").decode().strip()
                    if remote != url:
                        raise SafetyError("已有克隆的远程地址已变化，请保留现场")
                else:
                    self.github.clone(url, destination)
                    inspect_repository(destination)
                    with completion.open("x", encoding="utf-8") as stream:
                        json.dump({"url": url, "path": str(destination)}, stream)
                result = inspect_repository(destination)
                result["github_repository"] = slug
            elif action == "connect":
                result = self.connect(payload)
            elif action == 'task_model_workspace':
                result = self.task_model_workspace(payload)
            elif action == 'stop_workbench':
                with self.lock:
                    status = self.service()
                    if (self.stopping or payload['revision'] != status['stop_revision'] or status['workspace_active'] or status['history_active']
                            or status['monitor_enabled'] or self.monitor.queued
                            or status['github_push_active'] or not self.stop_callback):
                        raise SafetyError('工作台正在运行或停止核验已变化；保留当前任务')
                    self.stopping=True
                    result = {'stopping':True}
                    self.stop_callback()
            elif action.startswith("monitor_"):
                result = self.monitor.operate(action, payload)
            else:
                raise SafetyError("未知仓库操作")
            if action in {'local', 'pick', 'clone'} and isinstance(result, dict) and 'path' in result:
                saved_path = unlinked(self.root / 'projects' /
                    digest(os.path.normcase(result['path']).encode())[:16] / 'project.json')
                if saved_path.is_file():
                    if saved_path.stat().st_size > 16384:
                        raise SafetyError('已有维护配置异常，保留原记录')
                    saved = json.loads(saved_path.read_text(encoding='utf-8'))
                    if saved.get('repository') != result['path']:
                        raise SafetyError('已有维护配置归属不一致，保留原记录')
                    result['saved_scope'] = {'policy': saved['policy'], 'allow_auto': saved['allow_auto']}
            with self.lock:
                job.update(status="finished", result=result)
        except Exception as error:
            # A filesystem/subprocess exception can contain a credential or local raw output.
            message = str(error) if isinstance(error, SafetyError) else "操作未完成，请检查路径、仓库或环境；已有记录保留"
            with self.lock:
                job.update(status="failed", result={"error": message})
        finally:
            with self.lock:
                self.active = None

    def connect(self, payload, *, profile_id_override=None):
        profile_id_override = profile_id_override or payload.get('profile_id')
        if profile_id_override is not None:
            self.model_profiles.resolve_selectable(profile_id_override)
        # Legacy missing-config errors stay before scope parsing, as before.
        if profile_id_override is None and not self.provider_config.is_file():
            self.model_profiles.resolve()
        if payload["confirmed"] is not True:
            raise SafetyError("请确认维护文件范围和测试命令")
        source = inspect_repository(Path(payload["path"]))
        if not source["clean"] or source["revision"] != payload["revision"]:
            raise SafetyError("仓库有未提交修改或预览已变化，请保留修改并重新检查")
        repository = RepositoryGateway(Path(source["path"]))
        if self.root.is_relative_to(repository.root) or self.root.is_relative_to(repository.common_dir):
            raise SafetyError("工作台状态目录必须在目标仓库和 Git 元数据之外")
        policy = Policy(tuple(payload["allowed_paths"]), tuple(tuple(c) for c in payload["allowed_tests"]),
                        payload["timeout_seconds"], tuple(payload["readonly_paths"]))
        if not policy.allowed_paths:
            raise SafetyError('请选择至少一个可修改源码文件；仅查看项目可使用“先只读分析”。')
        policy.validate_scope()
        if len(policy.allowed_paths) + len(policy.readonly_paths) > LIMITS["source_context_max_files"]:
            raise SafetyError("每个会话的可写及只读文件数量超过当前配置")
        for name in (*policy.allowed_paths, *policy.readonly_paths):
            if name not in source["files"]:
                raise SafetyError(f"请选择预览中的已追踪普通文件；单个文件上限{LIMITS['source_file_max_bytes']}字节")
            repository.read(name).decode("utf-8")
        if payload["allow_auto"] and not self.allow_auto:
            raise SafetyError("启动时未授权自主模式")
        key = source["path"]
        directory = unlinked(self.root / "projects" / digest(os.path.normcase(key).encode())[:16])
        directory.mkdir(parents=True, exist_ok=True)
        specification = {"repository": key, "policy": asdict(policy), "allow_auto": payload["allow_auto"]}
        # Roundtrip arrays rather than dataclass tuples for exact comparison after restart.
        specification = json.loads(json.dumps(specification))
        policy_file = unlinked(directory / "project.json")
        primary_id = 'legacy'
        if policy_file.exists():
            if policy_file.stat().st_size > 16384:
                raise SafetyError("该仓库已有固定授权范围；本轮不能换范围并重置原额度")
            original = json.loads(policy_file.read_text(encoding='utf-8'))
            if (not isinstance(original, dict) or
                    set(original) not in (set(specification), set(specification) | {'model_profile_id'}) or
                    {k: original[k] for k in specification} != specification):
                raise SafetyError("该仓库已有固定授权范围；本轮不能换范围并重置原额度")
            primary_id = original.get('model_profile_id', 'legacy')
            if (primary_id != 'legacy' and (not isinstance(primary_id, str) or
                    not re.fullmatch(r'[a-f0-9]{32}', primary_id))):
                raise SafetyError('原项目模型归属异常，保留原记录')
        else:
            if any(directory.iterdir()):
                raise SafetyError('原项目缺少维护归属文件，保留已有目录、任务和账本，不重建模型会话')
            primary_id = profile_id_override or self.model_profiles.view().get('default_profile_id') or 'legacy'
            if primary_id != 'legacy':
                self.model_profiles.resolve_selectable(primary_id)  # Validate before writing a new scope.
                specification['model_profile_id'] = primary_id
            else:
                if self.model_profiles.view()['profiles'][0]['archived']:
                    raise SafetyError('原模型配置已删除，请先恢复或选择其他模型。')
                self.provider_settings.require_ready()
            with policy_file.open("x", encoding="utf-8") as stream:
                json.dump(specification, stream, ensure_ascii=False)
        selected_id = profile_id_override or primary_id
        if not isinstance(selected_id, str) or (selected_id != 'legacy' and not re.fullmatch(r'[a-f0-9]{32}', selected_id)):
            raise SafetyError('模型配置标识无效')
        # Keep the primary key and directory exactly, including every legacy journal.
        session_key = key if selected_id == primary_id else key + '::model:' + selected_id
        session_directory = directory if selected_id == primary_id else unlinked(directory/'model-workspaces'/selected_id)
        if selected_id == 'legacy':
            self.provider_settings.require_ready()
            profile = {'id': 'legacy', 'name': '原模型配置', 'model': '原模型', 'path': self.provider_config}
            try:
                legacy_view = self.provider_settings.view()
                if legacy_view.get('configured'):
                    profile['model'] = legacy_view['model']
            except (SafetyError, OSError, ValueError):
                pass  # Explicit injected providers retain their original no-credential test seam.
        else:
            profile = self.model_profiles.resolve(selected_id)
        if session_key in self.sessions:
            server = self.sessions[session_key]
            workspace = self._workspace_for_session(server.session)
            return {"launch_url": server.url, "repository": key, "reused": True,
                    'workspace': workspace.view() if workspace else None}
        session_directory.mkdir(parents=True, exist_ok=True)
        source_guard = None
        if selected_id != 'legacy':
            # Each persistent context pins its immutable configuration. Never add
            # metadata to a legacy context or rewrite the original registry.
            binding = {'schema_version': 1, 'profile_id': selected_id,
                       'config_sha256': profile['config_sha256']}
            binding_path = unlinked(session_directory/'model-binding.json')
            if binding_path.exists():
                if (not binding_path.is_file() or binding_path.stat().st_size > 4096
                        or json.loads(binding_path.read_text(encoding='utf-8')) != binding):
                    raise SafetyError('模型会话配置已变化，保留原任务和账本')
            else:
                if (session_directory/'runs').exists():
                    raise SafetyError('原模型会话缺少归属记录，保留原任务和账本')
                with binding_path.open('x', encoding='utf-8') as stream:
                    json.dump(binding, stream)
            def source_guard():
                current = self.model_profiles.resolve(selected_id)
                if (current['path'] != profile['path'] or
                        current['config_sha256'] != binding['config_sha256']):
                    raise SafetyError('模型会话配置已变化，保留原任务和账本')
                current_path = unlinked(binding_path)
                if (not current_path.is_file() or current_path.stat().st_size > 4096
                        or json.loads(current_path.read_text(encoding='utf-8')) != binding):
                    raise SafetyError('模型会话归属已变化，保留原任务和账本')
        resources = self.tools.binding(key)
        provider = self.provider_factory(policy) if self.provider_factory else PiProvider(
            policy.allowed_paths, readonly_paths=policy.readonly_paths, max_requests=self.request_budget)
        if (session_directory/'runs/ui-state/registry.sqlite3').is_file() and not self.provider_factory:
            from .service_state import saved_budget
            provider.max_requests = saved_budget(session_directory/'runs')
        if not getattr(provider,'mock_url',None):
            provider.config_file = profile['path']
        if resources:
            provider.memory_root = Path(resources['source_root']) if resources['source_root'] else None
            provider.memory_index = Path(resources['index']) if resources['index'] else None
            provider.memory_relations = Path(resources['relations']) if resources['relations'] else None
            provider.memory_relations_context = resources['relations_context'] == 'yes'
        session = UISession(repository.root, policy, provider, session_directory / "runs",
                            allow_auto=payload["allow_auto"], max_attempts=2 if payload["allow_auto"] else 1,
                            label="Pi · 当前仓库（按本地模型配置计费）",semantic_model_manifest=self.semantic_model_manifest,
                            structure_index=Path(resources['structure_index']) if resources.get('structure_index') else None,
                            structure_context_query=resources.get('structure_query') or None,
                            source_guard=source_guard)
        session.repository_picker_url = self.url
        session.model_profile_id = selected_id
        session.model_name = profile['model']
        session.model_profile_name = profile['name']
        session.correlation=self.correlation
        from .issue_workbench import IssueWorkbench
        session.issue_workbench = IssueWorkbench(session,self.github,permit_auto=self.allow_auto)
        try:
            server = self.server_factory(session)
        except BaseException:
            session.close()
            raise
        self.sessions[session_key] = server
        workspace = self._workspace_for_session(session)
        return {"launch_url": server.url, "repository": key, "reused": False,
                'workspace': workspace.view() if workspace else None}

    def _workspace_for_session(self, session):
        return next((w for w in self.workspaces.values() if w.session is session), None)

    def original_model_limits(self):
        """Preview the original monitor provider, never a different UI default."""
        from .provider_config import load_provider_config, resolve_model_limits
        runtime = self.runtime.view()
        values = {key: runtime[key] for key in ('max_output_tokens', 'planning_timeout_seconds')}
        try:
            config = load_provider_config(self.provider_config)
        except (SafetyError, OSError, ValueError):
            # An explicitly injected provider factory may not consume an env
            # file at all (e.g. offline replay). Preserve that original contract.
            # The ordinary Pi path still requires a valid original config.
            if self.provider_factory is None:
                raise
            return values
        values['max_output_tokens'] = resolve_model_limits(config)['max_output_tokens']
        return values

    def select_readiness_profile(self):
        # Installation readiness describes the new-task default only. The
        # monitor's original provider_config and all existing sessions stay put.
        from .service_readiness import ServiceReadiness
        try:
            path = self.model_profiles.resolve_selectable()['path']
        except (SafetyError, OSError, ValueError, TypeError):
            path = None
        with self.lock:
            if self.readiness.provider_config != path:
                self.readiness = ServiceReadiness(path)
            return self.readiness

    def task_model_workspace(self, payload):
        """Select an owned project/model context, never mutate or execute an old task."""
        if payload.get('confirmed') is not True:
            raise SafetyError('请确认新任务使用的模型和独立额度')
        workspace = self.workspaces.get(payload['workspace_id'])
        if workspace is None or not any(server.session is workspace.session for server in self.sessions.values()):
            raise SafetyError('请先明确连接原项目；监控任务继续使用原模型绑定')
        profile = self.model_profiles.resolve_selectable(payload['profile_id'])
        session = workspace.session
        source = inspect_repository(session.repo)
        policy = session.policy
        result = self.connect({'path': str(session.repo), 'revision': source['revision'],
            'allowed_paths': list(policy.allowed_paths), 'readonly_paths': list(policy.readonly_paths),
            'allowed_tests': [list(command) for command in policy.allowed_tests],
            'timeout_seconds': policy.timeout_seconds, 'allow_auto': session.allow_auto,
            'confirmed': True}, profile_id_override=profile['id'])
        if result['workspace'] is None:
            raise SafetyError('模型会话未挂入原工作台，保留原任务')
        return result

    def ci_baseline(self, packet):
        from .ci_success_baseline import baseline_for_packet
        return baseline_for_packet(packet, sources=self.ci_baseline_sources, remote=self.ci_baseline_remote)

    def configure_github_push(self, path):
        if self.github_push is not None or self.closed:
            raise SafetyError('推送入口已配置或服务已关闭')
        from .github_push_control import GitHubPushControl
        self.github_push = GitHubPushControl.from_file(self, path)
        return self.github_push.view()

    def close(self):
        if self.closed:
            return
        self.stopping=True
        if getattr(self,'assistant',None) is not None:
            self.assistant.close()
        if self.github_push is not None:
            self.github_push.close()
        def close_sessions():
            # UIRegistry's FileLock belongs to the worker that constructed UISession.
            # Release it on that same worker; preserve the existing registry protocol.
            for server in self.sessions.values():
                server.close()
            self.monitor.close()
        try:
            self.monitor.stop()
            self.history.close()
            self.read_pool.close()
            self.executor.submit(close_sessions).result()
        finally:
            self.executor.shutdown(wait=True)
            self.guard.release()
            self.closed = True


def validate_request(action, value):
    from .local_ui import identifier
    fields = {"local": {"path"}, "pick": set(), "login": set(), "account": set(),
              "repositories": {"page"}, "clone": {"url"}, "connect": {"path", "revision", "allowed_paths",
              "readonly_paths", "allowed_tests", "timeout_seconds", "allow_auto", "confirmed"}}
    fields['stop_workbench'] = {'revision'}
    fields['task_model_workspace'] = {'workspace_id', 'profile_id', 'confirmed'}
    fields['analyze_repo'] = {'path', 'revision'}
    fields['saved_projects'] = {'page'}
    fields['export_dashboard'] = fields['export_comparison'] = {'inputs'}
    fields['capability_preview'] = {'capability','params'}
    fields['capability_execute'] = {'preview_id','revision','confirmed'}
    from .monitor_rules import FIELDS as ROOT_FIELDS
    fields['monitor_rules_preview'] = ROOT_FIELDS
    fields['monitor_rules_configure'] = {'preview','revision','confirmed'}
    fields['monitor_repair_preview'] = {'monitor_id','event_id','max_attempts'}
    fields['monitor_repair_start'] = {'preview','revision','confirmed'}
    fields['maintenance_link_preview']={'left','right'}
    fields['maintenance_link_confirm']={'preview','revision','confirmed'}
    fields['monitor_validation_clarification_preview']={'monitor_id','event_id','revision','answer'}
    fields['monitor_validation_clarify']={'monitor_id','event_id','preview','revision','confirmed'}
    fields['monitor_validation_preview']={'monitor_id','event_id','request_text'}
    fields['monitor_validation_propose']={'preview','revision','confirmed'}
    fields['monitor_validation_review']={'monitor_id','event_id','revision','decision'}
    for name in ('monitor_validation_prepare','monitor_validation_reconcile','monitor_validation_stop'):
        fields[name]={'monitor_id','event_id','revision'}
    monitor_run={'monitor_id','event_id','run_id'}
    for delivery_action in ('monitor_delivery_prepare_local','monitor_delivery_push_preview','monitor_delivery_pr_preview',
                            'monitor_delivery_acceptance_preview'):
        fields[delivery_action]=monitor_run
    fields['monitor_delivery_publish_local']=monitor_run|{'revision'}
    for delivery_action in ('monitor_delivery_push','monitor_delivery_pr'):
        fields[delivery_action]=monitor_run|{'revision','approval_sha256'}
    fields['monitor_delivery_reconcile']=monitor_run|{'kind'}
    fields['monitor_delivery_acceptance_verify']=monitor_run|{'acceptance_id','revision','approval_sha256'}
    fields['monitor_delivery_watch_preview']=monitor_run|{'interval_seconds','expires_at'}
    fields['monitor_delivery_watch_configure']=monitor_run|{'preview','revision','confirmed'}
    for name in ('monitor_delivery_watch_pause','monitor_delivery_watch_resume'):
        fields[name]=monitor_run|{'watch_id','revision'}
    fields["monitor_start"] = {"path", "revision", "allowed_paths", "readonly_paths", "allowed_tests",
                               "timeout_seconds", "confirmed", "branch", "github_url", "interval_seconds"}
    for name in ("monitor_pause", "monitor_resume", "monitor_check"):
        fields[name] = {"monitor_id"}
    fields["monitor_open"] = {"monitor_id", "event_id"}
    fields['monitor_recovery_preview'] = {'monitor_id', 'event_id'}
    fields['monitor_continuation_inspect'] = {'monitor_id','event_id','run_id','mode'}
    fields['monitor_continuation_recover'] = {'preview','revision','confirmed'}
    fields['monitor_continuation_start'] = {'preview','revision','confirmed'}
    fields['monitor_model_preview'] = {'monitor_id','expires_at','max_attempts'}
    fields['monitor_model_configure'] = {'preview','revision','confirmed'}
    fields['monitor_model_pause'] = fields['monitor_model_revoke'] = {'monitor_id','grant_id'}
    fields['monitor_failure_preview'] = {'monitor_id','event_id','max_successors','expires_at'}
    fields['monitor_failure_configure'] = {'preview','revision','confirmed'}
    for name in ('monitor_failure_pause','monitor_failure_resume'):
        fields[name] = {'monitor_id','grant_id','revision'}
    accepted = fields.get(action, set()) | {'request_id'}
    if action == 'connect' and isinstance(value, dict) and 'profile_id' in value:
        accepted = accepted | {'profile_id'}
    if not isinstance(value, dict) or action not in fields or set(value) != accepted:
        raise SafetyError("仓库操作字段不匹配")
    identifier(value["request_id"])
    if action == 'connect' and 'profile_id' in value:
        if (not isinstance(value['profile_id'], str) or (value['profile_id'] != 'legacy'
                and not re.fullmatch(r'[a-f0-9]{32}', value['profile_id']))):
            raise SafetyError('模型配置标识无效')
    if action == 'task_model_workspace':
        identifier(value['workspace_id'])
        if (value['confirmed'] is not True or not isinstance(value['profile_id'], str)
                or (value['profile_id'] != 'legacy' and not re.fullmatch(r'[a-f0-9]{32}', value['profile_id']))):
            raise SafetyError('请选择本工作台的有效模型配置并确认')
    if action == 'capability_preview':
        from .workbench_tools import parameters
        parameters(value['capability'], value['params'])
    if action == 'capability_execute':
        identifier(value['preview_id'])
        if value['confirmed'] is not True or not re.fullmatch(r'[a-f0-9]{64}', str(value['revision'])):
            raise SafetyError('请确认当前工具预览')
    for name in ("path", "url", "revision"):
        if name in value and (not isinstance(value[name], str) or not value[name] or len(value[name]) > 4096
                              or any(ord(c) < 32 for c in value[name])):
            raise SafetyError("仓库路径或地址无效")
    if action in {"connect", "monitor_start"}:
        if not re.fullmatch(r"[a-f0-9]{64}", value["revision"]):
            raise SafetyError("仓库预览指纹无效")
        for name in ("allowed_paths", "readonly_paths"):
            if not isinstance(value[name], list) or len(value[name]) > LIMITS["source_context_max_files"] or not all(isinstance(p, str) for p in value[name]):
                raise SafetyError("文件范围无效")
        if len(value["allowed_paths"]) + len(value["readonly_paths"]) > LIMITS["source_context_max_files"]:
            raise SafetyError(f"最多选择{LIMITS['source_context_max_files']}个可写及只读文件")
        tests = value["allowed_tests"]
        if (not isinstance(tests, list) or not 1 <= len(tests) <= 5 or any(
                not isinstance(c, list) or not 1 <= len(c) <= 32 or any(
                    not isinstance(a, str) or len(a) > 1024 for a in c) for c in tests)):
            raise SafetyError("测试命令需要1至5个明确的参数数组")
        if type(value["timeout_seconds"]) not in {float, int} or not 0 < value["timeout_seconds"] <= LIMITS['test_timeout_max_seconds']:
            raise SafetyError(f"测试超时应大于0且不超过{LIMITS['test_timeout_max_seconds']}秒")
        if any(type(value[name]) is not bool for name in (("allow_auto", "confirmed") if action == "connect" else ("confirmed",))):
            raise SafetyError("请明确确认权限模式")
    if action == "monitor_start":
        from .github_connection import branch_name
        branch_name(value["branch"])
        if not isinstance(value["github_url"], str):
            raise SafetyError("监控地址无效")
        if value["github_url"]:
            github_url(value["github_url"])
        if type(value["interval_seconds"]) is not int or not 60 <= value["interval_seconds"] <= 3600:
            raise SafetyError("监控间隔须为60至3600秒")
    for name in ("monitor_id", "event_id"):
        if name in value and (not isinstance(value[name], str) or not re.fullmatch(r"[a-f0-9]{16}", value[name])):
            raise SafetyError("监控编号无效")
    if action in {"repositories", 'saved_projects'} and (type(value["page"]) is not int or not 1 <= value["page"] <= 99):
        raise SafetyError("仓库页码无效")
    if action in {'export_dashboard', 'export_comparison'}:
        paths = value['inputs']
        if (not isinstance(paths, list) or not 1 <= len(paths) <= (100 if action == 'export_dashboard' else 1)
                or any(not isinstance(p, str) or not p or len(p) > 4096
                       or any(ord(c) < 32 for c in p) or not Path(p).is_absolute() for p in paths)):
            raise SafetyError('请提供已有运行目录或摘要文件的完整本地路径')
    if action == "clone":
        github_url(value["url"])
    return {key: item for key, item in value.items() if key != "request_id"}


def create_workbench_app(workbench, port):
    workbench.port = port
    async def state(request):
        return JSONResponse(workbench.state())
    async def service(request):
        return JSONResponse(workbench.service())
    async def readiness(request):
        if request.query_params:
            return JSONResponse({'error':'本地自检不接受路径、模型或其他查询参数'}, status_code=400)
        try:
            return JSONResponse(await workbench.read_pool.run(lambda: workbench.select_readiness_profile().inspect(force=True)))
        except (SafetyError, OSError, ValueError, TypeError):
            return JSONResponse({'error':'本地自检暂不可用；未调用模型或修改运行记录'}, status_code=409)
    async def capability_definitions(request):
        if request.query_params:
            return JSONResponse({'error':'能力定义不接受额外参数'},status_code=400)
        return JSONResponse(workbench.tools.definitions())
    async def export_download(request):
        from .workbench_reports import read_artifact
        try:
            if request.query_params:
                raise SafetyError('下载不接受路径或查询参数')
            name = request.path_params['name']
            data, media_type = await workbench.read_pool.run(read_artifact, workbench, request.path_params['identity'], name)
            return Response(data, media_type=media_type, headers={
                'Content-Disposition': f'attachment; filename="{name}"'})
        except (SafetyError, OSError, ValueError, KeyError):
            return JSONResponse({'error': '导出记录或文件不可用，请核对原记录'}, status_code=409)
    async def provider_settings(request):
        try:
            if request.query_params:
                raise SafetyError('模型设置不接受查询参数')
            if request.method == 'GET':
                return JSONResponse(workbench.provider_settings.view())
            if request.headers.get('content-type') != 'application/json':
                return JSONResponse({'error': 'JSON required'}, status_code=415)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 16384:
                    return JSONResponse({'error': 'Request body too large'}, status_code=413)
            value = json.loads(content.decode(), object_pairs_hook=strict_object)
            # A separate endpoint keeps credentials out of queued jobs and state projections.
            result = workbench.provider_settings.save(value)
            return JSONResponse(result)
        except (SafetyError, ValueError, TypeError, OSError):
            return JSONResponse({'error': '无法保存模型设置：请检查 HTTPS URL、模型名和 Key；已有维护记录或配置变化时请刷新核对。'}, status_code=409)
    async def model_profiles(request):
        try:
            if request.query_params:
                raise SafetyError('模型列表不接受查询参数')
            if request.method == 'GET':
                return JSONResponse(await workbench.read_pool.run(workbench.model_profiles.view))
            if request.headers.get('content-type') != 'application/json':
                return JSONResponse({'error':'JSON required'},status_code=415)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 16384:
                    return JSONResponse({'error':'Request body too large'},status_code=413)
            value = json.loads(content.decode(),object_pairs_hook=strict_object)
            if not isinstance(value,dict) or value.get('action') not in {'create','set_default','reasoning_variant','archive','restore'}:
                raise SafetyError('模型配置操作无效')
            # Credentials are never queued in jobs or returned by state.
            method = getattr(workbench.model_profiles, value['action'])
            result = method(value)
            workbench.select_readiness_profile()
            return JSONResponse(result)
        except (SafetyError, ValueError, TypeError, OSError):
            return JSONResponse({'error':'模型配置无法读取或保存；请刷新核对。原任务及原账保持，不自动重试。'},status_code=409)
    async def provider_models(request):
        # Discovery is an explicit read of one provider directory, not a queued
        # maintenance operation. Never copy its credentials into jobs or state.
        from .provider_models import discover_provider_models
        try:
            if request.query_params:
                raise SafetyError('模型目录不接受查询参数')
            if request.headers.get('content-type') != 'application/json':
                return JSONResponse({'error': 'JSON required'}, status_code=415)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 16384:
                    return JSONResponse({'error': 'Request body too large'}, status_code=413)
            value = json.loads(content.decode(), object_pairs_hook=strict_object)
            if not isinstance(value, dict):
                raise SafetyError('模型目录字段无效')
            if set(value) == {'profile_id'}:
                profile_id = value['profile_id']
                if (not isinstance(profile_id, str) or
                        (profile_id != 'legacy' and not re.fullmatch(r'[a-f0-9]{32}', profile_id))):
                    raise SafetyError('模型配置标识无效')
                # Only this original profile's exact endpoint can receive its key.
                # The browser cannot supply an alternate URL alongside its ID.
                with workbench.lock:
                    profile = workbench.model_profiles.resolve_selectable(value['profile_id'])
                    config, _ = workbench.model_profiles._provider(profile['path'], profile['config_sha256'])
                    value = {'base_url': config['PI_MAINTENANCE_BASE_URL'],
                             'api_key': config['PI_MAINTENANCE_API_KEY']}
            elif set(value) != {'base_url', 'api_key'}:
                raise SafetyError('模型目录字段无效')
            return JSONResponse(await workbench.read_pool.run(discover_provider_models, value))
        except (SafetyError, ValueError, TypeError, OSError):
            return JSONResponse({'error': '无法获取可用模型。请检查 API 地址、密钥及服务商是否提供 /models；未调用模型、未更改配置。'}, status_code=409)

    async def help_view(request):
        from .help_docs import help_document
        try:
            if (set(request.query_params)-{'language'} or len(request.query_params.getlist('language'))>1):
                raise SafetyError('教程只接受界面语言')
            guide = await workbench.read_pool.run(help_document,request.query_params.get('language','zh-CN'))
            if tutorial_assets:
                guide['illustrated_guide'] = True
            return JSONResponse(guide)
        except (SafetyError,OSError,ValueError,TypeError):
            return JSONResponse({'error':'教程暂不可读取'},status_code=409)
    async def assistant_view(request):
        try:
            if request.query_params:raise SafetyError('会话读取不接受路径或其他参数')
            return JSONResponse(await workbench.read_pool.run(workbench.assistant.view))
        except (SafetyError,OSError,ValueError,TypeError,sqlite3.Error):
            return JSONResponse({'error':'会话记录暂不可读取；保留原账和历史'},status_code=409)
    async def assistant_action(request):
        try:
            if request.query_params:raise SafetyError('会话操作不接受查询参数')
            if request.headers.get('content-type')!='application/json':
                return JSONResponse({'error':'JSON required'},status_code=415)
            content=bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content)>256000:return JSONResponse({'error':'Request body too large'},status_code=413)
            value=json.loads(content.decode(),object_pairs_hook=strict_object)
            workbench.runtime.require_current()
            job=workbench.assistant.submit(request.path_params['action'],value)
            return JSONResponse(job,status_code=202)
        except (SafetyError,OSError,ValueError,TypeError):
            return JSONResponse({'error':'会话操作未接收；请检查当前字段与活动状态，保留原记录核对。'},status_code=409)
    async def delivery_inbox(request):
        from .delivery_inbox import projection
        try:
            if request.query_params:raise SafetyError('交付待办不接受路径或任意参数')
            return JSONResponse(await workbench.read_pool.run(projection,workbench))
        except (SafetyError,OSError,ValueError,KeyError,TypeError,sqlite3.Error):
            return JSONResponse({'error':'原交付证据读取失败；保留原记录，不执行操作'},status_code=409)
    from .workbench_read import ReadError, local_view
    async def monitor_history_view(request):
        from .monitor_history import history
        try:
            if request.query_params:
                raise ReadError('历史入口不接受路径或其他参数',400)
            result = await workbench.read_pool.run(history, workbench.monitor,
                request.path_params['monitor_id'], request.path_params['event_id'])
            return JSONResponse(result)  # LocalBoundary supplies the shared no-store/security headers.
        except (SafetyError, ValueError, TypeError, OSError, KeyError, sqlite3.Error):
            return JSONResponse({'error':'原事件历史读取失败；请保留现场，不会重建登记或额度'}, status_code=409)
    async def read_view(request):
        try:
            # Parameters remain data, never arbitrary commands, hosts, refs or API paths.
            if len(str(request.url.query)) > 8192 or any(len(request.query_params.getlist(k)) != 1 for k in request.query_params):
                raise ReadError('读取参数无效',400)
            if 'workspace_id' in request.path_params:
                if set(request.query_params) - {'path', 'page', 'query', 'revision'}:
                    raise ReadError('未知文件参数',400)
                with workbench.lock:
                    workspace = workbench.workspaces.get(request.path_params['workspace_id'])
                if workspace is None:
                    raise ReadError('本地会话已变化，请重新连接仓库',404)
                kind = request.path_params['kind']
                if kind != 'tree' and set(request.query_params) - {'path'}:
                    raise ReadError('此读取不接受文件分页参数',400)
                result = await workbench.read_pool.run(partial(local_view,workspace,kind,request.query_params.get('path'),
                    page=int(request.query_params.get('page','1')),query=request.query_params.get('query',''),
                    revision=request.query_params.get('revision')))
            else:
                if set(request.query_params) - {'url','page'}:
                    raise ReadError('未知 GitHub 参数',400)
                result = await workbench.read_pool.run(workbench.github_browser.view,
                    request.path_params['kind'],request.query_params.get('url'),int(request.query_params.get('page','1')))
            return JSONResponse(result)
        except (SafetyError,ValueError,TypeError,OSError,KeyError) as error:
            return JSONResponse({'error':str(error) if isinstance(error,SafetyError) else '资料读取失败，请检查来源并手动刷新',
                                 'code':'read_error'},status_code=getattr(error,'status',409))
    async def ci_read_view(request):
        from functools import partial
        from .ci_evidence import CiEvidenceReader
        from .ci_http import CiHttpTransport
        try:
            query = request.query_params
            allowed = {'url', 'run_id', 'attempt', 'job_id',
                       'expected_repository_id', 'expected_head_sha'}
            if (len(str(request.url.query)) > 8192 or set(query) - allowed
                    or any(len(query.getlist(k)) != 1 for k in query)
                    or not {'url', 'run_id', 'attempt'} <= set(query)):
                raise ValueError()
            def number(key, maximum=2**53-1):
                raw = query.get(key)
                if raw is None:
                    return None
                if not re.fullmatch(r'[1-9][0-9]{0,15}', raw) or int(raw) > maximum:
                    raise ValueError()
                return int(raw)
            github_url(query['url'])
            run_id, attempt = number('run_id'), number('attempt', 10000)
            job_id, expected = number('job_id'), number('expected_repository_id')
            head = query.get('expected_head_sha')
            if head is not None and not re.fullmatch(r'[a-f0-9]{40}', head):
                raise ValueError()
            reader = CiEvidenceReader(CiHttpTransport(workbench.github,
                policy=workbench.ci_download_policy), synthetic=False)
            operation = partial(reader.observe, query['url'], run_id, attempt, job_id,
                                expected_repository_id=expected, expected_head_sha=head)
            result = await workbench.read_pool.run(operation)
            result['comparable_baseline'] = await workbench.read_pool.run(workbench.ci_baseline, result)
            # Upstream read errors belong to the evidence, not the local login.
            return JSONResponse(result)
        except ReadError as error:
            return JSONResponse({'error': str(error), 'code': 'ci_read_error'},
                                status_code=error.status)
        except (SafetyError, ValueError, TypeError):
            return JSONResponse({'error': 'CI read parameters are invalid',
                                 'code': 'ci_invalid_selection'}, status_code=400)

    async def history_view(request):
        try:
            return JSONResponse(workbench.history.view(request.path_params['identity']))
        except (SafetyError,ValueError,OSError,KeyError) as error:
            return JSONResponse({'error':str(error) if isinstance(error,SafetyError) else '历史记录读取失败'},status_code=getattr(error,'status',409))
    async def history_fetch(request):
        try:
            if request.headers.get('content-type') != 'application/json':
                return JSONResponse({'error':'JSON required'},status_code=415)
            content=bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content)>16384:
                    return JSONResponse({'error':'Request body too large'},status_code=413)
            value=json.loads(content.decode(),object_pairs_hook=strict_object,
                parse_constant=lambda _: (_ for _ in ()).throw(SafetyError('Nonfinite JSON')))
            return JSONResponse(workbench.history.submit(value),status_code=202)
        except (SafetyError,ValueError,TypeError,OSError,KeyError) as error:
            return JSONResponse({'error':str(error) if isinstance(error,SafetyError) else '历史补取请求无效'},status_code=getattr(error,'status',409))
    async def operate(request):
        try:
            if request.headers.get("content-type") != "application/json":
                return JSONResponse({"error": "JSON required"}, status_code=415)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 16384:
                    return JSONResponse({"error": "Request body too large"}, status_code=413)
            value = json.loads(content.decode(), object_pairs_hook=strict_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(SafetyError("Nonfinite JSON")))
            action = request.path_params["action"]
            payload = validate_request(action, value)
            return JSONResponse(workbench.submit(value["request_id"], action, payload), status_code=202)
        except (SafetyError, ValueError, TypeError, KeyError):
            return JSONResponse({"error": "请求无效，请检查仓库信息和明确的授权范围"}, status_code=409)
    from .local_ui import ASSETS as maintenance_assets, DIFF_ASSETS
    from .help_docs import illustrated_guide, illustrated_zip
    tutorial_assets, tutorial_csp = {}, None
    files = {"/": ASSETS / "index.html", "/app.js": ASSETS / "app.js", "/style.css": ASSETS / "style.css",
             '/onboarding.html':ASSETS/'index.html', '/onboarding.js':ASSETS/'app.js', '/legacy':ASSETS/'index.html',
             "/maintenance.html": maintenance_assets / "index.html", "/maintenance.js": maintenance_assets / "app.js",
             "/session-access.js": maintenance_assets / "session-access.js",
             "/maintenance.css": ASSETS / "maintenance.css",
             "/diff2html.min.js": DIFF_ASSETS / "vendor/diff2html.min.js",
             "/diff2html.min.css": DIFF_ASSETS / "vendor/diff2html.min.css"}
    if workbench.ui == 'react':
        build = unlinked(Path(__file__).with_name('workbench_ui'))
        files['/'] = unlinked(build/'index.html')
        asset_directory = unlinked(build/'assets')
        if not files['/'].is_file() or not asset_directory.is_dir():
            raise ReactBuildError('缺少 React 工作台构建资产。源码检出请在 frontend 运行 npm ci 和 npm run build，再用原 --home 启动。')
        for asset in asset_directory.iterdir():
            if re.fullmatch(r'[A-Za-z0-9_-]+\.(js|css)',asset.name) and unlinked(asset).is_file():
                files['/assets/'+asset.name]=asset
        references=re.findall(r'(?:src|href)="(/assets/[^"<>]+)"',files['/'].read_text(encoding='utf-8'))
        if not references or any(path not in files for path in references):
            raise ReactBuildError('React 工作台构建资产不完整。源码检出请在 frontend 重新运行 npm run build，再用原 --home 启动。')
        tutorial_assets, tutorial_csp = illustrated_guide(build)
        files.update(('/tutorial/'+name, path) for name,path in tutorial_assets.items())
    async def static(request):
        return FileResponse(files[request.url.path])
    routes = [Route(path, static, methods=["GET"]) for path in files]
    if tutorial_assets:
        async def tutorial_download(request):
            try:
                archive = await workbench.read_pool.run(illustrated_zip, tutorial_assets)
            except (OSError, SafetyError):
                return JSONResponse({'error':'图文教程暂不可下载'},status_code=404)
            return Response(archive,media_type='application/zip',
                            headers={'Content-Disposition':'attachment; filename="RepoCare-illustrated-guide.zip"'})
        routes.append(Route('/tutorial/repocare-guide.zip', tutorial_download))
    routes += [Route('/api/state',state),Route('/api/service',service),Route('/api/readiness',readiness),Route('/api/delivery',delivery_inbox),
               Route('/api/capabilities',capability_definitions),
               Route('/api/exports/{identity}/{name}',export_download),
               Route('/api/provider-config',provider_settings,methods=['GET','POST']),
               Route('/api/model-profiles',model_profiles,methods=['GET','POST']),
               Route('/api/provider-models',provider_models,methods=['POST']),
               Route('/api/help',help_view),Route('/api/assistant/state',assistant_view),
               Route('/api/assistant/actions/{action}',assistant_action,methods=['POST']),
               Route('/api/browse/{workspace_id}/{kind}',read_view),
               Route('/api/github/{kind}',read_view),
               Route('/api/ci/evidence',ci_read_view),
               Route('/api/monitors/{monitor_id}/events/{event_id}/history',monitor_history_view),
               Route('/api/history',history_fetch,methods=['POST']),
               Route('/api/history/{identity}',history_view),
               Route("/api/actions/{action}", operate, methods=["POST"])]
    page_csp = {'/tutorial/index.html':tutorial_csp} if tutorial_csp else None
    return WorkspaceRouter(workbench, LocalBoundary(Starlette(routes=routes), workbench, port, page_csp=page_csp))


def serve_workbench(workbench, port=0, *, on_listen=None, print_url=True):
    import uvicorn
    if type(port) is not int or not 0 <= port <= 65535:
        raise SafetyError("Invalid loopback port")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        port = sock.getsockname()[1]
        workbench.url = f"http://127.0.0.1:{port}/#session={workbench.token}"
        server = uvicorn.Server(uvicorn.Config(create_workbench_app(workbench, port), access_log=False,
            proxy_headers=False, ws="none", log_level="warning"))
        workbench.stop_callback = lambda: setattr(server,'should_exit',True)
        if on_listen is not None:
            on_listen(workbench, port)
        if print_url:
            print(workbench.url, flush=True)
        server.run(sockets=[sock])
    finally:
        sock.close()
        workbench.close()
