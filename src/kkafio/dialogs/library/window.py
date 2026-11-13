"""
window — The library window of Organize Library (scenes, characters, outfits).

This module is the window: it draws the tabs and knows nothing about characters, coordinates or
scenes. The submodules are the tabs' data and callbacks: characters.py (series folders),
coordinates.py (character folders), scenes.py (author folders) and common.py (what the three
share). Each scans its cards, suggests folder names, moves a card when a name is confirmed and
returns a `LibraryTab` for `library_dialog()`. Imports run one way: tasks/organize_library.py ->
dialogs/library/<tab> -> tasks/group_*; the Group tasks never import this package.

A browser-style window: a search bar on top, a sidebar (All characters, Unknown series and the series
folders that exist) and a grid of character covers. Under every cover is the character's name and
its series — "Unknown series" until it has one. Click that text to type a series (with
suggestions); Enter confirms it and the card is moved straight into that series' folder, which is
created if it doesn't exist yet. Nothing is moved until a series is confirmed, and the window can
be closed at any time: every confirmed card has already been moved. Click the file name (the bold
text) instead to rename the file in place: Enter renames it, Esc cancels.

The library can hold thousands of cards, and CustomTkinter widgets are heavy (each is its own
canvas), so the grid is drawn on one plain Tk canvas and only the tiles in view exist: scrolling
just draws the tiles that come into view, and covers are decoded on worker threads.

Right-clicking a card offers "Mark to delete" and "Mark to archive" (the selected cards go along). Cards marked to delete
leave every other view and wait in a To Delete entry of the sidebar, under the unknown-series one;
cards marked to archive wait in a To Archive entry just above it — every tab has its own of each;
right-click there to restore. The files are only sent to the Recycle Bin (or archived) when the
window closes.

`LibraryModel` (what is shown: view, search, sort) has no GUI dependency; the window is built in
`_ctk_dialog()` (see common.py for the shared window behaviour).

The dialog doesn't know what a "series" is: a card's `series` is just the folder it sits in under
`CharaItem.root`, and every sentence that names things (the sidebar buttons, the status line, the
...) comes from a `DialogText`. a tab without one gets the defaults (series folders of
characters); the others pass their own `DialogText` (see library/common.py).
Moving and suggesting are callbacks, so a task only has to supply `items`, `suggest` and `assign`.

`library_dialog()` puts several of these libraries in one window (Organize Library: scenes, characters,
outfits): a tab bar on the left of the search input switches between them, each tab keeping its own
sidebar, search text, sort, selection and scroll position. `manual_dialog()` is the one-library case.
"""

from __future__ import annotations

import io
import queue
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from kkafio.core.errors import TaskFailedError
from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.dialogs.common import maximize, new_window, show_and_wait
from kkafio.dialogs.common import format_size
from kkafio.dialogs.library.common import is_plain_dir
from kkafio.dialogs.library.popup_menu import open_popup_menu
from kkafio.dialogs.library.suggestions import SUGGEST_EXISTING, SUGGEST_NONE, SuggestionSettings
from kkafio.dialogs.library.viewstate import LibraryViewState, TabView
from kkafio.dialogs.library.shortcuts import (
    ACTION_ARCHIVE, ACTION_DELETE, ACTION_EXPLORER, ACTION_FOLDER, ACTION_RENAME, ACTION_RESTORE, ShortcutSettings,
    combo_from_event,
)
from kkafio.dialogs.library.model import (
    COVER_MAX, COVER_RATIO, SORT_CREATED, SORT_FILENAME, SORT_NAME, SORT_SIZE, SORT_UPDATED,
    MODS_ALL, MODS_COMPLETE, MODS_MISSING,
    VIEW_ALL, VIEW_TO_ARCHIVE, VIEW_SERIES, VIEW_TO_DELETE, VIEW_UNTAGGED, AssignFn, CharaItem, DialogText, LibraryModel,
    FolderNameTaken, LibraryTab, chara_dialog_text, clean_file_stem, rename_card, rename_series_folders,
    sidebar_entries, sidebar_step,
)


# ---------------------------------------------------------------------------
# Small helpers (no GUI)
# ---------------------------------------------------------------------------

SIZE_MIN, SIZE_MAX, SIZE_DEFAULT = 110, 260, 170     # cover width (px at 100% scaling)
_PIL_CACHE_MAX = 500
_PHOTO_CACHE_MAX = 500
_MAX_SUGGESTIONS = 8
_SHOWN_SUGGESTIONS = 6


def load_cover(path: Path):
    """The card's cover as an RGBA PIL image (at most COVER_MAX), or None. Safe to call from a
    worker thread (touches no Tk objects); reads only the PNG part of the file."""
    from PIL import Image
    from kkafio.cards.chara.similarity import read_png_preview

    data = read_png_preview(path)
    if not data:
        return None
    with Image.open(io.BytesIO(data)) as im:
        im.thumbnail(COVER_MAX)
        return im.convert("RGBA")


def round_corners(img, radius: int):
    """`img` as RGBA with all four corners rounded by `radius` px (transparent outside the curve),
    smoothed by drawing the mask at 4x size."""
    from PIL import Image, ImageDraw

    img = img.convert("RGBA")
    w, h = img.size
    k = 4
    mask = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=min(radius, w // 2, h // 2) * k,
                                           fill=255)
    img.putalpha(mask.resize((w, h), Image.LANCZOS))
    return img


def rounded_rect_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    """Polygon points for a rounded rectangle; draw with create_polygon(..., smooth=True)."""
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def format_stamp(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y/%m/%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "?"


def ellipsize(measure: Callable[[str], int], text: str, max_px: int) -> str:
    """`text` cut to fit `max_px` (as `measure` counts it), ending in "…" if it was cut."""
    if measure(text) <= max_px:
        return text
    while text and measure(text + "…") > max_px:
        text = text[:-1]
    return (text + "…") if text else "…"


# (light, dark) palettes; the canvas and the plain Tk widgets take the one matching the theme,
# CustomTkinter widgets take both.
_LIGHT = dict(bg="#eef0f3", side="#e4e7ec", tile="#ffffff", text="#1b1f24", muted="#667085",
              accent="#3556d8", sel="#d3dcfb", hover="#d3d8e2", input="#ffffff", border="#c9ced6", cover="#d9dde4")
_DARK = dict(bg="#17181c", side="#1d1f24", tile="#25282f", text="#e6e8ee", muted="#8a92a3",
             accent="#8ea6ff", sel="#2f3d6b", hover="#2b2e37", input="#12131a", border="#3a3f4b", cover="#33363f")


def _apply_accent_to_palettes() -> None:
    """Colour the accent entries of both palettes from the configured accent (core/theme.py)."""
    from kkafio.core import theme

    sel_light, sel_dark = theme.accent_selection_pair()
    text_light, text_dark = theme.accent_text_pair()
    _LIGHT.update(accent=text_light, sel=sel_light)
    _DARK.update(accent=text_dark, sel=sel_dark)


_apply_accent_to_palettes()


# ---------------------------------------------------------------------------
# The sidebar list
# ---------------------------------------------------------------------------

try:                                    # tkinter is optional: without it manual_dialog() falls back (see there)
    import tkinter as tk
except ImportError:                     # pragma: no cover - no Tk on this machine
    tk = None


class SideList(tk.Canvas if tk is not None else object):
    """The sidebar's list of series/characters/authors: a single-selection list whose rows are drawn
    like the CTkButtons above it (same height, same corner radius, same gap), which a tk.Listbox
    can't do. Only the rows in view are drawn. It offers the part of the Listbox interface the
    dialog needs (delete/insert/get/size/selection_*/curselection/see) and sends <<ListboxSelect>>
    when a row is clicked."""

    def __init__(self, master, *, bg, fg, sel_bg, hover_bg, font, row_h: int, gap: int, radius: int,
                 pad_x: int, **kw):
        super().__init__(master, bg=bg, highlightthickness=0, borderwidth=0, **kw)
        self._fg, self._sel_bg, self._hover_bg, self._font = fg, sel_bg, hover_bg, font
        self._row_h, self._gap, self._radius, self._pad_x = row_h, gap, radius, pad_x
        self._rows: list[str] = []
        self._selected: int | None = None
        self._hover: int | None = None
        self._ysb_command = None
        self._redraw_job = None
        self.on_scroll = None               # called when the rows scroll (an input laid over a row would be misplaced)
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<Button-1>", self._on_click)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind(seq, self._on_wheel)

    # -- the Listbox interface the dialog uses ------------------------------

    def configure(self, cnf=None, **kw):
        if "yscrollcommand" in kw:                      # the scrollbar must follow our own drawing
            self._ysb_command = kw.pop("yscrollcommand")
            kw["yscrollcommand"] = self._on_yview
        return super().configure(cnf, **kw)

    config = configure

    def delete(self, first, last=None) -> None:
        self._rows.clear()
        self._selected = self._hover = None
        self._redraw_soon()                 # not now: the rows are usually refilled right away

    def insert(self, _index, *texts: str) -> None:
        self._rows.extend(texts)
        self._redraw_soon()

    def size(self) -> int:
        return len(self._rows)

    def get(self, first, last=None):
        return tuple(self._rows)

    def selection_set(self, index: int) -> None:
        self._selected = index
        self._redraw_soon()

    def selection_clear(self, first=None, last=None) -> None:
        self._selected = None
        self._redraw_soon()

    def curselection(self) -> tuple[int, ...]:
        return () if self._selected is None else (self._selected,)

    def see(self, index: int) -> None:
        top, bottom = index * self._step, index * self._step + self._row_h
        height = self.winfo_height()
        if height <= 1:
            return
        view_top = self.canvasy(0)
        if top < view_top:
            self.yview_moveto(top / max(1, self._total_height()))
        elif bottom > view_top + height:
            self.yview_moveto((bottom - height) / max(1, self._total_height()))

    def row_box(self, index: int) -> tuple[int, int, int, int]:
        """(x, y, width, height) of a row in the widget's own coordinates (as it is scrolled now)."""
        return 0, int(index * self._step - self.canvasy(0)), self.winfo_width(), self._row_h

    # -- layout and drawing ----------------------------------------------------

    @property
    def _step(self) -> int:
        return self._row_h + self._gap

    def _total_height(self) -> int:
        return max(0, len(self._rows) * self._step - self._gap)

    def row_box(self, index: int) -> tuple[int, int, int, int]:
        """(x, y, width, height) of a row in widget coordinates (y follows the scroll position)."""
        return 0, int(index * self._step - self.canvasy(0)), self.winfo_width(), self._row_h

    def _row_at(self, event_y: int) -> int | None:
        y = self.canvasy(event_y)
        i = int(y // self._step)
        return i if 0 <= i < len(self._rows) and y - i * self._step < self._row_h else None   # not in a gap

    def row_index_at(self, event_y: int) -> int | None:
        """The row under a pointer position (widget coordinates), None between or below the rows."""
        return self._row_at(event_y)

    def hovered_index(self) -> int | None:
        """The row the pointer is on."""
        return self._hover

    def _on_yview(self, first, last) -> None:
        if self._ysb_command is not None:
            self._ysb_command(first, last)

    def _round_rect(self, x1, y1, x2, y2, fill) -> None:
        r = max(0, min(self._radius, (x2 - x1) // 2, (y2 - y1) // 2))
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.create_polygon(pts, smooth=True, fill=fill, outline="")

    def _redraw_soon(self) -> None:
        """Redraw once, after whatever is changing the rows is done — so a rebuild (delete, insert,
        select) never paints the empty or half-filled list in between, and the scroll position
        isn't touched until the final rows are known."""
        if self._redraw_job is None:
            self._redraw_job = self.after_idle(self._redraw)

    def _redraw(self) -> None:
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except tk.TclError:
                pass
            self._redraw_job = None
        tk.Canvas.delete(self, "all")
        width, height = self.winfo_width(), self.winfo_height()
        total = self._total_height()
        self.configure(scrollregion=(0, 0, max(1, width), max(total, height)))
        if total <= height:
            self.yview_moveto(0)
        if not self._rows or height <= 1:
            return
        top = self.canvasy(0)
        first = max(0, int(top // self._step))
        last = min(len(self._rows) - 1, int((top + height) // self._step))
        for i in range(first, last + 1):
            y = i * self._step
            fill = self._sel_bg if i == self._selected else self._hover_bg if i == self._hover else None
            if fill:
                self._round_rect(0, y, width, y + self._row_h, fill)
            self.create_text(self._pad_x, y + self._row_h // 2, anchor="w", text=self._rows[i],
                             fill=self._fg, font=self._font)

    def _set_hover(self, index: int | None) -> None:
        if index != self._hover:
            self._hover = index
            tk.Canvas.configure(self, cursor="hand2" if index is not None else "")
            self._redraw()

    def _on_motion(self, event) -> None:
        self._set_hover(self._row_at(event.y))

    def _on_click(self, event) -> None:
        index = self._row_at(event.y)
        if index is not None:
            self._selected = index
            self._redraw()
            self.event_generate("<<ListboxSelect>>")

    def _on_wheel(self, event) -> str:
        if self._total_height() > self.winfo_height():
            if self.on_scroll is not None:
                self.on_scroll()
            num = getattr(event, "num", 0)
            step = -1 if num == 4 else 1 if num == 5 else (-1 if event.delta > 0 else 1)
            self.yview_scroll(step * 2, "units")
            self._redraw()
            self._set_hover(self._row_at(event.y))
        return "break"

    def yview_scroll(self, number, what) -> None:          # the scrollbar and the wheel both come here
        if self.on_scroll is not None:
            self.on_scroll()
        if what == "units":
            self.yview_moveto((self.canvasy(0) + number * self._step) / max(1, self._total_height()))
        else:
            super().yview_scroll(number, what)
        self._redraw()

    def yview(self, *args):
        result = super().yview(*args)
        if args:
            self._redraw()
        return result


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

def manual_dialog(title: str, items: Sequence[CharaItem],
                  suggest: Callable[[str], list[str]], assign: AssignFn,
                  text: DialogText | None = None, cover_ratio: float = COVER_RATIO) -> int:
    """Show the library; returns how many cards were moved into a series before it was closed.

    suggest   text typed so far -> series names to offer (best first)
    assign    (item, text typed) -> (new path, series folder) after moving the card there, or
              None if it couldn't be done. The dialog only shows; this does the moving.
    text      the dialog's sentences (default: Group Characters')
    cover_ratio  cover height / width (default: a character card's portrait; scenes are landscape)
    """
    tab = LibraryTab("", items, suggest, assign, text, cover_ratio)
    return library_dialog(title, [tab])[0]


HELD_VIEWS = (VIEW_TO_DELETE, VIEW_TO_ARCHIVE)      # the views of cards waiting for the bin / Archive Cards


def library_dialog(title: str, tabs: Sequence[LibraryTab], initial: int = 0,
                   mods=None, on_delete: Callable[[list[Path]], None] | None = None,
                   on_archive: Callable[[list[Path]], None] | None = None) -> list[int]:
    """Show one window holding a library per tab; returns, per tab, how many cards were moved
    before it was closed. With a single tab there is no tab bar (that is `manual_dialog`).

    `mods` (a mods_panel.CardMods) adds the collapsible mod dependencies sidebar on the right,
    listing the mods of the card under the cursor; None leaves it out.

    `on_delete` receives the paths of the cards left in the libraries' To Delete lists when the window closes
    normally, and deletes them itself (Organize Library hands them to Delete Cards); None sends them to
    the Recycle Bin.

    `on_archive` receives the paths of the cards left in the libraries' To Archive lists when the
    window closes normally (Organize Library hands them to Archive Cards); None leaves the list
    alone, nothing is archived."""
    if not tabs:
        raise ValueError("library_dialog needs at least one tab")
    try:
        return _ctk_dialog(title, list(tabs), min(max(initial, 0), len(tabs) - 1), mods, on_delete, on_archive)
    except ImportError as e:                       # tkinter / customtkinter missing
        raise TaskFailedError(f"The character library needs tkinter and customtkinter: {e}") from e
    finally:
        for tab in tabs:                           # one tab failing to tidy up must not stop the others
            if tab.finish is not None:
                try:
                    tab.finish()
                except Exception as e:
                    logger.warning("LIBRARY", f"Could not finish the '{tab.label}' tab: {e}")


def _send_to_bin(items: Sequence[CharaItem]) -> None:
    """Send the files of the cards that were left in the libraries' To Delete lists to the Recycle Bin."""
    if not items:
        return
    from send2trash import send2trash
    done = 0
    for item in items:
        try:
            send2trash(str(item.path))
            done += 1
        except Exception as e:                     # one failing must not keep the others in the library
            logger.warning("LIBRARY", f"Could not send {item.path.name} to the bin: {e}")
    logger.info("LIBRARY", f"Sent {done} of {len(items)} card(s) from To Delete to the bin")


class _Library:
    """What `_build_library` hands back to the window that hosts it."""

    def __init__(self):
        self.frame = None                          # the library's widgets (a CTkFrame)
        self.canvas = None                         # the tile canvas (takes the keyboard focus)
        self.moved_count = lambda: 0               # cards moved so far
        self.view = lambda: TabView()              # its sort and cover size now (for the next run)
        self.to_delete_items = lambda: []            # the cards in its To Delete list (for the bin when the window closes)
        self.to_archive_items = lambda: []           # the cards in its To Archive list (for Archive Cards then)
        self.search_text = lambda: ""              # the search text this library is showing
        self.on_search_key = lambda: None          # the shared search input changed
        self.on_show = lambda: None                # its tab was opened
        self.on_hide = lambda: None                # its tab was closed
        self.dispose = lambda: None                # the window closes: stop timers and workers
        self.close_popups = lambda: None           # the window closes: drop dialogs it opened
        self.cursor = lambda: None                 # the card under the keyboard cursor (None: no card)


def _ctk_dialog(title: str, tabs: list[LibraryTab], initial: int, mods=None,
                on_delete: Callable[[list[Path]], None] | None = None,
                on_archive: Callable[[list[Path]], None] | None = None) -> list[int]:
    import customtkinter as ctk

    root = new_window(title, topmost=False)       # sorting a library takes a while
    root.minsize(820, 520)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(1, weight=1)

    shortcuts = ShortcutSettings.from_user_config()     # one for the whole window: every tab shares it
    suggestions = SuggestionSettings.from_user_config()  # (and what the folder box offers, per kind of card)
    view_state = LibraryViewState.from_user_config()    # the last tab and each tab's sort / size
    keys = [tab.key or f"tab{i}" for i, tab in enumerate(tabs)]
    initial = view_state.initial_index(keys, initial, [bool(tab.items) for tab in tabs])   # the tab left open last

    # search bar, with the tabs on its left and the Settings and Info buttons on its right
    top = ctk.CTkFrame(root, fg_color="transparent")
    top.grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky="ew")
    top.grid_columnconfigure(1, weight=1)
    first_text = tabs[initial].text if tabs[initial].text is not None else chara_dialog_text()
    search_entry = ctk.CTkEntry(top, placeholder_text=first_text.search_placeholder, height=34)
    search_entry.grid(row=0, column=1, sticky="ew")

    def open_settings() -> None:
        from kkafio.dialogs.library.settings_dialog import open_settings_dialog
        open_settings_dialog(root, shortcuts, suggestions)

    ctk.CTkButton(top, text="\u2699 " + t("dialog.settings.button"), width=110, height=34,
                  command=open_settings).grid(row=0, column=2, padx=(10, 0))

    def open_info() -> None:
        from kkafio.dialogs.library.info_dialog import open_info_dialog
        open_info_dialog(root, shortcuts)

    ctk.CTkButton(top, text="\u24d8 " + t("dialog.info.button"), width=110, height=34,
                  command=open_info).grid(row=0, column=3, padx=(10, 0))

    stack = ctk.CTkFrame(root, fg_color="transparent")          # the libraries sit on top of each other
    stack.grid(row=1, column=0, sticky="nsew")
    stack.grid_columnconfigure(0, weight=1)
    stack.grid_rowconfigure(0, weight=1)

    def on_close(_event=None) -> None:
        for g in libraries:
            g.close_popups()
        root.quit()

    libraries: list[_Library] = []
    texts = [tab.text if tab.text is not None else chara_dialog_text() for tab in tabs]
    current = {"index": initial}
    sidebar = None                                              # the mod dependencies sidebar, if asked for

    def on_cursor(index: int, item) -> None:
        """The keyboard cursor of tab `index` moved: the sidebar follows the open tab's."""
        if sidebar is not None and index == current["index"]:
            sidebar.show(item.path if item is not None else None, tabs[index].guids, tabs[index].guid_index)

    for i, tab in enumerate(tabs):
        libraries.append(_build_library(root, stack, tab, texts[i], search_entry, active=(i == initial),
                                    shortcuts=shortcuts, suggestions=suggestions, view=view_state.view(keys[i]), mods=mods,
                                    on_cursor=lambda item, i=i: on_cursor(i, item)))

    if mods is not None:
        from kkafio.dialogs.library.mods_sidebar import ModsSidebar      # (it imports this module)
        import customtkinter as _ctk
        _apply_accent_to_palettes()
        _dark = _ctk.get_appearance_mode() == "Dark"
        try:
            _scale = float(root._get_window_scaling())
        except Exception:
            _scale = 1.0
        sidebar = ModsSidebar(root, root, mods, _DARK if _dark else _LIGHT, lambda k: (_LIGHT[k], _DARK[k]),
                              _scale, open_=view_state.mods_open)
        sidebar.frame.grid(row=1, column=1, sticky="ns", padx=(0, 12), pady=(0, 10))

    def show_tab(index: int) -> None:
        old = libraries[current["index"]]
        old.on_hide()
        old.frame.grid_remove()
        current["index"] = index
        g = libraries[index]
        g.frame.grid(row=0, column=0, sticky="nsew")
        search_entry.configure(placeholder_text=texts[index].search_placeholder)
        if sidebar is not None:
            sidebar.show(g.cursor().path if g.cursor() is not None else None, tabs[index].guids,
                         tabs[index].guid_index)
        search_entry.delete(0, "end")
        search_text = g.search_text()
        if search_text:
            search_entry.insert(0, search_text)
        g.on_show()
        g.canvas.focus_set()

    if len(tabs) > 1:
        labels = [tab.label for tab in tabs]
        tab_bar = ctk.CTkSegmentedButton(top, values=labels, height=34,
                                         command=lambda value: show_tab(labels.index(value)))
        tab_bar.set(labels[initial])
        tab_bar.grid(row=0, column=0, padx=(0, 10))

    libraries[initial].frame.grid(row=0, column=0, sticky="nsew")
    search_entry.bind("<KeyRelease>", lambda _e: libraries[current["index"]].on_search_key())
    root.bind("<Escape>", on_close)
    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        show_and_wait(root, libraries[initial].canvas, on_shown=lambda: maximize(root))      # always full screen
    finally:
        for g in libraries:
            g.dispose()
        if sidebar is not None:
            sidebar.dispose()
        view_state.remember(keys[current["index"]], {k: g.view() for k, g in zip(keys, libraries)},
                            mods_open=sidebar.is_open if sidebar is not None else None)
    to_delete = [item for g in libraries for item in g.to_delete_items()]          # only a window that closed normally
    to_archive = [item for g in libraries for item in g.to_archive_items()]
    if to_archive and on_archive is not None:                # first: archiving only reads the cards
        on_archive([item.path for item in to_archive])
    if on_delete is None:
        _send_to_bin(to_delete)
    elif to_delete:
        on_delete([item.path for item in to_delete])
    return [g.moved_count() for g in libraries]


def _build_library(root, parent, tab: LibraryTab, txt: DialogText, search_entry, active: bool,
                   shortcuts: ShortcutSettings | None = None, suggestions: SuggestionSettings | None = None,
                   view: TabView | None = None,
                   on_cursor: Callable[[CharaItem | None], None] | None = None, mods=None) -> _Library:
    """One library inside `parent` (a frame of the window `root`); it shows when its frame is
    gridded. `search_entry` is the window's shared search input, which only the open tab listens to.
    `on_cursor(item)` is called whenever the keyboard cursor moves to another card. `mods` (a
    mods_panel.CardMods) adds the toolbar's mods filter (all cards / all mods installed / missing mods)."""
    import tkinter as tk
    from tkinter import font as tkfont
    import customtkinter as ctk
    from PIL import Image, ImageOps, ImageTk

    items, suggest_source, assign_source, cover_ratio = tab.items, tab.suggest, tab.assign, tab.cover_ratio
    if shortcuts is None:
        shortcuts = ShortcutSettings()                  # the defaults, nothing saved
    if suggestions is None:
        suggestions = SuggestionSettings()              # every suggestion, nothing saved

    def apply_language() -> None:
        """The language of the series titles, as the Settings have it (only the Character tab has any)."""
        set_language = getattr(suggest_source, "set_language", None)
        if set_language is not None:
            set_language(suggestions.language)

    def assign(item, text: str):
        return assign_source(item, text)

    def suggest(text: str) -> list[str]:
        """The names to offer for `text`, as far as the Settings allow for this kind of card."""
        apply_language()
        mode = suggestions.mode(tab.key)
        if mode == SUGGEST_NONE:
            return []
        if mode == SUGGEST_EXISTING:
            return list(getattr(suggest_source, "existing", suggest_source)(text))
        return list(suggest_source(text))
    view = view or TabView()                            # how the tab was left last time
    handle = _Library()
    model = LibraryModel(items)

    model.set_view(VIEW_ALL)           # always opens on the "All" entry of the sidebar
    moved = {"count": 0}

    host = ctk.CTkFrame(parent, fg_color="transparent")
    handle.frame = host
    host.grid_columnconfigure(0, weight=1)
    host.grid_rowconfigure(0, weight=1)

    _apply_accent_to_palettes()
    dark = ctk.get_appearance_mode() == "Dark"
    pal = _DARK if dark else _LIGHT

    def pair(key: str) -> tuple[str, str]:
        return (_LIGHT[key], _DARK[key])

    try:
        scale = float(root._get_window_scaling())
    except Exception:
        scale = 1.0

    def px(n: float) -> int:
        return max(1, int(round(n * scale)))

    family = tkfont.nametofont("TkDefaultFont").actual("family")
    f_name = tkfont.Font(root=root, family=family, size=12, weight="bold")
    f_small = tkfont.Font(root=root, family=family, size=11)
    f_italic = tkfont.Font(root=root, family=family, size=11, slant="italic")
    f_series = tkfont.Font(root=root, family=family, size=11)                   # the folder (series) line of a card;
    f_series_italic = tkfont.Font(root=root, family=family, size=11, slant="italic")   # sized like the sidebar below
    lh_name = f_name.metrics("linespace") + px(3)
    lh_small = f_small.metrics("linespace") + px(2)
    row_series = lh_small + px(6)                  # tall enough for the series input
    radius = px(12)                                # corners of the tiles and of the covers in them

    # ---- layout / drawing state -------------------------------------------
    lay: dict = {"items": [], "cols": 1, "tile_w": 0, "tile_h": 0, "img_w": 0, "img_h": 0,
                 "pad": px(10), "gap": px(12), "series_y": 0, "region_h": 1}
    drawn: dict[int, list[int]] = {}               # tile index -> its canvas items
    cover_item: dict[int, tuple[int, int]] = {}    # tile index -> (cover image item, "no preview" text item)
    # Selection: `picked` are the selected cards (accent border); `item` is the keyboard cursor, the
    # card the arrow keys move from. Ctrl+arrows move the cursor without changing `picked`.
    sel: dict = {"item": None, "picked": {}}         # picked: id(card) -> card
    multi: dict = {"dlg": None}                      # the series dialog for several cards, while open
    edit: dict = {"idx": None, "entry": None, "lb": None, "suggestions": [], "after": None,
                  "field": "series"}                 # field: "series" (move) or "name" (rename the file)
    timers: dict[str, str | None] = {"poll": None, "search": None, "layout": None,
                                     "sidebar": None, "draw": None, "sidenav": None}

    # ---- covers: decoded on worker threads, applied on the Tk thread --------
    pil_cache: OrderedDict[Path, object] = OrderedDict()     # Path -> PIL image, or False
    photos: OrderedDict[tuple[Path, int], object] = OrderedDict()
    requested: set[Path] = set()
    cover_queue: queue.Queue = queue.Queue()
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kkafio-cover")
    scan_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kkafio-modscan")

    def request_cover(path: Path) -> None:
        if path in requested or path in pil_cache:
            return
        requested.add(path)

        def work() -> None:
            try:
                pil = load_cover(path)
            except Exception:
                pil = None
            cover_queue.put((path, pil))

        executor.submit(work)

    def photo_for(path: Path):
        """The cover at the current size as a PhotoImage, or None while it isn't loaded."""
        pil = pil_cache.get(path)
        if not pil:
            return None
        key = (path, lay["img_w"])
        photo = photos.get(key)
        if photo is None:
            fitted = ImageOps.fit(pil, (lay["img_w"], lay["img_h"]), Image.LANCZOS)    # fills the tile's width
            photo = ImageTk.PhotoImage(round_corners(fitted, radius))
            photos[key] = photo
            while len(photos) > _PHOTO_CACHE_MAX:
                photos.popitem(last=False)
        else:
            photos.move_to_end(key)
        return photo

    def poll_covers() -> None:
        for _ in range(12):                        # a few per tick keeps the UI smooth
            try:
                path, pil = cover_queue.get_nowait()
            except queue.Empty:
                break
            requested.discard(path)
            pil_cache[path] = pil if pil is not None else False
            while len(pil_cache) > _PIL_CACHE_MAX:
                pil_cache.popitem(last=False)
            apply_cover(path)
        drain_scan()
        timers["poll"] = root.after(50, poll_covers)

    def apply_cover(path: Path) -> None:
        """A cover finished loading: show it (or "no preview") on every drawn tile that has it."""
        for idx, (image_id, note_id) in list(cover_item.items()):
            if idx in drawn and idx < len(lay["items"]) and lay["items"][idx].path == path:
                photo = photo_for(path)
                try:
                    if photo is not None:
                        canvas.itemconfigure(image_id, image=photo)
                        canvas.itemconfigure(note_id, text="")
                    else:
                        canvas.itemconfigure(note_id, text=t("dialog.review.no_preview"))
                except tk.TclError:
                    pass

    # ---- widgets -------------------------------------------------------------
    # (the search input belongs to the window: it is the `search_entry` passed in)
    body = ctk.CTkFrame(host, fg_color="transparent")
    body.grid(row=0, column=0, padx=12, pady=(0, 0), sticky="nsew")
    body.grid_columnconfigure(1, weight=1)
    body.grid_rowconfigure(0, weight=1)

    # sidebar
    side = ctk.CTkFrame(body, fg_color=pair("side"), corner_radius=8, width=230)
    side.grid(row=0, column=0, sticky="ns", padx=(0, 10))
    side.grid_propagate(False)
    side.grid_rowconfigure(6, weight=1)
    side.grid_columnconfigure(0, weight=1)

    def side_button(row: int, command) -> ctk.CTkButton:
        b = ctk.CTkButton(side, text="", anchor="w", height=30, corner_radius=6, fg_color="transparent",
                          text_color=pair("text"), hover_color=pair("sel"), command=command)
        b.grid(row=row, column=0, padx=8, pady=(8 if row == 0 else 2, 0), sticky="ew")
        return b

    all_btn = side_button(0, lambda: pick_side_view(VIEW_ALL))
    untagged_btn = side_button(1, lambda: pick_side_view(VIEW_UNTAGGED))
    to_archive_btn = side_button(2, lambda: pick_side_view(VIEW_TO_ARCHIVE))   # under the unknown-series entry,
    to_delete_btn = side_button(3, lambda: pick_side_view(VIEW_TO_DELETE))       # just above To Delete
    ctk.CTkLabel(side, text=txt.series_header, anchor="w", text_color=pair("muted"),
                 font=ctk.CTkFont(size=11, weight="bold")).grid(row=4, column=0, padx=14, pady=(14, 2), sticky="ew")
    series_filter_entry = ctk.CTkEntry(side, placeholder_text=txt.filter_series, height=30)
    series_filter_entry.grid(row=5, column=0, padx=8, pady=(0, 6), sticky="ew")
    list_frame = tk.Frame(side, bg=pal["side"])
    list_frame.grid(row=6, column=0, padx=(8, 4), pady=(0, 8), sticky="nsew")
    list_frame.grid_columnconfigure(0, weight=1)
    list_frame.grid_rowconfigure(0, weight=1)
    # the list looks like the buttons above it: their font, height, corner radius and gap
    btn_font = all_btn.cget("font")
    scaling = ctk.ScalingTracker.get_widget_scaling(all_btn)
    f_side = tkfont.Font(root=root, family=btn_font.cget("family"),
                         size=-max(1, round(abs(btn_font.cget("size")) * scaling)))
    f_name.configure(size=f_side.cget("size"))      # the card titles use the sidebar's font size too
    lh_name = f_name.metrics("linespace") + px(3)   # (and the tiles are as tall as their title line)
    f_series.configure(size=f_side.cget("size"))    # same for the folder line under the title
    f_series_italic.configure(size=f_side.cget("size"))
    row_series = f_series.metrics("linespace") + px(8)   # tall enough for the folder input
    series_box = SideList(list_frame, bg=pal["side"], fg=pal["text"], sel_bg=pal["sel"], hover_bg=pal["hover"],
                          font=f_side, row_h=round(all_btn.cget("height") * scaling), gap=round(2 * scaling),
                          radius=round(all_btn.cget("corner_radius") * scaling), pad_x=round(12 * scaling))
    series_box.grid(row=0, column=0, sticky="nsew")
    series_sb = ctk.CTkScrollbar(list_frame, command=series_box.yview)
    series_sb.grid(row=0, column=1, sticky="ns")
    series_box.configure(yscrollcommand=series_sb.set)
    series_names: list[str] = []                   # what each row of series_box stands for

    # main area: toolbar + library
    main = ctk.CTkFrame(body, fg_color="transparent")
    main.grid(row=0, column=1, sticky="nsew")
    main.grid_columnconfigure(0, weight=1)
    main.grid_rowconfigure(1, weight=1)

    bar = ctk.CTkFrame(main, fg_color="transparent")
    bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))      # over the card grid only, not its scrollbar
    bar.grid_columnconfigure(3, weight=1)       # two equal gaps keep the mods filter centred between
    bar.grid_columnconfigure(6, weight=1)       # the sort order button and the size slider
    ctk.CTkLabel(bar, text=t("dialog.manual.sort_by"), text_color=pair("muted")).grid(row=0, column=0, padx=(0, 8))
    sort_labels = {t("dialog.manual.sort_created"): SORT_CREATED, t("dialog.manual.sort_updated"): SORT_UPDATED,
                   t("dialog.manual.sort_name"): SORT_NAME, t("dialog.manual.sort_filename"): SORT_FILENAME,
                   t("dialog.manual.sort_size"): SORT_SIZE}
    sort_menu = ctk.CTkOptionMenu(bar, values=list(sort_labels), width=150, command=lambda _v: on_sort())
    sort_menu.set(t("dialog.manual.sort_updated"))
    sort_menu.grid(row=0, column=1, padx=(0, 8))
    order_btn = ctk.CTkButton(bar, text=t("dialog.manual.descending"), width=120, fg_color=pair("sel"),
                              text_color=pair("text"), hover_color=pair("border"), command=lambda: on_order())
    order_btn.grid(row=0, column=2)
    ctk.CTkLabel(bar, text=t("dialog.manual.size"), text_color=pair("muted")).grid(row=0, column=7, padx=(0, 8))
    size_slider = ctk.CTkSlider(bar, from_=SIZE_MIN, to=SIZE_MAX, width=160, command=lambda _v: on_size())
    size_slider.set(SIZE_DEFAULT)
    size_slider.grid(row=0, column=8)
    # the sort and size it was left with (kept in `now` too: the widgets are gone when the window is)
    model.sort_key, model.descending = view.sort, view.descending
    sort_menu.set(next((label for label, key in sort_labels.items() if key == view.sort), t("dialog.manual.sort_updated")))
    order_btn.configure(text=t("dialog.manual.descending") if view.descending else t("dialog.manual.ascending"))
    size_slider.set(min(max(view.size, SIZE_MIN), SIZE_MAX) if view.size else SIZE_DEFAULT)
    now = TabView(view.sort, view.descending, view.size, view.mods)

    # the mods filter: which cards have all their mods / miss some (only with a known mods folder)
    mods_labels = {t("dialog.library.mods_filter_all"): MODS_ALL,
                   t("dialog.library.mods_filter_complete"): MODS_COMPLETE,
                   t("dialog.library.mods_filter_missing"): MODS_MISSING}
    scan: dict = {"queue": queue.Queue(), "stop": False, "started": set(), "total": 0, "done": 0}
    if mods is not None and tab.guids is not None and mods.lookup.available:
        ctk.CTkLabel(bar, text=t("dialog.library.mods_filter_label"), text_color=pair("muted")).grid(
            row=0, column=4, padx=(0, 8))
        mods_menu = ctk.CTkOptionMenu(bar, values=list(mods_labels), width=190, command=lambda _v: on_mods_filter())
        model.mods_filter = view.mods                      # the filter the tab was left with
        mods_menu.set(next(label for label, key in mods_labels.items() if key == model.mods_filter))
        mods_menu.grid(row=0, column=5)
    else:
        mods_menu = None

    canvas = tk.Canvas(main, bg=pal["bg"], highlightthickness=0, borderwidth=0,
                       width=px(900), height=px(540), yscrollincrement=px(40), takefocus=1)
    canvas.grid(row=1, column=0, sticky="nsew")
    scroll_sb = ctk.CTkScrollbar(main, command=canvas.yview)
    scroll_sb.grid(row=1, column=1, sticky="ns", padx=(4, 0))

    # status line: under the card grid only (not under its scrollbar)
    foot = ctk.CTkFrame(main, fg_color="transparent")
    foot.grid(row=2, column=0, pady=10, sticky="ew")
    foot.grid_columnconfigure(0, weight=1)
    status = ctk.CTkLabel(foot, text="", anchor="w")
    status.grid(row=0, column=0, sticky="ew")

    # -----------------------------------------------------------------------
    # layout + drawing
    # -----------------------------------------------------------------------
    def canvas_size() -> tuple[int, int]:
        return max(canvas.winfo_width(), px(300)), max(canvas.winfo_height(), px(200))

    def tile_xy(idx: int) -> tuple[int, int]:
        col, row = idx % lay["cols"], idx // lay["cols"]
        return (lay["gap"] + col * (lay["tile_w"] + lay["gap"]),
                lay["gap"] + row * (lay["tile_h"] + lay["gap"]))

    def relayout(keep_scroll: bool = True) -> None:
        """Recompute the grid for the current view/size/window and draw what is in view."""
        timers["layout"] = None
        cancel_edit()
        y0 = canvas.canvasy(0) if keep_scroll else 0
        cw, ch = canvas_size()
        pad, gap = lay["pad"], lay["gap"]
        # The slider sets the smallest cover; the columns that fit then share the row's width, so
        # the grid fills the window. It depends on the window and the slider only (not on how
        # many cards are shown), so a search doesn't make the tiles jump in size.
        cols = max(1, (cw - gap) // (px(size_slider.get()) + 2 * pad + gap))
        tile_w = max(px(80), (cw - gap * (cols + 1)) // cols)
        img_w = tile_w                             # the cover fills the frame's width, edge to edge
        img_h = int(round(img_w * cover_ratio))
        tile_h = img_h + px(8) + lh_name + row_series + 2 * lh_small + pad
        lay.update(items=model.visible(), img_w=img_w, img_h=img_h, tile_w=tile_w, tile_h=tile_h,
                   cols=cols, series_y=img_h + px(8) + lh_name)
        shown_ids = {id(i) for i in lay["items"]}
        if sel["item"] is not None and id(sel["item"]) not in shown_ids:
            sel["item"] = None
        sel["picked"] = {k: v for k, v in sel["picked"].items() if k in shown_ids}
        n = len(lay["items"])
        rows = -(-n // lay["cols"])
        total = gap + rows * (tile_h + gap)
        region_h = max(total, ch)
        lay["region_h"] = region_h
        canvas.delete("all")
        drawn.clear()
        cover_item.clear()
        canvas.configure(scrollregion=(0, 0, cw, region_h))
        canvas.yview_moveto(min(max(0.0, y0 / region_h), 1.0) if keep_scroll else 0.0)
        if n == 0:
            canvas.create_text(cw // 2, ch // 3, text=txt.no_cards, fill=pal["muted"], font=f_name)
        draw_visible()
        update_status()

    def schedule_layout(delay: int = 80, keep_scroll: bool = True) -> None:
        if timers["layout"] is not None:
            root.after_cancel(timers["layout"])
        timers["layout"] = root.after(delay, lambda: relayout(keep_scroll))

    def draw_visible() -> None:
        timers["draw"] = None
        n = len(lay["items"])
        if not n:
            return
        y_top = canvas.canvasy(0)
        y_bottom = y_top + canvas.winfo_height()
        step = lay["tile_h"] + lay["gap"]
        first_row = max(0, int((y_top - lay["gap"]) // step) - 1)
        last_row = int((y_bottom - lay["gap"]) // step) + 1
        lo, hi = first_row * lay["cols"], min(n, (last_row + 1) * lay["cols"])
        for idx in [i for i in drawn if i < lo or i >= hi]:
            for cid in drawn.pop(idx):
                canvas.delete(cid)
            cover_item.pop(idx, None)
        for idx in range(lo, hi):
            if idx not in drawn:
                draw_tile(idx)

    def schedule_draw() -> None:
        if timers["draw"] is None:
            timers["draw"] = root.after_idle(draw_visible)

    def draw_tile(idx: int) -> None:
        item = lay["items"][idx]
        pad, tile_w = lay["pad"], lay["tile_w"]
        tx, ty = tile_xy(idx)
        text_w = tile_w - 2 * pad
        ids = [canvas.create_polygon(rounded_rect_points(tx, ty, tx + tile_w, ty + lay["tile_h"], radius),
                                     smooth=True, fill=pal["tile"], outline="")]
        cx = tx + tile_w // 2
        ids.append(canvas.create_polygon(rounded_rect_points(tx, ty, tx + tile_w, ty + lay["img_h"], radius),
                                         smooth=True, fill=pal["cover"], outline=""))
        request_cover(item.path)
        photo = photo_for(item.path)
        # Both items always exist: the cover can arrive after the tile is drawn (apply_cover), and
        # a text item can't take an image. The note stays empty while the cover is loading.
        image_id = canvas.create_image(cx, ty, anchor="n", image=photo if photo is not None else "")
        note_id = canvas.create_text(cx, ty + lay["img_h"] // 2, fill=pal["muted"], font=f_small,
                                     justify="center",
                                     text=t("dialog.review.no_preview") if pil_cache.get(item.path) is False else "")
        ids += [image_id, note_id]
        cover_item[idx] = (image_id, note_id)
        y = ty + lay["img_h"] + px(8)
        ids.append(canvas.create_text(tx + pad, y, anchor="nw", fill=pal["text"], font=f_name,
                                      text=ellipsize(f_name.measure, item.name, text_w)))
        y += lh_name
        if item.series:
            ids.append(canvas.create_text(tx + pad, y + px(2), anchor="nw", fill=pal["accent"], font=f_series,
                                          text=ellipsize(f_series.measure, item.series, text_w)))
        else:
            ids.append(canvas.create_text(tx + pad, y + px(2), anchor="nw", fill=pal["muted"], font=f_series_italic,
                                          text=txt.unknown_series))
        y += row_series
        ids.append(canvas.create_text(tx + pad, y, anchor="nw", fill=pal["muted"], font=f_small,
                                      text=format_size(item.size)))
        ids.append(canvas.create_text(tx + pad, y + lh_small, anchor="nw", fill=pal["muted"], font=f_small,
                                      text=ellipsize(f_small.measure, format_stamp(item.mtime), text_w)))
        picked, cursor = id(item) in sel["picked"], sel["item"] is item
        if picked or cursor:                       # an accent border over the whole tile; the cursor
            half = px(3) / 2                       # alone (not picked) gets a thin dashed one
            ids.append(canvas.create_polygon(
                rounded_rect_points(tx + half, ty + half, tx + tile_w - half, ty + lay["tile_h"] - half, radius),
                smooth=True, splinesteps=24, fill="", outline=pal["accent"], width=px(3) if picked else px(1),
                joinstyle="round", dash=() if picked else (px(4), px(3))))
        drawn[idx] = ids

    def index_of(item) -> int | None:
        for i, other in enumerate(lay["items"]):
            if other is item:
                return i
        return None

    def redraw_tile(idx: int) -> None:
        if idx in drawn:
            for cid in drawn.pop(idx):
                canvas.delete(cid)
            cover_item.pop(idx, None)
            draw_tile(idx)

    def apply_selection(cursor, picked) -> None:
        """Make `cursor` the keyboard cursor and `picked` the selected cards; redraws what changed."""
        old_picked, old_cursor = sel["picked"], sel["item"]
        sel["item"] = cursor
        sel["picked"] = {id(i): i for i in picked}
        if on_cursor is not None and cursor is not old_cursor:
            on_cursor(cursor)
        changed = set(old_picked.keys() ^ sel["picked"].keys())
        changed |= {id(c) for c in (old_cursor, cursor) if c is not None}
        for idx in list(drawn):
            if idx < len(lay["items"]) and id(lay["items"][idx]) in changed:
                redraw_tile(idx)
        update_status()

    def select_only(item) -> None:
        apply_selection(item, [item] if item is not None else [])

    def picked_in_order() -> list:
        return [i for i in lay["items"] if id(i) in sel["picked"]]

    def first_visible_index() -> int:
        step = lay["tile_h"] + lay["gap"]
        row = max(0, int(-(-(canvas.canvasy(0) - lay["gap"]) // step)))
        return min(row * lay["cols"], len(lay["items"]) - 1)

    def move_card_selection(dx: int, dy: int, keep_picked: bool = False) -> None:
        items = lay["items"]
        n = len(items)
        if not n:
            return
        cur = index_of(sel["item"])
        if cur is None:
            new = first_visible_index()
        else:
            cols = lay["cols"]
            new = cur + dx + dy * cols
            if new < 0:
                new = cur
            elif new >= n:                         # down into a short last row lands on its last card
                new = n - 1 if dy > 0 and cur // cols < (n - 1) // cols else cur
        if keep_picked:
            apply_selection(items[new], list(sel["picked"].values()))
        else:
            select_only(items[new])
        ensure_visible(new)

    def toggle_cursor() -> None:
        cur = sel["item"]
        if cur is None:
            return
        picked = dict(sel["picked"])
        if id(cur) in picked:
            del picked[id(cur)]
        else:
            picked[id(cur)] = cur
        apply_selection(cur, picked.values())

    def select_all() -> None:
        items = lay["items"]
        if items:
            apply_selection(sel["item"] or items[first_visible_index()], items)

    def locate(ex: int, ey: int) -> tuple[int, bool, bool] | None:
        """(tile index, over its series line?, over its file name?) for a point in the canvas
        window, or None."""
        n = len(lay["items"])
        if not n:
            return None
        x, y = canvas.canvasx(ex), canvas.canvasy(ey)
        gap, tile_w, tile_h = lay["gap"], lay["tile_w"], lay["tile_h"]
        col, row = int((x - gap) // (tile_w + gap)), int((y - gap) // (tile_h + gap))
        if col < 0 or row < 0 or col >= lay["cols"]:
            return None
        idx = row * lay["cols"] + col
        tx, ty = tile_xy(idx)
        if idx >= n or x > tx + tile_w or y > ty + tile_h:
            return None
        in_text = tx + lay["pad"] <= x <= tx + tile_w - lay["pad"]
        sy = ty + lay["series_y"]
        ny = ty + lay["img_h"] + px(8)                 # the name line sits right above the series line
        in_series = sy <= y <= sy + row_series and in_text
        in_name = ny <= y < sy and in_text
        return idx, in_series, in_name

    def ensure_visible(idx: int) -> None:
        _tx, ty = tile_xy(idx)
        ch = canvas.winfo_height()
        top_y = canvas.canvasy(0)
        region_h = lay["region_h"]
        if ty - lay["gap"] < top_y:
            canvas.yview_moveto(max(0, ty - lay["gap"]) / region_h)
        elif ty + lay["tile_h"] + lay["gap"] > top_y + ch:
            canvas.yview_moveto(max(0, ty + lay["tile_h"] + lay["gap"] - ch) / region_h)
        canvas.update_idletasks()
        draw_visible()

    # -----------------------------------------------------------------------
    # sidebar + status
    # -----------------------------------------------------------------------
    def refresh_sidebar() -> None:
        timers["sidebar"] = None
        cancel_folder_edit()                                # the rows are about to change
        total, untagged = model.counts()
        all_btn.configure(text=f"{txt.all}   {total:,}")
        untagged_btn.configure(text=f"{txt.untagged}   {untagged:,}")
        all_btn.configure(fg_color=pair("sel") if model.mode == VIEW_ALL else "transparent")
        untagged_btn.configure(fg_color=pair("sel") if model.mode == VIEW_UNTAGGED else "transparent")
        to_archive_btn.configure(text=f"{t('dialog.library.to_archive')}   {model.to_archive_count():,}",
                              fg_color=pair("sel") if model.mode == VIEW_TO_ARCHIVE else "transparent")
        to_delete_btn.configure(text=f"{t('dialog.library.to_delete')}   {model.to_delete_count():,}",
                            fg_color=pair("sel") if model.mode == VIEW_TO_DELETE else "transparent")
        entries = model.series_list(series_filter_entry.get())
        series_names[:] = [name for name, _n in entries]
        series_box.delete(0, "end")
        for name, n in entries:
            series_box.insert("end", f"{name}   ({n})")
        if model.mode == VIEW_SERIES:
            for i, name in enumerate(series_names):
                if name.casefold() == model.series_filter.casefold():
                    series_box.selection_set(i)
                    series_box.see(i)

    def start_scan() -> None:
        """Check the mods of every card not checked yet, on a worker thread (the filter needs them all)."""
        todo = [i for i in model.items if i.mods_missing is None and id(i) not in scan["started"]]
        if not todo:
            return
        scan["started"].update(id(i) for i in todo)
        scan["total"] += len(todo)
        parse = tab.guids

        def work() -> None:
            # The GUIDs of all the tab's cards come from the incremental on-disk per-file cache
            # (unchanged cards aren't read again, new ones are parsed in parallel). It is read once
            # per window, shared with the sidebar (which may already have asked for it). A card
            # missing from it is read on its own, as before.
            index = mods.index_for(tab.guid_index) if tab.guid_index is not None else {}
            for item in todo:
                if scan["stop"]:
                    return
                try:
                    known = index.get(str(item.path))
                    rows = mods.rows_for(known) if known is not None else mods.rows(item.path, parse)
                    missing = any(r.path is None for r in rows)
                except Exception:                      # unreadable: in neither group
                    missing = None
                scan["queue"].put((item, missing))

        try:
            scan_executor.submit(work)
        except RuntimeError:                           # the window is closing
            pass

    def drain_scan() -> None:
        changed = False
        for _ in range(400):
            try:
                item, missing = scan["queue"].get_nowait()
            except queue.Empty:
                break
            scan["done"] += 1
            if missing is not None:
                item.mods_missing = missing
            changed = True
        if changed:
            if model.mods_filter != MODS_ALL and timers["layout"] is None:
                schedule_layout(300, keep_scroll=True)          # cards join the filtered view as they are checked
            else:
                update_status()

    def on_mods_filter() -> None:
        model.mods_filter = mods_labels.get(mods_menu.get(), MODS_ALL)
        now.mods = model.mods_filter
        if model.mods_filter != MODS_ALL:
            start_scan()
        relayout(keep_scroll=False)

    def update_status() -> None:
        total, untagged = model.counts()
        shown = len(lay["items"])
        text = (txt.status.format(total=total, untagged=untagged) if shown == total
                else txt.status_filtered.format(shown=shown, total=total, untagged=untagged))
        if len(sel["picked"]) > 1:
            text += " · " + t("dialog.manual.selected", count=len(sel["picked"]))
        if model.mods_filter != MODS_ALL and scan["done"] < scan["total"]:
            text += " · " + t("dialog.library.mods_checking", done=scan["done"], total=scan["total"])
        status.configure(text=text)

    def choose_view(mode: str, series: str = "") -> None:
        if timers["sidenav"] is not None:                  # a keyboard walk still waiting to switch: this wins
            root.after_cancel(timers["sidenav"])
            timers["sidenav"] = None
        side_nav["target"] = None
        model.set_view(mode, series)
        if mode != VIEW_SERIES:
            series_box.selection_clear(0, "end")
        refresh_sidebar()
        relayout(keep_scroll=False)

    def on_series_pick(_event=None) -> None:
        sel = series_box.curselection()
        if sel and sel[0] < len(series_names):
            series_box.focus_set()                         # clicking the sidebar gives it the arrow keys
            choose_view(VIEW_SERIES, series_names[sel[0]])

    # -----------------------------------------------------------------------
    # walking the sidebar with the keyboard: Up/Down or W/S while the sidebar has the focus
    # (a click on one of its entries gives it the focus; Right / D / Tab or a click on the cards
    # hand it back to the card grid, which uses the same keys to move between cards)
    # -----------------------------------------------------------------------
    side_nav: dict = {"target": None}                      # the entry a walk is heading for, while it is pending
    side_view_buttons = {VIEW_ALL: all_btn, VIEW_UNTAGGED: untagged_btn,
                         VIEW_TO_ARCHIVE: to_archive_btn, VIEW_TO_DELETE: to_delete_btn}

    def pick_side_view(mode: str) -> None:
        series_box.focus_set()
        choose_view(mode)

    def on_side_focus(has_focus: bool) -> None:
        side.configure(border_width=2 if has_focus else 0, border_color=pair("accent"))

    def paint_side(mode: str, series: str) -> None:
        """Show `mode`/`series` as the selected entry at once (the cards follow a moment later)."""
        for view, button in side_view_buttons.items():
            button.configure(fg_color=pair("sel") if view == mode else "transparent")
        index = next((i for i, name in enumerate(series_names)
                      if mode == VIEW_SERIES and name.casefold() == series.casefold()), None)
        if index is None:
            series_box.selection_clear(0, "end")
        else:
            series_box.selection_set(index)
            series_box.see(index)

    def commit_side_nav() -> None:
        timers["sidenav"] = None
        target = side_nav["target"]
        side_nav["target"] = None
        if target is not None:
            choose_view(*target)

    def side_step(delta: int) -> str:
        current = side_nav["target"] or (model.mode, model.series_filter if model.mode == VIEW_SERIES else "")
        target = sidebar_step(sidebar_entries(series_names), current, delta)
        if target != current:
            side_nav["target"] = target
            paint_side(*target)
            if timers["sidenav"] is not None:               # a held key repeats fast: lay the cards out once it settles
                root.after_cancel(timers["sidenav"])
            timers["sidenav"] = root.after(120, commit_side_nav)
        return "break"

    def leave_sidebar(_event=None) -> str:
        canvas.focus_set()
        return "break"

    def enter_sidebar(_event=None) -> str:
        series_box.focus_set()
        return "break"

    # -----------------------------------------------------------------------
    # renaming a folder (right click on it in the sidebar, or the rename shortcut while the pointer is on it)
    # -----------------------------------------------------------------------
    folder_edit: dict = {"entry": None, "old": "", "after": None}

    def cancel_folder_edit(_event=None) -> str:
        if folder_edit["after"] is not None:
            try:
                root.after_cancel(folder_edit["after"])
            except tk.TclError:
                pass
            folder_edit["after"] = None
        entry = folder_edit["entry"]
        folder_edit["entry"] = None
        if entry is not None:
            try:
                entry.destroy()
            except tk.TclError:
                pass
        return "break"

    def start_folder_rename(name: str) -> None:
        """Open the in-place input over the sidebar row of the folder `name`."""
        if name not in series_names:
            return
        cancel_edit()
        cancel_folder_edit()
        index = series_names.index(name)
        series_box.see(index)
        _x, y, width, height = series_box.row_box(index)
        entry = tk.Entry(series_box, font=f_side, bg=pal["input"], fg=pal["text"], insertbackground=pal["text"],
                         relief="flat", highlightthickness=1, highlightbackground=pal["border"],
                         highlightcolor=pal["accent"])
        entry.insert(0, name)
        entry.select_range(0, "end")
        entry.place(x=0, y=y, width=width, height=height)
        folder_edit.update(entry=entry, old=name)
        entry.bind("<Return>", confirm_folder_rename)
        entry.bind("<KP_Enter>", confirm_folder_rename)
        entry.bind("<Escape>", lambda _e: (cancel_folder_edit(), canvas.focus_set(), "break")[2])
        entry.bind("<FocusOut>", on_folder_entry_focus_out)
        entry.focus_set()

    def on_folder_entry_focus_out(_event=None) -> None:
        # Clicking elsewhere in the window ends the edit; the window being deactivated (Alt+Tab) doesn't.
        def check() -> None:
            folder_edit["after"] = None
            entry = folder_edit["entry"]
            if entry is None:
                return
            try:
                focused = root.focus_get()
            except (KeyError, tk.TclError):
                focused = None
            if focused is not None and focused is not entry:
                cancel_folder_edit()
        if folder_edit["after"] is None:
            folder_edit["after"] = root.after(120, check)

    def confirm_folder_rename(_event=None) -> str:
        entry, old = folder_edit["entry"], folder_edit["old"]
        if entry is None:
            return "break"
        text = entry.get().strip()
        cancel_folder_edit()
        canvas.focus_set()
        if not text or text == old:
            return "break"                                  # blank / unchanged: nothing to do
        new = clean_file_stem(text)
        if not new or not is_plain_dir(new):                # (a leading "_" or "." would hide the folder)
            status.configure(text=t("dialog.manual.folder_rename_invalid", new=text))
            return "break"
        roots = {item.root for item in model.items}
        try:
            renamed = rename_series_folders(roots, old, new)
        except FolderNameTaken:
            status.configure(text=t("dialog.manual.folder_rename_exists", new=new))
            return "break"
        except OSError as e:
            logger.warning("LIBRARY", f"Could not rename the folder {old}: {e}")
            status.configure(text=t("dialog.manual.folder_rename_failed", name=old))
            return "break"
        if not renamed:                                     # it vanished from the disk meanwhile
            status.configure(text=t("dialog.manual.folder_rename_failed", name=old))
            return "break"
        logger.success("LIBRARY", f"Renamed the folder {old} -> {new}")
        paths: dict[Path, Path] = {}
        for item in model.items:
            if (item.series or "").casefold() == old.casefold():
                rest = item.path.relative_to(item.root).parts[1:]
                paths[item.path] = item.root.joinpath(new, *rest)
        for src, dest in paths.items():
            if src in pil_cache:                            # the covers follow the files
                pil_cache[dest] = pil_cache.pop(src)
        model.series_renamed(old, new, paths)
        if tab.folder_renamed is not None:
            tab.folder_renamed(old, new, paths)             # suggestions and caches
        refresh_sidebar()
        relayout(keep_scroll=True)
        status.configure(text=t("dialog.manual.folder_renamed", old=old, new=new))
        return "break"

    def on_series_context(event) -> None:
        """Right click on a folder of the sidebar: Rename."""
        index = series_box.row_index_at(event.y)
        if index is None or index >= len(series_names):
            return
        name = series_names[index]
        if context_menu["close"] is not None:
            context_menu["close"]()
        entries = [(t("dialog.library.menu_rename_folder"), shortcuts.combo(ACTION_RENAME),
                    lambda: root.after(20, lambda: start_folder_rename(name)))]    # once the menu is gone
        context_menu["close"] = open_popup_menu(
            series_box, event.x_root, event.y_root, entries,
            {key: pair(name_) for key, name_ in (("bg", "tile"), ("border", "border"), ("hover", "sel"),
                                                 ("text", "text"), ("muted", "muted"))})

    # -----------------------------------------------------------------------
    # editing a card's series
    # -----------------------------------------------------------------------
    def cancel_edit(_event=None) -> str:
        if edit["after"] is not None:
            try:
                root.after_cancel(edit["after"])
            except tk.TclError:
                pass
            edit["after"] = None
        for key in ("lb", "entry"):
            widget = edit[key]
            edit[key] = None
            if widget is not None:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
        canvas.delete("editor")
        edit["idx"] = None
        edit["field"] = "series"
        edit["suggestions"] = []
        return "break"

    def cancel_and_focus(_event=None) -> str:
        cancel_edit()
        canvas.focus_set()                         # so the arrow keys / WASD / Enter work again
        return "break"

    def selected_suggestion() -> str | None:
        lb = edit["lb"]
        if lb is None:
            return None
        sel = lb.curselection()
        return edit["suggestions"][sel[0]] if sel and sel[0] < len(edit["suggestions"]) else None

    def refresh_suggestions() -> None:
        entry, idx = edit["entry"], edit["idx"]
        if entry is None or idx is None or edit["field"] != "series":     # a file name has no suggestions
            return
        try:
            names = list(suggest(entry.get()))[:_MAX_SUGGESTIONS]
        except Exception:
            names = []
        edit["suggestions"] = names
        canvas.delete("suggestions")
        lb = edit["lb"]
        if lb is not None:
            edit["lb"] = None
            lb.destroy()
        if not names:
            return
        lb = tk.Listbox(canvas, height=min(len(names), _SHOWN_SUGGESTIONS), bg=pal["input"], fg=pal["text"],
                        selectbackground=pal["sel"], selectforeground=pal["text"], activestyle="none",
                        exportselection=False, relief="flat", borderwidth=0, highlightthickness=1,
                        highlightbackground=pal["border"], highlightcolor=pal["border"], font=f_series, takefocus=0)
        for name in names:
            lb.insert("end", name)
        lb.bind("<Button-1>", on_suggestion_click)      # handled here so the entry never loses focus
        edit["lb"] = lb
        place_suggestions()

    def place_suggestions() -> None:
        lb, entry = edit["lb"], edit["entry"]
        if lb is None or entry is None:
            return
        canvas.delete("suggestions")
        tx, ty = tile_xy(edit["idx"])
        ex, ey = tx + lay["pad"], ty + lay["series_y"] - px(1)
        entry_h = row_series
        lb.update_idletasks()
        width = max(lay["tile_w"] - 2 * lay["pad"], px(240))
        height = lb.winfo_reqheight()
        view_top, view_bottom = canvas.canvasy(0), canvas.canvasy(0) + canvas.winfo_height()
        x = max(canvas.canvasx(0) + px(4), min(ex, canvas.canvasx(0) + canvas.winfo_width() - width - px(4)))
        if ey + entry_h + height > view_bottom and ey - height >= view_top:
            canvas.create_window(x, ey, anchor="sw", window=lb, width=width, height=height, tags="suggestions")
        else:
            canvas.create_window(x, ey + entry_h, anchor="nw", window=lb, width=width, height=height,
                                 tags="suggestions")

    def on_suggestion_click(event) -> str:
        lb = edit["lb"]
        if lb is not None:
            i = lb.nearest(event.y)
            if 0 <= i < len(edit["suggestions"]):
                confirm(edit["suggestions"][i])
        return "break"

    def move_selection(step: int):
        def handler(_event=None) -> str:
            lb = edit["lb"]
            if lb is not None and lb.size():
                sel = lb.curselection()
                nxt = 0 if not sel and step > 0 else (lb.size() - 1 if not sel else sel[0] + step)
                if 0 <= nxt < lb.size():
                    lb.selection_clear(0, "end")
                    lb.selection_set(nxt)
                    lb.see(nxt)
            return "break"
        return handler

    def on_tab(_event=None) -> str:
        entry = edit["entry"]
        pick = selected_suggestion() or (edit["suggestions"][0] if edit["suggestions"] else None)
        if entry is not None and pick:
            entry.delete(0, "end")
            entry.insert(0, pick)
            refresh_suggestions()
        return "break"

    def on_entry_key(event) -> None:
        if event.keysym not in ("Up", "Down", "Return", "Escape", "Tab", "Shift_L", "Shift_R", "Control_L",
                                "Control_R", "Alt_L", "Alt_R"):
            refresh_suggestions()

    def on_entry_focus_out(_event=None) -> None:
        # Clicking elsewhere in this window ends the edit. Focus going to "nothing" means the
        # whole window was deactivated (Alt+Tab), which must not cancel it.
        def check() -> None:
            edit["after"] = None
            entry = edit["entry"]
            if entry is None:
                return
            try:
                focused = root.focus_get()
            except (KeyError, tk.TclError):
                focused = None
            if focused is not None and focused is not entry:
                cancel_edit()
        if edit["after"] is None:
            edit["after"] = root.after(120, check)

    def start_edit(idx: int, field: str = "series") -> None:
        """Open the in-place input on a card: its series (the default) or its file name."""
        cancel_edit()
        ensure_visible(idx)
        item = lay["items"][idx]
        tx, ty = tile_xy(idx)
        renaming = field == "name"
        entry = tk.Entry(canvas, font=f_name if renaming else f_series, bg=pal["input"], fg=pal["text"],
                         insertbackground=pal["text"], relief="flat", highlightthickness=1,
                         highlightbackground=pal["border"], highlightcolor=pal["accent"])
        entry.insert(0, item.path.stem if renaming else (item.series or ""))     # the extension stays as it is
        entry.select_range(0, "end")
        edit.update(idx=idx, entry=entry, field=field)
        if renaming:                                # over the name line (a little taller than the text)
            y, height = ty + lay["img_h"] + px(5), lh_name + px(5)
        else:
            y, height = ty + lay["series_y"] - px(1), row_series
        canvas.create_window(tx + lay["pad"], y, anchor="nw", window=entry,
                             width=lay["tile_w"] - 2 * lay["pad"], height=height, tags="editor")
        entry.bind("<Return>", lambda _e: (confirm(), "break")[1])
        entry.bind("<KP_Enter>", lambda _e: (confirm(), "break")[1])
        entry.bind("<Escape>", cancel_and_focus)
        entry.bind("<Down>", move_selection(+1))
        entry.bind("<Up>", move_selection(-1))
        entry.bind("<Tab>", on_tab)
        entry.bind("<KeyRelease>", on_entry_key)
        entry.bind("<FocusOut>", on_entry_focus_out)
        entry.focus_set()
        if not renaming:
            refresh_suggestions()

    def confirm(text: str | None = None) -> None:
        idx, entry = edit["idx"], edit["entry"]
        if idx is None or entry is None:
            return
        if edit["field"] == "name":
            confirm_rename(idx, entry.get())
            return
        if text is None:
            text = selected_suggestion() or entry.get()
        text = text.strip()
        item = lay["items"][idx]
        cancel_edit()
        canvas.focus_set()
        if not text and item.series is None:
            return                                          # blank and not in a folder: nothing to do
        series = assign_one(item, text)
        if series is None:
            status.configure(text=txt.move_failed.format(name=item.name))
            return
        finish_assignment(idx)
        status.configure(text=txt.moved.format(name=item.name, series=series))

    def confirm_rename(idx: int, text: str) -> None:
        """Rename the file of the card at `idx` to `text` (its extension is kept)."""
        item = lay["items"][idx]
        cancel_edit()
        canvas.focus_set()
        old_path = item.path
        stem = clean_file_stem(text, old_path.suffix)
        if not stem:
            if text.strip():                                # typed something, but it can't be a file name
                status.configure(text=t("dialog.manual.rename_invalid", new=text.strip()))
            return
        if stem == old_path.stem:
            return                                          # unchanged
        try:
            new_path = rename_card(old_path, stem)
        except FileExistsError:
            status.configure(text=t("dialog.manual.rename_exists", new=stem + old_path.suffix))
            return
        except OSError as e:
            logger.warning("LIBRARY", f"Could not rename {old_path.name}: {e}")
            status.configure(text=t("dialog.manual.rename_failed", name=old_path.name))
            return
        logger.success("LIBRARY", f"Renamed {old_path.name} -> {new_path.name}")
        if old_path in pil_cache:                           # the cover follows the file
            pil_cache[new_path] = pil_cache.pop(old_path)
        model.renamed(item, new_path)
        relayout(keep_scroll=True)                          # the sort order may have changed
        new_idx = index_of(item)
        if new_idx is not None:                             # (a search may have filtered it out)
            select_only(item)
            ensure_visible(new_idx)
        status.configure(text=t("dialog.manual.renamed", old=old_path.name, new=new_path.name))

    def assign_one(item, text: str) -> str | None:
        """Move `item` into the series `text` (via `assign`) and update the model; the series
        folder it ended up in, or None if it couldn't be moved."""
        result = assign(item, text)
        if result is None:
            return None
        new_path, series = result
        old_path = item.path
        if new_path != old_path:
            if old_path in pil_cache:                      # the cover follows the card
                pil_cache[new_path] = pil_cache.pop(old_path)
            moved["count"] += 1
        # blank text = moved up out of its folder: the card is untagged again
        model.assigned(item, new_path, series if text.strip() else None)
        return series

    def finish_assignment(first_idx: int) -> None:
        """Refresh sidebar and grid after cards were moved. A card that left this view (e.g.
        Unknown series) takes the selection with it, so the next card (at `first_idx`) is selected."""
        refresh_sidebar()
        if ((model.mode == VIEW_SERIES and model.series_count(model.series_filter) == 0)
                or (model.mode == VIEW_TO_DELETE and model.to_delete_count() == 0)
                or (model.mode == VIEW_TO_ARCHIVE and model.to_archive_count() == 0)):
            model.set_view(VIEW_ALL)
            refresh_sidebar()
        relayout(keep_scroll=True)
        items = lay["items"]
        if sel["item"] is None and items:
            nxt = min(first_idx, len(items) - 1)
            select_only(items[nxt])
            ensure_visible(nxt)
        elif sel["item"] is not None:
            select_only(sel["item"])                       # back to the one card

    # -----------------------------------------------------------------------
    # events
    # -----------------------------------------------------------------------
    def on_click(event) -> None:
        hit = locate(event.x, event.y)
        if edit["idx"] is not None:
            cancel_edit()
        canvas.focus_set()
        if hit is None:
            return
        idx, in_series, in_name = hit
        item = lay["items"][idx]
        if event.state & 0x4:                      # Ctrl+click adds / removes the card
            picked = dict(sel["picked"])
            if id(item) in picked:
                del picked[id(item)]
            else:
                picked[id(item)] = item
            apply_selection(item, picked.values())
            return
        select_only(item)
        if model.mode in HELD_VIEWS:
            return
        if in_series:
            start_edit(idx)
        elif in_name:
            start_edit(idx, "name")

    def mark_to_delete_cards(targets: list) -> None:
        first_idx = min((i for i in (index_of(item) for item in targets) if i is not None), default=0)
        model.mark_to_delete(targets)
        finish_assignment(first_idx)
        status.configure(text=t("dialog.library.marked_to_delete", count=len(targets)))

    def mark_to_archive_cards(targets: list) -> None:
        first_idx = min((i for i in (index_of(item) for item in targets) if i is not None), default=0)
        model.mark_to_archive(targets)
        finish_assignment(first_idx)
        status.configure(text=t("dialog.library.marked_to_archive", count=len(targets)))

    def restore_cards(targets: list) -> None:
        first_idx = min((i for i in (index_of(item) for item in targets) if i is not None), default=0)
        model.restore(targets)
        finish_assignment(first_idx)
        status.configure(text=t("dialog.library.restored", count=len(targets)))

    def show_in_explorer(targets: list) -> None:
        """Open the file manager with the (first) card of `targets` selected."""
        if not targets:
            return
        from kkafio.dialogs.similar_chara import reveal_in_folder
        reveal_in_folder(targets[0].path)

    context_menu: dict = {"close": None}                    # closes the right-click menu that is open

    def on_context_menu(event) -> None:
        """Right click on a card: Mark to delete and Mark to archive (just Restore in the To Delete and To Archive
        views), plus Show in Explorer, for the selected cards when the card is one of them, else for that card alone."""
        hit = locate(event.x, event.y)
        if edit["idx"] is not None:
            cancel_edit()
        canvas.focus_set()
        if hit is None:
            return
        item = lay["items"][hit[0]]
        if id(item) not in sel["picked"]:
            select_only(item)
        targets = picked_in_order() or [item]
        suffix = f" ({len(targets)})" if len(targets) > 1 else ""
        if context_menu["close"] is not None:
            context_menu["close"]()                             # one menu at a time
        explorer_entry = (t("dialog.library.menu_explorer"), shortcuts.combo(ACTION_EXPLORER),
                          lambda: show_in_explorer(targets))
        if model.mode in HELD_VIEWS:
            entries = [explorer_entry,
                       (t("dialog.library.menu_restore") + suffix, shortcuts.combo(ACTION_RESTORE),
                        lambda: restore_cards(targets))]
        else:
            entries = [explorer_entry,
                       (t("dialog.library.menu_to_archive") + suffix, shortcuts.combo(ACTION_ARCHIVE),
                        lambda: mark_to_archive_cards(targets)),
                       (t("dialog.library.menu_to_delete") + suffix, shortcuts.combo(ACTION_DELETE),
                        lambda: mark_to_delete_cards(targets))]
        context_menu["close"] = open_popup_menu(
            canvas, event.x_root, event.y_root, entries,
            {key: pair(name) for key, name in (("bg", "tile"), ("border", "border"), ("hover", "sel"),
                                               ("text", "text"), ("muted", "muted"))})

    def change_folder() -> None:
        """The "change folder" shortcut: the folder input of the selected card (one dialog for
        several)."""
        items = lay["items"]
        if not items or model.mode in HELD_VIEWS:           # a card in To Delete / To Archive isn't sorted or renamed
            return
        targets = picked_in_order()
        if len(targets) > 1:
            open_multi_dialog(targets)
            return
        if targets:
            idx = index_of(targets[0])
        else:
            idx = index_of(sel["item"]) if sel["item"] is not None else first_visible_index()
            select_only(items[idx])
        start_edit(idx)

    def rename_card_shortcut() -> None:
        """The "rename" shortcut: the file name input of the cursor card (or the first selected)."""
        items = lay["items"]
        if not items or model.mode in HELD_VIEWS:
            return
        item = sel["item"] if sel["item"] is not None else (
            picked_in_order()[0] if sel["picked"] else items[first_visible_index()])
        select_only(item)
        idx = index_of(item)
        if idx is not None:
            start_edit(idx, "name")

    def delete_shortcut() -> None:
        """The "delete" shortcut: the selected cards go to To Delete (nothing in the To Delete and To Archive
        views)."""
        if model.mode in HELD_VIEWS:
            return
        targets = picked_in_order() or ([sel["item"]] if sel["item"] is not None else [])
        if targets:
            mark_to_delete_cards(targets)

    def archive_shortcut() -> None:
        """The "archive" shortcut: the selected cards go to To Archive (nothing in the To Delete
        and To Archive views)."""
        if model.mode in HELD_VIEWS:
            return
        targets = picked_in_order() or ([sel["item"]] if sel["item"] is not None else [])
        if targets:
            mark_to_archive_cards(targets)

    def restore_shortcut() -> None:
        """The "restore" shortcut: the selected cards leave To Delete or To Archive (only in
        those two views)."""
        if model.mode not in HELD_VIEWS:
            return
        targets = picked_in_order() or ([sel["item"]] if sel["item"] is not None else [])
        if targets:
            restore_cards(targets)

    def explorer_shortcut() -> None:
        """The "show in explorer" shortcut: the cursor card (or the first selected) in the file manager."""
        targets = picked_in_order() or ([sel["item"]] if sel["item"] is not None else [])
        show_in_explorer(targets)

    shortcut_actions = {ACTION_RENAME: rename_card_shortcut, ACTION_FOLDER: change_folder,
                        ACTION_DELETE: delete_shortcut, ACTION_ARCHIVE: archive_shortcut,
                        ACTION_RESTORE: restore_shortcut, ACTION_EXPLORER: explorer_shortcut}

    def on_key(event) -> str | None:
        """Any key the library has no binding of its own for: the user's shortcuts (the settings
        are read on every press, so a change in the Shortcuts dialog applies at once)."""
        action = shortcuts.action_for(combo_from_event(event.keysym, event.state) or "")
        if action is None:
            return None
        if action == ACTION_RENAME:                         # the pointer is on a folder of the sidebar: rename that
            hovered = series_box.hovered_index()
            if hovered is not None and hovered < len(series_names):
                start_folder_rename(series_names[hovered])
                return "break"
        shortcut_actions[action]()
        return "break"

    def nav(dx: int, dy: int):
        def handler(event) -> str:
            if dx < 0 and not event.state & 0x4:
                cur = index_of(sel["item"])
                if cur is not None and cur % max(1, lay["cols"]) == 0:           # Left / A past the first column:
                    return enter_sidebar()                                       # on to the sidebar
            move_card_selection(dx, dy, keep_picked=bool(event.state & 0x4))     # Ctrl: cursor only
            return "break"
        return handler

    def on_space(_event=None) -> str:
        toggle_cursor()
        return "break"

    def on_select_all(_event=None) -> str:
        select_all()
        return "break"

    def on_escape(_event=None) -> str | None:
        """Esc on the library: a multi-selection shrinks to the cursor card; otherwise it closes."""
        cur = sel["item"]
        if len(sel["picked"]) > 1 or (cur is not None and id(cur) not in sel["picked"] and sel["picked"]):
            select_only(cur if cur is not None else picked_in_order()[0])
            return "break"
        return None

    # ---- one series for several cards ---------------------------------------------------------------------
    def open_multi_dialog(targets: list) -> None:
        """A small dialog with the series input for all of `targets`; Enter moves them."""
        if multi["dlg"] is not None:
            return
        cancel_edit()
        count = len(targets)
        dlg = ctk.CTkToplevel(root)
        multi["dlg"] = dlg
        dlg.title(txt.multi_title)
        dlg.transient(root)
        dlg.resizable(False, False)
        dlg.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(dlg, text=txt.multi_prompt.format(count=count), anchor="w").grid(
            row=0, column=0, padx=16, pady=(16, 6), sticky="ew")
        entry = ctk.CTkEntry(dlg, width=px(420), height=34)
        entry.grid(row=1, column=0, padx=16, sticky="ew")
        box = tk.Listbox(dlg, height=_SHOWN_SUGGESTIONS, bg=pal["input"], fg=pal["text"],
                         selectbackground=pal["sel"], selectforeground=pal["text"], activestyle="none",
                         exportselection=False, relief="flat", borderwidth=0, highlightthickness=1,
                         highlightbackground=pal["border"], highlightcolor=pal["border"], font=f_series, takefocus=0)
        names: list[str] = []

        def refresh(_event=None) -> None:
            names[:] = list(suggest(entry.get()))[:_MAX_SUGGESTIONS]
            box.delete(0, "end")
            for name in names:
                box.insert("end", name)
            if names:
                box.configure(height=min(len(names), _SHOWN_SUGGESTIONS))
                box.grid(row=2, column=0, padx=16, pady=(4, 0), sticky="ew")
            else:
                box.grid_remove()

        def chosen() -> str | None:
            sel_idx = box.curselection()
            return names[sel_idx[0]] if sel_idx and sel_idx[0] < len(names) else None

        def close() -> None:
            multi["dlg"] = None
            try:
                dlg.grab_release()
            except tk.TclError:
                pass
            dlg.destroy()
            canvas.focus_set()

        def move_choice(step: int):
            def handler(_e=None) -> str:
                if box.size():
                    cur = box.curselection()
                    nxt = (0 if step > 0 else box.size() - 1) if not cur else cur[0] + step
                    if 0 <= nxt < box.size():
                        box.selection_clear(0, "end")
                        box.selection_set(nxt)
                        box.see(nxt)
                return "break"
            return handler

        def complete(_e=None) -> str:
            pick = chosen() or (names[0] if names else None)
            if pick:
                entry.delete(0, "end")
                entry.insert(0, pick)
                refresh()
            return "break"

        def confirm_multi(text: str | None = None) -> str:
            text = (text if text is not None else (chosen() or entry.get())).strip()
            if not text:                                    # blank: move the ones in a folder up
                targets_in_folder = [i for i in targets if i.series is not None]
                if not targets_in_folder:
                    return "break"                          # none is in a folder: nothing to do
                close()
                apply_many(targets_in_folder, text)
                return "break"
            close()
            apply_many(targets, text)
            return "break"

        def click_choice(event) -> str:
            i = box.nearest(event.y)
            if 0 <= i < len(names):
                confirm_multi(names[i])
            return "break"

        def on_return(_event=None) -> str:                  # Tk passes the event; confirm_multi takes a text
            return confirm_multi()

        buttons = ctk.CTkFrame(dlg, fg_color="transparent")
        buttons.grid(row=3, column=0, padx=16, pady=16, sticky="e")
        ctk.CTkButton(buttons, text=t("dialog.manual.multi_cancel"), width=90, fg_color=pair("sel"),
                      text_color=pair("text"), hover_color=pair("border"), command=close).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(buttons, text=t("dialog.manual.multi_ok"), width=90, command=on_return).grid(row=0, column=1)

        for seq, fn in (("<Return>", on_return), ("<KP_Enter>", on_return), ("<Escape>", lambda _e: (close(), "break")[1]),
                        ("<Down>", move_choice(+1)), ("<Up>", move_choice(-1)), ("<Tab>", complete)):
            entry.bind(seq, fn)
        entry.bind("<KeyRelease>", lambda e: refresh() if e.keysym not in (
            "Up", "Down", "Return", "Escape", "Tab", "Shift_L", "Shift_R", "Control_L", "Control_R") else None)
        box.bind("<Button-1>", click_choice)
        dlg.protocol("WM_DELETE_WINDOW", close)
        refresh()

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - w) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - h) // 3)
        dlg.geometry(f"+{x}+{y}")

        def focus_it() -> None:
            try:
                dlg.grab_set()
            except tk.TclError:
                pass
            entry.focus_force()
        dlg.after(120, focus_it)

    def apply_many(targets: list, text: str) -> None:
        first_idx = min((i for i in (index_of(item) for item in targets) if i is not None), default=0)
        done, failed, series = 0, 0, ""
        for item in targets:
            result = assign_one(item, text)
            if result is None:
                failed += 1
            else:
                done, series = done + 1, result
        finish_assignment(first_idx)
        if not done:
            status.configure(text=txt.move_failed.format(name=targets[0].name))
        elif failed:
            status.configure(text=txt.multi_moved_partial.format(count=done, series=series, failed=failed))
        else:
            status.configure(text=txt.multi_moved.format(count=done, series=series))

    def on_motion(event) -> None:
        hit = locate(event.x, event.y)
        canvas.configure(cursor="hand2" if hit is not None and (hit[1] or hit[2]) and model.mode not in HELD_VIEWS else "")

    def on_wheel(event) -> None:
        try:
            target = root.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        if target is not canvas:                       # the lists scroll themselves
            return
        if getattr(event, "num", 0) == 4:
            step = -1
        elif getattr(event, "num", 0) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        canvas.yview_scroll(step * 3, "units")

    def scroll_key(units=None, pages=None, home=None):
        def handler(_event=None) -> str | None:
            if edit["idx"] is not None:
                return None
            if home is not None:
                canvas.yview_moveto(home)
            elif pages is not None:
                canvas.yview_scroll(pages, "pages")
            else:
                canvas.yview_scroll(units, "units")
            return "break"
        return handler

    def on_yscroll(first, last) -> None:
        scroll_sb.set(first, last)
        schedule_draw()
        if edit["idx"] is not None:
            place_suggestions()

    def on_sort() -> None:
        model.sort_key = sort_labels.get(sort_menu.get(), SORT_UPDATED)
        now.sort = model.sort_key
        relayout(keep_scroll=False)

    def on_order() -> None:
        model.descending = not model.descending
        now.descending = model.descending
        order_btn.configure(text=t("dialog.manual.descending") if model.descending else t("dialog.manual.ascending"))
        relayout(keep_scroll=False)

    def on_size() -> None:
        now.size = float(size_slider.get())
        schedule_layout(60, keep_scroll=True)

    def on_search_key(_event=None) -> None:
        if timers["search"] is not None:
            root.after_cancel(timers["search"])

        def apply() -> None:
            timers["search"] = None
            model.search = search_entry.get()
            relayout(keep_scroll=False)
        timers["search"] = root.after(180, apply)

    def on_series_filter_key(_event=None) -> None:
        if timers["sidebar"] is not None:
            root.after_cancel(timers["sidebar"])
        timers["sidebar"] = root.after(150, refresh_sidebar)

    def on_canvas_configure(_event=None) -> None:
        schedule_layout(80, keep_scroll=True)

    def close_popups() -> None:
        if multi["dlg"] is not None:
            try:
                multi["dlg"].destroy()
            except tk.TclError:
                pass
            multi["dlg"] = None

    def dispose() -> None:
        for name, after_id in timers.items():
            if after_id is not None:
                try:
                    root.after_cancel(after_id)
                except tk.TclError:
                    pass
        if edit["after"] is not None:
            try:
                root.after_cancel(edit["after"])
            except tk.TclError:
                pass
        scan["stop"] = True
        scan_executor.shutdown(wait=False, cancel_futures=True)
        executor.shutdown(wait=False, cancel_futures=True)

    def on_show() -> None:
        schedule_layout(0, keep_scroll=True)       # the window may have been resized while it was hidden

    def on_hide() -> None:
        cancel_edit()
        if timers["search"] is not None:           # typed just before switching tabs: it belongs to this tab
            root.after_cancel(timers["search"])
            timers["search"] = None
            model.search = search_entry.get()

    canvas.configure(yscrollcommand=on_yscroll)
    canvas.bind("<Button-1>", on_click)
    canvas.bind("<Button-3>", on_context_menu)                   # right click
    if root.tk.call("tk", "windowingsystem") == "aqua":
        canvas.bind("<Button-2>", on_context_menu)               # macOS: the right button is Button-2
    canvas.bind("<Motion>", on_motion)
    canvas.bind("<Configure>", on_canvas_configure)
    canvas.bind("<Prior>", scroll_key(pages=-1))
    canvas.bind("<Next>", scroll_key(pages=1))
    for keys, dx, dy in ((("Left", "a", "A"), -1, 0), (("Right", "d", "D"), 1, 0),
                         (("Up", "w", "W"), 0, -1), (("Down", "s", "S"), 0, 1)):
        for key in keys:
            canvas.bind(f"<{key}>", nav(dx, dy))
    canvas.bind("<Tab>", enter_sidebar)              # Tab goes to the sidebar; Tab there comes back
    canvas.bind("<space>", on_space)
    canvas.bind("<Control-space>", on_space)
    canvas.bind("<Control-a>", on_select_all)
    canvas.bind("<Control-A>", on_select_all)
    canvas.bind("<Escape>", on_escape)
    canvas.bind("<KeyPress>", on_key)                # the shortcuts of the Shortcuts dialog (Enter, F2, Delete ...)
    canvas.bind("<Home>", scroll_key(home=0.0))
    canvas.bind("<End>", scroll_key(home=1.0))
    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind(seq, on_wheel, add="+")          # every tab listens; only the one under the pointer acts
    series_box.bind("<<ListboxSelect>>", on_series_pick)
    for key in ("Up", "w", "W"):
        series_box.bind(f"<{key}>", lambda _e: side_step(-1))
    for key in ("Down", "s", "S"):
        series_box.bind(f"<{key}>", lambda _e: side_step(1))
    for key in ("Right", "d", "D", "Tab"):
        series_box.bind(f"<{key}>", leave_sidebar)
    series_box.bind("<FocusIn>", lambda _e: on_side_focus(True))
    series_box.bind("<FocusOut>", lambda _e: on_side_focus(False))
    series_box.on_scroll = cancel_folder_edit
    series_box.bind("<Button-3>", on_series_context)             # right click
    if root.tk.call("tk", "windowingsystem") == "aqua":
        series_box.bind("<Button-2>", on_series_context)         # macOS: the right button is Button-2
    series_filter_entry.bind("<KeyRelease>", on_series_filter_key)

    refresh_sidebar()
    if model.mods_filter != MODS_ALL:
        start_scan()                               # a restored filter needs the cards checked
    timers["poll"] = root.after(50, poll_covers)
    if active:
        schedule_layout(0, keep_scroll=False)      # a tab that isn't open lays out when it is first shown

    handle.canvas = canvas
    handle.cursor = lambda: sel["item"]
    handle.search_text = lambda: model.search
    handle.on_search_key = on_search_key
    handle.on_show = on_show
    handle.on_hide = on_hide
    handle.close_popups = close_popups
    handle.dispose = dispose
    handle.moved_count = lambda: moved["count"]
    handle.view = lambda: TabView(now.sort, now.descending, now.size, now.mods)
    handle.to_delete_items = lambda: [i for i in model.items if i.to_delete]
    handle.to_archive_items = lambda: [i for i in model.items if i.to_archive]
    return handle
