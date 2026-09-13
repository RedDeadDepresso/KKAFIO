"""
job_object.py — Ensure spawned Windows child processes die with this process.

subprocess.Popen()'d child processes on Windows are NOT automatically killed
when the parent process exits. If kkafio_cli.exe is killed abruptly (closed
from Task Manager, the GUI force-stops the task, the process crashes, etc.)
while a PowerShell dialog (password_dialog.py / llm_dialog.py) is open, that
dialog is orphaned — it keeps running and showing on screen indefinitely,
since nothing ever tells it to close.

Windows Job Objects solve this at the OS level: assign the child process to a
job object created with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, and Windows
automatically terminates every process still in that job the moment the
job's last handle is closed. The OS closes all of a process's open handles
when it exits — including on a hard kill — so this works even when there's
no chance for any Python cleanup code (atexit, finally, signal handlers) to
run.
"""

import ctypes
import sys

_job_handle = None


def _create_kill_on_close_job():
    """Create (once, lazily) a Job Object that kills all of its member
    processes as soon as the job handle is closed (i.e. when this process
    exits, for any reason). Returns the job handle, or None if unavailable.
    """
    global _job_handle
    if _job_handle is not None:
        return _job_handle
    if sys.platform != "win32":
        return None

    try:
        kernel32 = ctypes.windll.kernel32

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit",     ctypes.c_int64),
                ("LimitFlags",              ctypes.c_uint32),
                ("MinimumWorkingSetSize",   ctypes.c_size_t),
                ("MaximumWorkingSetSize",   ctypes.c_size_t),
                ("ActiveProcessLimit",      ctypes.c_uint32),
                ("Affinity",                ctypes.c_size_t),
                ("PriorityClass",           ctypes.c_uint32),
                ("SchedulingClass",         ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount",  ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount",   ctypes.c_uint64),
                ("WriteTransferCount",  ctypes.c_uint64),
                ("OtherTransferCount",  ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo",                IO_COUNTERS),
                ("ProcessMemoryLimit",    ctypes.c_size_t),
                ("JobMemoryLimit",        ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed",     ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        ok = kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None

        _job_handle = job
        return job
    except Exception:
        return None


def die_with_parent(popen_obj) -> None:
    """Assign a subprocess.Popen child to the kill-on-close job, best-effort.

    Silently does nothing on non-Windows platforms, or if anything about
    this fails — the child simply behaves as it did before (able to outlive
    the parent), which is no worse than the status quo.
    """
    if sys.platform != "win32":
        return
    job = _create_kill_on_close_job()
    if job is None:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        # On Windows, Popen._handle is the raw Win32 process HANDLE. This is
        # a private attribute, but it's the standard, well-established way
        # to reach it — subprocess doesn't expose a public accessor for it.
        handle = int(popen_obj._handle)
        kernel32.AssignProcessToJobObject(job, handle)
    except Exception:
        pass
