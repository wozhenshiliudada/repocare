"""Fixed-host GitHub access through the installed Git Credential Manager.

Credentials are obtained only in the backend and never returned to the workbench.
No password form, embedded OAuth client secret, credential erase, or Git push.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .git import git_env
from .git_investigation import _capture
from .models import SafetyError

SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
DEVICE_CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b")


class SnapshotError(SafetyError):
    """Snapshot failure with a source-owned message safe for monitor events."""


def branch_name(value):
    if (not isinstance(value, str) or len(value) > 150 or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", value)
            or any(part in {"", ".", ".."} or part.endswith((".lock", ".")) or part.startswith(".")
                   for part in value.split("/")) or ".." in value or value.endswith("/")):
        raise SafetyError("请输入明确的 Git 分支名")
    return value


def github_url(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 250:
        raise SafetyError("请输入 GitHub 仓库的 HTTPS 地址")
    url = urlsplit(value)
    if (url.scheme != "https" or url.netloc != "github.com" or url.query or url.fragment):
        raise SafetyError("仅支持 https://github.com/所有者/仓库，不接受账号、参数或其他主机")
    parts = url.path.strip("/").split("/")
    if len(parts) != 2:
        raise SafetyError("请输入具体 GitHub 仓库地址")
    owner, name = parts
    name = name[:-4] if name.endswith(".git") else name
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner)
            or not SLUG.fullmatch(name) or name in {".", ".."}):
        raise SafetyError("GitHub 仓库名称无效")
    return f"{owner}/{name}", f"https://github.com/{owner}/{name}.git"


def fetch_snapshot(source, branch, sha, destination, environment=None, *, github=False):
    """Fetch into a new owned repository; never fetch/reset the user's checkout."""
    branch_name(branch)
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or destination.exists():
        raise SafetyError("快照已存在或提交无效；保留现场，不能覆盖")
    if github:
        _, source = github_url(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    config = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
              "-c", "core.symlinks=false", "-c", "http.followRedirects=false",
              "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=" + ("never" if github else "always")]
    if github:
        config += ["-c", "credential.helper=manager", "-c", "credential.interactive=never",
                   "-c", "http.sslVerify=true"]
    commands = [config + ["init", str(destination)],
                config + ["-C", str(destination), "fetch", "--depth=1", "--no-tags", "--no-recurse-submodules",
                          "--", str(source), "refs/heads/" + branch],
                config + ["-C", str(destination), "rev-parse", "FETCH_HEAD"],
                config + ["-C", str(destination), "checkout", "-b", "observed", sha]]
    for stage, command in zip(("init", "fetch", "rev-parse", "checkout"), commands):
        seconds = 120 if stage == "fetch" else 30
        context = "；快照现场保留，不会自动重放"
        if github and stage == "fetch":
            context += ("。GitHub API 能读取分支信息时，github.com 的 Git 下载仍可能失败。"
                        "请在服务运行环境用只读 git ls-remote 检查目标分支，再核对 HTTPS 代理、TLS 信任和仓库权限。")
        try:
            result = _capture(command, None, environment or git_env(), timeout=seconds,
                              timeout_limit=120, own_windows_job=True)
        except (OSError, subprocess.TimeoutExpired, SafetyError):
            raise SnapshotError(f"无法安全启动或结束 Git {stage} 进程{context}") from None
        if result['timed_out']:
            raise SnapshotError(f"Git {stage} 在 {seconds} 秒内未完成{context}")
        if result['truncated']:
            raise SnapshotError(f"Git {stage} 输出超过上限；快照现场保留，不会自动重放")
        if result['returncode']:
            raise SnapshotError(f"Git {stage} 失败（退出码 {result['returncode']}）{context}")
        if stage == "rev-parse" and result['stdout'].strip() != sha.encode('ascii'):
            raise SnapshotError("分支在读取期间变化；本次快照不用于修复")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubConnection:
    def environment(self, interactive=False):
        env = git_env()
        env.update(GCM_INTERACTIVE="always" if interactive else "never", GCM_GUI_PROMPT="false")
        # Never enable a credential trace or inherit transport/askpass overrides.
        for name in list(env):
            if name.startswith(("GCM_TRACE", "GCM_DEBUG", "GCM_CREDENTIAL_STORE")) or name in {
                "GIT_ASKPASS", "SSH_ASKPASS", "GCM_PROVIDER", "GCM_AUTHORITY"}:
                env.pop(name)
        return env

    def _run(self, args, *, data=None, timeout=20, propagate_timeout=False):
        try:
            result = subprocess.run(["git", "credential-manager", *args], input=data,
                                    capture_output=True, timeout=timeout, env=self.environment())
        except subprocess.TimeoutExpired:
            if propagate_timeout:
                raise TimeoutError('Credential read timed out') from None
            raise SafetyError("Git Credential Manager 不可用或响应超时；请检查本机 Git 安装") from None
        except OSError:
            raise SafetyError("Git Credential Manager 不可用或响应超时；请检查本机 Git 安装") from None
        if result.returncode or len(result.stdout) > 65536:
            raise SafetyError("尚未取得 GitHub 授权，请点击登录 GitHub")
        return result.stdout

    def credential(self, *, timeout=None):
        options = {} if timeout is None else {'timeout': min(20, timeout), 'propagate_timeout': True}
        raw = self._run(["get"], data=b"protocol=https\nhost=github.com\n\n", **options)
        fields = dict(line.split("=", 1) for line in raw.decode("utf-8").splitlines() if "=" in line)
        token = fields.get("password", "")
        if not token or any(c.isspace() for c in token):
            raise SafetyError("尚未登录 GitHub，请点击登录 GitHub")
        return token

    def api(self, path):
        repository_api = re.fullmatch(r"/repos/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)/(branches/([A-Za-z0-9_.%/-]+)|actions/runs\?branch=([A-Za-z0-9_.%/-]+)&head_sha=([a-f0-9]{40})&per_page=30)", path)
        issue_api = re.fullmatch(r"/repos/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+(?:/issues(?:\?state=open&per_page=30&page=[1-3]|/[1-9][0-9]{0,9}(?:/events\?per_page=30&page=[1-3])?))?", path)
        if issue_api:
            github_url('https://github.com/' + '/'.join(path.split('/')[2:4]))
        if not re.fullmatch(r"/(user|user/repos\?per_page=30&page=[1-9][0-9]?&sort=updated)", path) and not repository_api and not issue_api:
            raise SafetyError("Unsupported GitHub API operation")
        return self._request(path)

    def _request(self, path, *, payload=None, method=None, _token=None):
        """Internal transport; public operations must first validate fixed endpoint/schema."""
        token = self.credential() if _token is None else _token
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = Request("https://api.github.com" + path, data=data, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Pi-maintenance-workbench"}, method=method)
        try:
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                content = response.read(512 * 1024 + 1)
            if len(content) > 512 * 1024:
                raise SafetyError("GitHub 响应超过大小上限")
            return json.loads(content)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise SafetyError("GitHub 授权失效或权限不足；请重新授权或检查组织访问权限") from None
            raise SafetyError("GitHub 请求失败，请检查网络或仓库权限") from None
        except (URLError, TimeoutError, ValueError):
            raise SafetyError("GitHub 请求失败，请检查网络") from None
        finally:
            token = None

    def branch_head(self, url, branch):
        slug, _ = github_url(url)
        branch_name(branch)
        data = self.api(f"/repos/{slug}/branches/{quote(branch, safe='')}")
        sha = data.get("commit", {}).get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise SafetyError("GitHub 分支提交无效")
        return sha

    def workflow_runs(self, url, branch, sha):
        slug, _ = github_url(url)
        branch_name(branch)
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise SafetyError("GitHub 提交无效")
        data = self.api(f"/repos/{slug}/actions/runs?branch={quote(branch, safe='')}&head_sha={sha}&per_page=30")
        runs = data.get("workflow_runs") if isinstance(data, dict) else None
        if not isinstance(runs, list) or len(runs) > 30:
            raise SafetyError("GitHub CI 信息无效")
        result = []
        for run in runs:
            if not isinstance(run, dict) or run.get("head_sha") != sha or run.get("head_branch") != branch:
                continue
            identity, attempt = run.get("id"), run.get("run_attempt", 1)
            if type(identity) is not int or identity < 1 or type(attempt) is not int or attempt < 1:
                raise SafetyError("GitHub CI 编号无效")
            if run.get("status") == "completed" and run.get("conclusion") in {"failure", "timed_out", "action_required"}:
                result.append({"id": identity, "attempt": attempt, "name": str(run.get("name", "CI"))[:200],
                               "conclusion": run["conclusion"], "url": f"https://github.com/{slug}/actions/runs/{identity}"})
        return result

    def snapshot(self, url, branch, sha, destination):
        fetch_snapshot(url, branch, sha, destination, self.environment(), github=True)

    def account(self):
        data = self.api("/user")
        login = data.get("login") if isinstance(data, dict) else None
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
            raise SafetyError("GitHub 返回的账号信息无效")
        return {"login": login}

    def repositories(self, page):
        if type(page) is not int or not 1 <= page <= 99:
            raise SafetyError("仓库页码无效")
        data = self.api(f"/user/repos?per_page=30&page={page}&sort=updated")
        if not isinstance(data, list) or len(data) > 30:
            raise SafetyError("GitHub 仓库列表无效")
        result = []
        for item in data:
            if not isinstance(item, dict):
                raise SafetyError("GitHub 仓库条目无效")
            slug, url = github_url("https://github.com/" + str(item.get("full_name", "")))
            result.append({"name": slug, "url": url, "private": bool(item.get("private")),
                           "archived": bool(item.get("archived"))})
        return {"repositories": result, "page": page, "has_next": len(result) == 30}

    def login(self, update):
        # GCM owns its OAuth client and OS credential storage. No forced reauthentication.
        try:
            process = subprocess.Popen(["git", "credential-manager", "github", "login", "--url",
                                        "https://github.com", "--device", "--no-ui"],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, env=self.environment(True))
        except OSError:
            raise SafetyError("请安装带 Git Credential Manager 的 Git") from None
        import threading
        expired = threading.Event()
        def expire():
            expired.set()
            process.kill()
        timer = threading.Timer(180, expire)
        timer.start()
        try:
            size = 0
            for raw in iter(process.stdout.readline, b""):
                size += len(raw)
                if size > 65536:
                    process.kill()
                    raise SafetyError("登录工具输出异常")
                # Only the temporary device user-code is forwarded, never raw tool text.
                match = DEVICE_CODE.search(raw.decode("utf-8", errors="replace"))
                if match:
                    update({"verification_url": "https://github.com/login/device", "user_code": match.group()})
            result = process.wait(timeout=5)
            if expired.is_set() or result:
                raise SafetyError("登录未完成或已超时，可重新点击登录")
            return self.account()
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()

    def clone(self, url: str, destination: Path):
        _, canonical = github_url(url)
        if destination.exists():
            raise SafetyError("克隆目录已存在，保留已有文件")
        # Inherit no global filters/hooks, transport rewrites, custom credential helpers or trace.
        command = ["git", "-c", "credential.helper=manager", "-c", "credential.interactive=never",
                   "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
                   "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
                   "-c", "http.followRedirects=false", "clone", "--depth", "1", "--no-checkout",
                   "--no-recurse-submodules", "--", canonical, str(destination)]
        try:
            result = subprocess.run(command, capture_output=True, timeout=120, env=self.environment())
            if result.returncode:
                raise SafetyError("克隆失败，请检查 GitHub 登录、网络和仓库权限；半成品目录保留")
            result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
                                     "-c", "core.symlinks=false", "-C", str(destination), "checkout", "HEAD"],
                                    capture_output=True, timeout=30, env=self.environment())
            if result.returncode:
                raise SafetyError("克隆内容未能检出，保留半成品目录")
        except (OSError, subprocess.TimeoutExpired):
            raise SafetyError("克隆超时或 Git 不可用，保留半成品目录") from None
