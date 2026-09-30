"""
kkafio_cli.py — CLI entry point for KKAFIO
===========================================

Task commands (arguments override config; omit to use config value):
    kkafio_cli run
    kkafio_cli install-contents [--input DIR]
    kkafio_cli uninstall-contents  [--input DIR]
    kkafio_cli filter-convert-kks        [--input DIR] [--convert | --no-convert]
                             [--kk-action {Keep,Move,Delete}] [--kks-action {Keep,Move,Delete}]
    kkafio_cli create-backup [--output DIR] [--filename NAME]
                             [--mods | --no-mods]
                             [--userdata | --no-userdata]
                             [--bepinex | --no-bepinex]
    kkafio_cli list-instances

Shell context menu:
    Run kkafio_setup.bat and choose "Register context menu" to add KKAFIO to
    the Explorer right-click menu (no Administrator required). Choose
    "Unregister context menu" to remove it.

Global options:
    --config PATH   use a custom config.json instead of %APPDATA%/KKAFIO/config/mxu-KKAFIO.json
    --instance N    use instance N from the config (0-based, default: 0).
                    Use "--instance context-menu" to pick the instance that is
                    marked "Use in Explorer Context Menu" in MXU (right-click a
                    tab); falls back to instance 0 if none is marked. The
                    registered Explorer context-menu entries pass this.
"""

import contextlib
import functools
import sys
import argparse
import multiprocessing
import signal
import traceback
from typing import NoReturn


# ---------------------------------------------------------------------------
# [FIX-2026-09-14-GRACEFUL-STOP] Graceful stop signal handling
# ---------------------------------------------------------------------------
def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt()


def install_graceful_stop_handler() -> None:
    """
    Make CTRL_BREAK_EVENT (Windows) and SIGTERM behave like Ctrl+C.

    The MXU GUI wrapper stops this process by first sending
    GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid) and only escalating to an
    unconditional TerminateProcess kill if the process doesn't exit within a
    grace period. Python's default disposition for CTRL_BREAK_EVENT is to
    terminate the process immediately with no chance to run any cleanup code
    at all — no signal handler, no `finally` block, nothing — unless a
    handler is explicitly installed for it.

    Installing a handler that raises KeyboardInterrupt makes CTRL_BREAK_EVENT
    behave exactly like Ctrl+C: it propagates up through asyncio.run() and
    whatever task is currently running, unwinding through the same
    `try`/`finally` paths a normal KeyboardInterrupt already would. In
    particular, src/kkafio/tasks/download_missing_mods.py's own
    `finally: await teleget_downloader.shutdown()` block then runs as
    intended, sending the download daemon subprocess a proper, graceful IPC
    ShutdownRequest instead of leaving it orphaned mid-download.

    SIGTERM is handled the same way for parity outside of Windows / outside
    of the console-control-event mechanism specifically.
    """
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)

    try:
        signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    except (ValueError, AttributeError):
        # signal.signal() can only be called from the main thread, and
        # SIGTERM isn't available on every platform; skip silently rather
        # than fail startup over a best-effort parity handler.
        pass


# ---------------------------------------------------------------------------
# [FIX-2026-09-14-SUPPRESS-TELEGET-CONSOLE-LOGS] Quiet teleget9527's INFO
# console output in frozen builds, without losing it from the log file.
# ---------------------------------------------------------------------------
def suppress_teleget_console_logs() -> None:
    """
    Prevent teleget9527's per-module logs (download_manager_v2,
    download_daemon_core, download_ipc, etc. — all created via
    `logging.getLogger(__name__)` with no level/handlers of their own, so
    they inherit and propagate to the root logger) from being printed to
    this process's stdout at all, while still letting them reach any file
    handler teleget itself later attaches.

    Errors surfaced by src/kkafio/tasks/download_missing_mods.py are already reported
    to the user through KKAFIO's own error handling (which points them at
    the log file), so there's no need to also mirror teleget's own
    WARNING/ERROR-level lines to the console for visibility — this
    suppresses everything, not just INFO/DEBUG.

    Why this has to run *before* anything else touches logging: teleget's
    own AccountScheduler.__init__ does the equivalent of:

        root_logger = logging.getLogger()
        if not root_logger.handlers:
            logging.basicConfig(level=logging.INFO,
                                 handlers=[logging.StreamHandler(sys.stdout)])

    — i.e. it only adds its own console handler if the root logger doesn't
    already have one. Attaching a NullHandler here first satisfies that
    check (a NullHandler still counts as "a handler is present"), so
    teleget never adds its own real console handler at all — and since
    NullHandler discards everything unconditionally, no teleget-originated
    record reaches the console at any level.

    Note this only affects *this* (main/orchestrator) process. The
    download daemon runs as a separate multiprocessing child process that
    re-imports and re-executes fresh — multiprocessing.freeze_support()
    intercepts before any of this module's own __main__ code (including
    this function) ever runs there, so it can't reach the daemon's logging
    setup at all. The daemon's console output is silenced separately, via
    the `daemon_console_log_level` value passed into TGDownloader's
    `config=` argument (see src/kkafio/tasks/download_missing_mods.py), which requires
    a small corresponding change in teleget9527 itself to decouple its
    daemon-side file and console handler levels (previously tied together).
    """
    import logging

    root_logger = logging.getLogger()
    # Keep the *logger's* own level permissive (INFO) so records still reach
    # the handler-filtering stage at all — teleget's own file handler (added
    # later via _setup_unified_logging(), independent of this) sets its own,
    # more permissive level (DEBUG) and will still capture everything.
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logging.NullHandler())


# ---------------------------------------------------------------------------
# Core loader
# ---------------------------------------------------------------------------

# Value for the global --instance option that means "whichever instance is
# marked for the Explorer context menu in MXU" (see find_context_menu_instance).
CONTEXT_MENU_INSTANCE = "context-menu"


def _instance_arg(value: str):
    """argparse type for --instance: a 0-based index, or "context-menu"."""
    if value.strip().lower() == CONTEXT_MENU_INSTANCE:
        return CONTEXT_MENU_INSTANCE
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid instance '{value}' (expected a number or '{CONTEXT_MENU_INSTANCE}')") from None


def _load_core(config_path: str | None = None, instance_index: int | str = 0):
    from kkafio.core.config import Config, find_context_menu_instance
    from kkafio.core.constants import CONFIG_PATH
    from kkafio.core.file_manager import FileManager
    from kkafio.core.logger import logger

    path = config_path if config_path else str(CONFIG_PATH)
    if instance_index == CONTEXT_MENU_INSTANCE:
        marked = find_context_menu_instance(path)
        if marked is None:
            logger.info("CLI", "No instance is marked for the Explorer context menu "
                               "— using the first instance")
            instance_index = 0
        else:
            instance_index = marked
    config = Config(path, instance_index=instance_index)
    file_manager = FileManager(config)
    return config, file_manager


def _traceback_path():
    # CONFIG_DIR is a fixed, always-writable, per-platform location (the
    # same place config.json/telegram.json already live) —
    # writing "traceback.log" as a bare relative path instead landed
    # wherever the process happened to be launched from (the game's own
    # folder if double-clicked there, possibly a read-only location like
    # Program Files, and a different place every time depending on how
    # KKAFIO was started), so a user following "see traceback.log" often
    # couldn't find it or the write silently failed.
    from kkafio.core.constants import CONFIG_DIR
    return CONFIG_DIR / "traceback.log"


def _write_traceback(task: str) -> None:
    path = _traceback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{task}]\n")
        traceback.print_exc(None, f, True)
        f.write("\n")


def _report_failure(task: str) -> NoReturn:
    """Shared handler for an unexpected exception inside a single-task
    command. Must be called from within an ``except`` block.

    Logs a visible error line (so the GUI/console shows *why* the task
    failed instead of just a non-zero exit code), saves the full traceback
    to traceback.log, and exits with status 1. Failing to write the
    traceback must never mask the original error.
    """
    exc = sys.exc_info()[1]
    detail = f"{type(exc).__name__}: {exc}" if exc is not None else "unknown error"
    try:
        from kkafio.core.logger import logger
        logger.error("CLI", f"Task error: {task}: {detail}. "
                            f"See {_traceback_path()} for details.")
    except Exception:
        print(f"[ERROR] Task error: {task}: {detail}", file=sys.stderr)
    try:
        _write_traceback(task)
    except Exception as tb_err:
        print(f"[ERROR] Could not write {_traceback_path()}: {tb_err}", file=sys.stderr)
    sys.exit(1)


def _clear_traceback() -> None:
    # Best-effort: a stale traceback.log that can't be removed must never stop a run.
    with contextlib.suppress(Exception):
        _traceback_path().unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _run_task_command(spec, args):
    """Handler shared by every task subcommand (see kkafio.registry / task_specs).

    Enables the task, turns the given options into config overrides, and runs it.
    Any unexpected error is reported with the task's name and exits 1.
    """
    from kkafio.registry import run_task
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data[spec.name]["Enable"] = True
        overrides = spec.overrides_from(args)
        if spec.prepare is not None and spec.prepare(args, overrides) is False:
            return  # e.g. a sibling context-menu invocation is handling the whole batch
        run_task(spec, config, file_manager, overrides)
        if spec.after_run is not None:
            spec.after_run(args)
    except SystemExit:
        raise
    except Exception:
        _report_failure(spec.name)


def cmd_list_instances(args):
    """Print all instance names with their indices."""
    from kkafio.core.config import list_instances, find_context_menu_instance
    from kkafio.core.constants import CONFIG_PATH
    config_path = args.config if args.config else str(CONFIG_PATH)
    instances = list_instances(config_path)
    if not instances:
        print(f"No instances found in '{config_path}'")
        return
    marked = find_context_menu_instance(config_path)
    for idx, name in instances:
        tags = []
        if idx == 0:
            tags.append("default")
        if idx == marked:
            tags.append("context menu")
        suffix = f" ({', '.join(tags)})" if tags else ""
        print(f"  [{idx}] {name}{suffix}")




def _shares_extraction_folder(config) -> bool:
    """True if FilterConvertKKS will already have extracted InstallContents' archives.

    That is the case when both run, in that order, on the same folder, with
    extraction enabled in both. (If InstallContents were ordered first, skipping
    its own extraction would leave the archives unextracted at install time.)
    """
    from pathlib import Path
    fc_cfg = config.config_data["FilterConvertKKS"]
    ic_cfg = config.config_data["InstallContents"]
    order = [e["name"] for e in config.task_order]
    fc_before_ic = (
        "FilterConvertKKS" in order and "InstallContents" in order and
        order.index("FilterConvertKKS") < order.index("InstallContents")
    )
    return bool(
        fc_before_ic and
        fc_cfg.get("Enable", False) and ic_cfg.get("Enable", False) and
        fc_cfg.get("ExtractArchive", True) and ic_cfg.get("ExtractArchive", True) and
        "InputPath" in fc_cfg and "InputPath" in ic_cfg and
        Path(fc_cfg["InputPath"]) == Path(ic_cfg["InputPath"])
    )


def cmd_run(args):
    from kkafio.core.logger import logger
    from kkafio.registry import run_task
    from kkafio.system.special_tasks import is_special_task, run_special_task
    from kkafio.task_specs import TASKS_BY_NAME
    import threading
    _clear_traceback()
    config, file_manager = _load_core(args.config, instance_index=args.instance)

    same_path = _shares_extraction_folder(config)
    if same_path:
        logger.info("CLI", "FilterConvertKKS and InstallContents share the same input path — "
                           "archive extraction will run in FilterConvertKKS only")

    # Shared stop event — special tasks check this to abort early
    stop = threading.Event()

    # Execute tasks in the exact order defined in the MXU instance
    for entry in config.task_order:
        task_name = entry["name"]
        params    = entry.get("params", {})

        if stop.is_set():
            logger.info("CLI", "Stop requested — aborting remaining tasks")
            break

        logger.info("CLI", f"Start Task: {task_name}")

        if is_special_task(task_name):
            ok = run_special_task(task_name, params, stop)
            if not ok and not stop.is_set():
                logger.error("CLI", f"Special task failed: {task_name}")
                sys.exit(1)
        else:
            spec = TASKS_BY_NAME.get(task_name)
            if spec is None:
                logger.warning("CLI", f"Unknown task '{task_name}', skipping")
                continue
            run_kwargs = {"skip_extract": same_path} if task_name == "InstallContents" else {}
            try:
                run_task(spec, config, file_manager, **run_kwargs)
            except Exception:
                _report_failure(task_name)

    sys.exit(0)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    from kkafio.task_specs import TASK_SPECS
    parser = argparse.ArgumentParser(
        prog="kkafio_cli",
        description="KKAFIO — Koikatsu file I/O automation tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", "-c", metavar="PATH", default=None,
        help="Path to a config.json file (default: %%APPDATA%%/KKAFIO/config/mxu-KKAFIO.json)",
    )
    parser.add_argument(
        "--instance", "-n", metavar="N|context-menu", type=_instance_arg, default=0,
        help="Zero-based index of the instance to use (default: 0), or 'context-menu' for "
             "the instance marked \"Use in Explorer Context Menu\" in MXU (first instance if "
             "none is marked). Run 'list-instances' to see all available instances.",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # list-instances
    p = sub.add_parser("list-instances", help="List all instance names and their indices")
    p.set_defaults(func=cmd_list_instances)

    # run
    p = sub.add_parser("run", help="Run all enabled tasks in instance order")
    p.set_defaults(func=cmd_run)

    # One subcommand per registered task; options come from its TaskSpec.
    for spec in TASK_SPECS:
        extra = {"description": spec.description} if spec.description else {}
        p = sub.add_parser(spec.command, help=spec.help, **extra)
        spec.add_arguments(p)
        p.set_defaults(func=functools.partial(_run_task_command, spec))

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the KKAFIO CLI. Called by kkafio_cli.py, `python -m kkafio`, and the console script."""
    try:
        # [FIX-2026-09-13-FROZEN-DAEMON-ENTRYPOINT] Required for teleget9527's
        # daemon to launch correctly when this app is run as a PyInstaller
        # frozen exe: teleget9527 now spawns its download daemon via
        # multiprocessing.Process in frozen environments (since sys.executable
        # is this exe, not a generic Python interpreter, and can no longer be
        # used to run download_daemon.py as a script argument). This is the
        # standard multiprocessing requirement for any frozen Windows app that
        # creates additional processes — it must be called first, before any
        # other code, and has no effect when running from source.
        multiprocessing.freeze_support()

        # [FIX-2026-09-14-GRACEFUL-STOP] See install_graceful_stop_handler()
        # docstring above. Must be installed before parsing args / running
        # any task, so a Stop request arriving at any point afterward is
        # guaranteed to be caught.
        install_graceful_stop_handler()

        # [FIX-2026-09-14-SUPPRESS-TELEGET-CONSOLE-LOGS] Only suppress
        # teleget's verbose per-download-part INFO logging in packaged
        # (frozen) builds — keep full detail on the console for developers
        # running from source. Must run before build_parser()/args.func(args)
        # ever gets a chance to import/construct teleget's TGDownloader
        # (indirectly, via src/kkafio/tasks/download_missing_mods.py), since teleget
        # only attaches its own console handler if the root logger doesn't
        # already have one.
        if getattr(sys, "frozen", False):
            suppress_teleget_console_logs()

        parser = build_parser()
        args = parser.parse_args()
        args.func(args)

    except KeyboardInterrupt:
        # [FIX-2026-09-14-GRACEFUL-STOP] Raised either by a real Ctrl+C, or by
        # our own SIGBREAK/SIGTERM handler above standing in for the MXU GUI's
        # Stop button. By the time it reaches here, the task-level `finally`
        # blocks (e.g. src/kkafio/tasks/download_missing_mods.py's
        # `await teleget_downloader.shutdown()`) have already run while this
        # exception unwound through the running asyncio task. Exit quietly and
        # successfully rather than falling through to the traceback/dump-file
        # handling below, which is meant for genuinely unexpected errors.
        print("\nStopped.")
        sys.exit(0)

    except SystemExit:
        raise

    except Exception:
        from pathlib import Path
        try:
            from kkafio.core.constants import CONFIG_DIR
            _tb_path = CONFIG_DIR / "traceback.log"
        except Exception:
            # kkafio.core.constants itself failed to import/initialise — fall back to
            # the old relative path rather than losing the traceback entirely.
            _tb_path = Path("traceback.log")
        print(f"[ERROR] CLI initialisation error. See {_tb_path} for details.")
        with open(_tb_path, "w", encoding="utf-8") as f:
            f.write("CLI Initialisation Error\n")
            traceback.print_exc(None, f, True)
            f.write("\n")
        sys.exit(1)
