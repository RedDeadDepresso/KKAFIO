# coding:utf-8
"""
core/errors.py — The failures KKAFIO raises on purpose.

    KKAFIOError
    ├── UserError            the user can fix this — reported as one clean error line
    │   ├── ConfigError      a setting is missing or wrong (game path, task folder, ...)
    │   ├── InputError       the task was given nothing usable to work on
    │   └── ToolNotFoundError  a program KKAFIO needs (7-Zip, KoiCardTexTool) isn't available
    └── TaskFailedError      the work was attempted and failed (7-Zip errored, an archive
                             couldn't be created, ...) — reported with a traceback, since
                             the cause may be an environment problem or a bug

How the CLI reports them (see ``kkafio.cli``):

  - a `UserError` is logged as a single ``ERROR | <tag> | <message>`` line and the process
    exits 1. No traceback file is written: the message already says what to change, and a
    traceback would only bury it.
  - anything else — including `TaskFailedError` and unexpected exceptions — is logged as
    ``Task error: <task>: <detail>`` with the full traceback saved to traceback.log.

Raise a `UserError` *instead of* logging the problem and then raising: the CLI does the
logging, so the line appears exactly once, under the tag you give.
"""

from __future__ import annotations


class KKAFIOError(Exception):
    """Base class for failures KKAFIO raises deliberately (as opposed to bugs)."""


class UserError(KKAFIOError):
    """Something the user can fix. `tag` is the log category its error line is printed under."""

    def __init__(self, message: str, *, tag: str = "CLI"):
        super().__init__(message)
        self.tag = tag


class ConfigError(UserError):
    """A setting is missing, malformed or points at something that doesn't exist."""


class InputError(UserError):
    """The task was given nothing usable to work on (no folder, no files, nothing selected)."""


class ToolNotFoundError(UserError):
    """A program KKAFIO depends on is not installed or could not be obtained."""


class TaskFailedError(KKAFIOError):
    """The work was attempted and failed."""
