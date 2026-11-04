from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .models import SafetyError, canonical_path, digest, file_digest
from .path_safety import is_junction


def git_env() -> dict[str, str]:
    # Do not inherit caller overrides that could redirect Git to another checkout.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0")
    return env


class RepositoryGateway:
    def __init__(self, root: Path, audit: list | None = None):
        self.audit = audit
        self.root = root.resolve()
        top = Path(self.command("rev-parse", "--show-toplevel").decode().strip()).resolve()
        if self.root != top:
            raise SafetyError("--repo must point to the repository root")
        self.common_dir = Path(self.command("rev-parse", "--path-format=absolute",
                                           "--git-common-dir").decode().strip()).resolve()
        if self.command("ls-files", "--stage").find(b"160000 ") >= 0:
            raise SafetyError("Submodules are not supported in the MVP")

    def command(self, *args: str, check: bool = True, input_data: bytes | None = None,
                index_file: Path | None = None, on_start=None) -> bytes:
        started = time.monotonic()
        argv = ["git", "-c", "core.longpaths=true", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
                "-c", "commit.gpgsign=false", "-c", "core.quotepath=false", "-C", str(self.root), *args]
        env = git_env()
        if index_file is not None:
            env["GIT_INDEX_FILE"] = str(index_file.resolve())
        if on_start is None:
            result = subprocess.run(argv, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, env=env)
        else:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                on_start(process.pid)
                stdout, stderr = process.communicate(input=input_data, timeout=30)
            except BaseException:
                process.kill()
                process.communicate(timeout=10)
                raise
            result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        if self.audit is not None:
            self.audit.append({"command": argv, "returncode": result.returncode,
                               "duration_seconds": round(time.monotonic() - started, 6),
                               "stdin_sha256": digest(input_data) if input_data is not None else None,
                               "alternate_index": str(index_file) if index_file is not None else None,
                               "stderr": result.stderr.decode(errors="replace")[-20000:]})
        if check and result.returncode:
            raise SafetyError(result.stderr.decode(errors="replace").strip())
        return result.stdout

    @property
    def head(self) -> str:
        return self.command("rev-parse", "HEAD").decode().strip()

    def snapshot(self, exclude_branch: str = "", exclude_branches: tuple[str, ...] = ()) -> dict:
        paths = self.command("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        files = {}
        for raw in paths.split(b"\0"):
            if not raw:
                continue
            relative = os.fsdecode(raw)
            path = self.root / relative
            if path.is_symlink():
                files[relative] = {"symlink": os.readlink(path)}
            elif path.is_file():
                files[relative] = file_digest(path)
            else:
                files[relative] = None
        index = Path(self.command("rev-parse", "--path-format=absolute", "--git-path", "index")
                     .decode().strip())
        refs = self.command("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
        return {"head": self.head,
                "refs": [line for line in refs.decode().splitlines()
                         if not any(line.startswith(f"refs/heads/{branch} ")
                                    for branch in (exclude_branch, *exclude_branches))],
                "branch": self.command("symbolic-ref", "-q", "HEAD", check=False).decode().strip(),
                "status": self.command("status", "--porcelain=v1", "-z", "--untracked-files=all")
                    .decode(errors="replace"),
                "index_sha256": digest(index.read_bytes()) if index.exists() else None,
                "files": files}

    def require_clean(self, snapshot: dict) -> None:
        if snapshot["status"]:
            raise SafetyError("Source checkout is dirty; preserve it and use a clean checkout")

    def read(self, relative: str) -> bytes:
        return self.safe_file(relative).read_bytes()

    def safe_file(self, relative: str) -> Path:
        canonical_path(relative)
        path = self.root / relative
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != self.root):
            raise SafetyError("Symlink paths are not editable")
        if not path.resolve().is_relative_to(self.root) or not path.is_file():
            raise SafetyError(f"Expected an existing regular repository file: {relative}")
        if os.name == "nt" and any(is_junction(p) for p in [path, *path.parents]
                                   if p != self.root):
            raise SafetyError("Junction paths are not editable")
        return path

    def worktree_path_argument(self, path: Path) -> str:
        """Represent an already authorized target; this grants no ownership."""
        argument = str(path)
        if os.name == "nt":
            # Git for Windows checks the raw child GIT_DIR before resolving its
            # .git file. A relative argument keeps that string short without
            # moving the validated candidate or changing its persisted identity.
            try:
                relative = os.path.relpath(path.resolve(), self.root)
            except ValueError:  # Different drives retain the normal Git path.
                pass
            else:
                if (self.root / relative).resolve() != path.resolve():
                    raise SafetyError("Candidate path representation changed")
                argument = relative
        return argument

    def create_workspace(self, path: Path, branch: str, base: str) -> "RepositoryGateway":
        if path.exists() or path.resolve().is_relative_to(self.root):
            raise SafetyError("Candidate must be a new directory outside source checkout")
        self.command("worktree", "add", "-b", branch, self.worktree_path_argument(path), base)
        return RepositoryGateway(path, self.audit)

    def diff(self) -> str:
        return self.command("diff", "--no-ext-diff", "--no-textconv", "HEAD", "--")\
            .decode(errors="replace")

    def discard_candidate(self, path: Path, branch: str, base: str, owner_dir: Path) -> None:
        target = path.resolve()
        if target != (owner_dir.resolve() / "workspace") or target == self.root:
            raise SafetyError("Cleanup target does not belong to this run")
        # Verify both Git ownership and the branch before removing anything.
        candidate = RepositoryGateway(target, self.audit)
        if candidate.common_dir != self.common_dir or candidate.head != base:
            raise SafetyError("Candidate identity changed; retain it for inspection")
        if candidate.command("symbolic-ref", "--short", "HEAD").decode().strip() != branch:
            raise SafetyError("Candidate branch changed; retain it for inspection")
        self.command("worktree", "remove", "--force", str(target))
        # No reset/clean on the user checkout. Only delete the uncommitted run-owned branch.
        self.command("branch", "-d", branch)
