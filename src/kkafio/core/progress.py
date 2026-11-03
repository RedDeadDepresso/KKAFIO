# coding:utf-8
"""
core/progress.py — Progress reporting for downloads, independent of any UI.

The download code (content sources, mod sources, Telegram) reports what it is
doing through the module-level functions below. While no progress window is
open they do nothing, so they are free to call everywhere (CLI runs without a
display, tests, ...). The CustomTkinter window lives in
``dialogs/progress_dialog.py``; it installs a `ProgressTracker` here and
redraws from `ProgressTracker.snapshot()`.

Usage in a downloader::

    handle = progress.begin(filename, total_bytes)   # a row in the window
    handle.update(received_bytes)                    # as chunks arrive
    handle.close(ok=True)                            # row goes away (+1 downloaded)

    progress.count("skipped")                        # already have it
    progress.count("failed")                         # gave up on it

``close()`` without ``ok=True`` only removes the row (a retry, a cancelled or
filtered transfer): it never counts anything, so a failure is counted once,
where the downloader decides it is final. ``close()`` is idempotent, so it can
sit in a ``finally``.

Everything is guarded by a lock: Telegram's progress callbacks may arrive from
another thread than the one that draws the window.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RowSnapshot:
    id: int
    name: str
    received: int
    total: int | None


@dataclass(frozen=True)
class Snapshot:
    rows: list[RowSnapshot]
    ok: int
    skipped: int
    failed: int
    items_done: int
    items_total: int | None
    total_bytes: int            # bytes received so far by every transfer of the run
    current: str | None         # what is being worked on right now (a link, a mod GUID)
    cancelled: bool


@dataclass
class _Row:
    id: int
    name: str
    received: int = 0
    total: int | None = None
    started: float = field(default_factory=time.monotonic)


class TransferHandle:
    """One transfer (one row of the window). See the module docstring."""

    def __init__(self, tracker: "ProgressTracker", row: _Row):
        self._tracker = tracker
        self._row = row
        self._closed = False

    def update(self, received: int, total: int | None = None) -> None:
        if not self._closed:
            self._tracker._update(self._row, received, total)

    def close(self, ok: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        self._tracker._close(self._row, ok)


class _NullHandle:
    """What `begin` returns when no window is open."""

    def update(self, received: int, total: int | None = None) -> None:
        pass

    def close(self, ok: bool = False) -> None:
        pass


_NULL_HANDLE = _NullHandle()


class ProgressTracker:
    def __init__(self, items_total: int | None = None,
                 on_cancel: Callable[[], None] | None = None):
        self._lock = threading.Lock()
        self._rows: dict[int, _Row] = {}
        self._ids = itertools.count(1)
        self._counts = {"ok": 0, "skipped": 0, "failed": 0}
        self._items_done = 0
        self._items_total = items_total
        self._bytes = 0
        self._current: str | None = None
        self._cancelled = False
        self._on_cancel = on_cancel

    # -- reporting side ----------------------------------------------------

    def begin(self, name: str, total: int | None = None) -> TransferHandle:
        with self._lock:
            row = _Row(next(self._ids), name, 0, total if total and total > 0 else None)
            self._rows[row.id] = row
        return TransferHandle(self, row)

    def count(self, result: str) -> None:
        with self._lock:
            if result in self._counts:
                self._counts[result] += 1

    def item_done(self, n: int = 1) -> None:
        with self._lock:
            self._items_done += n

    def set_current(self, label: str | None) -> None:
        with self._lock:
            self._current = label

    def _update(self, row: _Row, received: int, total: int | None) -> None:
        with self._lock:
            if received > row.received:
                self._bytes += received - row.received
            row.received = received
            if total and total > 0:
                row.total = total

    def _close(self, row: _Row, ok: bool) -> None:
        with self._lock:
            self._rows.pop(row.id, None)
            if ok:
                self._counts["ok"] += 1

    # -- window side -------------------------------------------------------

    def request_cancel(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            callback = self._on_cancel
        if callback is not None:
            callback()

    def snapshot(self) -> Snapshot:
        with self._lock:
            rows = [RowSnapshot(r.id, r.name, r.received, r.total)
                    for r in sorted(self._rows.values(), key=lambda r: r.id)]
            return Snapshot(
                rows=rows, ok=self._counts["ok"], skipped=self._counts["skipped"],
                failed=self._counts["failed"], items_done=self._items_done,
                items_total=self._items_total, total_bytes=self._bytes,
                current=self._current, cancelled=self._cancelled)


# ---------------------------------------------------------------------------
# Module-level API (what the downloaders call)
# ---------------------------------------------------------------------------

_active: ProgressTracker | None = None


def install(tracker: ProgressTracker) -> None:
    global _active
    _active = tracker


def uninstall() -> None:
    global _active
    _active = None


def begin(name: str, total: int | None = None) -> TransferHandle | _NullHandle:
    tracker = _active
    return tracker.begin(name, total) if tracker is not None else _NULL_HANDLE


def count(result: str) -> None:
    """Count a finished file: "ok", "skipped" or "failed"."""
    if _active is not None:
        _active.count(result)


def item_done(n: int = 1) -> None:
    """One more unit of the overall bar (a link, a mod) is finished."""
    if _active is not None:
        _active.item_done(n)


def set_current(label: str | None) -> None:
    if _active is not None:
        _active.set_current(label)


def content_length(response) -> int | None:
    """The Content-Length of an httpx response, or None if absent / unusable."""
    try:
        value = response.headers.get("content-length", "")
        return int(value) if str(value).isdigit() else None
    except Exception:
        return None
