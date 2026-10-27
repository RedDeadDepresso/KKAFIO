"""
manual_group_dialog.py — The gallery dialog of Group Characters' Manual mode.

A library-style window: a search bar on top, a sidebar (All characters, Untagged and the series
folders that exist) and a grid of character covers. Under every cover is the character's name and
its series — "Unknown series" until it has one. Click that text to type a series (with
suggestions); Enter confirms it and the card is moved straight into that series' folder, which is
created if it doesn't exist yet. Nothing is moved until a series is confirmed, and the window can
be closed at any time: every confirmed card has already been moved.

The gallery can hold thousands of cards, and CustomTkinter widgets are heavy (each is its own
canvas), so the grid is drawn on one plain Tk canvas and only the tiles in view exist: scrolling
just draws the tiles that come into view, and covers are decoded on worker threads.

`GalleryModel` (what is shown: view, search, sort) has no GUI dependency; the window is built in
`_ctk_dialog()` (see dialog_common.py for the shared window behaviour).
"""

from __future__ import annotations

import io
import queue
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kkafio.core.errors import TaskFailedError
from kkafio.core.i18n import t
from kkafio.system.dialog_common import new_window, show_and_wait
from kkafio.system.similar_chara_dialog import format_size


# ---------------------------------------------------------------------------
# Data + what is shown (no GUI)
# ---------------------------------------------------------------------------

SORT_UPDATED = "updated"
SORT_NAME = "name"
SORT_SIZE = "size"
SORT_KEYS = (SORT_UPDATED, SORT_NAME, SORT_SIZE)

VIEW_ALL = "all"
VIEW_UNTAGGED = "untagged"
VIEW_SERIES = "series"


@dataclass
class CharaItem:
    """One character card in the gallery."""

    path: Path
    root: Path                 # the chara folder it was found under; series folders go inside it
    name: str                  # shown under the cover
    series: str | None         # its series folder (first folder under `root`); None = untagged
    size: int                  # bytes
    mtime: float


class GalleryModel:
    """Which cards the gallery shows: the sidebar view, the search text and the sort order."""

    def __init__(self, items: Iterable[CharaItem]):
        self.items = list(items)
        self.mode = VIEW_ALL
        self.series_filter = ""            # the series shown when mode == VIEW_SERIES
        self.search = ""
        self.sort_key = SORT_UPDATED
        self.descending = True

    # -- what to show -------------------------------------------------------

    def set_view(self, mode: str, series: str = "") -> None:
        self.mode = mode
        self.series_filter = series if mode == VIEW_SERIES else ""

    def visible(self) -> list[CharaItem]:
        needle = self.search.strip().casefold()
        want = self.series_filter.casefold()
        out = []
        for item in self.items:
            if self.mode == VIEW_UNTAGGED and item.series is not None:
                continue
            if self.mode == VIEW_SERIES and (item.series or "").casefold() != want:
                continue
            if needle and needle not in f"{item.name}\n{item.path.name}\n{item.series or ''}".casefold():
                continue
            out.append(item)
        keys = {SORT_UPDATED: lambda i: i.mtime,
                SORT_NAME: lambda i: (i.name.casefold(), i.path.name.casefold()),
                SORT_SIZE: lambda i: i.size}
        out.sort(key=keys.get(self.sort_key, keys[SORT_UPDATED]), reverse=self.descending)
        return out

    # -- the sidebar --------------------------------------------------------

    def counts(self) -> tuple[int, int]:
        """(all cards, untagged cards)."""
        return len(self.items), sum(1 for i in self.items if i.series is None)

    def series_list(self, text: str = "") -> list[tuple[str, int]]:
        """(series, number of cards) for every series that has a card, by name; `text` filters
        by a case-insensitive substring. A series written in different cases is one entry."""
        names: dict[str, str] = {}
        counts: dict[str, int] = {}
        for item in self.items:
            if item.series:
                k = item.series.casefold()
                names.setdefault(k, item.series)
                counts[k] = counts.get(k, 0) + 1
        needle = text.strip().casefold()
        return [(names[k], counts[k]) for k in sorted(names) if needle in k]

    def series_count(self, series: str) -> int:
        k = series.casefold()
        return sum(1 for i in self.items if (i.series or "").casefold() == k)

    # -- changes ------------------------------------------------------------

    def assigned(self, item: CharaItem, new_path: Path, series: str) -> None:
        """`item` was moved to `new_path`, into the folder `series`."""
        item.path = Path(new_path)
        item.series = series


# ---------------------------------------------------------------------------
# Small helpers (no GUI)
# ---------------------------------------------------------------------------

COVER_MAX = (252, 352)         # the cover of a card is this big; never decoded larger
COVER_RATIO = COVER_MAX[1] / COVER_MAX[0]
SIZE_MIN, SIZE_MAX, SIZE_DEFAULT = 110, 260, 170     # cover width (px at 100% scaling)
_PIL_CACHE_MAX = 500
_PHOTO_CACHE_MAX = 500
_MAX_SUGGESTIONS = 8
_SHOWN_SUGGESTIONS = 6


def load_cover(path: Path):
    """The card's cover as an RGBA PIL image (at most COVER_MAX), or None. Safe to call from a
    worker thread (touches no Tk objects); reads only the PNG part of the file."""
    from PIL import Image
    from kkafio.cards.similarity import read_png_preview

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
              accent="#3556d8", sel="#d3dcfb", input="#ffffff", border="#c9ced6", cover="#d9dde4")
_DARK = dict(bg="#17181c", side="#1d1f24", tile="#25282f", text="#e6e8ee", muted="#8a92a3",
             accent="#8ea6ff", sel="#2f3d6b", input="#12131a", border="#3a3f4b", cover="#33363f")


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

# assign(item, text) -> (new path, series folder it ended up in), or None if it couldn't be moved
AssignFn = Callable[[CharaItem, str], "tuple[Path, str] | None"]


def manual_dialog(title: str, items: Sequence[CharaItem],
                  suggest: Callable[[str], list[str]], assign: AssignFn) -> int:
    """Show the gallery; returns how many cards were moved into a series before it was closed.

    suggest   text typed so far -> series names to offer (best first)
    assign    (item, text typed) -> (new path, series folder) after moving the card there, or
              None if it couldn't be done. The dialog only shows; this does the moving.
    """
    try:
        return _ctk_dialog(title, items, suggest, assign)
    except ImportError as e:                       # tkinter / customtkinter missing
        raise TaskFailedError(f"The character gallery needs tkinter and customtkinter: {e}") from e


def _ctk_dialog(title, items, suggest, assign) -> int:
    import tkinter as tk
    from tkinter import font as tkfont
    import customtkinter as ctk
    from PIL import Image, ImageOps, ImageTk

    model = GalleryModel(items)
    moved = {"count": 0}

    root = new_window(title, topmost=False)       # sorting a library takes a while
    root.minsize(820, 520)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(1, weight=1)

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
    lh_name = f_name.metrics("linespace") + px(3)
    lh_small = f_small.metrics("linespace") + px(2)
    row_series = lh_small + px(6)                  # tall enough for the series input
    radius = px(12)                                # corners of the tiles and of the covers in them

    # ---- layout / drawing state -------------------------------------------
    lay: dict = {"items": [], "cols": 1, "tile_w": 0, "tile_h": 0, "img_w": 0, "img_h": 0,
                 "pad": px(10), "gap": px(12), "series_y": 0, "region_h": 1}
    drawn: dict[int, list[int]] = {}               # tile index -> its canvas items
    cover_item: dict[int, tuple[int, int]] = {}    # tile index -> (cover image item, "no preview" text item)
    sel: dict = {"item": None}                     # the selected card (highlighted; Enter edits its series)
    edit: dict = {"idx": None, "entry": None, "lb": None, "suggestions": [], "after": None}
    timers: dict[str, str | None] = {"poll": None, "search": None, "layout": None,
                                     "sidebar": None, "draw": None}

    # ---- covers: decoded on worker threads, applied on the Tk thread --------
    pil_cache: OrderedDict[Path, object] = OrderedDict()     # Path -> PIL image, or False
    photos: OrderedDict[tuple[Path, int], object] = OrderedDict()
    requested: set[Path] = set()
    cover_queue: queue.Queue = queue.Queue()
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kkafio-cover")

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
    # search bar
    top = ctk.CTkFrame(root, fg_color="transparent")
    top.grid(row=0, column=0, padx=12, pady=(12, 8), sticky="ew")
    top.grid_columnconfigure(0, weight=1)
    search_entry = ctk.CTkEntry(top, placeholder_text=t("dialog.manual.search_placeholder"), height=34)
    search_entry.grid(row=0, column=0, sticky="ew")

    body = ctk.CTkFrame(root, fg_color="transparent")
    body.grid(row=1, column=0, padx=12, pady=0, sticky="nsew")
    body.grid_columnconfigure(1, weight=1)
    body.grid_rowconfigure(0, weight=1)

    # sidebar
    side = ctk.CTkFrame(body, fg_color=pair("side"), corner_radius=8, width=230)
    side.grid(row=0, column=0, sticky="ns", padx=(0, 10))
    side.grid_propagate(False)
    side.grid_rowconfigure(4, weight=1)
    side.grid_columnconfigure(0, weight=1)

    def side_button(row: int, command) -> ctk.CTkButton:
        b = ctk.CTkButton(side, text="", anchor="w", height=30, corner_radius=6, fg_color="transparent",
                          text_color=pair("text"), hover_color=pair("sel"), command=command)
        b.grid(row=row, column=0, padx=8, pady=(8 if row == 0 else 2, 0), sticky="ew")
        return b

    all_btn = side_button(0, lambda: choose_view(VIEW_ALL))
    untagged_btn = side_button(1, lambda: choose_view(VIEW_UNTAGGED))
    ctk.CTkLabel(side, text=t("dialog.manual.series_header"), anchor="w", text_color=pair("muted"),
                 font=ctk.CTkFont(size=11, weight="bold")).grid(row=2, column=0, padx=14, pady=(14, 2), sticky="ew")
    series_filter_entry = ctk.CTkEntry(side, placeholder_text=t("dialog.manual.filter_series"), height=30)
    series_filter_entry.grid(row=3, column=0, padx=8, pady=(0, 6), sticky="ew")
    list_frame = tk.Frame(side, bg=pal["side"])
    list_frame.grid(row=4, column=0, padx=(8, 4), pady=(0, 8), sticky="nsew")
    list_frame.grid_columnconfigure(0, weight=1)
    list_frame.grid_rowconfigure(0, weight=1)
    series_box = tk.Listbox(list_frame, bg=pal["side"], fg=pal["text"], selectbackground=pal["sel"],
                            selectforeground=pal["text"], highlightthickness=0, borderwidth=0,
                            activestyle="none", exportselection=False, font=f_small)
    series_box.grid(row=0, column=0, sticky="nsew")
    series_sb = ctk.CTkScrollbar(list_frame, command=series_box.yview)
    series_sb.grid(row=0, column=1, sticky="ns")
    series_box.configure(yscrollcommand=series_sb.set)
    series_names: list[str] = []                   # what each row of series_box stands for

    # main area: toolbar + gallery
    main = ctk.CTkFrame(body, fg_color="transparent")
    main.grid(row=0, column=1, sticky="nsew")
    main.grid_columnconfigure(0, weight=1)
    main.grid_rowconfigure(1, weight=1)

    bar = ctk.CTkFrame(main, fg_color="transparent")
    bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    bar.grid_columnconfigure(3, weight=1)
    ctk.CTkLabel(bar, text=t("dialog.manual.sort_by"), text_color=pair("muted")).grid(row=0, column=0, padx=(0, 8))
    sort_labels = {t("dialog.manual.sort_updated"): SORT_UPDATED, t("dialog.manual.sort_name"): SORT_NAME,
                   t("dialog.manual.sort_size"): SORT_SIZE}
    sort_menu = ctk.CTkOptionMenu(bar, values=list(sort_labels), width=150, command=lambda _v: on_sort())
    sort_menu.set(t("dialog.manual.sort_updated"))
    sort_menu.grid(row=0, column=1, padx=(0, 8))
    order_btn = ctk.CTkButton(bar, text=t("dialog.manual.descending"), width=120, fg_color=pair("sel"),
                              text_color=pair("text"), hover_color=pair("border"), command=lambda: on_order())
    order_btn.grid(row=0, column=2)
    ctk.CTkLabel(bar, text=t("dialog.manual.size"), text_color=pair("muted")).grid(row=0, column=4, padx=(0, 8))
    size_slider = ctk.CTkSlider(bar, from_=SIZE_MIN, to=SIZE_MAX, width=160, command=lambda _v: on_size())
    size_slider.set(SIZE_DEFAULT)
    size_slider.grid(row=0, column=5)

    canvas = tk.Canvas(main, bg=pal["bg"], highlightthickness=0, borderwidth=0,
                       width=px(900), height=px(540), yscrollincrement=px(40), takefocus=1)
    canvas.grid(row=1, column=0, sticky="nsew")
    scroll_sb = ctk.CTkScrollbar(main, command=canvas.yview)
    scroll_sb.grid(row=1, column=1, sticky="ns", padx=(4, 0))

    hint = ctk.CTkLabel(root, text=t("dialog.manual.hint"), anchor="w", justify="left", wraplength=px(1000),
                        text_color=pair("muted"), font=ctk.CTkFont(size=11))
    hint.grid(row=2, column=0, padx=14, pady=(8, 0), sticky="ew")
    foot = ctk.CTkFrame(root, fg_color="transparent")
    foot.grid(row=3, column=0, padx=12, pady=10, sticky="ew")
    foot.grid_columnconfigure(0, weight=1)
    status = ctk.CTkLabel(foot, text="", anchor="w")
    status.grid(row=0, column=0, sticky="ew")
    ctk.CTkButton(foot, text=t("dialog.close"), width=110, command=lambda: on_close()).grid(row=0, column=1)

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
        img_h = int(round(img_w * COVER_RATIO))
        tile_h = img_h + px(8) + lh_name + row_series + 2 * lh_small + pad
        lay.update(items=model.visible(), img_w=img_w, img_h=img_h, tile_w=tile_w, tile_h=tile_h,
                   cols=cols, series_y=img_h + px(8) + lh_name)
        if sel["item"] is not None and index_of(sel["item"]) is None:
            sel["item"] = None
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
            canvas.create_text(cw // 2, ch // 3, text=t("dialog.manual.no_cards"), fill=pal["muted"], font=f_name)
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
            ids.append(canvas.create_text(tx + pad, y + px(2), anchor="nw", fill=pal["accent"], font=f_small,
                                          text=ellipsize(f_small.measure, item.series, text_w)))
        else:
            ids.append(canvas.create_text(tx + pad, y + px(2), anchor="nw", fill=pal["muted"], font=f_italic,
                                          text=t("dialog.manual.unknown_series")))
        y += row_series
        ids.append(canvas.create_text(tx + pad, y, anchor="nw", fill=pal["muted"], font=f_small,
                                      text=format_size(item.size)))
        ids.append(canvas.create_text(tx + pad, y + lh_small, anchor="nw", fill=pal["muted"], font=f_small,
                                      text=ellipsize(f_small.measure, format_stamp(item.mtime), text_w)))
        if sel["item"] is item:                    # highlight: an accent border over the whole tile
            half = px(3) / 2
            ids.append(canvas.create_polygon(
                rounded_rect_points(tx + half, ty + half, tx + tile_w - half, ty + lay["tile_h"] - half, radius),
                smooth=True, splinesteps=24, fill="", outline=pal["accent"], width=px(3), joinstyle="round"))
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

    def set_selected(item) -> None:
        old = sel["item"]
        if old is item:
            return
        sel["item"] = item
        for it in (old, item):
            i = index_of(it) if it is not None else None
            if i is not None:
                redraw_tile(i)

    def first_visible_index() -> int:
        step = lay["tile_h"] + lay["gap"]
        row = max(0, int(-(-(canvas.canvasy(0) - lay["gap"]) // step)))
        return min(row * lay["cols"], len(lay["items"]) - 1)

    def move_card_selection(dx: int, dy: int) -> None:
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
        set_selected(items[new])
        ensure_visible(new)

    def locate(ex: int, ey: int) -> tuple[int, bool] | None:
        """(tile index, over its series line?) for a point in the canvas window, or None."""
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
        sy = ty + lay["series_y"]
        in_series = sy <= y <= sy + row_series and tx + lay["pad"] <= x <= tx + tile_w - lay["pad"]
        return idx, in_series

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
        total, untagged = model.counts()
        all_btn.configure(text=f"{t('dialog.manual.all')}   {total:,}")
        untagged_btn.configure(text=f"{t('dialog.manual.untagged')}   {untagged:,}")
        all_btn.configure(fg_color=pair("sel") if model.mode == VIEW_ALL else "transparent")
        untagged_btn.configure(fg_color=pair("sel") if model.mode == VIEW_UNTAGGED else "transparent")
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

    def update_status() -> None:
        total, untagged = model.counts()
        shown = len(lay["items"])
        status.configure(text=t("dialog.manual.status", total=total, untagged=untagged) if shown == total
                         else t("dialog.manual.status_filtered", shown=shown, total=total, untagged=untagged))

    def choose_view(mode: str, series: str = "") -> None:
        model.set_view(mode, series)
        if mode != VIEW_SERIES:
            series_box.selection_clear(0, "end")
        refresh_sidebar()
        relayout(keep_scroll=False)

    def on_series_pick(_event=None) -> None:
        sel = series_box.curselection()
        if sel and sel[0] < len(series_names):
            choose_view(VIEW_SERIES, series_names[sel[0]])

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
        if entry is None or idx is None:
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
                        highlightbackground=pal["border"], highlightcolor=pal["border"], font=f_small, takefocus=0)
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

    def start_edit(idx: int) -> None:
        cancel_edit()
        ensure_visible(idx)
        item = lay["items"][idx]
        tx, ty = tile_xy(idx)
        entry = tk.Entry(canvas, font=f_small, bg=pal["input"], fg=pal["text"], insertbackground=pal["text"],
                         relief="flat", highlightthickness=1, highlightbackground=pal["border"],
                         highlightcolor=pal["accent"])
        entry.insert(0, item.series or "")
        entry.select_range(0, "end")
        edit.update(idx=idx, entry=entry)
        canvas.create_window(tx + lay["pad"], ty + lay["series_y"] - px(1), anchor="nw", window=entry,
                             width=lay["tile_w"] - 2 * lay["pad"], height=row_series, tags="editor")
        entry.bind("<Return>", lambda _e: (confirm(), "break")[1])
        entry.bind("<KP_Enter>", lambda _e: (confirm(), "break")[1])
        entry.bind("<Escape>", cancel_and_focus)
        entry.bind("<Down>", move_selection(+1))
        entry.bind("<Up>", move_selection(-1))
        entry.bind("<Tab>", on_tab)
        entry.bind("<KeyRelease>", on_entry_key)
        entry.bind("<FocusOut>", on_entry_focus_out)
        entry.focus_set()
        refresh_suggestions()

    def confirm(text: str | None = None) -> None:
        idx, entry = edit["idx"], edit["entry"]
        if idx is None or entry is None:
            return
        if text is None:
            text = selected_suggestion() or entry.get()
        text = text.strip()
        item = lay["items"][idx]
        cancel_edit()
        canvas.focus_set()
        if not text:
            return
        result = assign(item, text)
        if result is None:
            status.configure(text=t("dialog.manual.move_failed", name=item.name))
            return
        new_path, series = result
        old_path = item.path
        if new_path != old_path:
            if old_path in pil_cache:                      # the cover follows the card
                pil_cache[new_path] = pil_cache.pop(old_path)
            moved["count"] += 1
        model.assigned(item, new_path, series)
        refresh_sidebar()
        if model.mode == VIEW_SERIES and model.series_count(model.series_filter) == 0:
            model.set_view(VIEW_ALL)
            refresh_sidebar()
        relayout(keep_scroll=True)
        if sel["item"] is None and lay["items"]:    # it left this view (e.g. Untagged): select the next card
            nxt = min(idx, len(lay["items"]) - 1)
            set_selected(lay["items"][nxt])
            ensure_visible(nxt)
        status.configure(text=t("dialog.manual.moved", name=item.name, series=series))

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
        idx, in_series = hit
        set_selected(lay["items"][idx])
        if in_series:
            start_edit(idx)

    def on_enter_key(_event=None) -> str:
        items = lay["items"]
        if items:
            idx = index_of(sel["item"])
            if idx is None:
                idx = first_visible_index()
                set_selected(items[idx])
            start_edit(idx)
        return "break"

    def nav(dx: int, dy: int):
        def handler(event) -> str | None:
            if event.state & 0x4:                  # Ctrl+A etc. are not navigation
                return None
            move_card_selection(dx, dy)
            return "break"
        return handler

    def on_motion(event) -> None:
        hit = locate(event.x, event.y)
        canvas.configure(cursor="hand2" if hit is not None and hit[1] else "")

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
        relayout(keep_scroll=False)

    def on_order() -> None:
        model.descending = not model.descending
        order_btn.configure(text=t("dialog.manual.descending") if model.descending else t("dialog.manual.ascending"))
        relayout(keep_scroll=False)

    def on_size() -> None:
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

    def on_close(_event=None) -> None:
        root.quit()

    canvas.configure(yscrollcommand=on_yscroll)
    canvas.bind("<Button-1>", on_click)
    canvas.bind("<Motion>", on_motion)
    canvas.bind("<Configure>", on_canvas_configure)
    canvas.bind("<Prior>", scroll_key(pages=-1))
    canvas.bind("<Next>", scroll_key(pages=1))
    for keys, dx, dy in ((("Left", "a", "A"), -1, 0), (("Right", "d", "D"), 1, 0),
                         (("Up", "w", "W"), 0, -1), (("Down", "s", "S"), 0, 1)):
        for key in keys:
            canvas.bind(f"<{key}>", nav(dx, dy))
    canvas.bind("<Return>", on_enter_key)
    canvas.bind("<KP_Enter>", on_enter_key)
    canvas.bind("<Home>", scroll_key(home=0.0))
    canvas.bind("<End>", scroll_key(home=1.0))
    root.bind("<MouseWheel>", on_wheel)
    root.bind("<Button-4>", on_wheel)
    root.bind("<Button-5>", on_wheel)
    series_box.bind("<<ListboxSelect>>", on_series_pick)
    search_entry.bind("<KeyRelease>", on_search_key)
    series_filter_entry.bind("<KeyRelease>", on_series_filter_key)
    root.bind("<Escape>", on_close)
    root.protocol("WM_DELETE_WINDOW", on_close)

    refresh_sidebar()
    timers["poll"] = root.after(50, poll_covers)
    schedule_layout(0, keep_scroll=False)

    try:
        show_and_wait(root, canvas)
    finally:
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
        executor.shutdown(wait=False, cancel_futures=True)
    return moved["count"]
