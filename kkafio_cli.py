"""
kkafio_cli.py — CLI entry point for KKAFIO
===========================================

Task commands (arguments override config; omit to use config value):
    kkafio_cli run
    kkafio_cli install-chara [--input DIR]
    kkafio_cli remove-chara  [--input DIR]
    kkafio_cli fc-kks        [--input DIR] [--convert | --no-convert]
    kkafio_cli create-backup [--output DIR] [--filename NAME]
                             [--mods | --no-mods]
                             [--userdata | --no-userdata]
                             [--bepinex | --no-bepinex]
    kkafio_cli list-instances

Shell context menu:
    Run register_context_menu.bat as Administrator to add KKAFIO to the
    Explorer right-click menu.  Run unregister_context_menu.bat to remove it.

Global options:
    --config PATH   use a custom config.json instead of %APPDATA%/KKAFIO/config/mxu-KKAFIO.json
    --instance N    use instance N from the config (0-based, default: 0)
"""

import sys
import argparse
import traceback


# ---------------------------------------------------------------------------
# Core loader
# ---------------------------------------------------------------------------

def _load_core(config_path: str | None = None, instance_index: int = 0):
    from util.config import Config
    from util.constants import CONFIG_PATH
    from util.file_manager import FileManager

    path = config_path if config_path else str(CONFIG_PATH)
    config = Config(path, instance_index=instance_index)
    file_manager = FileManager(config)
    return config, file_manager


def _write_traceback(task: str) -> None:
    with open("traceback.log", "a") as f:
        f.write(f"[{task}]\n")
        traceback.print_exc(None, f, True)
        f.write("\n")


def _clear_traceback() -> None:
    try:
        from pathlib import Path
        Path("traceback.log").unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Task runners
# ---------------------------------------------------------------------------

def run_install_chara(config, file_manager, input_path: str | None = None,
                      extract_archive: bool | None = None, skip_extract: bool = False):
    from tasks.install_chara import InstallChara
    from pathlib import Path
    module = InstallChara(config, file_manager)
    if extract_archive is not None:
        module.extract_archive = extract_archive
    module.run(folder_path=Path(input_path) if input_path else None,
               skip_extract=skip_extract)


def run_uninstall_chara(config, file_manager, input_path: str | None = None):
    from tasks.uninstall_chara import UninstallChara
    from pathlib import Path
    if input_path is not None:
        config.uninstall_chara["InputPath"] = Path(input_path)
    UninstallChara(config, file_manager).run()


def run_fc_kks(config, file_manager, input_path: str | None = None,
               convert: bool | None = None, extract_archive: bool | None = None):
    from tasks.fc_kks import FilterConvertKKS
    from pathlib import Path
    if input_path is not None:
        config.fc_kks["InputPath"] = Path(input_path)
    if convert is not None:
        config.fc_kks["Convert"] = convert
    module = FilterConvertKKS(config, file_manager)
    if extract_archive is not None:
        module.extract_archive = extract_archive
    module.run()


def run_download_chara(config, file_manager, links: str | None = None,
                       output_dir: str | None = None,
                       skip_downloaded: bool | None = None):
    from tasks.download_chara import DownloadChara
    module = DownloadChara(config, file_manager)
    if links is not None:
        module.links = links
    if output_dir is not None:
        module.output_dir_str = output_dir
    if skip_downloaded is not None:
        module.skip_downloaded = skip_downloaded
    module.run()


def run_delete_chara(config, file_manager, chara_paths: list[str] | None = None,
                     auto_resolve: bool | None = None,
                     use_cache: bool | None = None,
                     mods_dir: str | None = None, coord_dir: str | None = None):
    from tasks.delete_chara import DeleteChara
    module = DeleteChara(config, file_manager)
    if chara_paths is not None:
        module.chara_paths = chara_paths
    if auto_resolve is not None:
        module.auto_resolve = auto_resolve
    if use_cache is not None:
        module.use_cache = use_cache
    if mods_dir is not None:
        module.mods_dir_str = mods_dir
    if coord_dir is not None:
        module.coord_dir_str = coord_dir
    module.run()


def run_archive_chara(config, file_manager, chara_paths: list[str] | None = None,
                      fmt: str | None = None, auto_resolve: bool | None = None,
                      use_cache: bool | None = None,
                      include_modpack: bool | None = None,
                      combined: bool | None = None,
                      mods_dir: str | None = None, coord_dir: str | None = None,
                      output_dir: str | None = None):
    from tasks.archive_chara import ArchiveChara
    module = ArchiveChara(config, file_manager)
    if chara_paths is not None:
        module.chara_paths = chara_paths
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


def run_group_chara(config, file_manager, input_path: str | None = None,
                    response: str | None = None, include_subfolders: bool | None = None):
    from tasks.group_chara import process
    from pathlib import Path
    folder = Path(input_path) if input_path else Path(config.group_chara["InputPath"])
    json_str = response if response is not None else config.group_chara.get("Response", "")
    if not json_str:
        print("[ERROR] No LLM response found. Use --response or paste a response in Settings first.")
        import sys; sys.exit(1)
    process(folder, json_str)


def run_filter_duplicates(config, file_manager, input_path: str | None = None,
                          delete: bool | None = None, fuzzy: bool | None = None,
                          keep: str | None = None):
    from tasks.filter_duplicates import FilterDuplicates
    from pathlib import Path
    if input_path is not None:
        config.filter_duplicates["InputPath"] = Path(input_path)
    module = FilterDuplicates(config, file_manager)
    if delete is not None:
        module.delete = delete
    if fuzzy is not None:
        module.fuzzy_chara = fuzzy
    if keep is not None:
        module.keep = keep
    module.run()


def run_create_backup(config, file_manager, output_path: str | None = None,
                      filename: str | None = None, mods: bool | None = None,
                      userdata: bool | None = None, bepinex: bool | None = None):
    from tasks.create_backup import CreateBackup
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
    from util.config import list_instances
    from util.constants import CONFIG_PATH
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
    from util.logger import logger
    from util.special_tasks import is_special_task, run_special_task
    import threading
    _clear_traceback()
    config, file_manager = _load_core(args.config, instance_index=args.instance)

    # fc_kks + InstallChara same-path detection
    fc_cfg = config.config_data["FilterConvertKKS"]
    ic_cfg = config.config_data["InstallChara"]
    same_path = (
        fc_cfg.get("Enable", False) and ic_cfg.get("Enable", False) and
        fc_cfg.get("ExtractArchive", True) and ic_cfg.get("ExtractArchive", True) and
        "InputPath" in fc_cfg and "InputPath" in ic_cfg and
        Path(fc_cfg["InputPath"]) == Path(ic_cfg["InputPath"])
    )

    kkafio_task_map = {
        "ArchiveChara":     lambda: run_archive_chara(config, file_manager),
        "DeleteChara":      lambda: run_delete_chara(config, file_manager),
        "DownloadChara":    lambda: run_download_chara(config, file_manager),
        "CreateBackup":     lambda: run_create_backup(config, file_manager),
        "FilterConvertKKS": lambda: run_fc_kks(config, file_manager),
        "FilterDuplicates": lambda: run_filter_duplicates(config, file_manager),
        "GroupChara":       lambda: run_group_chara(config, file_manager),
        "InstallChara":     lambda: run_install_chara(config, file_manager,
                                                      skip_extract=same_path),
        "UninstallChara":      lambda: run_uninstall_chara(config, file_manager),
        "UngroupChara":     lambda: run_ungroup_chara(config, file_manager),
    }

    if same_path:
        logger.info("CLI", "FilterConvertKKS and InstallChara share the same input path — "
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
                logger.error("CLI", f"Task error: {task_name}. See traceback.log for details.")
                _write_traceback(task_name)
                sys.exit(1)

    sys.exit(0)


def cmd_install_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["InstallChara"]["Enable"] = True
        extract = None
        if args.extract_archive is True:
            extract = True
        elif args.extract_archive is False:
            extract = False
        run_install_chara(config, file_manager, input_path=args.input,
                          extract_archive=extract)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("InstallChara")
        sys.exit(1)


def cmd_uninstall_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["UninstallChara"]["Enable"] = True
        run_uninstall_chara(config, file_manager, input_path=args.input)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("UninstallChara")
        sys.exit(1)


def cmd_fc_kks(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["FilterConvertKKS"]["Enable"] = True
        convert = None
        if args.convert is True:
            convert = True
        elif args.convert is False:
            convert = False
        extract = None
        if args.extract_archive is True:
            extract = True
        elif args.extract_archive is False:
            extract = False
        run_fc_kks(config, file_manager, input_path=args.input,
                   convert=convert, extract_archive=extract)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("FilterConvertKKS")
        sys.exit(1)


def cmd_download_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["DownloadChara"]["Enable"] = True
        links = None
        if args.links:
            from pathlib import Path as _Path
            p = _Path(args.links)
            links = p.read_text(encoding="utf-8") if p.is_file() else args.links
        skip = None
        if args.skip_downloaded is True:
            skip = True
        elif args.skip_downloaded is False:
            skip = False
        run_download_chara(config, file_manager, links=links,
                           output_dir=args.output_dir,
                           skip_downloaded=skip)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("DownloadChara")
        sys.exit(1)


def cmd_delete_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["DeleteChara"]["Enable"] = True
        chara_paths  = args.chara if args.chara else None
        auto_resolve = None if args.auto_resolve is None else bool(args.auto_resolve)
        use_cache    = None if args.use_cache is None else bool(args.use_cache)
        run_delete_chara(config, file_manager,
                         chara_paths=chara_paths, auto_resolve=auto_resolve,
                         use_cache=use_cache,
                         mods_dir=args.mods_dir, coord_dir=args.coord_dir)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("DeleteChara")
        sys.exit(1)


def cmd_archive_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["ArchiveChara"]["Enable"] = True
        chara_paths     = args.chara if args.chara else None
        auto_resolve    = None if args.auto_resolve is None else bool(args.auto_resolve)
        use_cache       = None if args.use_cache is None else bool(args.use_cache)
        include_modpack = None if args.include_modpack is None else bool(args.include_modpack)
        combined        = None if args.combined is None else bool(args.combined)
        run_archive_chara(config, file_manager,
                          chara_paths=chara_paths, fmt=args.format,
                          auto_resolve=auto_resolve,
                          use_cache=use_cache,
                          include_modpack=include_modpack,
                          combined=combined,
                          mods_dir=args.mods_dir, coord_dir=args.coord_dir,
                          output_dir=args.output_dir)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("ArchiveChara")
        sys.exit(1)


def cmd_ungroup_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["UngroupChara"]["Enable"] = True
        delete_empty = None
        if args.delete_empty is True:
            delete_empty = True
        elif args.delete_empty is False:
            delete_empty = False
        run_ungroup_chara(config, file_manager,
                          input_path=args.input, delete_empty=delete_empty)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("UngroupChara")
        sys.exit(1)


def cmd_group_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["GroupChara"]["Enable"] = True

        if args.export:
            from tasks.group_chara import export
            from pathlib import Path
            folder = Path(args.input) if args.input else Path(config.group_chara["InputPath"])
            prompt = config.group_chara.get("Prompt", "")
            include_sub = getattr(args, 'include_subfolders', False) or config.group_chara.get('IncludeSubfolders', False)
            result = export(folder, include_subfolders=include_sub)
            if result:
                import json as _json
                json_start = result.find('{')
                json_only = result[json_start:] if json_start != -1 else result
                full = (prompt.rstrip() + "\n" + json_only) if prompt else result
                print(full)
        else:
            response = args.response
            if response and response.endswith(".json"):
                from pathlib import Path as _Path
                try:
                    response = _Path(response).read_text(encoding="utf-8")
                except Exception as e:
                    print(f"[ERROR] Could not read response file: {e}")
                    sys.exit(1)
            run_group_chara(config, file_manager,
                            input_path=args.input, response=response)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("GroupChara")
        sys.exit(1)


def cmd_filter_duplicates(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config, instance_index=args.instance)
        config.config_data["FilterDuplicates"]["Enable"] = True
        delete = None
        if args.delete is True:
            delete = True
        elif args.delete is False:
            delete = False
        fuzzy = None
        if args.fuzzy is True:
            fuzzy = True
        elif args.fuzzy is False:
            fuzzy = False
        run_filter_duplicates(config, file_manager, input_path=args.input,
                              delete=delete, fuzzy=fuzzy, keep=args.keep)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("FilterDuplicates")
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

    # install-chara
    p = sub.add_parser("install-chara", help="Copy cards / mods / overlays into the game")
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to scan (default: InstallChara.InputPath from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--extract-archive",    dest="extract_archive", action="store_true",  default=None,
                   help="Extract ZIP/RAR/7z archives before installing (overrides config)")
    g.add_argument("--no-extract-archive", dest="extract_archive", action="store_false",
                   help="Skip archive extraction (overrides config)")
    p.set_defaults(func=cmd_install_chara)

    # remove-chara
    p = sub.add_parser("remove-chara", help="Remove cards / mods from the game")
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to scan (default: UninstallChara.InputPath from config)")
    p.set_defaults(func=cmd_uninstall_chara)

    # fc-kks
    p = sub.add_parser("fc-kks", help="Filter and optionally convert KKS cards")
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to scan (default: FilterConvertKKS.InputPath from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--convert",    dest="convert", action="store_true",  default=None,
                   help="Enable KKS->KK conversion (overrides config)")
    g.add_argument("--no-convert", dest="convert", action="store_false",
                   help="Disable KKS->KK conversion (overrides config)")
    g2 = p.add_mutually_exclusive_group()
    g2.add_argument("--extract-archive",    dest="extract_archive", action="store_true",  default=None,
                    help="Extract ZIP/RAR/7z archives before filtering (overrides config)")
    g2.add_argument("--no-extract-archive", dest="extract_archive", action="store_false",
                    help="Skip archive extraction (overrides config)")
    p.set_defaults(func=cmd_fc_kks)

    # download-chara
    p = sub.add_parser(
        "download-chara",
        help="Download character cards from db.bepis.moe or koikatsucards.com",
    )
    p.add_argument("--links", default=None, metavar="URLS_OR_FILE",
                   help="Newline-separated URLs, or path to a .txt file containing them "
                        "(default: DownloadChara.Links from config)")
    p.add_argument("--output-dir", default=None, metavar="DIR",
                   help="Directory to save downloaded cards (default: DownloadChara.OutputDir from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--skip-downloaded",    dest="skip_downloaded", action="store_true",  default=None,
                   help="Skip already-downloaded URLs (overrides config)")
    g.add_argument("--no-skip-downloaded", dest="skip_downloaded", action="store_false",
                   help="Re-download even if previously downloaded (overrides config)")
    p.set_defaults(func=cmd_download_chara)

    # delete-chara
    p = sub.add_parser(
        "delete-chara",
        help="Send character cards and their associated mods/coords to the recycle bin",
    )
    p.add_argument("chara", nargs="*", metavar="CHARA",
                   help="Character PNG paths (default: DeleteChara.CharaPaths from config)")
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
    p.add_argument("--mods-dir",  default=None, metavar="DIR",
                   help="Mods directory (only used when --no-auto-resolve)")
    p.add_argument("--coord-dir", default=None, metavar="DIR",
                   help="Coordinate directory (only used when --no-auto-resolve)")
    p.set_defaults(func=cmd_delete_chara)

    # archive-chara
    p = sub.add_parser(
        "archive-chara",
        help="Bundle character cards with their zipmods and matching coordinates",
    )
    p.add_argument("chara", nargs="*", metavar="CHARA",
                   help="Character PNG paths (default: ArchiveChara.CharaPaths from config)")
    p.add_argument("--format", choices=["7z", "zip"], default=None,
                   help="Archive format (default: ArchiveChara.Format from config)")
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
    p.add_argument("--mods-dir",   default=None, metavar="DIR",
                   help="Mods directory override (only used when --no-auto-resolve)")
    p.add_argument("--coord-dir",  default=None, metavar="DIR",
                   help="Coordinate directory override")
    p.add_argument("--output-dir", default=None, metavar="DIR",
                   help="Output directory (default: same folder as chara card)")
    p.set_defaults(func=cmd_archive_chara)

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

    # group-chara
    p = sub.add_parser(
        "group-chara",
        help="Move character cards into series subfolders using the LLM JSON response",
        description=(
            "Step 1: run with --export to scan the input folder and print the prompt+JSON to stdout. "
            "Paste that into your LLM, copy the response, save it, then run without --export to move files."
        ),
    )
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder containing character PNGs (default: GroupChara.InputPath from config)")
    p.add_argument("--response", metavar="JSON_FILE_OR_STRING", default=None,
                   help="the LLM JSON response as a string or path to a .json file "
                        "(default: GroupChara.Response from config)")
    p.add_argument("--export", action="store_true", default=False,
                   help="Scan folder and print prompt+JSON to stdout instead of moving files")
    p.add_argument("--include-subfolders", action="store_true", default=False,
                   help="Include character cards from subfolders when exporting (overrides config)")
    p.set_defaults(func=cmd_group_chara)

    # filter-duplicates
    p = sub.add_parser(
        "filter-duplicates",
        help="Find and handle duplicate PNG cards and zipmod files",
        description=(
            "Scans the input folder recursively for duplicate PNG cards and zipmod files. "
            "Duplicates are identified by content (not filename). "
            "By default they are moved to a _duplicates_/ subfolder. "
            "With --delete they are sent to the recycle bin."
        ),
    )
    p.add_argument("--input", "-i", metavar="DIR", default=None,
                   help="Folder to scan (default: FilterDuplicates.InputPath from config)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--fuzzy",    dest="fuzzy", action="store_true",  default=None,
                   help="Enable fuzzy matching for chara cards (overrides config)")
    g.add_argument("--no-fuzzy", dest="fuzzy", action="store_false",
                   help="Disable fuzzy matching (overrides config)")
    p.add_argument("--keep", metavar="STRATEGY", default=None,
                   choices=['None — move all copies', 'Newest', 'Oldest', 'Biggest file size', 'Smallest file size', 'Last alphabetically', 'First alphabetically'],
                   help="Which copy to keep as the original (overrides config)")
    g2 = p.add_mutually_exclusive_group()
    g2.add_argument("--delete",    dest="delete", action="store_true",  default=None,
                    help="Send duplicates to recycle bin (overrides config)")
    g2.add_argument("--no-delete", dest="delete", action="store_false",
                    help="Move duplicates to _duplicates_/ folder (overrides config)")
    p.set_defaults(func=cmd_filter_duplicates)

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
        parser = build_parser()
        args = parser.parse_args()
        args.func(args)

except SystemExit:
    raise

except Exception:
    print("[ERROR] CLI initialisation error. See traceback.log for details.")
    with open("traceback.log", "w") as f:
        f.write("CLI Initialisation Error\n")
        traceback.print_exc(None, f, True)
        f.write("\n")
    sys.exit(1)
