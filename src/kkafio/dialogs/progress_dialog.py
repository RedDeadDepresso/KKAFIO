"""
progress_dialog.py — A CustomTkinter window showing the progress of downloads.

Used by Download Missing Mods and Download Contents::

    async with download_progress(t("dialog.progress.mods_title"), "mods", total_items=12):
        await ...      # the downloaders report through kkafio.core.progress

Unlike the other dialogs this one does not block: Tk is driven from the
asyncio loop the downloads run in (a small task calls ``update()`` ten times a
second), so the window stays on the main thread — where Tk has to be — while
the downloads proceed, and the password / login prompts the downloaders can
open mid-run still work (they run their own nested loop on the same thread).

* Cancel (or the window's close button) cancels the task that is inside the
  ``async with``; the ``.part`` files the downloaders leave behind are removed by
  their own cleanup. The caller sees an ``asyncio.CancelledError`` come out.
* The window closes by itself when the run ends, failures or not (the log and the
  report list what failed). `hold_on_failure=True` keeps it open on the final totals
  until the user closes it when something failed.
* Where no window can be made (no display, tests, ``KKAFIO_PROGRESS_DIALOG=0``)
  the context manager does nothing and the downloads run as before.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Literal

from kkafio.core import progress
from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.dialogs.common import _center_on_screen, _get_host, format_size, new_window

_REFRESH_SECONDS = 0.1
_WIDTH = 560
_NAME_MAX_CHARS = 64
# CustomTkinter finishes its Windows window setup (title bar colour, icon) ~200 ms
# after a window is created; setting the icon on a window that is already visible
# makes Windows blank and redraw it. So the window stays hidden until that is done.
_SHOW_DELAY_SECONDS = 0.4
_RATE_SMOOTHING = 0.3          # weight of the newest speed sample


def _enabled() -> bool:
    override = os.environ.get("KKAFIO_PROGRESS_DIALOG", "").strip().lower()
    if override in ("0", "false", "no", "off"):
        return False
    if override in ("1", "true", "yes", "on"):
        return True
    return "PYTEST_CURRENT_TEST" not in os.environ


def _shorten(text: str, limit: int = _NAME_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    keep = limit - 1
    head = keep // 2 + keep % 2
    return text[:head] + "…" + text[len(text) - (keep - head):]


class _Row:
    """The widgets of one transfer."""

    def __init__(self, parent, name: str):
        import customtkinter as ctk

        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.grid_columnconfigure(0, weight=1)
        self.name = ctk.CTkLabel(self.frame, text=_shorten(name), anchor="w")
        self.name.grid(row=0, column=0, sticky="ew")
        self.info = ctk.CTkLabel(self.frame, text="", anchor="e")
        self.info.grid(row=0, column=1, padx=(8, 0))
        self.bar = ctk.CTkProgressBar(self.frame, height=8)
        self.bar.set(0)
        self.bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        self.frame.pack(fill="x", padx=4)
        self._shown_info = ""
        self._shown_fraction = -1.0

    def show(self, received: int, total: int | None) -> None:
        info = f"{format_size(received)} / {format_size(total)}" if total else format_size(received)
        if info != self._shown_info:
            self._shown_info = info
            self.info.configure(text=info)
        fraction = min(received / total, 1.0) if total else 0.0
        if abs(fraction - self._shown_fraction) >= 0.005:
            self._shown_fraction = fraction
            self.bar.set(fraction)

    def destroy(self) -> None:
        self.frame.destroy()


class _ProgressWindow:
    def __init__(self, tracker: progress.ProgressTracker, title: str, unit: Literal["mods", "links"]):
        import customtkinter as ctk

        self._tracker = tracker
        self._unit = unit
        self._rows: dict[int, _Row] = {}
        self._shown: dict[str, str] = {}      # label name -> text currently displayed
        self._last_bytes = 0
        self._last_time = time.monotonic()
        self._rate = 0.0
        self.finished = False
        self.closed = False
        self.alive = True
        self._reported = False

        # Not topmost: the password / login prompts the downloads can open are, and
        # they must end up above this window.
        self.win = new_window(title, topmost=False)
        win = self.win
        win.resizable(False, False)
        win.grid_columnconfigure(0, weight=1)

        self.status = ctk.CTkLabel(win, text=t("dialog.progress.waiting"), anchor="w",
                                   justify="left", wraplength=_WIDTH - 24)
        self.status.grid(row=0, column=0, padx=12, pady=(14, 6), sticky="ew")

        self.overall = ctk.CTkProgressBar(win, width=_WIDTH - 24)
        self.overall.set(0)
        self.overall.grid(row=1, column=0, padx=12, sticky="ew")

        summary = ctk.CTkFrame(win, fg_color="transparent")
        summary.grid(row=2, column=0, padx=12, pady=(4, 8), sticky="ew")
        summary.grid_columnconfigure(1, weight=1)
        self.items = ctk.CTkLabel(summary, text="", anchor="w")
        self.items.grid(row=0, column=0, sticky="w")
        self.counts = ctk.CTkLabel(summary, text="", anchor="e")
        self.counts.grid(row=0, column=1, sticky="e")

        self.list = ctk.CTkScrollableFrame(win, width=_WIDTH - 48, height=200)
        self.list.grid(row=3, column=0, padx=12, sticky="ew")

        self.button = ctk.CTkButton(win, text=t("dialog.cancel"), width=90, command=self._on_button)
        self.button.grid(row=4, column=0, padx=12, pady=12, sticky="e")
        win.protocol("WM_DELETE_WINDOW", self._on_button)

        self._draw(self._tracker.snapshot())
        self._created = time.monotonic()
        self._visible = False       # shown by refresh() once CustomTkinter's own setup is done

    # -- events ------------------------------------------------------------

    def _on_button(self) -> None:
        logger.info("PROGRESS", "Progress window button / close pressed"
                    + (" (finished)" if self.finished else " (cancelling)"))
        if self.finished:
            self.closed = True
            return
        if self._tracker.snapshot().cancelled:
            return
        self.button.configure(state="disabled")
        self._set_text(self.status, "status", t("dialog.progress.cancelling"))
        self._tracker.request_cancel()

    # -- drawing -----------------------------------------------------------

    def _set_text(self, label, key: str, text: str) -> None:
        if self._shown.get(key) != text:
            self._shown[key] = text
            label.configure(text=text)

    def refresh(self) -> None:
        """Redraw from the tracker and let Tk handle its pending events."""
        import tkinter as tk
        import traceback

        if not self.alive:
            return
        try:
            self._draw(self._tracker.snapshot())
        except tk.TclError as exc:
            logger.warning("PROGRESS", f"Progress window was lost while drawing: {exc}")
            self.alive = False            # the window is gone; downloads go on regardless
            self.closed = True
            return
        except Exception:
            # A drawing bug must not leave a blank window with no explanation.
            if not self._reported:
                self._reported = True
                logger.warning("PROGRESS", "Progress window could not be drawn:\n" + traceback.format_exc())
        try:
            self.win.update_idletasks()
            self.win.update()
            if not self._visible and time.monotonic() - self._created >= _SHOW_DELAY_SECONDS:
                self._visible = True
                _center_on_screen(self.win)
                self.win.deiconify()
                self.win.lift()
        except tk.TclError as exc:
            logger.warning("PROGRESS", f"Progress window was lost during update: {exc}")
            self.alive = False
            self.closed = True
        except Exception:
            if not self._reported:
                self._reported = True
                logger.warning("PROGRESS", "Progress window error:\n" + traceback.format_exc())

    def _draw(self, snap: progress.Snapshot) -> None:
        now = time.monotonic()
        if now - self._last_time >= 0.5:
            sample = (snap.total_bytes - self._last_bytes) / (now - self._last_time)
            self._rate = sample if self._rate == 0 else (
                _RATE_SMOOTHING * sample + (1 - _RATE_SMOOTHING) * self._rate)
            self._last_bytes, self._last_time = snap.total_bytes, now

        if not self.finished and not snap.cancelled:
            if snap.current:
                self._set_text(self.status, "status", t("dialog.progress.working_on", name=_shorten(snap.current, 90)))
            elif not snap.rows and snap.ok + snap.skipped + snap.failed == 0:
                self._set_text(self.status, "status", t("dialog.progress.waiting"))

        if snap.items_total:
            self.overall.set(min(snap.items_done / snap.items_total, 1.0))
            done, total = snap.items_done, snap.items_total
            text = (t("dialog.progress.items_mods", done=done, total=total) if self._unit == "mods"
                    else t("dialog.progress.items_links", done=done, total=total))
            self._set_text(self.items, "items", text)

        counts = t("dialog.progress.counts", ok=snap.ok, skipped=snap.skipped, failed=snap.failed)
        if snap.rows and self._rate >= 1:
            counts += f" · {format_size(int(self._rate))}/s"
        self._set_text(self.counts, "counts", counts)

        live = {row.id for row in snap.rows}
        for row_id in [i for i in self._rows if i not in live]:
            self._rows.pop(row_id).destroy()
        for row in snap.rows:
            widgets = self._rows.get(row.id)
            if widgets is None:
                widgets = self._rows[row.id] = _Row(self.list, row.name)
            widgets.show(row.received, row.total)

    # -- end of the run ----------------------------------------------------

    def show_finished(self) -> None:
        """Freeze on the final numbers and turn the button into Close."""
        snap = self._tracker.snapshot()
        self.finished = True
        self._draw(snap)
        if snap.items_total:
            self.overall.set(min(snap.items_done / snap.items_total, 1.0))
        text = (t("dialog.progress.finished_issues", failed=snap.failed) if snap.failed
                else t("dialog.progress.finished"))
        self._set_text(self.status, "status", text)
        self.button.configure(text=t("dialog.close"), state="normal")
        self.refresh()

    def destroy(self) -> None:
        import tkinter as tk

        self.alive = False
        try:
            self.win.destroy()
        except tk.TclError:
            pass


async def _pump(window: _ProgressWindow) -> None:
    while True:
        window.refresh()
        await asyncio.sleep(_REFRESH_SECONDS)


def _open_window(tracker: progress.ProgressTracker, title: str,
                 unit: Literal["mods", "links"]) -> _ProgressWindow | None:
    try:
        _get_host()                       # the hidden root; fails first if there is no display
        return _ProgressWindow(tracker, title, unit)
    except Exception as exc:
        import traceback
        logger.warning("PROGRESS", f"Progress window unavailable — continuing without it:\n{traceback.format_exc()}")
        return None


@asynccontextmanager
async def download_progress(
    title: str, unit: Literal["mods", "links"], total_items: int | None = None,
    hold_on_failure: bool = False,
):
    """Show the progress window while the body runs (see the module docstring).

    `unit` names what `total_items` counts, for the overall bar's caption.
    """
    if not _enabled():
        yield
        return

    task = asyncio.current_task()
    tracker = progress.ProgressTracker(
        items_total=total_items,
        on_cancel=(lambda: task.cancel()) if task is not None else None)
    window = _open_window(tracker, title, unit)
    if window is None:
        yield
        return

    progress.install(tracker)
    pump = asyncio.create_task(_pump(window))
    completed = False
    logger.info("PROGRESS", "Progress window opened")
    try:
        yield
        completed = True
    except BaseException as exc:
        logger.warning("PROGRESS", f"Progress window closing because the run ended with {type(exc).__name__}: {exc}")
        raise
    finally:
        if completed:
            logger.info("PROGRESS", "Progress window closing: the run finished")
        progress.uninstall()
        pump.cancel()
        try:
            if completed and hold_on_failure and tracker.snapshot().failed:
                # Something went wrong: leave the totals up until the user closes the window.
                window.show_finished()
                while not window.closed:
                    window.refresh()
                    await asyncio.sleep(_REFRESH_SECONDS)
        finally:
            window.destroy()
