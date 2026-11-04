"""Start a Windows command in its own Job before it can start descendants."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import subprocess

import psutil


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _BasicLimits), ('IoInfo', ctypes.c_ulonglong * 6),
        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


@contextmanager
def windows_job_process(argv, **kwargs):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateJobObjectW
    create.argtypes, create.restype = (ctypes.c_void_p, wintypes.LPCWSTR), wintypes.HANDLE
    configure = kernel.SetInformationJobObject
    configure.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    configure.restype = wintypes.BOOL
    assign = kernel.AssignProcessToJobObject
    assign.argtypes, assign.restype = (wintypes.HANDLE, wintypes.HANDLE), wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes, close.restype = (wintypes.HANDLE,), wintypes.BOOL

    job = create(None, None)  # Unnamed and non-inheritable.
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())

    def stop():
        nonlocal job
        if job:
            if not close(job):
                raise ctypes.WinError(ctypes.get_last_error())
            job = None

    try:
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not configure(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        process = subprocess.Popen(argv, creationflags=_CREATE_SUSPENDED, **kwargs)
        try:
            # The Windows Popen handle binds the actual process even if its PID exits.
            if not assign(job, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            psutil.Process(process.pid).resume()
        except BaseException as error:
            # No command runs before assignment. Kill the exact Popen child if startup failed.
            try:
                process.kill()
                process.wait(timeout=5)
            finally:
                process.stdout.close()
                process.stderr.close()
            if isinstance(error, psutil.Error):
                raise OSError('Could not resume Git in its Windows job') from error
            raise
        yield process, stop
    finally:
        stop()
