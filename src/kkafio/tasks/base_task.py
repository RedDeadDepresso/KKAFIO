# coding:utf-8
"""
tasks/base_task.py — Minimal base class for all KKAFIO tasks.

Provides only what is genuinely shared across every task:
  - constructor storing config and file_manager
  - two logging helpers so task modules don't repeat logger.line() boilerplate
"""

import os
import subprocess
import sys
from pathlib import Path

from kkafio.core.errors import InputError
from kkafio.core.logger import logger

# Default folder offered by interface.json for the "downloads" InputPath
# options (Filter & Convert KKS, Filter Duplicate Contents, Install Contents,
# Uninstall Contents). Kept here so validate_input_path can auto-create it on
# first run instead of erroring — must match the "default" value of the
# "DownloadsInputPath" option in interface.json.
DEFAULT_DOWNLOADS_PATH = Path("C:/KKAFIO/Downloads")

# Fallback for the "LlmWebsiteUrl" option (Group / Rename Characters) when it
# is blank — must match the option's "default" in interface.json.
DEFAULT_LLM_WEBSITE_URL = "https://claude.ai/"


def open_in_default_app(tag: str, path: Path) -> None:
    """Open `path` with the system's default program (best effort; a failure
    is only logged under `tag`). Used to show the reports tasks write."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))                          # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        logger.warning(tag, f"Could not open report: {e}")


def copy_to_clipboard(tag: str, text: str) -> bool:
    """Put `text` on the system clipboard (best effort; a failure is only logged
    under `tag`). Returns whether it worked.

    Uses the platform's own clipboard tool where there is one, because the text
    then stays on the clipboard after this process exits (a Tk clipboard does not
    on most platforms); Tk is the fallback."""
    attempts: list[tuple[list[str], bytes]] = []
    if sys.platform == "win32":
        # clip.exe reads UTF-16 when it starts with a BOM, which the "utf-16" codec adds.
        attempts.append((["clip"], text.encode("utf-16")))
    elif sys.platform == "darwin":
        attempts.append((["pbcopy"], text.encode("utf-8")))
    else:
        data = text.encode("utf-8")
        attempts += [(["wl-copy"], data),
                     (["xclip", "-selection", "clipboard"], data),
                     (["xsel", "--clipboard", "--input"], data)]

    for cmd, data in attempts:
        try:
            subprocess.run(cmd, input=data, check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue

    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()                    # make Tk render the data before it goes away
        root.destroy()
        return True
    except Exception as e:
        logger.warning(tag, f"Could not copy to the clipboard: {e}")
        return False


def handle_failed_downloads(tag: str, report_path: Path | None, actions: list[str],
                            failed_items: list[str]) -> None:
    """The "If Downloads Fail" option: after a run with failures, do what `actions`
    (any of "OpenReport", "CopyToClipboard") asks. "OpenReport" opens the report
    at `report_path`; "CopyToClipboard" copies `failed_items` (the failed links or
    mod GUIDs, one per line). Best effort — a problem is only logged."""
    if "CopyToClipboard" in actions and failed_items:
        if copy_to_clipboard(tag, "\n".join(failed_items)):
            logger.info(tag, f"Copied {len(failed_items)} failed item(s) to the clipboard")
    if "OpenReport" in actions:
        if report_path is None:
            logger.warning(tag, "The report could not be written — nothing to open")
        else:
            logger.info(tag, "Some downloads failed — opening the report")
            open_in_default_app(tag, report_path)


def validate_input_path(tag: str, folder_path: Path, default_path: Path | None = None) -> None:
    """Raise `InputError` (logged by the CLI under `tag`) if folder_path is unset
    or doesn't exist.

    Every task validates its InputPath the same way before doing anything
    else; this used to be duplicated almost verbatim across ~10 call sites
    (both BaseTask subclasses and the module-level task functions in
    group_chara.py / rename_chara.py).

    If `default_path` is given and folder_path is exactly that default (i.e.
    the user hasn't pointed it elsewhere) but doesn't exist yet, it's created
    automatically instead of raising — a missing default folder on first run
    shouldn't be treated the same as a missing folder the user chose
    themselves, which is more likely to be a typo worth surfacing.
    """
    if not str(folder_path).strip() or str(folder_path) == ".":
        raise InputError("InputPath is not set. Configure it in MXU.", tag=tag)
    if not folder_path.exists():
        if default_path is not None and Path(folder_path) == Path(default_path):
            logger.info(tag, f"InputPath does not exist yet, creating default folder: {folder_path}")
            folder_path.mkdir(parents=True, exist_ok=True)
            return
        raise InputError(f"InputPath does not exist: {folder_path}", tag=tag)


def resolve_chara_dirs(game_path: dict, custom: str, tag: str) -> list[Path]:
    """The chara folder(s) a task works on.

    Blank `custom` -> the game's female and male folders. The game only reads
    cards that sit inside those two folders, so the game's chara folder
    (`UserData/chara`) itself is treated as shorthand for the pair: tasks
    work inside female/ and male/ and never place anything directly in
    `UserData/chara`. Any other folder is used as-is.

    Raises `InputError` if a custom folder is set but doesn't exist.
    """
    female, male = Path(game_path["charaFemale"]), Path(game_path["charaMale"])
    if not custom or not custom.strip():
        return [female, male]
    folder = Path(custom)
    if not folder.exists():
        raise InputError(f"Custom chara directory does not exist: {folder}", tag=tag)
    if os.path.normcase(os.path.realpath(folder)) == os.path.normcase(os.path.realpath(female.parent)):
        return [female, male]
    return [folder]


class BaseTask:
    def __init__(self, config, file_manager):
        self.config       = config
        self.file_manager = file_manager

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def log_start(self, tag: str, path: str | None = None) -> None:
        """Print a separator line and an opening info message."""
        logger.line()
        if path:
            logger.info(tag, str(path))

    def log_done(self, tag: str, moved: int = 0, skipped: int = 0,
                 extra: str | None = None) -> None:
        """Print a separator line and a success summary."""
        logger.line()
        parts = []
        if moved:
            parts.append(f"moved: {moved}")
        if skipped:
            parts.append(f"skipped: {skipped}")
        msg = "Done"
        if parts:
            msg += " - " + ", ".join(parts)
        if extra:
            msg += f" ({extra})"
        logger.success(tag, msg)
