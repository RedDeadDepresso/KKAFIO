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

Shell context menu:
    Run register_context_menu.bat as Administrator to add KKAFIO to the
    Explorer right-click menu.  Run unregister_context_menu.bat to remove it.

Global options:
    --config PATH   use a custom config.json instead of %APPDATA%/KKAFIO/config.json
"""

import os
import sys
import argparse
import traceback


# ---------------------------------------------------------------------------
# Resolve the executable path
# ---------------------------------------------------------------------------

def _self_path() -> str:
    """Return absolute path to this exe (frozen) or this .py file (source)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(__file__)



# ---------------------------------------------------------------------------
# Core loader
# ---------------------------------------------------------------------------

def _load_core(config_path: str | None = None):
    from util.config import Config
    from util.constants import CONFIG_PATH
    from util.file_manager import FileManager

    path = config_path if config_path else str(CONFIG_PATH)
    config = Config(path)
    file_manager = FileManager(config)
    return config, file_manager


def _write_traceback(task: str) -> None:
    with open("traceback.log", "a") as f:
        f.write(f"[{task}]\n")
        traceback.print_exc(None, f, True)
        f.write("\n")


def _clear_traceback() -> None:
    with open("traceback.log", "w"):
        pass


# ---------------------------------------------------------------------------
# Task runners
# ---------------------------------------------------------------------------

def run_install_chara(config, file_manager, input_path: str | None = None,
                      extract_archive: bool | None = None, skip_extract: bool = False):
    from modules.install_chara import InstallChara
    from pathlib import Path
    module = InstallChara(config, file_manager)
    if extract_archive is not None:
        module.extract_archive = extract_archive
    module.run(folder_path=Path(input_path) if input_path else None,
               skip_extract=skip_extract)


def run_remove_chara(config, file_manager, input_path: str | None = None):
    from modules.remove_chara import RemoveChara
    from pathlib import Path
    if input_path is not None:
        config.remove_chara["InputPath"] = Path(input_path)
    RemoveChara(config, file_manager).run()


def run_fc_kks(config, file_manager, input_path: str | None = None,
               convert: bool | None = None, extract_archive: bool | None = None):
    from modules.fc_kks import FilterConvertKKS
    from pathlib import Path
    if input_path is not None:
        config.fc_kks["InputPath"] = Path(input_path)
    if convert is not None:
        config.fc_kks["Convert"] = convert
    module = FilterConvertKKS(config, file_manager)
    if extract_archive is not None:
        module.extract_archive = extract_archive
    module.run()


def run_filter_duplicates(config, file_manager, input_path: str | None = None,
                         delete: bool | None = None, fuzzy: bool | None = None,
                         keep: str | None = None):
    from modules.filter_duplicates import FilterDuplicates
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
    from modules.create_backup import CreateBackup
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

def cmd_run(args):
    from pathlib import Path
    from util.logger import logger
    _clear_traceback()
    config, file_manager = _load_core(args.config)

    # If both FilterConvertKKS and InstallChara are enabled and share the same
    # input path, fc_kks will extract archives first so InstallChara must skip
    # extraction to avoid double-extracting the same archives.
    fc_cfg = config.config_data["FilterConvertKKS"]
    ic_cfg = config.config_data["InstallChara"]
    fc_enabled = fc_cfg.get("Enable", False)
    ic_enabled = ic_cfg.get("Enable", False)
    fc_extracts = fc_cfg.get("ExtractArchive", True)
    ic_extracts = ic_cfg.get("ExtractArchive", True)
    same_path = (
        fc_enabled and ic_enabled and fc_extracts and ic_extracts and
        "InputPath" in fc_cfg and "InputPath" in ic_cfg and
        Path(fc_cfg["InputPath"]) == Path(ic_cfg["InputPath"])
    )

    task_map = {
        "CreateBackup":      lambda: run_create_backup(config, file_manager),
        "FilterConvertKKS":  lambda: run_fc_kks(config, file_manager),
        "FilterDuplicates":  lambda: run_filter_duplicates(config, file_manager),
        "InstallChara":      lambda: run_install_chara(config, file_manager,
                                                       skip_extract=same_path),
        "RemoveChara":       lambda: run_remove_chara(config, file_manager),
    }

    if same_path:
        logger.info("CLI", "FilterConvertKKS and InstallChara share the same input path — "
                           "archive extraction will run in FilterConvertKKS only")

    for task, fn in task_map.items():
        if not config.config_data[task]["Enable"]:
            continue
        logger.info("CLI", f"Start Task: {task}")
        try:
            fn()
        except Exception:
            logger.error("CLI", f"Task error: {task}. See traceback.log for details.")
            _write_traceback(task)
            sys.exit(1)

    sys.exit(0)


def cmd_install_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config)
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


def cmd_remove_chara(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config)
        config.config_data["RemoveChara"]["Enable"] = True
        run_remove_chara(config, file_manager, input_path=args.input)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("RemoveChara")
        sys.exit(1)


def cmd_fc_kks(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config)
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


def cmd_filter_duplicates(args):
    _clear_traceback()
    try:
        config, file_manager = _load_core(args.config)
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
        config, file_manager = _load_core(args.config)
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
        help="Path to a config.json file (default: %%APPDATA%%/KKAFIO/config.json)",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # run
    p = sub.add_parser("run", help="Run all tasks enabled in config")
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
                   help="Folder to scan (default: RemoveChara.InputPath from config)")
    p.set_defaults(func=cmd_remove_chara)

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
    with open("traceback.log", "w"):
        pass
except Exception:
    pass

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
