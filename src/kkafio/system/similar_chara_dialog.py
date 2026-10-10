"""
similar_chara_dialog.py — The review dialog of Review Similar Characters.

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
  * Keys: Left / Right change page, Up / Down scroll the list.

`review_dialog()` returns the cards the user chose to trash (empty if the
dialog was closed without trashing). The dialog itself never deletes anything.

The selection logic lives in `ReviewState`, which has no GUI dependency; the
CustomTkinter window (see dialog_common.py for the shared window behaviour)
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
from kkafio.system.dialog_common import new_window, show_and_wait


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

# Groups shown per page. CustomTkinter widgets are heavy (each is its own canvas), so
# putting every card in one scrolling list makes scrolling and restoring the window
# stutter; a page keeps the live widget count small.
GROUPS_PER_PAGE = 6


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # unreachable; keeps type checkers happy


def format_date(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y/%m/%d")
    except (OverflowError, OSError, ValueError):
        return "?"


def created_timestamp(st: os.stat_result) -> float:
    """File creation time (st_birthtime where it exists, else st_ctime — which
    is the creation time on Windows)."""
    return getattr(st, "st_birthtime", st.st_ctime)


def load_thumbnail(path: Path):
    """The card's cover as a small RGBA PIL image, or None. Safe to call from
    a worker thread (touches no Tk objects). Reads only the PNG part of the file."""
    from PIL import Image
    from kkafio.cards.similarity import read_png_preview

    data = read_png_preview(path)
    if not data:
        return None
    with Image.open(io.BytesIO(data)) as im:
        im.thumbnail(THUMB_MAX)
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
_ACCENT = ("#15803d", "#4ade80")
_GROUP_BG = ("gray88", "gray17")
_THUMB_BG = ("gray78", "gray25")
_LOCK_ON = ("#b45309", "#fbbf24")
_LOCK_OFF = ("gray45", "gray60")
_BTN_GREY = ("gray75", "gray30")
_BTN_GREY_HOVER = ("gray68", "gray38")
_TRASH = ("#b91c1c", "#dc2626")
_TRASH_HOVER = ("#991b1b", "#b91c1c")


def _ctk_dialog(title, groups, keep_choices, select_keep, extra_details) -> list[Path]:
    import tkinter as tk
    from tkinter import messagebox
    import customtkinter as ctk

    state = ReviewState(groups, select_keep)
    result: list[Path] = []
    keep_by_label = dict(keep_choices)

    # A normal window (not always-on-top): reviewing hundreds of cards takes a while
    # and the user may want to look at other apps meanwhile.
    root = new_window(title, topmost=False)
    root.minsize(780, 520)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(2, weight=1)       # the list takes all extra space

    # ---- widgets & per-card views, rebuilt whenever the search changes -----
    views: dict[tuple[int, int], dict] = {}   # (group, card) -> its widgets
    thumb_labels: dict[Path, object] = {}     # cover label of each rendered card
    group_frames: list = []
    group_locks: dict[int, object] = {}      # group -> its lock button
    build = {"order": [], "page": 0}
    timers: dict[str, str | None] = {"poll": None, "search": None}

    # ---- thumbnails: decoded on worker threads, applied on the Tk thread ---
    thumb_queue: queue.Queue = queue.Queue()
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="kkafio-thumb")
    images: dict[Path, object] = {}           # path -> CTkImage, or None if no preview
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

    listing = ctk.CTkScrollableFrame(root, width=820, height=420, fg_color="transparent")
    listing.grid(row=2, column=0, sticky="nsew", padx=(10, 6))
    listing.grid_columnconfigure(0, weight=1)

    pager = ctk.CTkFrame(root, fg_color="transparent")
    pager.grid(row=3, column=0, pady=(6, 0))
    prev_btn = ctk.CTkButton(pager, text=t("dialog.review.previous"), width=110, command=lambda: go_page(-1))
    prev_btn.grid(row=0, column=0, padx=8)
    page_label = ctk.CTkLabel(pager, text="", width=190)
    page_label.grid(row=0, column=1)
    next_btn = ctk.CTkButton(pager, text=t("dialog.review.next"), width=110, command=lambda: go_page(1))
    next_btn.grid(row=0, column=2, padx=8)

    bar = ctk.CTkFrame(root, fg_color="transparent")
    bar.grid(row=4, column=0, sticky="ew", padx=16, pady=12)
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
    # refreshing widgets from the state
    # -----------------------------------------------------------------------
    def refresh_card(gi: int, ci: int) -> None:
        view = views.get((gi, ci))
        if view is None:
            return
        view["var"].set(state.selected[gi][ci])

    def refresh_lock(gi: int) -> None:
        button = group_locks.get(gi)
        if button is not None:
            locked = state.locked[gi]
            button.configure(text="🔒" if locked else "🔓",
                             text_color=_LOCK_ON if locked else _LOCK_OFF)

    def refresh_all() -> None:
        for gi, ci in list(views):
            refresh_card(gi, ci)
        for gi in list(group_locks):
            refresh_lock(gi)
        update_counts()

    def update_counts() -> None:
        n = state.selected_count()
        trash_btn.configure(text=t("dialog.review.trash", count=n),
                            state="normal" if n else "disabled")
        undo_btn.configure(state="normal" if state.can_undo() else "disabled")

    def page_count() -> int:
        return max(1, -(-len(build["order"]) // GROUPS_PER_PAGE))

    def update_summary(shown: int) -> None:
        total = len(state.groups)
        files = sum(len(g.paths) for g in state.groups)
        if shown != total:
            text = t("dialog.review.summary_filtered", shown=shown, total=total, files=files)
        else:
            text = t("dialog.review.summary", total=total, files=files)
        summary.configure(text=text)
        pages = page_count()
        page_label.configure(text=t("dialog.review.page", page=build["page"] + 1, pages=pages))
        prev_btn.configure(state="normal" if build["page"] > 0 else "disabled")
        next_btn.configure(state="normal" if build["page"] < pages - 1 else "disabled")

    # -----------------------------------------------------------------------
    # building the list (one group per timer tick, so the window stays responsive)
    # -----------------------------------------------------------------------
    def build_card(parent, gi: int, ci: int, path: Path, row: int) -> None:
        card = ctk.CTkFrame(parent, fg_color="transparent")
        card.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        card.grid_columnconfigure(2, weight=1)

        var = tk.BooleanVar(value=state.selected[gi][ci])
        check = ctk.CTkCheckBox(card, text="", width=26, checkbox_width=24, checkbox_height=24,
                                variable=var, command=lambda: on_check(gi, ci))
        check.grid(row=0, column=0, padx=(4, 8))

        thumb = ctk.CTkLabel(card, text="", width=THUMB_MAX[0], height=THUMB_MAX[1],
                             fg_color=_THUMB_BG, corner_radius=4)
        thumb.grid(row=0, column=1, padx=(0, 12))

        try:
            st = path.stat()
            meta = t("dialog.review.card_meta", size=format_size(st.st_size),
                     date=format_date(created_timestamp(st)))
        except OSError:
            meta = t("dialog.review.file_not_found")
        info = ctk.CTkFrame(card, fg_color="transparent")
        info.grid(row=0, column=2, sticky="w")
        ctk.CTkLabel(info, text=path.name, anchor="w",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(info, text=meta, anchor="w", text_color=_MUTED).grid(row=1, column=0, sticky="w")
        ctk.CTkLabel(info, text=str(path.parent), anchor="w", justify="left", wraplength=520,
                     text_color=_MUTED).grid(row=2, column=0, sticky="w")

        ctk.CTkButton(card, text=t("dialog.review.details_button"), width=110, fg_color=_BTN_GREY,
                      hover_color=_BTN_GREY_HOVER, text_color=("gray10", "gray90"),
                      command=lambda: show_details(path)).grid(row=0, column=3, padx=(12, 4))

        views[(gi, ci)] = {"var": var, "check": check}
        thumb_labels[path] = thumb
        refresh_card(gi, ci)
        request_thumb(path)

    def build_group(gi: int) -> None:
        group = state.groups[gi]
        frame = ctk.CTkFrame(listing, corner_radius=8, fg_color=_GROUP_BG)
        frame.grid(row=len(group_frames), column=0, sticky="ew", padx=2, pady=(0, 10))
        frame.grid_columnconfigure(1, weight=1)
        lock = ctk.CTkButton(frame, text="🔓", width=34, height=34, fg_color="transparent",
                             hover_color=_BTN_GREY_HOVER, text_color=_LOCK_OFF,
                             font=ctk.CTkFont(size=18), command=lambda: on_lock(gi))
        lock.grid(row=0, column=0, rowspan=2, padx=(10, 2), pady=(8, 2), sticky="n")
        group_locks[gi] = lock
        ctk.CTkLabel(frame, text=t("dialog.review.group_header", count=len(group.paths), label=group.label), anchor="w",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=_ACCENT).grid(row=0, column=1, sticky="w", padx=(4, 14), pady=(10, 0))
        ctk.CTkLabel(frame, text=group.description, anchor="w", justify="left", wraplength=760,
                     text_color=_MUTED).grid(row=1, column=1, sticky="w", padx=(4, 14), pady=(0, 4))
        refresh_lock(gi)
        for ci, path in enumerate(group.paths):
            build_card(frame, gi, ci, path, row=2 + ci)
        group_frames.append(frame)

    def haystack(group: ReviewGroup) -> str:
        return " ".join([group.label, group.description, *map(str, group.paths)]).lower()

    def render_page() -> None:
        """Replace the list with the groups of the current page."""
        for frame in group_frames:
            frame.destroy()
        group_frames.clear()
        views.clear()
        group_locks.clear()
        thumb_labels.clear()
        start = build["page"] * GROUPS_PER_PAGE
        for gi in build["order"][start:start + GROUPS_PER_PAGE]:
            build_group(gi)
        if not build["order"]:
            empty = ctk.CTkLabel(listing, text=t("dialog.review.no_matches"), text_color=_MUTED)
            empty.grid(row=0, column=0, pady=40)
            group_frames.append(empty)
        update_summary(len(build["order"]))
        update_counts()
        try:
            listing._parent_canvas.yview_moveto(0)     # back to the top
        except (AttributeError, tk.TclError):
            pass

    def rebuild() -> None:
        """Re-apply the search filter and go back to the first page."""
        needle = search.get().strip().lower()
        build["order"] = [gi for gi, g in enumerate(state.groups)
                          if not needle or needle in haystack(g)]
        build["page"] = 0
        render_page()

    def typing_in_entry() -> bool:
        """True while a text field has focus: the arrow keys belong to it then."""
        try:
            focused = root.focus_get()
            return focused is not None and focused.winfo_class() in ("Entry", "Text")
        except (KeyError, tk.TclError):
            return False

    def on_arrow(step: int):
        def handler(event=None):
            if typing_in_entry() or not is_plain_key(event):
                return None
            go_page(step)
            return "break"
        return handler

    def scroll_list(direction: int, fraction: float = 0.15) -> None:
        """Scroll the list by a fraction of what's visible (the same step at any
        window size or display scaling), clamped to the ends."""
        canvas = listing._parent_canvas
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

    def go_page(step: int) -> None:
        new = min(max(build["page"] + step, 0), page_count() - 1)
        if new != build["page"]:
            build["page"] = new
            render_page()

    # -----------------------------------------------------------------------
    # thumbnails
    # -----------------------------------------------------------------------
    def apply_thumb(path: Path) -> None:
        label = thumb_labels.get(path)
        if label is None:
            return
        image = images.get(path)
        try:
            if image is None:
                label.configure(text=t("dialog.review.no_preview"))
            else:
                label.configure(image=image, text="")
        except tk.TclError:
            pass  # that card was rebuilt or the window is closing

    def request_thumb(path: Path) -> None:
        if path in images:
            apply_thumb(path)
        elif path not in requested:
            requested.add(path)

            def work() -> None:
                try:
                    pil = load_thumbnail(path)
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
            images[path] = (ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                            if pil is not None else None)
            apply_thumb(path)
        timers["poll"] = root.after(60, poll_thumbs)

    # -----------------------------------------------------------------------
    # user actions
    # -----------------------------------------------------------------------
    def on_check(gi: int, ci: int) -> None:
        state.set_selected(gi, ci, views[(gi, ci)]["var"].get())   # also locks the group
        refresh_card(gi, ci)
        refresh_lock(gi)
        update_counts()
        status.configure(text="")

    def on_lock(gi: int) -> None:
        state.set_locked(gi, not state.locked[gi])
        refresh_lock(gi)

    def on_auto(choice: str) -> None:
        auto_menu.set(t("dialog.review.auto_placeholder"))
        applied, skipped, failed = state.apply_keep(keep_by_label[choice])
        refresh_all()
        parts = [t("dialog.review.status_applied", choice=choice, count=applied)]
        if skipped:
            parts.append(t("dialog.review.status_skipped", count=skipped))
        if failed:
            parts.append(t("dialog.review.status_failed", count=failed))
        status.configure(text=t("dialog.review.status_sep").join(parts))

    def on_undo() -> None:
        restored, kept = state.undo_auto()
        refresh_all()
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
            from kkafio.cards.similarity import read_png_preview
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
    for keys, handler in ((("<Left>", "<a>", "<A>"), on_arrow(-1)),
                          (("<Right>", "<d>", "<D>"), on_arrow(1)),
                          (("<Up>", "<w>", "<W>"), on_scroll_key(-1)),
                          (("<Down>", "<s>", "<S>"), on_scroll_key(1))):
        for key in keys:
            root.bind(key, handler)
    # Enter in the search box hands the keyboard back to the list, so WASD / arrows work again.
    search.bind("<Return>", lambda _e: root.focus_set())
    root.protocol("WM_DELETE_WINDOW", on_close)

    update_counts()
    rebuild()
    timers["poll"] = root.after(60, poll_thumbs)
    show_and_wait(root)
    return result
