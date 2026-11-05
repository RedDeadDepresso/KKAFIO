"""
mods_sidebar — The collapsible "Mod dependencies" sidebar on the right of the library window.

It lists the mods the card under the keyboard cursor uses, one row per GUID:

    ✔ guid.of.an.installed.mod                 ↗   reveals the zipmod in the file manager
    ✖ guid.of.a.missing.mod                    🔍  searches the GUID on Google in the browser

Missing mods come first. The header counts installed and missing mods. The arrow strip on the
sidebar's left edge collapses it to nothing but that strip; while it is collapsed no card is read.

What the mods are comes from mods_panel.py (no GUI there); this module only draws. The list is
one plain Tk canvas that draws the rows in view (the icons are canvas shapes, not emoji, so they
look the same everywhere), and the card is read on a worker thread: moving the cursor with the
arrow keys must not wait for a big Studio scene to be parsed.
"""

from __future__ import annotations

import queue
import webbrowser
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.dialogs.library.window import ellipsize
from kkafio.dialogs.library.mods_panel import (
    CardMods, ModRow, google_search_url, reveal_in_file_manager, summarize,
)

_OK = "#3fb950"
_MISSING = "#e5484d"
_UNKNOWN = "#8a92a3"
_READ_DELAY_MS = 120          # the cursor rests this long on a card before it is read


class ModsSidebar:
    """`frame` is what the window grids; everything else is driven through `show()` and
    `set_open()`. `on_toggle(open)` tells the window when the user collapsed or expanded it."""

    def __init__(self, root, parent, cards: CardMods, pal: dict, pair: Callable[[str], tuple[str, str]],
                 scale: float, open_: bool = True, on_toggle: Callable[[bool], None] | None = None):
        import tkinter as tk
        from tkinter import font as tkfont
        import customtkinter as ctk

        self._tk = tk
        self._root = root
        self._cards = cards
        self._pal = pal
        self._scale = scale
        self._on_toggle = on_toggle
        self._open = open_

        self._path: Path | None = None
        self._parse: Callable[[Path], Sequence[str]] | None = None
        self._load_index: Callable | None = None       # the tab's LibraryTab.guid_index
        self._index_asked: set = set()                 # the readers already started
        self._rows: list[ModRow] = []
        self._message = t("dialog.library.mods_select")
        self._top = 0                                      # pixels scrolled
        self._hover_icon = False
        self._token = 0                                    # a result for an older card is ignored
        self._timer: str | None = None
        self._poll: str | None = None
        self._results: queue.Queue = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kkafio-mods")
        self._index_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kkafio-modsindex")

        px = self._px
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        self._f_guid = tkfont.Font(root=root, family=family, size=11)
        self._f_note = tkfont.Font(root=root, family=family, size=11)
        self._row_h = px(44)
        self._pad = px(12)
        self._icon_zone = px(34)

        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        # the mod GUIDs use the same font size as the buttons of the series sidebar (CustomTkinter's default)
        scaling = ctk.ScalingTracker.get_widget_scaling(self.frame)
        self._f_guid.configure(size=-max(1, round(abs(ctk.CTkFont().cget("size")) * scaling)))
        self.frame.grid_rowconfigure(0, weight=1)

        self._toggle = ctk.CTkButton(self.frame, text="", width=22, corner_radius=6, fg_color=pair("side"),
                                     text_color=pair("text"), hover_color=pair("sel"), command=self._flip,
                                     font=ctk.CTkFont(size=20, weight="bold"))
        self._toggle.grid(row=0, column=0, sticky="ns", padx=(0, 6))

        self._panel = ctk.CTkFrame(self.frame, fg_color=pair("side"), corner_radius=8, width=340)
        self._panel.grid_propagate(False)
        self._panel.grid_columnconfigure(0, weight=1)
        self._panel.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(self._panel, text=t("dialog.library.mods_title"), anchor="w", text_color=pair("text"),
                     font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, columnspan=2, padx=14,
                                                                    pady=(12, 0), sticky="ew")
        self._summary = ctk.CTkLabel(self._panel, text="", anchor="w", text_color=pair("muted"),
                                     font=ctk.CTkFont(size=12))
        self._summary.grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 6), sticky="ew")

        self._canvas = tk.Canvas(self._panel, bg=pal["side"], highlightthickness=0, borderwidth=0,
                                 width=px(300), height=px(200))
        self._canvas.grid(row=2, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        self._sb = ctk.CTkScrollbar(self._panel, command=self._yview)
        self._sb.grid(row=2, column=1, sticky="ns", padx=(2, 4), pady=(0, 8))

        self._canvas.bind("<Configure>", lambda _e: self._draw())
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", lambda _e: self._canvas.configure(cursor=""))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind(seq, self._on_wheel)

        self._apply_open()
        self._poll = root.after(60, self._poll_results)

    # ------------------------------------------------------------------
    # What the window calls
    # ------------------------------------------------------------------

    def show(self, path: Path | None, parse: Callable[[Path], Sequence[str]] | None,
             load_index: Callable | None = None) -> None:
        """Show the mods of the card `path` (None: no card), parsed with `parse`. `load_index` is
        the tab's GUID index reader: once the sidebar is open it is read in the background (once)
        so cards it lists are looked up instead of parsed; until then a card is parsed as usual."""
        self._path, self._parse, self._load_index = path, parse, load_index
        self._token += 1
        self._cancel_timer()
        if path is None or parse is None:
            self._set_message(t("dialog.library.mods_select"))
            return
        if not self._open:
            return                                         # read when it is expanded
        self._set_message(t("dialog.library.mods_reading"))
        self._start_index_read()
        token = self._token
        self._timer = self._root.after(_READ_DELAY_MS, lambda: self._submit(token, path, parse))

    def set_open(self, open_: bool) -> None:
        """Expand or collapse (the state the window remembered)."""
        if open_ != self._open:
            self._open = open_
            self._apply_open()
            if open_:
                self.show(self._path, self._parse, self._load_index)

    @property
    def is_open(self) -> bool:
        return self._open

    def dispose(self) -> None:
        self._cancel_timer()
        if self._poll is not None:
            try:
                self._root.after_cancel(self._poll)
            except self._tk.TclError:
                pass
            self._poll = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._index_executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Reading a card (worker thread -> queue -> Tk thread)
    # ------------------------------------------------------------------

    def _px(self, n: float) -> int:
        return max(1, int(round(n * self._scale)))

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            try:
                self._root.after_cancel(self._timer)
            except self._tk.TclError:
                pass
            self._timer = None

    def _start_index_read(self) -> None:
        """Read the tab's GUID index on its own thread (CardMods reads it once, so this is a no-op
        when the filter scan or an earlier call already has); the card the cursor is on doesn't
        wait for it."""
        load = self._load_index
        if load is None or load in self._index_asked:
            return
        self._index_asked.add(load)
        try:
            self._index_executor.submit(self._cards.index_for, load)
        except RuntimeError:                               # the window is closing
            pass

    def _submit(self, token: int, path: Path, parse) -> None:
        self._timer = None

        def work() -> None:
            try:
                self._results.put((token, self._cards.rows(path, parse), None))
            except Exception as e:                         # an unreadable card must not kill the window
                logger.warning("LIBRARY", f"Could not read the mods of {path.name}: {e}")
                self._results.put((token, None, e))

        try:
            self._executor.submit(work)
        except RuntimeError:                               # the window is closing
            pass

    def _poll_results(self) -> None:
        latest = None
        while True:
            try:
                latest = self._results.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            token, rows, error = latest
            if token == self._token:                       # still the card the cursor is on
                if error is not None:
                    self._set_message(t("dialog.library.mods_failed"))
                elif not rows:
                    self._set_message(t("dialog.library.mods_none"))
                else:
                    self._rows, self._message, self._top = rows, "", 0
                    self._summary.configure(text=self._summary_text(rows))
                    self._draw()
        self._poll = self._root.after(60, self._poll_results)

    def _summary_text(self, rows: Sequence[ModRow]) -> str:
        if not self._cards.lookup.available:
            return t("dialog.library.mods_summary_unknown", count=len(rows))
        installed, missing = summarize(rows)
        return t("dialog.library.mods_summary", installed=installed, missing=missing)

    def _set_message(self, message: str) -> None:
        self._rows, self._message, self._top = [], message, 0
        self._summary.configure(text="")
        self._draw()

    # ------------------------------------------------------------------
    # Collapsing
    # ------------------------------------------------------------------

    def _flip(self) -> None:
        self.set_open(not self._open)
        if self._on_toggle is not None:
            self._on_toggle(self._open)

    def _apply_open(self) -> None:
        if self._open:
            self._panel.grid(row=0, column=1, sticky="ns")
            self._toggle.configure(text="›")          # collapse: points to where the panel goes
        else:
            self._panel.grid_remove()
            self._toggle.configure(text="‹")          # expand: points back into the window

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _max_top(self) -> int:
        return max(0, len(self._rows) * self._row_h - self._canvas.winfo_height())

    def _draw(self) -> None:
        c = self._canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
        pal = self._pal
        if not self._rows:
            if self._message:
                c.create_text(w // 2, self._px(36), text=self._message, fill=pal["muted"], font=self._f_note,
                              width=max(w - 2 * self._pad, 40), justify="center")
            self._sb.set(0, 1)
            return
        self._top = min(max(self._top, 0), self._max_top())
        rh = self._row_h
        first = self._top // rh
        last = min(len(self._rows), (self._top + h) // rh + 1)
        known = self._cards.lookup.available
        text_x = self._pad + self._px(34)
        max_text = w - text_x - self._icon_zone - self._pad
        for i in range(first, last):
            row = self._rows[i]
            y0 = i * rh - self._top
            cy = y0 + rh // 2
            if not known:
                self._status_dot(self._pad + self._px(10), cy, _UNKNOWN, None)
            elif row.path is not None:
                self._status_dot(self._pad + self._px(10), cy, _OK, "check")
            else:
                self._status_dot(self._pad + self._px(10), cy, _MISSING, "cross")
            c.create_text(text_x, cy, anchor="w", fill=pal["text"], font=self._f_guid,
                          text=ellipsize(self._f_guid.measure, row.guid, max_text))
            icon_x = w - self._pad - self._px(8)
            if row.path is not None:
                self._icon_open(icon_x, cy, pal["muted"])
            elif known:
                self._icon_search(icon_x, cy, pal["muted"])
            c.create_line(0, y0 + rh - 1, w, y0 + rh - 1, fill=pal["border"])
        total = len(self._rows) * rh
        self._sb.set(self._top / total, min(1.0, (self._top + h) / total))

    def _status_dot(self, cx: int, cy: int, color: str, glyph: str | None) -> None:
        c, r = self._canvas, self._px(10)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")
        k, wd = r * 0.45, self._px(2)
        if glyph == "check":
            c.create_line(cx - k, cy, cx - k * 0.25, cy + k * 0.8, cx + k, cy - k * 0.7, fill="white",
                          width=wd, capstyle="round", joinstyle="round")
        elif glyph == "cross":
            c.create_line(cx - k, cy - k, cx + k, cy + k, fill="white", width=wd, capstyle="round")
            c.create_line(cx - k, cy + k, cx + k, cy - k, fill="white", width=wd, capstyle="round")

    def _icon_search(self, cx: int, cy: int, color: str) -> None:
        c, px = self._canvas, self._px
        r = px(5)
        ox, oy = cx - px(2), cy - px(2)
        c.create_oval(ox - r, oy - r, ox + r, oy + r, outline=color, width=px(2))
        c.create_line(ox + r * 0.75, oy + r * 0.75, cx + px(7), cy + px(7), fill=color, width=px(2),
                      capstyle="round")

    def _icon_open(self, cx: int, cy: int, color: str) -> None:
        c, px = self._canvas, self._px
        wd = px(2)
        c.create_line(cx + px(1), cy - px(6), cx - px(6), cy - px(6), cx - px(6), cy + px(6),
                      cx + px(6), cy + px(6), cx + px(6), cy + px(1), fill=color, width=wd, joinstyle="round")
        c.create_line(cx - px(1), cy + px(1), cx + px(7), cy - px(7), fill=color, width=wd, capstyle="round")
        c.create_line(cx + px(2), cy - px(7), cx + px(7), cy - px(7), cx + px(7), cy - px(2), fill=color,
                      width=wd, joinstyle="round", capstyle="round")

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _row_at(self, x: int, y: int) -> tuple[ModRow, bool] | None:
        """(the row under the point, whether the point is on its icon), or None."""
        i = (y + self._top) // self._row_h
        if y < 0 or not 0 <= i < len(self._rows):
            return None
        on_icon = x >= self._canvas.winfo_width() - self._pad - self._icon_zone
        return self._rows[i], on_icon

    def _on_motion(self, event) -> None:
        hit = self._row_at(event.x, event.y)
        actionable = hit is not None and hit[1] and (hit[0].path is not None or self._cards.lookup.available)
        self._canvas.configure(cursor="hand2" if actionable else "")

    def _on_click(self, event) -> None:
        hit = self._row_at(event.x, event.y)
        if hit is None or not hit[1]:
            return
        row = hit[0]
        if row.path is not None:
            reveal_in_file_manager(row.path)
        elif self._cards.lookup.available:                 # missing: look the GUID up
            webbrowser.open(google_search_url(row.guid))

    def _on_wheel(self, event) -> None:
        if getattr(event, "num", 0) == 4:
            step = -1
        elif getattr(event, "num", 0) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self._scroll_to(self._top + step * self._row_h)

    def _scroll_to(self, top: int) -> None:
        top = min(max(top, 0), self._max_top())
        if top != self._top:
            self._top = top
            self._draw()

    def _yview(self, *args) -> None:                       # the scrollbar's command
        total = len(self._rows) * self._row_h
        if args[0] == "moveto":
            self._scroll_to(int(float(args[1]) * total))
        elif args[0] == "scroll":
            n = int(args[1])
            unit = self._canvas.winfo_height() if args[2] == "pages" else self._row_h
            self._scroll_to(self._top + n * unit)
