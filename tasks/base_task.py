# coding:utf-8
"""
utils/base_task.py — Minimal base class for all KKAFIO task tasks.

Provides only what is genuinely shared across every task:
  - constructor storing config and file_manager
  - two logging helpers so task modules don't repeat logger.line() boilerplate
"""

from pathlib import Path

from utils.logger import logger

# Default folder offered by interface.json for the "downloads" InputPath
# options (Filter & Convert KKS, Filter Duplicate Contents, Install Contents,
# Uninstall Contents). Kept here so validate_input_path can auto-create it on
# first run instead of erroring — must match the "default" value of the
# "DownloadsInputPath" option in interface.json.
DEFAULT_DOWNLOADS_PATH = Path("C:/KKAFIO/Downloads")


def validate_input_path(tag: str, folder_path: Path, default_path: Path | None = None) -> None:
    """Raise if folder_path is unset or doesn't exist, logging via `tag` first.

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
        logger.error(tag, "InputPath is not set. Configure it in MXU.")
        raise Exception("InputPath is not set")
    if not folder_path.exists():
        if default_path is not None and Path(folder_path) == Path(default_path):
            logger.info(tag, f"InputPath does not exist yet, creating default folder: {folder_path}")
            folder_path.mkdir(parents=True, exist_ok=True)
            return
        logger.error(tag, f"InputPath does not exist: {folder_path}")
        raise Exception(f"InputPath does not exist: {folder_path}")


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
