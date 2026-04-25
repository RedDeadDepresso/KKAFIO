# coding:utf-8
"""
util/base_task.py — Minimal base class for all KKAFIO task tasks.

Provides only what is genuinely shared across every task:
  - constructor storing config and file_manager
  - two logging helpers so task modules don't repeat logger.line() boilerplate
"""

from util.logger import logger


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
            msg += " — " + ", ".join(parts)
        if extra:
            msg += f" ({extra})"
        logger.success(tag, msg)
