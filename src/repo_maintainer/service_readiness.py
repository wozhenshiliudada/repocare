"""Cached, local-only installation and explicit provider configuration checks.

Neither a successful report nor a reachable web service proves a model request
will work. Reports contain fixed presentation text, never file contents, paths,
exception messages or credentials. Call inspect() in the existing read pool;
snapshot() performs no filesystem or process work.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path, PureWindowsPath
import stat
import threading
import time
from urllib.parse import urlsplit

from .path_safety import is_junction
from .provider_config import load_provider_config


def _row(identity, group, status, label, detail, action=""):
    return {"id": identity, "group": group, "status": status, "label": label,
            "detail": detail, "next_action": action}


def _application_root():
    package = Path(__file__).resolve().parent
    return package.parents[1] if package.parent.name == "src" else package.parent


def _local_path(path):
    """Lexical rejection before any stat/open, including Windows network devices."""
    value = str(path)
    windows = PureWindowsPath(value)
    if (not value or len(value) > 4096 or any(ord(char) < 32 for char in value)
            or value.startswith(('\\\\', '//')) or windows.drive.startswith('\\')
            or not path.is_absolute()):
        return False
    if os.name == 'nt':
        reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)),
                    *(f'LPT{i}' for i in range(10))}
        for part in windows.parts[1:]:
            if (any(char in part for char in '<>:"|?*') or part.endswith(('.', ' '))
                    or part.split('.')[0].upper() in reserved):
                return False
    return True


def _unlinked_local(path):
    # Check ancestors before descendants, so inspecting a leaf cannot traverse
    # a parent junction (which might itself point at a remote directory).
    return _local_path(path) and not any(item.is_symlink() or is_junction(item)
                                        for item in reversed((path, *path.parents)))


def _inspect_installation(root):
    # Package-owned code also ships in a wheel. Never execute a checker script
    # from the working directory or a mutable project checkout.
    from .installation_check import inspect_environment
    return inspect_environment(root=root, profile="web")


def _environment_rows(report):
    if not isinstance(report, dict) or not isinstance(report.get("checks"), list):
        return [_row("installation", "installation", "unknown", "程序安装与运行时",
                     "本次未取得完整安装检查结果。", "使用当前源码目录运行安装自检。")]
    known = {
        "Python": ("python", "installation", "Python 版本", "使用 Python 3.11 或更新版本。"),
        "git": ("git", "installation", "Git 工具", "安装 Git 并确认当前程序可以找到它。"),
        "node": ("node", "runtime", "Node.js 版本", "安装符合 SETUP.md 要求的 Node.js。"),
        "Pi npm lock": ("pi_lock", "runtime", "Pi 依赖锁文件", "使用完整源码目录，并按 SETUP.md 安装 Pi 依赖。"),
        "React workbench index": ("ui_index", "assets", "工作台网页入口", "按 SETUP.md 安装并构建前端。"),
    }
    installed = report.get('installation_kind') == 'installed'
    runtime=report.get('pi_runtime') if isinstance(report.get('pi_runtime'),dict) else {}
    packaged=installed and runtime.get('kind')=='wheel' and runtime.get('present') is True
    if packaged:
        known['Pi runtime']=('pi_runtime','runtime','Pi 运行资源','重新安装可信发行包。')
        known['Pi package resources']=('pi_resources','runtime','Pi 资源完整性','重新安装包含完整运行资源与许可的发行包。')
    if installed and not packaged:
        identity, group, label, _ = known['Pi npm lock']
        known['Pi npm lock'] = (identity, group, label,
            'Python 安装包未携带 Pi 运行目录；请按 SETUP.md 使用受支持的完整源码安装。')
    checks = report["checks"]
    # An incomplete/malformed checker response must never become a green report.
    required = set(known) | {"filelock", "psutil", "junitparser", "starlette", "uvicorn",
                             "Pi dependency @earendil-works/pi-coding-agent", "Pi dependency typebox"}
    names = [item.get("name") for item in checks if isinstance(item, dict)]
    complete = (required.issubset(names)
                and any(isinstance(name, str) and name.startswith("Pi dependency ") for name in names)
                and any(isinstance(name, str) and name.startswith("React asset ") for name in names)
                and len(checks) <= 100)
    rows = []
    for name, (identity, group, label, action) in known.items():
        matches = [item for item in checks if isinstance(item, dict) and item.get("name") == name]
        status = "ready" if len(matches) == 1 and matches[0].get("status") == "ok" else "blocked"
        detail = ("Python 安装包未携带外部 Pi 运行目录；不能直接执行维护。"
                  if installed and not packaged and identity == 'pi_lock' else "缺失、版本不符或本地检查未完成。")
        if status == "ready":
            detail = "本地检查通过。"
            if identity == "git":
                detail += "GitHub API（api.github.com）和 Git 快照下载（github.com）需分别验证，本次均未联网检查。"
        rows.append(_row(identity, group, status, label, detail,
                         "" if status == "ready" else action))
    for identity, group, label, predicate, action in (
        ("python_dependencies", "installation", "Python 网页依赖",
         lambda name: name in {"filelock", "psutil", "junitparser", "starlette", "uvicorn"},
         "在当前 Python 环境按 SETUP.md 安装网页依赖。"),
        ("pi_dependencies", "runtime", "Pi 安装依赖",
         lambda name: name.startswith("Pi dependency "),
         "在当前源码目录按锁文件安装 Pi 依赖。"),
        ("ui_assets", "assets", "网页脚本与样式",
         lambda name: name.startswith("React asset "),
         "按 SETUP.md 重新构建前端，然后重新检查。"),
    ):
        selected = [item for item in checks if isinstance(item, dict)
                    and isinstance(item.get("name"), str) and predicate(item["name"])]
        ready = bool(selected) and all(item.get("status") == "ok" for item in selected)
        if packaged and identity=='pi_dependencies':
            action='运行 repo-maintainer runtime install 查看锁定依赖的安装计划，再明确确认。'
        elif installed and identity == 'pi_dependencies':
            action = 'Python 安装包不包含外部 Pi Node 依赖；请按 SETUP.md 使用完整源码安装。'
        rows.append(_row(identity, group, "ready" if ready else "blocked", label,
                         "本地检查通过。" if ready else "所需文件或依赖未全部通过检查。",
                         "" if ready else action))
    if not complete or any(not isinstance(item, dict) or item.get("status") not in
                           {"ok", "missing_or_incompatible"} for item in checks):
        rows.append(_row("installation_report", "installation", "unknown", "安装检查完整性",
                         "安装检查结果不完整，不能认定本地条件齐备。", "重新运行当前版本的安装自检。"))
    elif any(item["status"] != "ok" for item in checks) and all(row["status"] == "ready" for row in rows):
        rows.append(_row("installation_extra", "installation", "blocked", "其他安装条件",
                         "安装检查仍有未通过项目。", "查看当前版本安装自检并补齐缺项。"))
    return rows


def _provider_row(path):
    identity = ("provider_config", "provider")
    action = "在本机编辑启动时指定的模型配置文件，然后重新检查。"
    if path is None:
        return _row(*identity, "unknown", "模型配置文件", "尚未明确指定模型配置；安装检查不会自动寻找密钥。",
                    "选择自己的本地模型配置文件，并在启动或自检时明确指定。")
    try:
        if not _local_path(path):
            return _row(*identity, "blocked", "模型配置文件", "需要明确指定本地配置文件。", action)
        if not _unlinked_local(path):
            return _row(*identity, "blocked", "模型配置文件", "配置路径包含链接目录或链接文件，不能安全读取。", action)
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            return _row(*identity, "blocked", "模型配置文件", "配置必须是大小受限的普通本地文件。", action)
        config = load_provider_config(path)
        placeholders = {"REPLACE_WITH_YOUR_KEY", "REPLACE_WITH_PROVIDER_MODEL"}
        endpoint = urlsplit(config["PI_MAINTENANCE_BASE_URL"])
        # urlsplit accepts malformed ports until .port is accessed. This is a
        # local syntax check only; it never resolves or connects to the host.
        port = endpoint.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError('Invalid provider port')
        if (any(value.upper() in placeholders for value in config.values())
                or endpoint.hostname == "invalid" or (endpoint.hostname or "").endswith(".invalid")):
            return _row(*identity, "blocked", "模型配置文件", "示例内容尚未替换，请填写自己的密钥、地址和模型。", action)
    except FileNotFoundError:
        return _row(*identity, "blocked", "模型配置文件", "指定的本地配置文件不存在。", action)
    except Exception:
        # Parsing, decoding, permissions and stat errors may contain the key or
        # the private path. Do not include their messages in service payloads.
        return _row(*identity, "blocked", "模型配置文件", "文件不可安全读取，或密钥、HTTPS 地址、模型字段格式无效。", action)
    return _row(*identity, "ready", "模型配置文件", "本地字段格式通过；尚未验证真实模型请求。")


class ServiceReadiness:
    """One cached inspector per original workbench; no ledger or model ownership."""

    def __init__(self, provider_config, root=None, ttl_seconds=30, *,
                 inspect_installation=None, monotonic=time.monotonic, utcnow=None):
        if not 1 <= ttl_seconds <= 3600:
            raise ValueError("Readiness cache lifetime is out of range")
        self.provider_config = Path(provider_config) if provider_config is not None else None
        self.root = Path(root) if root is not None else _application_root()
        self.ttl_seconds = ttl_seconds
        self._inspect_installation = inspect_installation or _inspect_installation
        self._monotonic = monotonic
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc).isoformat())
        self._inspect_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._report = None
        self._checked_monotonic = None

    def snapshot(self):
        """Return a copy of cached evidence without reading config or spawning tools."""
        with self._state_lock:
            if self._report is None:
                return {"schema_version": 1, "status": "unknown", "checked_at": None,
                        "stale": False, "scope": "local_only", "network_checked": False,
                        "model_calls": 0, "valid_for_seconds": self.ttl_seconds, "checks": []}
            value = deepcopy(self._report)
            value["stale"] = self._monotonic() - self._checked_monotonic >= self.ttl_seconds
            return value

    def inspect(self, force=False):
        """Explicit bounded local check, serialized and cached; run in a read worker."""
        cached = self.snapshot()
        if cached["checked_at"] is not None and not cached["stale"] and not force:
            return cached
        if not self._inspect_lock.acquire(timeout=0.1):
            return self.snapshot()  # An existing inspection owns the bounded tool work.
        try:
            cached = self.snapshot()
            if cached["checked_at"] is not None and not cached["stale"] and not force:
                return cached
            try:
                report = self._inspect_installation(self.root)
                checks = _environment_rows(report)
            except Exception:
                checks = _environment_rows(None)
            checks.append(_provider_row(self.provider_config))
            statuses = {row["status"] for row in checks}
            value = {"schema_version": 1,
                     "status": "blocked" if "blocked" in statuses else "unknown" if "unknown" in statuses else "ready",
                     "checked_at": self._utcnow(), "stale": False, "scope": "local_only",
                     "network_checked": False, "model_calls": 0, "valid_for_seconds": self.ttl_seconds,
                     "checks": checks}
            with self._state_lock:
                self._report = value
                self._checked_monotonic = self._monotonic()
            return deepcopy(value)
        finally:
            self._inspect_lock.release()
