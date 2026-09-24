"""
MXU special task runner for KKAFIO CLI.

Each special task is a standalone function that receives the merged
custom_action_param dict (as stored in the MXU config) and a stop_event
(threading.Event) that is set when the user requests a stop.

Returns True on success, False on failure/interruption.

Tasks implemented:
  __MXU_SLEEP__      — wait N seconds
  __MXU_WAITUNTIL__  — wait until a clock time
  __MXU_LAUNCH__     — launch a program
  __MXU_WEBHOOK__    — HTTP GET a URL
  __MXU_NOTIFY__     — OS desktop notification
  __MXU_KILLPROC__   — kill a process by name  (kill_self is a no-op here)
  __MXU_POWER__      — shutdown / restart / screen-off / sleep
"""

import os
import sys
import time
import subprocess
import threading
from typing import Any


# ── logger forwarded from utils.logger ──────────────────────────────────────

def _log():
    from utils.logger import logger
    return logger


# ── helpers ─────────────────────────────────────────────────────────────────

def _interruptible_sleep(seconds: float, stop: threading.Event) -> bool:
    """Sleep for `seconds`, returning False early if stop is set."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if stop.wait(timeout=min(0.2, remaining)):
            return False
    return True


def _run_cmd(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    extra: dict[str, Any] = {}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW so no console flashes up
        extra["creationflags"] = 0x0800_0000
    return subprocess.run(args, **extra, **kwargs)


# ── individual action functions ─────────────────────────────────────────────

def run_sleep(param: dict, stop: threading.Event) -> bool:
    try:
        secs = int(param.get("sleep_time", 5))
    except (ValueError, TypeError):
        secs = 5
    _log().info("MXU_SLEEP", f"Sleeping {secs} seconds")
    if not _interruptible_sleep(secs, stop):
        _log().warning("MXU_SLEEP", "Interrupted")
        return False
    _log().success("MXU_SLEEP", "Done")
    return True


def run_wait_until(param: dict, stop: threading.Event) -> bool:
    import datetime
    target_time = str(param.get("target_time", "")).strip()
    if not target_time:
        _log().error("MXU_WAITUNTIL", "No target_time specified")
        return False
    try:
        parts = target_time.split(":")
        h, m = int(parts[0]), int(parts[1])
        assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        _log().error("MXU_WAITUNTIL", f"Invalid time format: {target_time!r}")
        return False

    now = datetime.datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    wait_secs = (target - now).total_seconds()
    _log().info("MXU_WAITUNTIL", f"Waiting {wait_secs:.0f}s until {h:02d}:{m:02d}")
    if not _interruptible_sleep(wait_secs, stop):
        _log().warning("MXU_WAITUNTIL", "Interrupted")
        return False
    _log().success("MXU_WAITUNTIL", "Target time reached")
    return True


def _split_args(args_str: str) -> list[str]:
    """Split a user-typed argument string into a list.

    shlex's default POSIX mode treats backslash as an escape character, which
    silently strips the separators out of Windows paths ("C:\\Games\\x"
    becomes "C:Gamesx"). On Windows use non-POSIX mode and drop the outer
    quotes it leaves on quoted tokens (subprocess re-quotes as needed).
    """
    import shlex
    if not args_str:
        return []
    if sys.platform != "win32":
        return shlex.split(args_str)
    tokens = shlex.split(args_str, posix=False)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
            for t in tokens]


def _is_process_running_windows(exe_name: str) -> bool:
    """Return True if a process whose image name equals `exe_name` is running.

    `tasklist` truncates the image-name column to 25 characters, so a
    substring check against its output never matches a longer name. Walk
    the process snapshot through the Win32 API instead, which returns the
    full image name (szExeFile holds up to MAX_PATH characters).
    """
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap is None or snap == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        target = exe_name.lower()
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == target:
                return True
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return False
    finally:
        k32.CloseHandle(snap)


def run_launch(param: dict, stop: threading.Event) -> bool:
    from pathlib import Path

    program = str(param.get("program", "")).strip()
    if not program:
        _log().error("MXU_LAUNCH", "No program specified")
        return False

    args_str  = str(param.get("args", "")).strip()
    wait_exit = bool(param.get("wait_for_exit", False))
    skip_run  = bool(param.get("skip_if_running", False))
    use_cmd   = bool(param.get("use_cmd", False))

    if skip_run:
        exe_name = Path(program).name.lower()
        try:
            if sys.platform == "win32":
                if _is_process_running_windows(exe_name):
                    _log().info("MXU_LAUNCH", f"'{program}' already running, skipping")
                    return True
            else:
                out = subprocess.check_output(["pgrep", "-f", exe_name], text=True)
                if out.strip():
                    _log().info("MXU_LAUNCH", f"'{program}' already running, skipping")
                    return True
        except subprocess.CalledProcessError:
            pass  # not running (pgrep exits 1 when nothing matches)
        except Exception as e:
            # Couldn't tell — launch anyway rather than silently skipping.
            _log().warning("MXU_LAUNCH", f"Could not check for a running '{exe_name}': {e}")

    args_list = _split_args(args_str)
    cwd = str(Path(program).parent) if Path(program).parent.exists() else None

    if use_cmd and sys.platform == "win32":
        cmd = ["cmd", "/c", program] + args_list
    else:
        cmd = [program] + args_list

    _log().info("MXU_LAUNCH", f"Launching {cmd}")

    extra: dict[str, Any] = {}
    if sys.platform == "win32":
        extra["creationflags"] = 0x0000_0008 | 0x0000_0200  # DETACHED | NEW_GROUP

    try:
        if wait_exit:
            result = subprocess.run(cmd, cwd=cwd, **extra)
            _log().success("MXU_LAUNCH", f"Exited with code {result.returncode}")
        else:
            subprocess.Popen(cmd, cwd=cwd, **extra)
            _log().success("MXU_LAUNCH", "Spawned")
        return True
    except Exception as e:
        _log().error("MXU_LAUNCH", f"Failed: {e}")
        return False


def run_webhook(param: dict, stop: threading.Event) -> bool:
    import urllib.request
    url = str(param.get("url", "")).strip()
    if not url:
        _log().error("MXU_WEBHOOK", "No url specified")
        return False
    _log().info("MXU_WEBHOOK", f"GET {url}")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            _log().success("MXU_WEBHOOK", f"Status {resp.status}")
        return True
    except Exception as e:
        _log().error("MXU_WEBHOOK", f"Failed: {e}")
        return False


def run_notify(param: dict, stop: threading.Event) -> bool:
    title = str(param.get("title", "MXU"))
    body  = str(param.get("body",  ""))
    _log().info("MXU_NOTIFY", f"title={title!r} body={body!r}")
    try:
        if sys.platform == "win32":
            # Use PowerShell toast — no extra deps needed.
            #
            # The text is handed to PowerShell through environment variables
            # rather than interpolated into the script, so apostrophes and
            # other quoting characters can't break the PowerShell string. It
            # is then XML-escaped inside PowerShell (& < > " ') because it
            # goes into the toast's XML payload; unescaped, an "&" or "<"
            # makes LoadXml throw.
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] | Out-Null; "
                "$t = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
                "ContentType=WindowsRuntime]::new(); "
                "$title = [System.Security.SecurityElement]::Escape($env:KKAFIO_TOAST_TITLE); "
                "$body = [System.Security.SecurityElement]::Escape($env:KKAFIO_TOAST_BODY); "
                "$t.LoadXml('<toast><visual><binding template=\"ToastText02\">"
                "<text id=\"1\">' + $title + '</text><text id=\"2\">' + $body + '</text>"
                "</binding></visual></toast>'); "
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MXU')"
                ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
            )
            env = {**os.environ,
                   "KKAFIO_TOAST_TITLE": title,
                   "KKAFIO_TOAST_BODY": body}
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                creationflags=0x0800_0000, timeout=5, env=env,
            )
        elif sys.platform == "darwin":
            # Pass the text as argv items to the AppleScript instead of
            # splicing it into the script source, so quotes/backslashes
            # can't break out of the string literal.
            subprocess.run(
                ["osascript",
                 "-e", "on run argv",
                 "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                 "-e", "end run",
                 title, body],
                timeout=5,
            )
        else:
            subprocess.run(
                ["notify-send", "--", title, body], timeout=5,
            )
        _log().success("MXU_NOTIFY", "Sent")
        return True
    except Exception as e:
        _log().error("MXU_NOTIFY", f"Failed: {e}")
        return False


def run_killproc(param: dict, stop: threading.Event) -> bool:
    kill_self = bool(param.get("kill_self", True))
    if kill_self:
        # "stop current tasks" — in CLI mode this means we stop iteration.
        # The caller checks return value and stops if False.
        _log().info("MXU_KILLPROC", "kill_self requested — stopping task execution")
        stop.set()
        return True  # let the loop handle the stop flag check

    proc_name = str(param.get("process_name", "")).strip()
    if not proc_name:
        _log().error("MXU_KILLPROC", "No process_name specified")
        return False

    _log().info("MXU_KILLPROC", f"Killing '{proc_name}'")
    try:
        if sys.platform == "win32":
            _run_cmd(["taskkill", "/F", "/IM", proc_name])
        else:
            _run_cmd(["pkill", "-f", proc_name])
        _log().success("MXU_KILLPROC", f"Sent kill to '{proc_name}'")
        return True
    except Exception as e:
        _log().error("MXU_KILLPROC", f"Failed: {e}")
        return False


def _windows_screen_off() -> None:
    """Turn the monitor(s) off by broadcasting WM_SYSCOMMAND / SC_MONITORPOWER.

    The script is passed with -EncodedCommand so no shell quoting is involved.
    Fixes over the previous inline version: the type literal needs brackets
    ([W.W]::..., "W.W::..." is a PowerShell parse error), the broadcast handle
    is HWND_BROADCAST (0xFFFF), not -1, and PostMessage is used instead of
    SendMessage so an unresponsive window can't hang the call.
    """
    import base64
    ps = (
        "Add-Type -Name W -Namespace W -MemberDefinition '"
        "[DllImport(\"user32.dll\")] public static extern bool PostMessage("
        "IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);' | Out-Null; "
        "[void][W.W]::PostMessage([IntPtr]0xFFFF, 0x0112, [IntPtr]0xF170, [IntPtr]2)"
    )
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    subprocess.run(
        ["powershell", "-NoProfile", "-EncodedCommand", encoded],
        creationflags=0x0800_0000,
    )


# Grace period before a shutdown/restart actually happens, giving the user a
# window to notice and cancel it (`shutdown /a` on Windows, `shutdown -c` on
# Linux) if it was triggered unexpectedly — e.g. by a misconfigured pipeline,
# or while something important is still open elsewhere. Not applied to
# sleep/screenoff, which are non-destructive and instantly reversible.
POWER_ACTION_DELAY_SECONDS = 30


def run_power(param: dict, stop: threading.Event) -> bool:
    action = str(param.get("power_action", "shutdown")).strip()
    delay  = param.get("power_delay_seconds", POWER_ACTION_DELAY_SECONDS)
    try:
        delay = max(0, int(delay))
    except (TypeError, ValueError):
        delay = POWER_ACTION_DELAY_SECONDS
    _log().info("MXU_POWER", f"action={action}")
    try:
        if sys.platform == "win32":
            if action == "shutdown":
                _log().info("MXU_POWER",
                    f"Shutting down in {delay}s — run 'shutdown /a' to cancel.")
                _run_cmd(["shutdown", "/s", "/f", "/t", str(delay)])
                if delay and stop.wait(delay):
                    _log().info("MXU_POWER", "Stop requested — cancelling scheduled shutdown.")
                    _run_cmd(["shutdown", "/a"])
                    return False
            elif action == "restart":
                _log().info("MXU_POWER",
                    f"Restarting in {delay}s — run 'shutdown /a' to cancel.")
                _run_cmd(["shutdown", "/r", "/f", "/t", str(delay)])
                if delay and stop.wait(delay):
                    _log().info("MXU_POWER", "Stop requested — cancelling scheduled restart.")
                    _run_cmd(["shutdown", "/a"])
                    return False
            elif action == "sleep":
                _run_cmd(["rundll32", "powrprof.dll,SetSuspendState", "0", "1", "0"])
            elif action == "screenoff":
                _windows_screen_off()
        elif sys.platform == "darwin":
            if action == "shutdown":
                if delay and stop.wait(delay):
                    _log().info("MXU_POWER", "Shutdown cancelled (Stop requested).")
                    return False
                subprocess.run(["osascript", "-e", "tell app \"System Events\" to shut down"])
            elif action == "restart":
                if delay and stop.wait(delay):
                    _log().info("MXU_POWER", "Restart cancelled (Stop requested).")
                    return False
                subprocess.run(["osascript", "-e", "tell app \"System Events\" to restart"])
            elif action == "sleep":
                subprocess.run(["pmset", "sleepnow"])           # whole system
            elif action == "screenoff":
                subprocess.run(["pmset", "displaysleepnow"])    # display only
        else:
            if action == "shutdown":
                _log().info("MXU_POWER",
                    f"Shutting down in {delay}s — run 'shutdown -c' to cancel.")
                subprocess.run(["shutdown", "-P", f"+{max(1, delay // 60) if delay else 0}"]
                               if delay else ["systemctl", "poweroff"])
            elif action == "restart":
                _log().info("MXU_POWER",
                    f"Restarting in {delay}s — run 'shutdown -c' to cancel.")
                subprocess.run(["shutdown", "-r", f"+{max(1, delay // 60) if delay else 0}"]
                               if delay else ["systemctl", "reboot"])
            elif action == "sleep":
                subprocess.run(["systemctl", "suspend"])
            elif action == "screenoff":
                subprocess.run(["xset", "dpms", "force", "off"])
        _log().success("MXU_POWER", f"Executed {action}")
        return True
    except Exception as e:
        _log().error("MXU_POWER", f"Failed: {e}")
        return False


# ── dispatch table ──────────────────────────────────────────────────────────

_HANDLERS = {
    "__MXU_SLEEP__":     run_sleep,
    "__MXU_WAITUNTIL__": run_wait_until,
    "__MXU_LAUNCH__":    run_launch,
    "__MXU_WEBHOOK__":   run_webhook,
    "__MXU_NOTIFY__":    run_notify,
    "__MXU_KILLPROC__":  run_killproc,
    "__MXU_POWER__":     run_power,
}


def is_special_task(task_name: str) -> bool:
    return task_name in _HANDLERS


def run_special_task(task_name: str, param: dict, stop: threading.Event) -> bool:
    fn = _HANDLERS.get(task_name)
    if fn is None:
        _log().warning("MXU", f"Unknown special task: {task_name}")
        return False
    return fn(param, stop)
