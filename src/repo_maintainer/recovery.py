"""Native cooperative operation locks and read-only test-process identity probes."""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout
import psutil

from .models import SafetyError
from .path_safety import is_junction


def unlinked(path: Path) -> Path:
    absolute = path.absolute()
    for item in (absolute, *absolute.parents):
        if item.is_symlink() or is_junction(item):
            raise SafetyError("Recovery paths cannot traverse links or junctions")
    return absolute.resolve()


@contextmanager
def operation_lock(run_dir: Path):
    directory = unlinked(run_dir)
    record_path = unlinked(directory / "run.json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{32}", directory.name) or record.get("run_id") != directory.name:
        raise SafetyError("Invalid run directory identity")
    lock_path = unlinked(directory / "integration-operation.lock")
    # Native OS locks only; no stale existence lock and no unlinked-inode split.
    lock = FileLock(lock_path, timeout=0, blocking=False, fallback_to_soft=False, preserve_lock_file=True)
    try:
        with lock:
            yield directory
    except Timeout as error:
        raise SafetyError("Another operation owns this run; do not recover or clean it") from error


def process_identity(pid: int) -> dict | None:
    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return {"pid": pid, "created_at": process.create_time()}
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied as error:
        raise SafetyError("Test process identity is inaccessible; retain verification workspace") from error


def require_test_stopped(review: dict) -> None:
    if review.get("operation_stage") == "test_starting":
        raise SafetyError("Interrupted before test PID was journaled; manual process verification required")
    identity = review.get("test_inflight")
    if identity:
        actual = process_identity(identity["pid"])
        if actual == identity:
            raise SafetyError("Recorded test process is still alive; retain its workspace")


def guard_preparation(run_dir: Path) -> None:
    review_path = unlinked(run_dir / "integration-review.json")
    if review_path.exists():
        review = json.loads(review_path.read_text(encoding="utf-8"))
        if review.get("status") in {"preparing", "recovering", "cleanup_required"}:
            raise SafetyError("Recover integration preparation before candidate cleanup")
        require_test_stopped(review)
    if unlinked(run_dir / "integration-verification").exists():
        raise SafetyError("Verification workspace remains; recover it before candidate cleanup")
