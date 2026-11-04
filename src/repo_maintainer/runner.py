from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def stop_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
        subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()


PREVIEW_LIMIT = 20000


def _start_readers(process: subprocess.Popen):
    """Start one reader per pipe, keeping only a bounded tail preview."""
    import threading

    previews: dict[object, list[bytes]] = {process.stdout: [], process.stderr: []}
    sizes: dict[object, int] = {process.stdout: 0, process.stderr: 0}
    totals: dict[object, int] = {process.stdout: 0, process.stderr: 0}
    truncated: dict[object, bool] = {process.stdout: False, process.stderr: False}

    def keep(stream, data: bytes) -> None:
        totals[stream] += len(data)
        if totals[stream] > PREVIEW_LIMIT:
            truncated[stream] = True
        previews[stream].append(data)
        sizes[stream] += len(data)
        while sizes[stream] > PREVIEW_LIMIT:
            head = previews[stream].pop(0)
            excess = sizes[stream] - PREVIEW_LIMIT
            if excess >= len(head):
                sizes[stream] -= len(head)
            else:
                previews[stream].insert(0, head[excess:])
                sizes[stream] = PREVIEW_LIMIT

    def read_stream(stream) -> None:
        try:
            while True:
                data = os.read(stream.fileno(), 65536)
                if not data:
                    break
                keep(stream, data)
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=read_stream, args=(stream,), daemon=True)
               for stream in (process.stdout, process.stderr)]
    for thread in threads:
        thread.start()
    return threads, previews, truncated


def _finish_readers(process: subprocess.Popen, threads, previews, truncated,
                    wait: float) -> tuple[bytes, bytes, bool]:
    """Join the existing readers once, then close their pipes."""
    deadline = time.monotonic() + wait
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except OSError:
            pass
    stdout = b''.join(previews[process.stdout])
    stderr = b''.join(previews[process.stderr])
    was_truncated = bool(truncated[process.stdout] or truncated[process.stderr])
    return stdout, stderr, was_truncated


def run_test(command: tuple[str, ...], workspace: Path, timeout: float, on_start=None) -> dict:
    argv = [sys.executable if part == "{python}" else part for part in command]
    started = time.monotonic()
    # Credential isolation: test children receive a minimal OS environment, no API tokens.
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT", "HOME",
            "USERPROFILE", "LANG", "LC_ALL", "LC_CTYPE"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # shell=False and exact command authorization. This is not an OS security sandbox.
    process = subprocess.Popen(argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=os.name != "nt",
                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    timed_out = False
    threads, previews, truncated = _start_readers(process)
    deadline = started + timeout if timeout is not None else None
    try:
        if on_start is not None:
            on_start(process.pid)
        while process.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(argv, timeout)
            time.sleep(0.01)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_tree(process)
    except BaseException:
        stop_tree(process)
        raise
    finally:
        try:
            # kill() requests termination; on POSIX it does not reap the child
            # or populate returncode until wait()/poll() observes the exit.
            process.wait(timeout=10)
        finally:
            stdout, stderr, truncated_flag = _finish_readers(
                process, threads, previews, truncated, 10.0)
    return {"command": argv, "returncode": process.returncode, "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "output_truncated": truncated_flag}
