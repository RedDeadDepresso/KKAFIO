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

import sys
import time
import subprocess
import threading
from typing import Any


# ── logger forwarded from util.logger ──────────────────────────────────────

def _log():
    from util.logger import logger
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


def run_launch(param: dict, stop: threading.Event) -> bool:
    import shlex
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
                out = subprocess.check_output(
                    ["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
                    text=True, creationflags=0x0800_0000,
                )
                if exe_name.lower() in out.lower():
                    _log().info("MXU_LAUNCH", f"'{program}' already running, skipping")
                    return True
            else:
                out = subprocess.check_output(["pgrep", "-f", exe_name], text=True)
                if out.strip():
                    _log().info("MXU_LAUNCH", f"'{program}' already running, skipping")
                    return True
        except subprocess.CalledProcessError:
            pass  # not running

    args_list = shlex.split(args_str) if args_str else []
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
            # Use PowerShell toast — no extra deps needed
            ps = (
                f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                f"ContentType = WindowsRuntime] | Out-Null; "
                f"$t = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]::new(); "
                f"$t.LoadXml('<toast><visual><binding template=\"ToastText02\">"
                f"<text id=\"1\">{title}</text><text id=\"2\">{body}</text>"
                f"</binding></visual></toast>'); "
                f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MXU')"
                f".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                creationflags=0x0800_0000, timeout=5,
            )
        elif sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{body}" with title "{title}"'],
                timeout=5,
            )
        else:
            subprocess.run(
                ["notify-send", title, body], timeout=5,
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


def run_power(param: dict, stop: threading.Event) -> bool:
    action = str(param.get("power_action", "shutdown")).strip()
    _log().info("MXU_POWER", f"action={action}")
    try:
        if sys.platform == "win32":
            if action == "shutdown":
                _run_cmd(["shutdown", "/s", "/f", "/t", "0"])
            elif action == "restart":
                _run_cmd(["shutdown", "/r", "/f", "/t", "0"])
            elif action == "sleep":
                _run_cmd(["rundll32", "powrprof.dll,SetSuspendState", "0", "1", "0"])
            elif action == "screenoff":
                # Send WM_SYSCOMMAND SC_MONITORPOWER 2 via PowerShell
                ps = (
                    "Add-Type -Name W -Namespace W -MemberDefinition "
                    "'[DllImport(\"user32.dll\")] public static extern int "
                    "SendMessage(IntPtr h,int m,int w,int l);' | Out-Null; "
                    "W.W::SendMessage(-1, 0x0112, 0xF170, 2)"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    creationflags=0x0800_0000,
                )
        elif sys.platform == "darwin":
            if action == "shutdown":
                subprocess.run(["osascript", "-e", "tell app \"System Events\" to shut down"])
            elif action == "restart":
                subprocess.run(["osascript", "-e", "tell app \"System Events\" to restart"])
            elif action in ("sleep", "screenoff"):
                subprocess.run(["pmset", "displaysleepnow"])
        else:
            if action == "shutdown":
                subprocess.run(["systemctl", "poweroff"])
            elif action == "restart":
                subprocess.run(["systemctl", "reboot"])
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
