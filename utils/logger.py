import logging
import sys

# Custom levels, slotted in around the stdlib ones so filtering still makes
# sense (e.g. logger.setLevel(logging.WARNING) still hides SUCCESS/INFO).
# stdlib: DEBUG=10 INFO=20 WARNING=30 ERROR=40 CRITICAL=50
SUCCESS = 25
SKIPPED = 31
REPLACED = 32
RENAMED = 33
REMOVED = 34

for _level_value, _level_name in (
    (SUCCESS, "SUCCESS"),
    (SKIPPED, "SKIPPED"),
    (REPLACED, "REPLACED"),
    (RENAMED, "RENAMED"),
    (REMOVED, "REMOVED"),
):
    logging.addLevelName(_level_value, _level_name)

COLORS = {
    "DEBUG": "\033[90m",     # grey
    "INFO": "\033[94m",      # blue
    "SUCCESS": "\033[92m",   # green
    "WARNING": "\033[93m",   # yellow/orange
    "SKIPPED": "\033[93m",   # yellow/orange
    "REPLACED": "\033[93m",  # yellow/orange
    "RENAMED": "\033[93m",   # yellow/orange
    "REMOVED": "\033[93m",   # yellow/orange
    "ERROR": "\033[91m",     # red
    "CRITICAL": "\033[91m",  # red
}
RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    """
    Formats records as `LEVEL    | category | message`, colored by level.

    Colors are only emitted when `use_color` is True. Callers should base
    that on whether the output stream is an actual terminal (`isatty()`) —
    when stdout is piped (e.g. to a GUI subprocess reader), there's no
    terminal to render ANSI codes, so skip generating them entirely rather
    than having the consumer strip them back out.
    """

    def __init__(self, use_color: bool):
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        category = getattr(record, "category", "")
        level_name = record.levelname.ljust(8)
        category = category.ljust(8)
        message = record.getMessage()
        if not self.use_color:
            return f"{level_name} | {category} | {message}"
        color = COLORS.get(record.levelname, "")
        return f"{color}{level_name} | {category} | {message}{RESET}"


class KafioLogger(logging.LoggerAdapter):
    """
    Thin wrapper around a stdlib Logger that:
      - requires a `category` (task tag) on every call, matching the old API
      - exposes the custom status levels as named methods
    """

    def __init__(self, logger: logging.Logger):
        super().__init__(logger, extra={})

    def _log_with_category(self, level: int, category: str, message) -> None:
        # Exceptions get logged as their string form, same as before.
        self.logger.log(level, str(message), extra={"category": category})

    def info(self, category: str, message: str) -> None:
        self._log_with_category(logging.INFO, category, message)

    def success(self, category: str, message) -> None:
        self._log_with_category(SUCCESS, category, message)

    def error(self, category: str, message) -> None:
        self._log_with_category(logging.ERROR, category, message)

    def warning(self, category: str, message: str) -> None:
        self._log_with_category(logging.WARNING, category, message)

    def skipped(self, category: str, message: str) -> None:
        self._log_with_category(SKIPPED, category, message)

    def replaced(self, category: str, message: str) -> None:
        self._log_with_category(REPLACED, category, message)

    def renamed(self, category: str, message: str) -> None:
        self._log_with_category(RENAMED, category, message)

    def removed(self, category: str, message: str) -> None:
        self._log_with_category(REMOVED, category, message)

    def line(self) -> None:
        print("--------------------------------------------------------------------", flush=True)

    def error_count_guard(self) -> "_ErrorCountGuard":
        """Context manager: counts ERROR-level records logged inside the
        `with` block (via this logger only) and exposes it as `.count`
        afterward. Used to gate destructive cleanup (e.g. deleting an
        extracted-archive temp folder) on "everything in it actually got
        processed without error" rather than assuming success just because
        no exception propagated — most per-file failures here are caught
        and only logged, not raised.
        """
        return _ErrorCountGuard(self.logger)


class _ErrorCountGuard(logging.Handler):
    def __init__(self, target_logger: logging.Logger):
        super().__init__(level=logging.ERROR)
        self._target = target_logger
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1

    def __enter__(self) -> "_ErrorCountGuard":
        self.count = 0
        self._target.addHandler(self)
        return self

    def __exit__(self, *exc) -> None:
        self._target.removeHandler(self)


def _build_logger() -> KafioLogger:
    # When running as script.exe stdout is block-buffered by default, which
    # delays output until the process exits. Force line-buffering so each
    # line is flushed immediately.
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    base_logger = logging.getLogger("KAFIO")
    base_logger.setLevel(logging.INFO)
    base_logger.propagate = False  # don't also emit via the root logger

    if not base_logger.handlers:  # avoid duplicate handlers if re-imported
        # Only color output when stdout is an actual terminal. When KKAFIO
        # is spawned as a subprocess (e.g. by the MXU GUI) stdout is a pipe,
        # isatty() is False, and there's no point emitting ANSI codes that
        # the consumer would just have to strip back out.
        use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(ColorFormatter(use_color=use_color))
        base_logger.addHandler(handler)

    return KafioLogger(base_logger)


logger = _build_logger()