"""
similar_chara.py — The review dialog of Review Similar Characters.

Shows groups of character cards that look like the same character saved more
than once. For every card there is a lock, a checkbox ("remove this one"), a
thumbnail of its cover, its file details and a Details button.

How the lock works (one lock per group, in the group's header):
  * Every group starts unlocked (🔓).
  * Ticking or unticking any card in a group is a decision about that group,
    so it locks the group (🔒). The lock can also be clicked by hand.
  * "Auto-select" fills in every *remaining* group — one that isn't locked — by
    keeping one card (chosen by the strategy in its dropdown) and ticking the
    others. Locked groups are left alone. Auto-select never locks anything, so
    it can be re-applied with another strategy until the user is happy.
  * "Undo auto-select" steps back one Auto-select at a time. It only touches groups
    that are still unlocked, so choices you made by hand since then are kept.
  * Keys: Left / Right (A / D) scroll a page, Up / Down (W / S) scroll a little.

The list is drawn like the Manage Library grid: one plain Tk canvas, only the rows in view exist
(a group's header, one row per card), and thumbnails are decoded on worker threads. So any
number of groups can be shown in one scrolling list without lag; there are no pages.

`review_dialog()` returns the cards the user chose to trash (empty if the
dialog was closed without trashing). The dialog itself never deletes anything.

The selection logic lives in `ReviewState`, which has no GUI dependency; the
CustomTkinter window (see common.py for the shared window behaviour)
is built inside `_ctk_dialog()`.
"""

from __future__ import annotations

import io
import os
import queue
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kkafio.core.errors import TaskFailedError
from kkafio.core.i18n import t
from kkafio.dialogs.common import format_size, maximize, new_window, show_and_wait


# ---------------------------------------------------------------------------
# Data + selection state (no GUI)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReviewGroup:
    """One set of cards that look like the same character."""

    label: str                 # shown after "N files · " in the group header
    description: str           # one line under the header
    paths: tuple[Path, ...]


class ReviewState:
    """Which cards are ticked, and which groups are locked."""

    def __init__(self, groups: Sequence[ReviewGroup],
                 select_keep: Callable[[list[Path], str], Path | None]):
        self.groups = list(groups)
        self._select_keep = select_keep
        self.selected = [[False] * len(g.paths) for g in self.groups]
        self.locked = [False] * len(self.groups)         # one lock per group
        self._undo: list[dict[int, list[bool]]] = []     # per Auto-select: group -> ticks before it

    def set_selected(self, gi: int, ci: int, value: bool, *, lock: bool = True) -> None:
        """Tick/untick a card. By default this also locks its group: the user made a choice."""
        self.selected[gi][ci] = bool(value)
        if lock:
            self.locked[gi] = True

    def set_locked(self, gi: int, value: bool) -> None:
        self.locked[gi] = bool(value)

    def is_remaining(self, gi: int) -> bool:
        """A group is still open for Auto-select while it isn't locked."""
        return not self.locked[gi]

    def apply_keep(self, keep: str) -> tuple[int, int, int]:
        """Auto-select every remaining group using strategy `keep`: the card the
        strategy picks stays unticked, all the others are ticked (`None` -> the
        strategy picks nothing, so every card is ticked).

        Returns (groups updated, groups skipped because they're locked,
        groups that couldn't be evaluated, e.g. a file vanished)."""
        applied = skipped = failed = 0
        before: dict[int, list[bool]] = {}
        for gi, group in enumerate(self.groups):
            if not self.is_remaining(gi):
                skipped += 1
                continue
            try:
                keep_path = self._select_keep(list(group.paths), keep)
            except OSError:
                failed += 1
                continue
            before[gi] = list(self.selected[gi])
            self.selected[gi] = [p != keep_path for p in group.paths]
            applied += 1
        if before:
            self._undo.append(before)
        return applied, skipped, failed

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo_auto(self) -> tuple[int, int]:
        """Take back the most recent Auto-select. Returns (groups restored,
        groups left as they are because the user has since made a choice in
        them, i.e. they're locked)."""
        if not self._undo:
            return 0, 0
        restored = kept = 0
        for gi, ticks in self._undo.pop().items():
            if self.locked[gi]:
                kept += 1
            else:
                self.selected[gi] = ticks
                restored += 1
        return restored, kept

    # -- results ------------------------------------------------------------

    def selected_paths(self) -> list[Path]:
        return [p for gi, g in enumerate(self.groups)
                for ci, p in enumerate(g.paths) if self.selected[gi][ci]]

    def selected_count(self) -> int:
        return sum(sum(row) for row in self.selected)

    def fully_selected_groups(self) -> list[int]:
        """Groups where *every* card is ticked, i.e. no copy would survive."""
        return [gi for gi, row in enumerate(self.selected) if row and all(row)]


# ---------------------------------------------------------------------------
# Small helpers (no GUI)
# ---------------------------------------------------------------------------

THUMB_MAX = (100, 140)
DETAILS_MAX = (340, 460)       # the cover in the Details window is scaled to fit this box

THUMB_CACHE_MAX = 600          # cover images kept as PhotoImages (least recently drawn go first)


def format_date(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y/%m/%d")
    except (OverflowError, OSError, ValueError):
        return "?"


def created_timestamp(st: os.stat_result) -> float:
    """File creation time (st_birthtime where it exists, else st_ctime — which
    is the creation time on Windows)."""
    return getattr(st, "st_birthtime", st.st_ctime)


def load_thumbnail(path: Path, box: tuple[int, int] = THUMB_MAX):
    """The card's cover as a small RGBA PIL image fitting `box`, or None. Safe to call
    from a worker thread (touches no Tk objects). Reads only the PNG part of the file."""
    from PIL import Image
    from kkafio.cards.chara.similarity import read_png_preview

    data = read_png_preview(path)
    if not data:
        return None
    with Image.open(io.BytesIO(data)) as im:
        im.thumbnail(box)
        return im.convert("RGBA")


def blocking_modifiers(platform: str) -> int:
    """The Tk key-event `state` bits that mean "a shortcut, not a plain key press"
    (Ctrl and Alt/Command). Lock keys must NOT be in here: on Windows NumLock is
    reported as bit 0x8, so treating 0x8 as Alt silently disabled every shortcut
    key whenever NumLock was on."""
    if platform == "win32":
        return 0x4 | 0x20000          # Control | Alt
    if platform == "darwin":
        return 0x4 | 0x8 | 0x10       # Control | Command | Option
    return 0x4 | 0x8                  # Control | Alt (Mod1; NumLock is Mod2 on X11)


def is_plain_key(event, platform: str | None = None) -> bool:
    """False if Ctrl/Alt/Command is held, so Ctrl+A and friends keep their usual meaning."""
    state_bits = getattr(event, "state", 0)
    if not isinstance(state_bits, int):
        return True
    return not (state_bits & blocking_modifiers(platform or sys.platform))


def fit_image(im, box: tuple[int, int]):
    """`im` as an RGBA copy scaled down to fit inside `box`, keeping its aspect
    ratio. Images already smaller than the box are not enlarged."""
    from PIL import Image
    out = im.convert("RGBA")
    out.thumbnail(box, Image.LANCZOS)
    return out


def reveal_command(path: Path, platform: str):
    """The command that opens the file manager on `path`, with the file selected
    where the platform supports that. Returns a string on Windows (see below) and
    a list elsewhere."""
    if platform == "win32":
        # explorer wants ONE argument of the form  /select,"C:\a b\c.png"  — quotes
        # around the path only. Passed as a list, Python would quote the whole
        # argument ("/select,C:\a b\c.png"), explorer wouldn't understand it and
        # would just open a default folder, so the command line is built by hand.
        return f'explorer /select,"{os.path.normpath(str(path))}"'
    if platform == "darwin":
        return ["open", "-R", str(path)]
    return ["xdg-open", str(path.parent)]       # no portable "select" on Linux


def reveal_in_folder(path: Path) -> None:
    try:
        subprocess.Popen(reveal_command(path, sys.platform))
    except Exception:
        pass  # a convenience button; never worth an error


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

def review_dialog(
    title: str,
    groups: Sequence[ReviewGroup],
    keep_choices: Sequence[tuple[str, str]],
    select_keep: Callable[[list[Path], str], Path | None],
    extra_details: Callable[[Path], list[tuple[str, str]]] | None = None,
) -> list[Path]:
    """Show the review dialog and return the cards the user chose to trash.

    keep_choices   (menu label, strategy key) pairs for the Auto-select dropdown
    select_keep    (paths, strategy key) -> the path to keep, or None to keep none
    extra_details  optional (path -> [(label, value), ...]) for the Details window
    """
    try:
        return _ctk_dialog(title, groups, keep_choices, select_keep, extra_details)
    except ImportError as e:                       # tkinter / customtkinter missing
        raise TaskFailedError(f"The review dialog needs tkinter and customtkinter: {e}") from e


# (light, dark) colour pairs, as CustomTkinter expects
_MUTED = ("gray40", "gray65")
_LOCK_ON = ("#b45309", "#fbbf24")        # (light, dark); the canvas picks the one for the theme
_LOCK_OFF = ("gray45", "gray60")
_ACCENT = ("#15803d", "#4ade80")
_BTN_GREY = ("gray75", "gray30")
_BTN_GREY_HOVER = ("gray68", "gray38")
_TRASH = ("#b91c1c", "#dc2626")
_TRASH_HOVER = ("#991b1b", "#b91c1c")


def _ctk_dialog(title, groups, keep_choices, select_keep, extra_details) -> list[Path]:
    import tkinter as tk
    from bisect import bisect_right
    from collections import OrderedDict
    from tkinter import font as tkfont
    from tkinter import messagebox
    import customtkinter as ctk
    from PIL import ImageTk
    from kkafio.dialogs.library.window import _DARK, _LIGHT, ellipsize, round_corners, rounded_rect_points

    state = ReviewState(groups, select_keep)
    result: list[Path] = []
    keep_by_label = dict(keep_choices)

    # A normal window (not always-on-top): reviewing hundreds of cards takes a while
    # and the user may want to look at other apps meanwhile.
    root = new_window(title, topmost=False)
    root.minsize(780, 520)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(2, weight=1)       # the list takes all extra space

    dark = ctk.get_appearance_mode() == "Dark"
    pal = _DARK if dark else _LIGHT           # the canvas takes the colours of the theme
    lock_on, lock_off = (_LOCK_ON[1], _LOCK_OFF[1]) if dark else (_LOCK_ON[0], _LOCK_OFF[0])
    accent = _ACCENT[1] if dark else _ACCENT[0]

    try:
        scale = float(root._get_window_scaling())
    except Exception:
        scale = 1.0

    def px(n: float) -> int:
        return max(1, int(round(n * scale)))

    family = tkfont.nametofont("TkDefaultFont").actual("family")
    f_group = tkfont.Font(root=root, family=family, size=13, weight="bold")
    f_name = tkfont.Font(root=root, family=family, size=12, weight="bold")
    f_small = tkfont.Font(root=root, family=family, size=11)
    f_lock = tkfont.Font(root=root, family=family, size=15)
    # The file name and the green group line use the font size of a card's file name in the Manage
    # Library grid (a button's font size, scaled like the widgets are), so both windows read alike.
    try:
        grid_size = -max(1, round(abs(ctk.CTkFont().cget("size")) * ctk.ScalingTracker.get_widget_scaling(root)))
        f_name.configure(size=grid_size)
        f_group.configure(size=grid_size)
    except Exception:
        pass                                    # keep the fixed sizes above
    lh_group = f_group.metrics("linespace")
    lh_name = f_name.metrics("linespace") + px(2)
    lh_small = f_small.metrics("linespace") + px(1)

    # ---- geometry of the rows (all fixed, so a row's position is plain arithmetic) --------
    thumb_w, thumb_h = px(THUMB_MAX[0]), px(THUMB_MAX[1])
    card_h = thumb_h + px(16)                          # a card's row
    hdr_h = max(px(44), lh_group + lh_small + px(24))  # a group's header row
    gap_h = px(10)                                     # between two groups
    margin = px(4)                                     # the group panel's distance from the sides
    radius = px(8)
    check_size = px(24)
    check_x = margin + px(16)
    btn_w, btn_h = px(110), px(32)
    ROW_HEADER, ROW_CARD, ROW_GAP = 0, 1, 2

    # ---- what is shown ------------------------------------------------------------------
    build = {"order": []}                     # group indices that match the search
    # rows: (kind, group, card) per row of the list; tops: the y of each row's top edge
    lay: dict = {"rows": [], "tops": [], "total": 0, "cw": 1, "region_h": 1}
    drawn: dict[int, list[int]] = {}          # row -> its canvas items
    thumb_items: dict[int, tuple[int, int]] = {}   # card row -> (cover image item, "no preview" text item)
    meta_cache: dict[Path, str] = {}
    timers: dict[str, str | None] = {"poll": None, "search": None, "layout": None, "draw": None}

    # ---- thumbnails: decoded on worker threads, applied on the Tk thread ---
    thumb_queue: queue.Queue = queue.Queue()
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="kkafio-thumb")
    photos: OrderedDict[Path, object] = OrderedDict()   # path -> PhotoImage (least recently drawn first)
    no_preview: set[Path] = set()
    requested: set[Path] = set()

    # -----------------------------------------------------------------------
    # header / search / list / bottom bar
    # -----------------------------------------------------------------------
    header = ctk.CTkFrame(root, fg_color="transparent")
    header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
    header.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(header, text=title, anchor="w",
                 font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w")
    summary = ctk.CTkLabel(header, text="", anchor="e", text_color=_MUTED)
    summary.grid(row=0, column=1, sticky="e")
    ctk.CTkLabel(header, text=t("dialog.review.hint"), anchor="w", justify="left", wraplength=840,
                 text_color=_MUTED).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    search = ctk.CTkEntry(root, placeholder_text=t("dialog.review.search_placeholder"))
    search.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 8))

    listing = ctk.CTkFrame(root, fg_color="transparent")
    listing.grid(row=2, column=0, sticky="nsew", padx=(16, 10))
    listing.grid_columnconfigure(0, weight=1)
    listing.grid_rowconfigure(0, weight=1)
    canvas = tk.Canvas(listing, bg=pal["bg"], highlightthickness=0, borderwidth=0,
                       width=px(820), height=px(420), yscrollincrement=px(40), takefocus=1)
    canvas.grid(row=0, column=0, sticky="nsew")
    scroll_sb = ctk.CTkScrollbar(listing, command=canvas.yview)
    scroll_sb.grid(row=0, column=1, sticky="ns", padx=(4, 0))

    bar = ctk.CTkFrame(root, fg_color="transparent")
    bar.grid(row=3, column=0, sticky="ew", padx=16, pady=12)
    bar.grid_columnconfigure(3, weight=1)

    auto_menu = ctk.CTkOptionMenu(bar, values=[label for label, _ in keep_choices], width=260,
                                  dynamic_resizing=False, command=lambda choice: on_auto(choice))
    auto_menu.set(t("dialog.review.auto_placeholder"))
    auto_menu.grid(row=0, column=0, padx=(0, 6))

    undo_btn = ctk.CTkButton(bar, text=t("dialog.review.undo"), width=90, fg_color=_BTN_GREY,
                             hover_color=_BTN_GREY_HOVER, text_color=("gray10", "gray90"),
                             command=lambda: on_undo())
    undo_btn.grid(row=0, column=1, padx=(0, 10))

    trash_btn = ctk.CTkButton(bar, text="", width=170, fg_color=_TRASH, hover_color=_TRASH_HOVER,
                              command=lambda: on_trash())
    trash_btn.grid(row=0, column=2)

    status = ctk.CTkLabel(bar, text="", anchor="w", text_color=_MUTED)
    status.grid(row=0, column=3, sticky="ew", padx=14)

    ctk.CTkButton(bar, text=t("dialog.close"), width=90, fg_color=_BTN_GREY, hover_color=_BTN_GREY_HOVER,
                  text_color=("gray10", "gray90"), command=lambda: on_close()).grid(row=0, column=4)

    # -----------------------------------------------------------------------
    # counts / summary
    # -----------------------------------------------------------------------
    def update_counts() -> None:
        n = state.selected_count()
        trash_btn.configure(text=t("dialog.review.trash", count=n),
                            state="normal" if n else "disabled")
        undo_btn.configure(state="normal" if state.can_undo() else "disabled")

    def update_summary(shown: int) -> None:
        total = len(state.groups)
        files = sum(len(g.paths) for g in state.groups)
        if shown != total:
            text = t("dialog.review.summary_filtered", shown=shown, total=total, files=files)
        else:
            text = t("dialog.review.summary", total=total, files=files)
        summary.configure(text=text)

    # -----------------------------------------------------------------------
    # layout + drawing (only the rows in view exist on the canvas)
    # -----------------------------------------------------------------------
    def build_rows() -> None:
        """The list of rows for the groups in `build["order"]` and where each one starts."""
        rows: list[tuple[int, int, int]] = []
        tops: list[int] = []
        y = px(2)
        for gi in build["order"]:
            rows.append((ROW_HEADER, gi, -1))
            tops.append(y)
            y += hdr_h
            for ci in range(len(state.groups[gi].paths)):
                rows.append((ROW_CARD, gi, ci))
                tops.append(y)
                y += card_h
            rows.append((ROW_GAP, gi, -1))
            tops.append(y)
            y += gap_h
        lay.update(rows=rows, tops=tops, total=y)

    def relayout(keep_scroll: bool = True) -> None:
        """Size the scroll region for the window and draw what is in view."""
        timers["layout"] = None
        y0 = canvas.canvasy(0) if keep_scroll else 0
        cw, ch = max(canvas.winfo_width(), px(300)), max(canvas.winfo_height(), px(200))
        region_h = max(lay["total"], ch)
        lay.update(cw=cw, region_h=region_h)
        canvas.delete("all")
        drawn.clear()
        thumb_items.clear()
        canvas.configure(scrollregion=(0, 0, cw, region_h))
        canvas.yview_moveto(min(max(0.0, y0 / region_h), 1.0) if keep_scroll else 0.0)
        if not lay["rows"]:
            canvas.create_text(cw // 2, ch // 3, text=t("dialog.review.no_matches"), fill=pal["muted"], font=f_name)
        draw_visible()

    def schedule_layout(delay: int = 80, keep_scroll: bool = True) -> None:
        if timers["layout"] is not None:
            root.after_cancel(timers["layout"])
        timers["layout"] = root.after(delay, lambda: relayout(keep_scroll))

    def draw_visible() -> None:
        timers["draw"] = None
        rows, tops = lay["rows"], lay["tops"]
        if not rows:
            return
        y_top = canvas.canvasy(0)
        y_bottom = y_top + canvas.winfo_height()
        lo = max(0, bisect_right(tops, y_top) - 2)
        hi = min(len(rows), bisect_right(tops, y_bottom) + 1)
        for idx in [i for i in drawn if i < lo or i >= hi]:
            for cid in drawn.pop(idx):
                canvas.delete(cid)
            thumb_items.pop(idx, None)
        for idx in range(lo, hi):
            if idx not in drawn:
                draw_row(idx)

    def schedule_draw() -> None:
        if timers["draw"] is None:
            timers["draw"] = root.after_idle(draw_visible)

    def card_meta(path: Path) -> str:
        meta = meta_cache.get(path)
        if meta is None:
            try:
                st = path.stat()
                meta = t("dialog.review.card_meta", size=format_size(st.st_size),
                         date=format_date(created_timestamp(st)))
            except OSError:
                meta = t("dialog.review.file_not_found")
            meta_cache[path] = meta
        return meta

    def panel(x0: float, y0: float, x1: float, y1: float) -> int:
        return canvas.create_polygon(rounded_rect_points(x0, y0, x1, y1, radius), smooth=True,
                                     fill=pal["tile"], outline="")

    def draw_row(idx: int) -> None:
        kind, gi, ci = lay["rows"][idx]
        top = lay["tops"][idx]
        x0, x1 = margin, lay["cw"] - margin
        ids: list[int] = []
        drawn[idx] = ids
        if kind == ROW_GAP:
            return
        group = state.groups[gi]
        n = len(group.paths)
        if kind == ROW_HEADER:
            # the panel's top corners are rounded: the polygon reaches into the first card row's padding
            ids.append(panel(x0, top, x1, top + hdr_h + (radius if n else 0)))
            locked = state.locked[gi]
            ids.append(canvas.create_text(x0 + px(26), top + px(24), text="🔒" if locked else "🔓", font=f_lock,
                                          fill=lock_on if locked else lock_off))
            text_x = x0 + px(54)
            text_w = x1 - text_x - px(14)
            ids.append(canvas.create_text(
                text_x, top + px(10), anchor="nw", fill=accent, font=f_group,
                text=ellipsize(f_group.measure,
                               t("dialog.review.group_header", count=n, label=group.label), text_w)))
            ids.append(canvas.create_text(
                text_x, top + px(10) + lh_group + px(2), anchor="nw", fill=pal["muted"], font=f_small,
                text=ellipsize(f_small.measure, " ".join(group.description.split()), text_w)))
            return
        path = group.paths[ci]
        if ci == n - 1:                                   # the last card: the panel's bottom corners
            ids.append(panel(x0, top - radius, x1, top + card_h))
        else:
            ids.append(canvas.create_rectangle(x0, top, x1, top + card_h, fill=pal["tile"], outline=""))
        # the checkbox
        by = top + (card_h - check_size) // 2
        checked = state.selected[gi][ci]
        ids.append(canvas.create_polygon(
            rounded_rect_points(check_x, by, check_x + check_size, by + check_size, px(5)), smooth=True,
            fill=pal["accent"] if checked else pal["input"], outline=pal["accent"] if checked else pal["muted"],
            width=px(2)))
        if checked:
            ids.append(canvas.create_text(check_x + check_size // 2, by + check_size // 2, text="✓",
                                          fill=pal["bg"], font=f_name))
        # the cover
        tx = check_x + check_size + px(14)
        ty = top + px(8)
        ids.append(canvas.create_polygon(rounded_rect_points(tx, ty, tx + thumb_w, ty + thumb_h, px(4)),
                                         smooth=True, fill=pal["cover"], outline=""))
        request_thumb(path)
        photo = photos.get(path)
        if photo is not None:
            photos.move_to_end(path)
        cx, cy = tx + thumb_w // 2, ty + thumb_h // 2
        # Both items always exist: the cover can arrive after the row is drawn (apply_thumb), and
        # a text item can't take an image.
        image_id = canvas.create_image(cx, cy, image=photo if photo is not None else "")
        note_id = canvas.create_text(cx, cy, fill=pal["muted"], font=f_small, justify="center",
                                     text=t("dialog.review.no_preview") if path in no_preview else "")
        ids += [image_id, note_id]
        thumb_items[idx] = (image_id, note_id)
        # the file's details
        info_x = tx + thumb_w + px(14)
        info_w = max(px(60), x1 - btn_w - px(30) - info_x)
        y = top + (card_h - (lh_name + 2 * lh_small)) // 2
        ids.append(canvas.create_text(info_x, y, anchor="nw", fill=pal["text"], font=f_name,
                                      text=ellipsize(f_name.measure, path.name, info_w)))
        ids.append(canvas.create_text(info_x, y + lh_name, anchor="nw", fill=pal["muted"], font=f_small,
                                      text=ellipsize(f_small.measure, card_meta(path), info_w)))
        ids.append(canvas.create_text(info_x, y + lh_name + lh_small, anchor="nw", fill=pal["muted"],
                                      font=f_small, text=ellipsize(f_small.measure, str(path.parent), info_w)))
        # the Details button
        bx0, by0 = x1 - px(14) - btn_w, top + (card_h - btn_h) // 2
        ids.append(canvas.create_polygon(rounded_rect_points(bx0, by0, bx0 + btn_w, by0 + btn_h, px(8)),
                                         smooth=True, fill=pal["sel"], outline=""))
        ids.append(canvas.create_text(bx0 + btn_w // 2, by0 + btn_h // 2, fill=pal["text"], font=f_small,
                                      text=t("dialog.review.details_button")))

    def redraw_row(idx: int) -> None:
        if idx in drawn:
            for cid in drawn.pop(idx):
                canvas.delete(cid)
            thumb_items.pop(idx, None)
            draw_row(idx)

    def redraw_group(gi: int) -> None:
        for idx in [i for i in drawn if lay["rows"][i][1] == gi]:
            redraw_row(idx)

    def redraw_all() -> None:
        for idx in list(drawn):
            redraw_row(idx)

    def locate(ex: int, ey: int):
        """What is at a point of the canvas window: ("lock", group), ("check", group, card),
        ("details", group, card), or None."""
        tops = lay["tops"]
        if not tops:
            return None
        x, y = canvas.canvasx(ex), canvas.canvasy(ey)
        idx = bisect_right(tops, y) - 1
        if idx < 0:
            return None
        kind, gi, ci = lay["rows"][idx]
        top = tops[idx]
        if kind == ROW_HEADER:
            if margin + px(6) <= x <= margin + px(46) and y <= top + hdr_h:
                return ("lock", gi)
        elif kind == ROW_CARD:
            if margin + px(6) <= x <= check_x + check_size + px(8):
                return ("check", gi, ci)
            bx0 = lay["cw"] - margin - px(14) - btn_w
            by0 = top + (card_h - btn_h) // 2
            if bx0 <= x <= bx0 + btn_w and by0 <= y <= by0 + btn_h:
                return ("details", gi, ci)
        return None

    def on_click(event) -> None:
        canvas.focus_set()                      # the keys work on the list again (the search box lets go)
        hit = locate(event.x, event.y)
        if hit is None:
            return
        if hit[0] == "lock":
            on_lock(hit[1])
        elif hit[0] == "check":
            on_check(hit[1], hit[2])
        else:
            show_details(state.groups[hit[1]].paths[hit[2]])

    def on_motion(event) -> None:
        try:
            canvas.configure(cursor="hand2" if locate(event.x, event.y) is not None else "")
        except tk.TclError:
            pass

    def on_yscroll(first, last) -> None:
        scroll_sb.set(first, last)
        schedule_draw()

    def on_wheel(event) -> None:
        try:
            target = root.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        if target is not canvas:
            return
        if getattr(event, "num", 0) == 4:
            step = -1
        elif getattr(event, "num", 0) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        canvas.yview_scroll(step * 3, "units")

    def haystack(group: ReviewGroup) -> str:
        return " ".join([group.label, group.description, *map(str, group.paths)]).lower()

    def rebuild() -> None:
        """Re-apply the search filter and go back to the top of the list."""
        timers["search"] = None
        needle = search.get().strip().lower()
        build["order"] = [gi for gi, g in enumerate(state.groups)
                          if not needle or needle in haystack(g)]
        build_rows()
        update_summary(len(build["order"]))
        update_counts()
        relayout(keep_scroll=False)

    def typing_in_entry() -> bool:
        """True while a text field has focus: the arrow keys belong to it then."""
        try:
            focused = root.focus_get()
            return focused is not None and focused.winfo_class() in ("Entry", "Text")
        except (KeyError, tk.TclError):
            return False

    def on_page_key(step: int):
        def handler(event=None):
            if typing_in_entry() or not is_plain_key(event):
                return None
            canvas.yview_scroll(step, "pages")
            return "break"
        return handler

    def scroll_list(direction: int, fraction: float = 0.15) -> None:
        """Scroll the list by a fraction of what's visible (the same step at any
        window size or display scaling), clamped to the ends."""
        top, bottom = canvas.yview()
        if bottom - top >= 1.0:
            return                                   # everything fits: nothing to scroll
        canvas.yview_moveto(min(max(top + direction * (bottom - top) * fraction, 0.0), 1.0))

    def on_scroll_key(direction: int):
        def handler(event=None):
            if typing_in_entry() or not is_plain_key(event):
                return None
            try:
                scroll_list(direction)
            except (AttributeError, tk.TclError):
                pass
            return "break"
        return handler

    def on_home_end(fraction: float):
        def handler(event=None):
            if typing_in_entry() or not is_plain_key(event):
                return None
            canvas.yview_moveto(fraction)
            return "break"
        return handler

    # -----------------------------------------------------------------------
    # thumbnails
    # -----------------------------------------------------------------------
    def apply_thumb(path: Path) -> None:
        """A cover finished loading: show it (or "no preview") on every drawn row that has it."""
        photo = photos.get(path)
        for idx, (image_id, note_id) in list(thumb_items.items()):
            if idx not in drawn:
                continue
            _kind, gi, ci = lay["rows"][idx]
            if state.groups[gi].paths[ci] != path:
                continue
            try:
                if photo is not None:
                    canvas.itemconfigure(image_id, image=photo)
                    canvas.itemconfigure(note_id, text="")
                else:
                    canvas.itemconfigure(note_id, text=t("dialog.review.no_preview"))
            except tk.TclError:
                pass  # the window is closing

    def request_thumb(path: Path) -> None:
        if path in photos or path in no_preview or path in requested:
            return
        requested.add(path)

        def work() -> None:
            try:
                pil = load_thumbnail(path, (thumb_w, thumb_h))
                if pil is not None:
                    pil = round_corners(pil, px(4))
            except Exception:
                pil = None
            thumb_queue.put((path, pil))

        executor.submit(work)

    def poll_thumbs() -> None:
        for _ in range(8):                      # a few per tick keeps the UI smooth
            try:
                path, pil = thumb_queue.get_nowait()
            except queue.Empty:
                break
            requested.discard(path)
            if pil is None:
                no_preview.add(path)
            else:
                photos[path] = ImageTk.PhotoImage(pil)
                while len(photos) > THUMB_CACHE_MAX:
                    photos.popitem(last=False)
            apply_thumb(path)
        timers["poll"] = root.after(60, poll_thumbs)

    # -----------------------------------------------------------------------
    # user actions
    # -----------------------------------------------------------------------
    def on_check(gi: int, ci: int) -> None:
        state.set_selected(gi, ci, not state.selected[gi][ci])      # also locks the group
        redraw_group(gi)
        update_counts()
        status.configure(text="")

    def on_lock(gi: int) -> None:
        state.set_locked(gi, not state.locked[gi])
        redraw_group(gi)

    def on_auto(choice: str) -> None:
        auto_menu.set(t("dialog.review.auto_placeholder"))
        applied, skipped, failed = state.apply_keep(keep_by_label[choice])
        redraw_all()
        update_counts()
        parts = [t("dialog.review.status_applied", choice=choice, count=applied)]
        if skipped:
            parts.append(t("dialog.review.status_skipped", count=skipped))
        if failed:
            parts.append(t("dialog.review.status_failed", count=failed))
        status.configure(text=t("dialog.review.status_sep").join(parts))

    def on_undo() -> None:
        restored, kept = state.undo_auto()
        redraw_all()
        update_counts()
        parts = [t("dialog.review.status_undone", count=restored)]
        if kept:
            parts.append(t("dialog.review.status_kept", count=kept))
        status.configure(text=t("dialog.review.status_sep").join(parts))

    def on_trash() -> None:
        paths = state.selected_paths()
        if not paths:
            return
        everything = state.fully_selected_groups()
        if everything and not messagebox.askyesno(
                t("dialog.review.confirm_all_title"),
                t("dialog.review.confirm_all_body", count=len(everything)),
                parent=root):
            return
        result.extend(paths)
        finish()

    def on_close(_event=None) -> None:
        n = state.selected_count()
        if n and not messagebox.askyesno(
                t("dialog.review.confirm_close_title"),
                t("dialog.review.confirm_close_body", count=n), parent=root):
            return
        finish()

    def on_search_key(_event=None) -> None:
        cancel_timer("search")
        timers["search"] = root.after(300, rebuild)      # debounce typing

    def show_details(path: Path) -> None:
        top = ctk.CTkToplevel(root)
        top.title(t("dialog.review.details_title", name=path.name))
        top.transient(root)
        top.grid_columnconfigure(1, weight=1)
        top.bind("<Escape>", lambda _e: top.destroy())

        rows: list[tuple[str, str]] = [(t("dialog.review.detail_file"), path.name),
                                       (t("dialog.review.detail_folder"), str(path.parent))]
        try:
            st = path.stat()
            rows += [(t("dialog.review.detail_size"),
                      t("dialog.review.size_value", size=format_size(st.st_size), bytes=f"{st.st_size:,}")),
                     (t("dialog.review.detail_created"),
                      datetime.fromtimestamp(created_timestamp(st)).strftime("%Y/%m/%d %H:%M:%S")),
                     (t("dialog.review.detail_modified"),
                      datetime.fromtimestamp(st.st_mtime).strftime("%Y/%m/%d %H:%M:%S"))]
        except OSError:
            rows.append((t("dialog.review.detail_status"), t("dialog.review.file_not_found")))
        if extra_details is not None:
            try:
                rows += extra_details(path)
            except Exception:
                pass

        try:
            from PIL import Image
            from kkafio.cards.chara.similarity import read_png_preview
            data = read_png_preview(path)
            if data:
                with Image.open(io.BytesIO(data)) as im:
                    # Covers come in all resolutions: fit into a fixed box (keeping the
                    # aspect ratio, never enlarging) so the window stays a sensible size.
                    big = fit_image(im, DETAILS_MAX)
                preview = ctk.CTkImage(light_image=big, dark_image=big, size=big.size)
                ctk.CTkLabel(top, text="", image=preview).grid(row=0, column=0, padx=14, pady=14,
                                                              sticky="n")
        except Exception:
            pass

        box = ctk.CTkTextbox(top, width=420, height=max(160, 28 * len(rows)), wrap="word")
        box.grid(row=0, column=1, padx=(0, 14), pady=14, sticky="nsew")
        box.insert("1.0", "\n".join(t("dialog.review.detail_line", label=k, value=v) for k, v in rows))
        box.configure(state="disabled")

        buttons = ctk.CTkFrame(top, fg_color="transparent")
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", padx=14, pady=(0, 14))
        ctk.CTkButton(buttons, text=t("dialog.review.show_in_folder"), width=140,
                      command=lambda: reveal_in_folder(path)).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(buttons, text=t("dialog.close"), width=90, fg_color=_BTN_GREY,
                      hover_color=_BTN_GREY_HOVER, text_color=("gray10", "gray90"),
                      command=top.destroy).grid(row=0, column=1)

    # -----------------------------------------------------------------------
    # shutting down
    # -----------------------------------------------------------------------
    def cancel_timer(name: str) -> None:
        after_id = timers.get(name)
        if after_id is not None:
            try:
                root.after_cancel(after_id)
            except tk.TclError:
                pass
            timers[name] = None

    def finish() -> None:
        # Cancel our timers before the window goes away: one still queued would
        # fire during the next dialog and print "invalid command name" errors.
        for name in list(timers):
            cancel_timer(name)
        executor.shutdown(wait=False, cancel_futures=True)
        root.quit()

    search.bind("<KeyRelease>", on_search_key)
    root.bind("<Escape>", on_close)
    for keys, handler in ((("<Left>", "<a>", "<A>", "<Prior>"), on_page_key(-1)),
                          (("<Right>", "<d>", "<D>", "<Next>"), on_page_key(1)),
                          (("<Up>", "<w>", "<W>"), on_scroll_key(-1)),
                          (("<Down>", "<s>", "<S>"), on_scroll_key(1)),
                          (("<Home>",), on_home_end(0.0)),
                          (("<End>",), on_home_end(1.0))):
        for key in keys:
            root.bind(key, handler)
    # Enter in the search box hands the keyboard back to the list, so WASD / arrows work again.
    search.bind("<Return>", lambda _e: canvas.focus_set())
    root.protocol("WM_DELETE_WINDOW", on_close)

    canvas.configure(yscrollcommand=on_yscroll)
    canvas.bind("<Button-1>", on_click)
    canvas.bind("<Motion>", on_motion)
    canvas.bind("<Configure>", lambda _e: schedule_layout(60, keep_scroll=True))
    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind(seq, on_wheel, add="+")

    update_counts()
    rebuild()
    timers["poll"] = root.after(60, poll_thumbs)
    show_and_wait(root, on_shown=lambda: maximize(root))      # always full screen
    return result
