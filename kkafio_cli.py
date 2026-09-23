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
    --instance N    use instance N from the config (0-based, default: 0)
"""

import sys
import argparse
import multiprocessing
import signal
import traceback


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
    particular, tasks/download_missing_mods.py's own
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

    Errors surfaced by tasks/download_missing_mods.py are already reported
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
    `config=` argument (see tasks/download_missing_mods.py), which requires
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

def _load_core(config_path: str | None = None, instance_index: int = 0):
    from utils.config import Config
    from utils.constants import CONFIG_PATH
    from utils.file_manager import FileManager

    path = config_path if config_path else str(CONFIG_PATH)
    config = Config(path, instance_index=instance_index)
    file_manager = FileManager(config)
    return config, file_manager


def _traceback_path():
    # CONFIG_DIR is a fixed, always-writable, per-platform location (the
    # same place config.json/7zip.json/telegram.json already live) —
    # writing "traceback.log" as a bare relative path instead landed
    # wherever the process happened to be launched from (the game's own
    # folder if double-clicked there, possibly a read-only location like
    # Program Files, and a different place every time depending on how
    # KKAFIO was started), so a user following "see traceback.log" often
    # couldn't find it or the write silently failed.
    from utils.constants import CONFIG_DIR
    return CONFIG_DIR / "traceback.log"


def _write_traceback(task: str) -> None:
    with open(_traceback_path(), "a", encoding="utf-8") as f:
        f.write(f"[{task}]\n")
        traceback.print_exc(None, f, True)
        f.write("\n")


def _clear_traceback() -> None:
    try:
        _traceback_path().unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Task runners
# ---------------------------------------------------------------------------

def run_install_contents(config, file_manager, input_path: str | None = None,
                      extract_archive: bool | None = None, skip_extract: bool = False,
                      chara: bool | None = None, mods: bool | None = None,
                      coords: bool | None = None, scenes: bool | None = None,
                      overlays: bool | None = None):
    from tasks.install_contents import InstallContents
    from pathlib import Path
    module = InstallContents(config, file_manager)
    if extract_archive is not None:
        module.extract_archive = extract_archive
    if chara    is not None: module.do_chara    = chara
    if mods     is not None: module.do_mods     = mods
    if coords   is not None: module.do_coords   = coords
    if scenes   is not None: module.do_scenes   = scenes
    if overlays is not None: module.do_overlays = overlays
    module.run(folder_path=Path(input_path) if input_path else None,
               skip_extract=skip_extract)


def run_uninstall_contents(config, file_manager, input_path: str | None = None,
                           chara: bool | None = None, mods: bool | None = None,
                           coords: bool | None = None, scenes: bool | None = None,
                           overlays: bool | None = None):
    from tasks.uninstall_contents import UninstallContents
    from pathlib import Path
    if input_path is not None:
        config.uninstall_contents["InputPath"] = Path(input_path)
    module = UninstallContents(config, file_manager)
    if chara    is not None: module.do_chara    = chara
    if mods     is not None: module.do_mods     = mods
    if coords   is not None: module.do_coords   = coords
    if scenes   is not None: module.do_scenes   = scenes
    if overlays is not None: module.do_overlays = overlays
    module.run()


def run_filter_convert_kks(config, file_manager, input_path: str | None = None,
               convert: bool | None = None,
               kk_action: str | None = None,
               kks_action: str | None = None,
               extract_archive: bool | None = None):
    from tasks.filter_convert_kks import FilterConvertKKS
    from pathlib import Path
    if input_path is not None:
        config.filter_convert_kks["InputPath"] = Path(input_path)
    if convert is not None:
        config.filter_convert_kks["Convert"] = convert
    if kk_action is not None:
        config.filter_convert_kks["KKAction"] = kk_action
    if kks_action is not None:
        config.filter_convert_kks["KKSAction"] = kks_action
    module = FilterConvertKKS(config, file_manager)
    if extract_archive is not None:
        module.extract_archive = extract_archive
    module.run()


def run_download_contents(config, file_manager, links: str | None = None,
                       output_dir: str | None = None,
                       skip_downloaded: bool | None = None,
):
    from tasks.download_contents import DownloadContents
    module = DownloadContents(config, file_manager)
    if links is not None:
        module.links = links
    if output_dir is not None:
        module.output_dir_str = output_dir
    if skip_downloaded is not None:
        module.skip_downloaded = skip_downloaded
    module.run()


def run_download_missing_mods(config, file_manager,
                              mods_dir: str | None = None,
                              chara_dir: str | None = None,
                              scene_dir: str | None = None,
                              coord_dir: str | None = None,
                              content_types: list[str] | None = None,
                              use_cache: bool | None = None,
                              modpack_mode: str | None = None,
                              telegram_source: str | None = None,
                              telegram_chat_links: str | None = None):
    from tasks.download_missing_mods import DownloadMissingMods
    module = DownloadMissingMods(config, file_manager)
    if mods_dir is not None:
        module.mods_dir_str = mods_dir
    if chara_dir is not None:
        module.chara_dir_str = chara_dir
    if scene_dir is not None:
        module.scene_dir_str = scene_dir
    if coord_dir is not None:
        module.coord_dir_str = coord_dir
    if content_types is not None:
        module.content_types = content_types
    if use_cache is not None:
        module.use_cache = use_cache
    if modpack_mode is not None:
        module.modpack_mode = modpack_mode
    if telegram_source is not None:
        module.telegram_source = telegram_source
    if telegram_chat_links is not None:
        module.telegram_chat_links_raw = telegram_chat_links
    module.run()


def run_export_mods(config, file_manager,
                    output_path: str | None = None,
                    guids: str | None = None,
                    rename_to_guid: bool | None = None,
                    use_cache: bool | None = None,
                    mods_dir: str | None = None):
    from tasks.export_mods import ExportMods
    module = ExportMods(config, file_manager)
    if output_path is not None:
        module.output_path_str = output_path
    if guids is not None:
        module.guids_raw = guids
    if rename_to_guid is not None:
        module.rename_to_guid = rename_to_guid
    if use_cache is not None:
        module.use_cache = use_cache
    if mods_dir is not None:
        module.mods_dir_str = mods_dir
    module.run()


def run_compress_cards_textures(config, file_manager,
                                input_path: str | None = None,
                                tool_path: str | None = None,
                                delete_original: bool | None = None):
    from tasks.compress_cards_textures import CompressCardsTextures
    module = CompressCardsTextures(config, file_manager)
    if input_path is not None:
        module.input_path_str = input_path
    if tool_path is not None:
        module.tool_path_str = tool_path
    if delete_original is not None:
        module.delete_original = delete_original
    module.run()



def run_delete_cards(config, file_manager, content_paths: list[str] | None = None,
                     check_shared_mods: bool | None = None,
                     auto_resolve: bool | None = None,
                     use_cache: bool | None = None,
                     include_coordinates: bool | None = None,
                     mods_dir: str | None = None, chara_dir: str | None = None,
                     scene_dir: str | None = None, coord_dir: str | None = None):
    from tasks.delete_cards import DeleteCards
    module = DeleteCards(config, file_manager)
    if content_paths is not None:
        module.content_paths = content_paths
    if check_shared_mods is not None:
        module.check_shared_mods = check_shared_mods
    if auto_resolve is not None:
        module.auto_resolve = auto_resolve
    if use_cache is not None:
        module.use_cache = use_cache
    if include_coordinates is not None:
        module.include_coordinates = include_coordinates
    if mods_dir is not None:
        module.mods_dir_str = mods_dir
    if chara_dir is not None:
        module.chara_dir_str = chara_dir
    if scene_dir is not None:
        module.scene_dir_str = scene_dir
    if coord_dir is not None:
        module.coord_dir_str = coord_dir
    module.run()


def run_archive_cards(config, file_manager, content_paths: list[str] | None = None,
                      fmt: str | None = None, auto_resolve: bool | None = None,
                      use_cache: bool | None = None,
                      include_modpack: bool | None = None,
                      combined: bool | None = None,
                      include_coordinates: bool | None = None,
                      mods_dir: str | None = None, coord_dir: str | None = None,
                      output_dir: str | None = None):
    from tasks.archive_cards import ArchiveCards
    module = ArchiveCards(config, file_manager)
    if content_paths is not None:
        module.content_paths = content_paths
    if fmt is not None:
        module.format = fmt
    if auto_resolve is not None:
        module.auto_resolve = auto_resolve
    if use_cache is not None:
        module.use_cache = use_cache
    if include_modpack is not None:
        module.include_modpack = include_modpack
    if combined is not None:
        module.combined_archive = combined
    if include_coordinates is not None:
        module.include_coordinates = include_coordinates
    if mods_dir is not None:
        module.mods_dir_str = mods_dir
    if coord_dir is not None:
        module.coord_dir_str = coord_dir
    if output_dir is not None:
        module.output_dir_str = output_dir
    module.run()


def run_ungroup_chara(config, file_manager, input_path: str | None = None,
                      delete_empty: bool | None = None):
    from tasks.ungroup_chara import UngroupChara
    from pathlib import Path
    if input_path is not None:
        config.ungroup_chara["InputPath"] = Path(input_path)
    module = UngroupChara(config, file_manager)
    if delete_empty is not None:
        module.delete_empty = delete_empty
    module.run()


def run_rename_chara(config, file_manager, input_path: str | None = None,
                     skip_already_renamed: bool | None = None,
                     update_metadata: bool | None = None,
                     rename_files: bool | None = None):
    from tasks.rename_chara import RenameChara
    module = RenameChara(config, file_manager)
    if input_path is not None:
        module.input_path_str = input_path
    if skip_already_renamed is not None:
        module.skip_already_renamed = skip_already_renamed
    if update_metadata is not None:
        module.update_metadata = update_metadata
    if rename_files is not None:
        module.rename_files = rename_files
    module.run()


def run_group_chara(config, file_manager, input_path: str | None = None,
                    include_subfolders: bool | None = None):
    from tasks.group_chara import GroupChara
    module = GroupChara(config, file_manager)
    if input_path is not None:
        module.input_path_str = input_path
    if include_subfolders is not None:
        module.include_subfolders = include_subfolders
    module.run()


def run_filter_duplicate_contents(config, file_manager, input_path: str | None = None,
                          fuzzy: bool | None = None,
                          keep: str | None = None, duplicate_action: str | None = None,
                          use_cache: bool | None = None):
    from tasks.filter_duplicate_contents import FilterDuplicateContents
    from pathlib import Path
    if input_path is not None:
        config.filter_duplicate_contents["InputPath"] = Path(input_path)
    module = FilterDuplicateContents(config, file_manager)
    if fuzzy is not None:
        module.fuzzy_chara = fuzzy
    if keep is not None:
        module.keep = keep
    if duplicate_action is not None:
        module.duplicate_action = duplicate_action
    if use_cache is not None:
        module.use_cache = use_cache
    module.run()


def run_create_backup(config, file_manager, output_path: str | None = None,
                      filename: str | None = None, mods: bool | None = None,
                      userdata: bool | None = None, bepinex: bool | None = None):
    from tasks.create_backup  import CreateBackup
    from pathlib import Path
    if output_path is not None:
        config.create_backup["OutputPath"] = Path(output_path)
    if filename is not None:
        config.create_backup["Filename"] = filename
    if mods is not None:
        config.create_backup["mods"] = mods
    if userdata is not None:
        config.create_backup["UserData"] = userdata
    if bepinex is not None:
        config.create_backup["BepInEx"] = bepinex
    CreateBackup(config, file_manager).run()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_list_instances(args):
    """Print all instance names with their indices."""
    from utils.config import list_instances
    from utils.constants import CONFIG_PATH
    config_path = args.config if args.config else str(CONFIG_PATH)
    instances = list_instances(config_path)
    if not instances:
        print(f"No instances found in '{config_path}'")
        return
    for idx, name in instances:
        marker = " (default)" if idx == 0 else ""
        print(f"  [{idx}] {name}{marker}")


def cmd_run(args):
    from pathlib import Path
    from utils.logger import logger
    from utils.special_tasks import is_special_task, run_special_task
    import threading
    _clear_traceback()
    config, file_manager = _load_core(args.config, instance_index=args.instance)

    # filter_convert_kks + InstallContents same-path detection
    fc_cfg = config.config_data["FilterConvertKKS"]
    ic_cfg = config.config_data["InstallContents"]
    same_path = (
        fc_cfg.get("Enable", False) and ic_cfg.get("Enable", False) and
        fc_cfg.get("ExtractArchive", True) and ic_cfg.get("ExtractArchive", True) and
        "InputPath" in fc_cfg and "InputPath" in ic_cfg and
        Path(fc_cfg["InputPath"]) == Path(ic_cfg["InputPath"])
    )

    kkafio_task_map = {
        "ArchiveCards":     lambda: run_archive_cards(config, file_manager),
        "DeleteCards":      lambda: run_delete_cards(config, file_manager),
        "DownloadContents":    lambda: run_download_contents(config, file_manager),
        "DownloadMissingMods": lambda: run_download_missing_mods(config, file_manager),
        "ExportMods":       lambda: run_export_mods(config, file_manager),
        "CompressCardsTextures": lambda: run_compress_cards_textures(config, file_manager),
        "CreateBackup":     lambda: run_create_backup(config, file_manager),
        "FilterConvertKKS": lambda: run_filter_convert_kks(config, file_manager),
        "FilterDuplicateContents": lambda: run_filter_duplicate_contents(config, file_manager),
        "RenameChara":     lambda: run_rename_chara(config, file_manager),
        "GroupChara":       lambda: run_group_chara(config, file_manager),
        "InstallContents":     lambda: run_install_contents(config, file_manager,
                                                      skip_extract=same_path),
        "UninstallContents":      lambda: run_uninstall_contents(config, file_manager),
        "UngroupChara":     lambda: run_ungroup_chara(config, file_manager),
    }

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
            fn = kkafio_task_map.get(task_name)
            if fn is None:
                logger.warning("CLI", f"Unknown task '{task_name}', skipping")
                continue
            try:
                fn()
            except Exception:
                logger.error("CLI", f"Task error: {task_name}. See {_traceback_path()} for details.")
                _write_traceback(task_name)
                sys.exit(1)

    sys.exit(0)


def cmd_install_contents(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["InstallContents"]["Enable"] = True
        extract = args.extract_archive  # argparse store_true/store_false pair -> already True/False/None
        run_install_contents(config, file_manager, input_path=args.input,
                          extract_archive=extract,
                          chara=args.chara, mods=args.mods,
                          coords=args.coords, scenes=args.scenes,
                          overlays=args.overlays)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("InstallContents")
        sys.exit(1)


def cmd_uninstall_contents(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["UninstallContents"]["Enable"] = True
        run_uninstall_contents(config, file_manager, input_path=args.input,
                               chara=args.chara, mods=args.mods,
                               coords=args.coords, scenes=args.scenes,
                               overlays=args.overlays)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("UninstallContents")
        sys.exit(1)


def cmd_filter_convert_kks(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["FilterConvertKKS"]["Enable"] = True
        convert = args.convert  # argparse store_true/store_false pair -> already True/False/None
        extract = args.extract_archive  # argparse store_true/store_false pair -> already True/False/None
        run_filter_convert_kks(config, file_manager, input_path=args.input,
                   convert=convert,
                   kk_action=args.kk_action,
                   kks_action=args.kks_action,
                   extract_archive=extract)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("FilterConvertKKS")
        sys.exit(1)


def cmd_download_contents(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["DownloadContents"]["Enable"] = True
        links = None
        if args.links:
            from pathlib import Path as _Path
            p = _Path(args.links)
            links = p.read_text(encoding="utf-8") if p.is_file() else args.links
        skip = args.skip_downloaded  # argparse store_true/store_false pair -> already True/False/None
        run_download_contents(config, file_manager, links=links,
                           output_dir=args.output_dir,
                           skip_downloaded=skip,
)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("DownloadContents")
        sys.exit(1)


def cmd_download_missing_mods(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["DownloadMissingMods"]["Enable"] = True
        use_cache = None if args.use_cache is None else bool(args.use_cache)
        content_types = None
        if args.no_chara or args.no_scene or args.no_coord:
            content_types = [t for t, skip in
                             (("Chara", args.no_chara), ("Scene", args.no_scene), ("Coord", args.no_coord))
                             if not skip]
        run_download_missing_mods(
            config, file_manager,
            mods_dir=args.mods_dir or None,
            chara_dir=args.chara_dir or None,
            scene_dir=args.scene_dir or None,
            coord_dir=args.coord_dir or None,
            content_types=content_types,
            use_cache=use_cache,
            modpack_mode=args.modpack_mode or None,
            telegram_source=args.telegram_source,
            telegram_chat_links=args.telegram_chat_links,
        )
    except SystemExit:
        raise
    except Exception:
        _write_traceback("DownloadMissingMods")
        sys.exit(1)


def cmd_export_mods(args):
    _clear_traceback()
    try:
        from pathlib import Path
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["ExportMods"]["Enable"] = True
        guids = args.guids
        if args.guids_file:
            guids = Path(args.guids_file).read_text(encoding="utf-8")
        rename_to_guid = None if args.rename_to_guid is None else bool(args.rename_to_guid)
        use_cache      = None if args.use_cache is None else bool(args.use_cache)
        run_export_mods(config, file_manager,
                        output_path=args.output,
                        guids=guids,
                        rename_to_guid=rename_to_guid,
                        use_cache=use_cache,
                        mods_dir=args.mods_dir)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("ExportMods")
        sys.exit(1)


def cmd_compress_cards_textures(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["CompressCardsTextures"]["Enable"] = True
        delete_original = None if args.delete_original is None else bool(args.delete_original)
        run_compress_cards_textures(config, file_manager,
                                    input_path=args.input,
                                    tool_path=args.tool_path,
                                    delete_original=delete_original)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("CompressCardsTextures")
        sys.exit(1)


def cmd_delete_cards(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["DeleteCards"]["Enable"] = True
        content_paths = args.content if args.content else None

        if getattr(args, "context_menu", False) and content_paths and len(content_paths) == 1:
            from pathlib import Path
            from utils.context_menu_batch import coordinate_batch
            batch = coordinate_batch("delete-cards", Path(content_paths[0]))
            if batch is None:
                return  # a sibling invocation is handling this whole batch
            content_paths = [str(p) for p in batch]

        check_shared_mods   = None if args.check_shared_mods is None else bool(args.check_shared_mods)
        auto_resolve        = None if args.auto_resolve is None else bool(args.auto_resolve)
        use_cache           = None if args.use_cache is None else bool(args.use_cache)
        include_coordinates = None if args.include_coordinates is None else bool(args.include_coordinates)
        run_delete_cards(config, file_manager,
                         content_paths=content_paths,
                         check_shared_mods=check_shared_mods,
                         auto_resolve=auto_resolve,
                         use_cache=use_cache,
                         include_coordinates=include_coordinates,
                         mods_dir=args.mods_dir, chara_dir=args.chara_dir,
                         scene_dir=args.scene_dir, coord_dir=args.coord_dir)
        if getattr(args, "context_menu", False):
            input("\nPress Enter to close...")
    except SystemExit:
        raise
    except Exception:
        _write_traceback("DeleteCards")
        sys.exit(1)


def cmd_archive_cards(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["ArchiveCards"]["Enable"] = True
        content_paths       = args.content if args.content else None
        output_dir          = args.output_dir

        if getattr(args, "context_menu", False) and content_paths and len(content_paths) == 1:
            import os
            from pathlib import Path
            from utils.context_menu_batch import coordinate_batch
            batch = coordinate_batch("archive-cards", Path(content_paths[0]))
            if batch is None:
                return  # a sibling invocation is handling this whole batch
            content_paths = [str(p) for p in batch]
            if output_dir is None:
                # Default to the common parent folder of everything selected
                # (the same folder they're in, for a normal single-folder
                # multi-select) rather than each card's own folder, since
                # they're now being combined into one archive.
                output_dir = os.path.commonpath([str(Path(p).parent) for p in batch])

        auto_resolve        = None if args.auto_resolve is None else bool(args.auto_resolve)
        use_cache           = None if args.use_cache is None else bool(args.use_cache)
        include_modpack     = None if args.include_modpack is None else bool(args.include_modpack)
        combined            = None if args.combined is None else bool(args.combined)
        include_coordinates = None if args.include_coordinates is None else bool(args.include_coordinates)
        run_archive_cards(config, file_manager,
                          content_paths=content_paths, fmt=args.format,
                          auto_resolve=auto_resolve,
                          use_cache=use_cache,
                          include_modpack=include_modpack,
                          combined=combined,
                          include_coordinates=include_coordinates,
                          mods_dir=args.mods_dir, coord_dir=args.coord_dir,
                          output_dir=output_dir)
        if getattr(args, "context_menu", False):
            input("\nPress Enter to close...")
    except SystemExit:
        raise
    except Exception:
        _write_traceback("ArchiveCards")
        sys.exit(1)


def cmd_ungroup_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["UngroupChara"]["Enable"] = True
        delete_empty = args.delete_empty  # argparse store_true/store_false pair -> already True/False/None
        run_ungroup_chara(config, file_manager,
                          input_path=args.input, delete_empty=delete_empty)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("UngroupChara")
        sys.exit(1)


def cmd_rename_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["RenameChara"]["Enable"] = True
        skip = None if args.skip_already_renamed is None else bool(args.skip_already_renamed)
        meta = None if args.update_metadata is None else bool(args.update_metadata)
        ren  = None if args.rename_files is None else bool(args.rename_files)
        run_rename_chara(config, file_manager,
                         input_path=args.input,
                         skip_already_renamed=skip,
                         update_metadata=meta,
                         rename_files=ren)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("RenameChara")
        sys.exit(1)


def cmd_group_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["GroupChara"]["Enable"] = True
        include_sub = getattr(args, 'include_subfolders', None)
        run_group_chara(config, file_manager,
                        input_path=args.input, include_subfolders=include_sub)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("GroupChara")
        sys.exit(1)


def cmd_filter_duplicate_contents(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["FilterDuplicateContents"]["Enable"] = True
        fuzzy = args.fuzzy  # argparse store_true/store_false pair -> already True/False/None
        use_cache = args.use_cache  # argparse store_true/store_false pair -> already True/False/None
        duplicate_action = {
            "move-rename": "Move & Rename",
            "move":        "Move",
            "delete":      "Delete",
        }.get(args.action)
        run_filter_duplicate_contents(config, file_manager, input_path=args.input,
                              fuzzy=fuzzy, keep=args.keep,
                              duplicate_action=duplicate_action, use_cache=use_cache)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("FilterDuplicateContents")
        sys.exit(1)


def cmd_create_backup(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["CreateBackup"]["Enable"] = True
        mods     = True if args.mods     else (False if args.no_mods     else None)
        userdata = True if args.userdata else (False if args.no_userdata else None)
        bepinex  = True if args.bepinex  else (False if args.no_bepinex  else None)
        run_create_backup(config, file_manager, output_path=args.output,
                          filename=args.filename, mods=mods,
                          userdata=userdata, bepinex=bepinex)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("CreateBackup")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
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
        "--instance", "-n", metavar="N", type=int, default=0,
        help="Zero-based index of the instance to use (default: 0). "
             "Run 'list-instances' to see all available instances.",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # list-instances
    p = sub.add_parser("list-instances", help="List all instance names and their indices")
    p.set_defaults(func=cmd_list_instances)

    # run
    p = sub.add_parser("run", help="Run all enabled tasks in instance order")
    p.set_defaults(func=cmd_run)

    # install-contents
    p = sub.add_parser("install-contents", help="Copy cards / mods / overlays into the game")
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to scan (default: InstallContents.InputPath from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--extract-archive",    dest="extract_archive", action="store_true",  default=None)
    g.add_argument("--no-extract-archive", dest="extract_archive", action="store_false")
    for flag in ("chara", "mods", "coords", "scenes", "overlays"):
        g2 = p.add_mutually_exclusive_group()
        g2.add_argument(f"--{flag}",    dest=flag, action="store_true",  default=None)
        g2.add_argument(f"--no-{flag}", dest=flag, action="store_false")
    p.set_defaults(func=cmd_install_contents)

    # uninstall-contents
    p = sub.add_parser("uninstall-contents", help="Remove cards / mods from the game")
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to scan (default: UninstallContents.InputPath from config)")
    for flag in ("chara", "mods", "coords", "scenes", "overlays"):
        g2 = p.add_mutually_exclusive_group()
        g2.add_argument(f"--{flag}",    dest=flag, action="store_true",  default=None)
        g2.add_argument(f"--no-{flag}", dest=flag, action="store_false")
    p.set_defaults(func=cmd_uninstall_contents)

    # filter-convert-kks
    p = sub.add_parser("filter-convert-kks", help="Convert KKS cards to KK and/or sort each type into its own folder")
    p.add_argument("--input", "-i", metavar="DIR", default=None)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--convert",    dest="convert", action="store_true",  default=None,
                   help="Produce a KK-compatible copy of each KKS card, saved next to its original "
                        "(then sorted/kept by --kk-action, not --kks-action)")
    g.add_argument("--no-convert", dest="convert", action="store_false")
    p.add_argument("--kk-action", dest="kk_action", choices=["Keep", "Move", "Delete"], default=None,
                   help="What to do with KK/KKSP cards found (including converted KKS copies). Default: Keep")
    p.add_argument("--kks-action", dest="kks_action", choices=["Keep", "Move", "Delete"], default=None,
                   help="What to do with the original KKS cards found. Default: Keep")
    g2 = p.add_mutually_exclusive_group()
    g2.add_argument("--extract-archive",    dest="extract_archive", action="store_true",  default=None)
    g2.add_argument("--no-extract-archive", dest="extract_archive", action="store_false")
    p.set_defaults(func=cmd_filter_convert_kks)

    # download-contents
    p = sub.add_parser(
        "download-contents",
        help="Download character cards from db.bepis.moe or koikatsucards.com",
    )
    p.add_argument("--links", default=None, metavar="URLS_OR_FILE",
                   help="Newline-separated URLs, or path to a .txt file containing them "
                        "(default: DownloadContents.Links from config)")
    p.add_argument("--output-dir", default=None, metavar="DIR",
                   help="Directory to save downloaded cards (default: DownloadContents.OutputDir from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--skip-downloaded",    dest="skip_downloaded", action="store_true",  default=None,
                   help="Skip already-downloaded URLs (overrides config)")
    g.add_argument("--no-skip-downloaded", dest="skip_downloaded", action="store_false",
                   help="Re-download even if previously downloaded (overrides config)")
    p.set_defaults(func=cmd_download_contents)

    # download-missing-mods
    p = sub.add_parser(
        "download-missing-mods",
        help="Find mods referenced by chara cards but missing locally, then download them",
    )
    p.add_argument("--mods-dir", default=None, metavar="DIR",
                   help="Override the mods directory (default: game mods dir from config)")
    p.add_argument("--chara-dir", default=None, metavar="DIR",
                   help="Override the chara directory to scan (default: game chara dirs from config)")
    p.add_argument("--scene-dir", default=None, metavar="DIR",
                   help="Override the Studio scene directory to scan (default: game scene dir from config, if Studio is installed)")
    p.add_argument("--coord-dir", default=None, metavar="DIR",
                   help="Override the coordinate directory to scan (default: game coordinate dir from config)")
    p.add_argument("--no-chara", action="store_true", default=False,
                   help="Skip scanning character cards for referenced mod GUIDs")
    p.add_argument("--no-scene", action="store_true", default=False,
                   help="Skip scanning Studio scenes for referenced mod GUIDs")
    p.add_argument("--no-coord", action="store_true", default=False,
                   help="Skip scanning coordinate cards for referenced mod GUIDs")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--use-cache",    dest="use_cache", action="store_true",  default=None,
                   help="Use mods and chara cache to skip scanning (default: on)")
    g.add_argument("--no-use-cache", dest="use_cache", action="store_false")
    p.add_argument("--modpack-mode", default=None,
                   choices=["Skip", "OnlyUsed", "All"],
                   help="How to handle Sideloader Modpack mods: "
                        "Skip=ignore modpack entirely, "
                        "OnlyUsed=download missing mods used by chara (default), "
                        "All=download all missing modpack mods")
    p.add_argument("--telegram-source", default=None,
                   choices=["No", "KoikatsuCards", "ChatLinks", "Both"],
                   help="Where to look for mods not covered by BetterRepack: "
                        "No=don't use Telegram (default), "
                        "KoikatsuCards=look up each GUID on koikatsucards.com, "
                        "ChatLinks=search the chats in --telegram-chat-links directly, "
                        "Both=try koikatsucards.com first, then ChatLinks for anything it couldn't find")
    p.add_argument("--telegram-chat-links", default=None, metavar="LINKS",
                   help="Newline-separated Telegram chat/channel/group links to search "
                        "(one per line; add a topic ID like .../299 to search only that "
                        "forum topic; a trailing '# comment' is ignored). Only used when "
                        "--telegram-source is ChatLinks or Both. Default: the two example "
                        "chats shipped in the config.")
    p.set_defaults(func=cmd_download_missing_mods)

    # export-mods
    p = sub.add_parser(
        "export-mods",
        help="Find zipmods by GUID and copy them into an output folder",
    )
    p.add_argument("--output", "-o", metavar="DIR", default=None,
                   help="Output directory to copy exported zipmods into (default: ExportMods.OutputPath from config)")
    g_guids = p.add_mutually_exclusive_group()
    g_guids.add_argument("--guids", metavar="TEXT", default=None,
                   help="GUIDs to export — one per line or comma-separated. Accepts a Download "
                        "Missing Mods report pasted directly (bullets like '!', '\u2717', '+', '~' and "
                        "trailing '(...)' notes are stripped automatically). "
                        "(default: ExportMods.Guids from config)")
    g_guids.add_argument("--guids-file", metavar="FILE", default=None,
                   help="Read GUIDs from a text file instead of passing them inline "
                        "(e.g. a saved kkafio_missing_mods_report.txt)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--rename-to-guid",    dest="rename_to_guid", action="store_true",  default=None,
                   help="Rename each exported zipmod to [guid].zipmod (default: on)")
    g.add_argument("--no-rename-to-guid", dest="rename_to_guid", action="store_false",
                   help="Keep each exported zipmod's original filename")
    g_cache = p.add_mutually_exclusive_group()
    g_cache.add_argument("--use-cache",    dest="use_cache", action="store_true",  default=None,
                    help="Use the mods cache to skip re-scanning unchanged zipmods (default: on)")
    g_cache.add_argument("--no-use-cache", dest="use_cache", action="store_false")
    p.add_argument("--mods-dir", default=None, metavar="DIR",
                   help="Override the mods directory to search (default: game mods dir from config)")
    p.set_defaults(func=cmd_export_mods)

    # compress-cards-textures
    p = sub.add_parser(
        "compress-cards-textures",
        help="Recompress the textures inside chara/coordinate cards with KoiCardTexTool",
    )
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to scan (default: CompressCardsTextures.InputPath from config)")
    p.add_argument("--tool-path", metavar="DIR", default=None,
                   help="Folder containing (or where to install) KoiCardTexTool.exe "
                        "(default: the input folder itself)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--delete-original",    dest="delete_original", action="store_true",  default=None,
                   help="Send the original card to the Recycle Bin once a compressed "
                        "[zip] version exists alongside it (default: off)")
    g.add_argument("--no-delete-original", dest="delete_original", action="store_false",
                   help="Keep both the original and the compressed [zip] version")
    p.set_defaults(func=cmd_compress_cards_textures)

    # delete-cards
    p = sub.add_parser(
        "delete-cards",
        help="Send character cards, coordinate cards, or Studio scenes and their associated mods/coords to the recycle bin",
    )
    p.add_argument("content", nargs="*", metavar="CONTENT",
                   help="Character/coordinate/scene PNG paths (default: DeleteCards.ContentPaths from config)")
    g_shared = p.add_mutually_exclusive_group()
    g_shared.add_argument("--check-shared-mods",    dest="check_shared_mods", action="store_true",  default=None,
                   help="Before deleting a zipmod, verify no other installed character/scene/coordinate "
                        "still uses it (default: on)")
    g_shared.add_argument("--no-check-shared-mods", dest="check_shared_mods", action="store_false",
                   help="Skip the shared-mod check — faster, but may delete mods other characters still need")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--auto-resolve",    dest="auto_resolve", action="store_true",  default=None,
                   help="Auto-resolve mods and coord dirs (overrides config)")
    g.add_argument("--no-auto-resolve", dest="auto_resolve", action="store_false",
                   help="Use explicit --mods-dir / --coord-dir instead")
    g_cache2 = p.add_mutually_exclusive_group()
    g_cache2.add_argument("--use-cache",    dest="use_cache", action="store_true",  default=None,
                     help="Cache mod/coord directory scans (overrides config)")
    g_cache2.add_argument("--no-use-cache", dest="use_cache", action="store_false",
                     help="Disable cache and do a full scan (overrides config)")
    g_coord = p.add_mutually_exclusive_group()
    g_coord.add_argument("--include-coordinates",    dest="include_coordinates", action="store_true",  default=None,
                   help="When deleting a character card, also delete its matching coordinate "
                        "cards and their mods (default: on)")
    g_coord.add_argument("--no-include-coordinates", dest="include_coordinates", action="store_false",
                   help="Only delete the character card itself, leave its coordinates alone")
    p.add_argument("--mods-dir",  default=None, metavar="DIR",
                   help="Mods directory (only used when --no-auto-resolve)")
    p.add_argument("--chara-dir", default=None, metavar="DIR",
                   help="Custom chara directory for the shared-mod check (default: game's chara folders)")
    p.add_argument("--scene-dir", default=None, metavar="DIR",
                   help="Custom scene directory for the shared-mod check (default: game's Studio scene folder)")
    p.add_argument("--coord-dir", default=None, metavar="DIR",
                   help="Coordinate directory (used for coordinate matching when --no-auto-resolve, "
                        "and for the shared-mod check; default: game's coordinate folder)")
    p.add_argument("--context-menu", action="store_true", default=False,
                   help="Internal flag set by the context menu (via kkafio_setup.bat): coalesces multiple "
                        "simultaneous Explorer-selection invocations (one per selected file) "
                        "into a single combined run instead of processing each file separately.")
    p.set_defaults(func=cmd_delete_cards)

    # archive-cards
    p = sub.add_parser(
        "archive-cards",
        help="Bundle character cards, coordinate cards, or Studio scenes with their zipmods and matching coordinates",
    )
    p.add_argument("content", nargs="*", metavar="CONTENT",
                   help="Character/coordinate/scene PNG paths (default: ArchiveCards.ContentPaths from config)")
    p.add_argument("--format", choices=["7z", "zip"], default=None,
                   help="Archive format (default: ArchiveCards.Format from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--auto-resolve",    dest="auto_resolve", action="store_true",  default=None,
                   help="Auto-resolve mods and coord dirs (overrides config)")
    g.add_argument("--no-auto-resolve", dest="auto_resolve", action="store_false",
                   help="Disable auto-resolution (overrides config)")
    g_cache = p.add_mutually_exclusive_group()
    g_cache.add_argument("--use-cache",    dest="use_cache", action="store_true",  default=None,
                    help="Cache mod/coord directory scans (overrides config)")
    g_cache.add_argument("--no-use-cache", dest="use_cache", action="store_false",
                    help="Disable cache and do a full scan (overrides config)")
    g2 = p.add_mutually_exclusive_group()
    g2.add_argument("--include-modpack",    dest="include_modpack", action="store_true",  default=None,
                    help="Include zipmods from Sideloader Modpack folders (overrides config)")
    g2.add_argument("--no-include-modpack", dest="include_modpack", action="store_false",
                    help="Exclude Sideloader Modpack zipmods (overrides config)")
    g3 = p.add_mutually_exclusive_group()
    g3.add_argument("--combined",    dest="combined", action="store_true",  default=None,
                    help="Put all cards in one archive (overrides config)")
    g3.add_argument("--no-combined", dest="combined", action="store_false",
                    help="One archive per card (overrides config)")
    g4 = p.add_mutually_exclusive_group()
    g4.add_argument("--include-coordinates",    dest="include_coordinates", action="store_true",  default=None,
                    help="When archiving a character card, also bundle its matching coordinate "
                         "cards and their mods (default: on)")
    g4.add_argument("--no-include-coordinates", dest="include_coordinates", action="store_false",
                    help="Only bundle the character card itself, leave its coordinates out")
    p.add_argument("--mods-dir",   default=None, metavar="DIR",
                   help="Mods directory override (only used when --no-auto-resolve)")
    p.add_argument("--coord-dir",  default=None, metavar="DIR",
                   help="Coordinate directory override")
    p.add_argument("--output-dir", default=None, metavar="DIR",
                   help="Output directory (default: same folder as chara card/scene)")
    p.add_argument("--context-menu", action="store_true", default=False,
                   help="Internal flag set by the context menu (via kkafio_setup.bat): coalesces multiple "
                        "simultaneous Explorer-selection invocations (one per selected file) "
                        "into a single combined run instead of processing each file separately, "
                        "and defaults --output-dir to the common parent folder of the selection.")
    p.set_defaults(func=cmd_archive_cards)

    # ungroup-chara
    p = sub.add_parser(
        "ungroup-chara",
        help="Move character cards from subfolders back to the top-level folder",
    )
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to ungroup (default: UngroupChara.InputPath from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--delete-empty",    dest="delete_empty", action="store_true",  default=None,
                   help="Remove empty subfolders after moving (overrides config)")
    g.add_argument("--no-delete-empty", dest="delete_empty", action="store_false",
                   help="Keep empty subfolders (overrides config)")
    p.set_defaults(func=cmd_ungroup_chara)

    # rename-chara
    p = sub.add_parser("rename-chara",
                        help="Translate character card names to English using an LLM "
                             "(shows a native Copy/Paste dialog when it runs)")
    p.add_argument("--input", "-i", metavar="DIR", default=None)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--skip-already-renamed",    dest="skip_already_renamed",
                   action="store_true", default=None)
    g.add_argument("--no-skip-already-renamed", dest="skip_already_renamed",
                   action="store_false")
    g2 = p.add_mutually_exclusive_group()
    g2.add_argument("--update-metadata",    dest="update_metadata",
                    action="store_true", default=None,
                    help="Write translated names into card metadata (default: on)")
    g2.add_argument("--no-update-metadata", dest="update_metadata",
                    action="store_false")
    g3 = p.add_mutually_exclusive_group()
    g3.add_argument("--rename-files",    dest="rename_files",
                    action="store_true", default=None,
                    help="Also rename the PNG file to match the translated name")
    g3.add_argument("--no-rename-files", dest="rename_files",
                    action="store_false")
    p.set_defaults(func=cmd_rename_chara)

    # group-chara
    p = sub.add_parser(
        "group-chara",
        help="Move character cards into series subfolders using an LLM "
             "(shows a native Copy/Paste dialog when it runs)",
    )
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder containing character PNGs (default: GroupChara.InputPath from config)")
    p.add_argument("--include-subfolders", action="store_true", default=None,
                   help="Include character cards from subfolders when scanning (overrides config)")
    p.set_defaults(func=cmd_group_chara)

    # filter-duplicate-contents
    p = sub.add_parser(
        "filter-duplicate-contents",
        help="Find and handle duplicate PNG cards and zipmod files",
        description=(
            "Scans the input folder recursively for duplicate PNG cards and zipmod files. "
            "Duplicates are identified by content (not filename). "
            "By default they are moved to a _duplicates_/ subfolder and renamed. "
            "With --action delete they are sent to the recycle bin instead."
        ),
    )
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to scan (default: FilterDuplicateContents.InputPath from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--fuzzy",    dest="fuzzy", action="store_true",  default=None,
                   help="Enable fuzzy matching for chara cards (overrides config)")
    g.add_argument("--no-fuzzy", dest="fuzzy", action="store_false",
                   help="Disable fuzzy matching (overrides config)")
    p.add_argument("--keep", metavar="STRATEGY", default=None,
                   choices=['None', 'Newest', 'Oldest', 'Biggest file size', 'Smallest file size', 'Last alphabetically', 'First alphabetically'],
                   help="Which copy to keep as the original (overrides config)")
    p.add_argument("--action", choices=["move-rename", "move", "delete"], default=None,
                   help="What to do with duplicates (overrides config). 'move-rename' (default) "
                        "moves duplicates to _duplicates_/ and renames them after the kept copy "
                        "(or the first duplicate found, if --keep is 'None'), "
                        "with a number suffix. 'move' moves them to _duplicates_/ keeping their "
                        "original filenames. 'delete' sends them straight to the recycle bin.")
    g2 = p.add_mutually_exclusive_group()
    g2.add_argument("--use-cache",    dest="use_cache", action="store_true",  default=None,
                    help="Cache file hashes (and phashes, for fuzzy matching) to speed up "
                         "repeat scans (overrides config)")
    g2.add_argument("--no-use-cache", dest="use_cache", action="store_false",
                    help="Disable cache and re-hash every file (overrides config)")
    p.set_defaults(func=cmd_filter_duplicate_contents)

    # create-backup
    p = sub.add_parser("create-backup", help="Create a 7-Zip backup of game folders")
    p.add_argument("--output",   "-o", metavar="DIR",  default=None)
    p.add_argument("--filename", "-f", metavar="NAME", default=None)
    fg = p.add_argument_group("folder selection (each pair overrides its config flag)")
    fg.add_argument("--mods",         dest="mods",        action="store_true", default=False)
    fg.add_argument("--no-mods",      dest="no_mods",     action="store_true", default=False)
    fg.add_argument("--userdata",     dest="userdata",     action="store_true", default=False)
    fg.add_argument("--no-userdata",  dest="no_userdata",  action="store_true", default=False)
    fg.add_argument("--bepinex",      dest="bepinex",      action="store_true", default=False)
    fg.add_argument("--no-bepinex",   dest="no_bepinex",   action="store_true", default=False)
    p.set_defaults(func=cmd_create_backup)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

try:
    if __name__ == "__main__":
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
        # (indirectly, via tasks/download_missing_mods.py), since teleget
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
    # blocks (e.g. tasks/download_missing_mods.py's
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
        from utils.constants import CONFIG_DIR
        _tb_path = CONFIG_DIR / "traceback.log"
    except Exception:
        # utils.constants itself failed to import/initialise — fall back to
        # the old relative path rather than losing the traceback entirely.
        _tb_path = Path("traceback.log")
    print(f"[ERROR] CLI initialisation error. See {_tb_path} for details.")
    with open(_tb_path, "w", encoding="utf-8") as f:
        f.write("CLI Initialisation Error\n")
        traceback.print_exc(None, f, True)
        f.write("\n")
    sys.exit(1)