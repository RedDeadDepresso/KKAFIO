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

Shell context menu (Windows Explorer right-click integration):
    kkafio_cli register      # generate + import .reg file (requires Admin)
    kkafio_cli unregister    # remove entries (requires Admin)

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
# Shell context menu — .reg file approach
#
# Writing registry keys from Python/winreg is unreliable for Explorer context
# menus because the shell caches and validates entries in ways that differ
# across Windows versions.  The proven approach used by tools like 7-Zip is a
# plain .reg file imported via regedit.  We generate that file dynamically so
# it always contains the correct absolute path, then invoke regedit /s to
# import it silently (requires Admin).
# ---------------------------------------------------------------------------

# Each tuple: (registry key name, visible label, CLI subcommand, extra args)
# Key names must be plain identifiers — no slashes or special chars.
# %1 = right-clicked folder  (Directory\shell)
# %V = open folder background (Directory\Background\shell) — substituted below
MENU_TASKS = [
    ("InstallChara", "Install Chara",        "install-chara", '--input "%1"'),
    ("RemoveChara",  "Remove Chara",          "remove-chara",  '--input "%1"'),
    ("FilterKKS",    "Filter / Convert KKS", "fc-kks",        '--input "%1"'),
    ("RunAll",       "Run All (from config)", "run",           ""),
]

REG_ROOTS = [
    r"Directory\shell\KKAFIO",
    r"Directory\Background\shell\KKAFIO",
]


def _reg_esc(path: str) -> str:
    """Escape backslashes in a path for use inside a .reg REG_SZ value."""
    return path.replace("\\", "\\\\")


def _cmd_prefix() -> str:
    """Return the command prefix already escaped for a .reg file value.

    Frozen  ->  \\"C:\\\\path\\\\kkafio_cli.exe\\"
    Source  ->  \\"C:\\\\path\\\\python.exe\\" \\"C:\\\\path\\\\kkafio_cli.py\\"
    """
    if getattr(sys, "frozen", False):
        return '\\"' + _reg_esc(_self_path()) + '\\"'
    else:
        python = _reg_esc(os.path.abspath(sys.executable))
        script = _reg_esc(_self_path())
        return '\\"' + python + '\\" \\"' + script + '\\"'


def _generate_reg(unregister: bool = False) -> str:
    """Return the full content of a .reg file for register or unregister."""
    lines = ["Windows Registry Editor Version 5.00", ""]

    if unregister:
        for root in REG_ROOTS:
            lines.append("[-HKEY_CLASSES_ROOT\\" + _reg_esc(root) + "]")
            lines.append("")
        return "\n".join(lines)

    prefix = _cmd_prefix()

    for root in REG_ROOTS:
        is_bg = "Background" in root
        reg_root = "HKEY_CLASSES_ROOT\\" + _reg_esc(root)

        # Parent key — label + cascade indicator
        lines.append("[" + reg_root + "]")
        lines.append('@="KKAFIO"')
        lines.append('"MUIVerb"="KKAFIO"')
        if getattr(sys, "frozen", False):
            lines.append('"Icon"="\\"' + _reg_esc(_self_path()) + '\\""')
        lines.append("")

        for key_name, label, subcmd, extra in MENU_TASKS:
            actual_extra = extra.replace("%1", "%V") if is_bg and "%1" in extra else extra
            full_cmd = (prefix + " " + subcmd + " " + actual_extra).strip()

            child = reg_root + "\\\\shell\\\\" + key_name
            lines.append("[" + child + "]")
            lines.append('@="' + label + '"')
            lines.append('"MUIVerb"="' + label + '"')
            lines.append("")
            lines.append("[" + child + "\\\\command]")
            lines.append('@="' + full_cmd + '"')
            lines.append("")

    return "\n".join(lines)


def _run_reg_file(reg_content: str, verb: str) -> None:
    """Write a temp .reg file and import it silently via regedit (requires Admin)."""
    import tempfile
    import subprocess

    # regedit requires UTF-16 LE with BOM for .reg files
    with tempfile.NamedTemporaryFile(mode="w", suffix=".reg",
                                     delete=False, encoding="utf-16") as f:
        f.write(reg_content)
        tmp_path = f.name

    try:
        result = subprocess.run(["regedit.exe", "/s", tmp_path], capture_output=True)
        if result.returncode != 0:
            print(f"[ERROR] regedit exited with code {result.returncode}.")
            print("        Make sure you are running as Administrator.")
            sys.exit(1)
        print(f"[OK] KKAFIO context menu {verb}.")
        if verb == "registered":
            print("     Right-click any folder in Explorer to see it.")
            print("     To remove:  kkafio_cli unregister")
    finally:
        os.unlink(tmp_path)


def cmd_register(args):
    _run_reg_file(_generate_reg(unregister=False), "registered")


def cmd_unregister(args):
    _run_reg_file(_generate_reg(unregister=True), "unregistered")


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

def run_install_chara(config, file_manager, input_path: str | None = None):
    from modules.install_chara import InstallChara
    from pathlib import Path
    module = InstallChara(config, file_manager)
    module.run(folder_path=Path(input_path) if input_path else None)


def run_remove_chara(config, file_manager, input_path: str | None = None):
    from modules.remove_chara import RemoveChara
    from pathlib import Path
    if input_path is not None:
        config.remove_chara["InputPath"] = Path(input_path)
    RemoveChara(config, file_manager).run()


def run_fc_kks(config, file_manager, input_path: str | None = None,
               convert: bool | None = None):
    from modules.fc_kks import FilterConvertKKS
    from pathlib import Path
    if input_path is not None:
        config.fc_kks["InputPath"] = Path(input_path)
    if convert is not None:
        config.fc_kks["Convert"] = convert
    FilterConvertKKS(config, file_manager).run()


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
    from util.logger import logger
    _clear_traceback()
    config, file_manager = _load_core(args.config)

    task_map = {
        "CreateBackup":     lambda: run_create_backup(config, file_manager),
        "FilterConvertKKS": lambda: run_fc_kks(config, file_manager),
        "InstallChara":     lambda: run_install_chara(config, file_manager),
        "RemoveChara":      lambda: run_remove_chara(config, file_manager),
    }

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
        run_install_chara(config, file_manager, input_path=args.input)
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
        run_fc_kks(config, file_manager, input_path=args.input, convert=convert)
    except SystemExit:
        raise
    except Exception:
        _write_traceback("FilterConvertKKS")
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
    p.set_defaults(func=cmd_fc_kks)

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

    # register
    p = sub.add_parser(
        "register",
        help="Add KKAFIO to the Explorer folder right-click menu (requires Admin)",
        description=(
            "Generates a .reg file with the correct absolute path to this executable\n"
            "(or Python interpreter when run from source) and imports it via regedit.\n\n"
            "Requires Administrator privileges.\n\n"
            "To test from source without building:\n"
            "  python kkafio_cli.py register"
        ),
    )
    p.set_defaults(func=cmd_register)

    # unregister
    p = sub.add_parser(
        "unregister",
        help="Remove KKAFIO from the Explorer folder right-click menu (requires Admin)",
    )
    p.set_defaults(func=cmd_unregister)

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
