"""Bounded pipe draining for live UI planning; expose only validated evidence frames."""
import subprocess
import threading
import time
from . import pi_evidence
from .job_control import OperationStopped
from .runner import stop_tree

def communicate_live(process, request, allowance, report, cancel, timeout, *, output_limit_bytes=2100000):
    if type(output_limit_bytes) is not int or not 1 <= output_limit_bytes <= 64*1024**2:
        raise ValueError('Invalid bounded Pi output capture size')
    output, errors = bytearray(), bytearray()
    failures = []
    def write():
        try:
            process.stdin.write(request)
            process.stdin.flush()
        except (OSError, ValueError) as error:
            if process.poll() is None:
                failures.append(error)
        finally:
            process.stdin.close()
    def read_output():
        try:
            while chunk := process.stdout.read(4096):
                remaining = output_limit_bytes + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
        except (OSError, ValueError) as error:
            failures.append(error)
    def read_errors():
        oversized = False
        try:
            while line := process.stderr.readline(128001):
                remaining = pi_evidence.MAX_PROGRESS_BYTES + 1 - len(errors)
                if remaining > 0:
                    errors.extend(line[:remaining])
                complete = line.endswith(b'\n')
                if not oversized and complete and len(line) <= 128000 and len(errors) <= pi_evidence.MAX_PROGRESS_BYTES:
                    evidence = pi_evidence.progress_evidence(line, allowance)
                    if evidence is not None:
                        report(evidence)
                oversized = not complete
        except Exception as error:
            failures.append(error)
    threads = [threading.Thread(target=target, daemon=True) for target in (write, read_output, read_errors)]
    for thread in threads:
        thread.start()
    problem = None
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancel.is_set():
                raise OperationStopped('Planning stopped by operator; model consumption remains unknown')
            if failures:
                raise RuntimeError('Planning progress could not be read or persisted')
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, timeout)
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
    except BaseException as error:
        problem = error
        stop_tree(process)  # The exact Popen instance created by this planning call.
        process.wait(timeout=10)
    finally:
        for thread in threads:
            thread.join(timeout=10)
    if any(thread.is_alive() for thread in threads):
        problem = problem or RuntimeError('Planning pipes did not close; preserve unknown outcome')
    if failures:
        problem = problem or RuntimeError('Planning progress could not be persisted')
    if problem is not None:
        problem.partial = bytes(errors)
        raise problem
    return bytes(output), bytes(errors)
