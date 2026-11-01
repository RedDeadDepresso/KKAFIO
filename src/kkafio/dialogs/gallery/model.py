"""
model — What the gallery window shows and is told, with no GUI in it.

`CharaItem` (one card), `GalleryModel` (which cards show: view, search, sort), `DialogText` (the
window's sentences), `GalleryTab` (one tab: its items and the suggest / assign callbacks that do
the real work) and the plain file helpers behind renaming a card. The tabs (characters.py,
coordinates.py, scenes.py) build `GalleryTab`s from this module; window.py draws them.

The dialog doesn't know what a "series" is: a card's `series` is just the folder it sits in under
`CharaItem.root`, and every sentence that names things comes from a `DialogText`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from kkafio.core.i18n import t


SORT_CREATED = "created"
SORT_UPDATED = "updated"
SORT_NAME = "name"
SORT_FILENAME = "filename"
SORT_SIZE = "size"

VIEW_ALL = "all"
VIEW_UNTAGGED = "untagged"
VIEW_SERIES = "series"
VIEW_TRASH = "trash"          # the cards marked for the Recycle Bin (sent there when the window closes)


@dataclass
class CharaItem:
    """One character card in the gallery."""

    path: Path
    root: Path                 # the chara folder it was found under; series folders go inside it
    name: str                  # shown under the cover
    series: str | None         # its series folder (first folder under `root`); None = untagged
    size: int                  # bytes
    mtime: float               # updated
    ctime: float = 0.0         # created (0: unknown)
    alias: str = ""            # extra text the search also looks at (e.g. the character's own name)
    trashed: bool = False      # in the gallery's trash: hidden everywhere else, the file is only
                               # sent to the Recycle Bin when the window closes


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
            if item.trashed != (self.mode == VIEW_TRASH):      # the trash only shows in its own view
                continue
            if self.mode == VIEW_UNTAGGED and item.series is not None:
                continue
            if self.mode == VIEW_SERIES and (item.series or "").casefold() != want:
                continue
            if needle and needle not in f"{item.name}\n{item.alias}\n{item.path.name}\n{item.series or ''}".casefold():
                continue
            out.append(item)
        keys = {SORT_CREATED: lambda i: i.ctime or i.mtime,
                SORT_UPDATED: lambda i: i.mtime,
                SORT_NAME: lambda i: (i.name.casefold(), i.path.name.casefold()),
                SORT_FILENAME: lambda i: (i.path.name.casefold(), i.name.casefold()),
                SORT_SIZE: lambda i: i.size}
        out.sort(key=keys.get(self.sort_key, keys[SORT_UPDATED]), reverse=self.descending)
        return out

    # -- the sidebar --------------------------------------------------------

    def counts(self) -> tuple[int, int]:
        """(all cards, untagged cards); the cards in the trash are in neither."""
        live = [i for i in self.items if not i.trashed]
        return len(live), sum(1 for i in live if i.series is None)

    def trash_count(self) -> int:
        return sum(1 for i in self.items if i.trashed)

    def series_list(self, text: str = "") -> list[tuple[str, int]]:
        """(series, number of cards) for every series that has a card, by name; `text` filters
        by a case-insensitive substring. A series written in different cases is one entry."""
        names: dict[str, str] = {}
        counts: dict[str, int] = {}
        for item in self.items:
            if item.series and not item.trashed:
                k = item.series.casefold()
                names.setdefault(k, item.series)
                counts[k] = counts.get(k, 0) + 1
        needle = text.strip().casefold()
        return [(names[k], counts[k]) for k in sorted(names) if needle in k]

    def series_count(self, series: str) -> int:
        k = series.casefold()
        return sum(1 for i in self.items if not i.trashed and (i.series or "").casefold() == k)

    # -- changes ------------------------------------------------------------

    def assigned(self, item: CharaItem, new_path: Path, series: str) -> None:
        """`item` was moved to `new_path`, into the folder `series`."""
        item.path = Path(new_path)
        item.series = series

    def trash(self, items: Iterable[CharaItem]) -> None:
        """Move the cards into the trash. Nothing happens to the files here."""
        for item in items:
            item.trashed = True

    def restore(self, items: Iterable[CharaItem]) -> None:
        """Take the cards out of the trash; each goes back to the folder it is still in."""
        for item in items:
            item.trashed = False

    def renamed(self, item: CharaItem, new_path: Path) -> None:
        """`item`'s file was renamed to `new_path` (same folder); its shown name follows."""
        item.path = Path(new_path)
        item.name = item.path.stem


COVER_MAX = (252, 352)         # the cover of a card is this big; never decoded larger
COVER_RATIO = COVER_MAX[1] / COVER_MAX[0]


_INVALID_NAME_CHARS = frozenset('<>:"/\\|?*') | frozenset(chr(i) for i in range(32))
_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                             *(f"LPT{i}" for i in range(1, 10))})


def clean_file_stem(text: str, suffix: str = "") -> str:
    """The file name (without its extension) that `text` stands for, or "" if it can't be one.
    Typing the extension too is fine (`suffix` is cut off once); characters no file system
    accepts, Windows' reserved names and names of only dots/spaces are refused."""
    stem = text.strip()
    if suffix and stem.casefold().endswith(suffix.casefold()):
        stem = stem[:-len(suffix)]
    stem = stem.rstrip(". ").lstrip()               # Windows drops a trailing dot or space
    if not stem or any(c in _INVALID_NAME_CHARS for c in stem) or stem.split(".")[0].upper() in _RESERVED_NAMES:
        return ""
    return stem


def rename_card(path: Path, stem: str) -> Path:
    """Rename `path` to `stem` + its extension, in the same folder; returns the new path.
    Raises FileExistsError if another file already has that name (nothing is overwritten) and
    OSError if the rename fails. Changing only the case of the name is allowed."""
    dest = path.with_name(stem + path.suffix)
    if dest == path:
        return path
    if dest.exists() and not os.path.samefile(dest, path):
        raise FileExistsError(str(dest))
    path.rename(dest)
    return dest


@dataclass(frozen=True)
class DialogText:
    """The words of one use of the gallery. Plain translated strings; the ones with `{...}` in them
    are str.format templates:
      status / status_filtered   {total} {untagged}  /  {shown} {total} {untagged}
      moved                      {name} {series}
      move_failed                {name}
      multi_prompt               {count}
      multi_moved                {count} {series}
      multi_moved_partial        {count} {series} {failed}
    """

    search_placeholder: str
    all: str                    # sidebar: every card
    untagged: str               # sidebar: the cards that aren't in a folder yet
    series_header: str          # sidebar: heading of the folder list
    filter_series: str          # sidebar: placeholder of the folder filter
    unknown_series: str         # under a cover that isn't in a folder yet
    no_cards: str
    hint: str
    status: str
    status_filtered: str
    moved: str
    move_failed: str
    multi_title: str
    multi_prompt: str
    multi_moved: str
    multi_moved_partial: str


def chara_dialog_text() -> DialogText:
    """The window's default words, also the Character tab's: characters sorted into series folders."""
    return DialogText(
        search_placeholder=t("dialog.manual.search_placeholder"),
        all=t("dialog.manual.all"),
        untagged=t("dialog.manual.untagged"),
        series_header=t("dialog.manual.series_header"),
        filter_series=t("dialog.manual.filter_series"),
        unknown_series=t("dialog.manual.unknown_series"),
        no_cards=t("dialog.manual.no_cards"),
        hint=t("dialog.manual.hint"),
        status=t("dialog.manual.status"),
        status_filtered=t("dialog.manual.status_filtered"),
        moved=t("dialog.manual.moved"),
        move_failed=t("dialog.manual.move_failed"),
        multi_title=t("dialog.manual.multi_title"),
        multi_prompt=t("dialog.manual.multi_prompt"),
        multi_moved=t("dialog.manual.multi_moved"),
        multi_moved_partial=t("dialog.manual.multi_moved_partial"),
    )


# assign(item, text) -> (new path, series folder it ended up in), or None if it couldn't be moved
AssignFn = Callable[[CharaItem, str], "tuple[Path, str] | None"]


@dataclass
class GalleryTab:
    """One gallery of a `gallery_dialog()`.

    label        the tab's text
    items        the cards it shows
    suggest      text typed so far -> series names to offer (best first)
    assign       (item, text typed) -> (new path, series folder) after moving the card there, or
                 None if it couldn't be done. The dialog only shows; this does the moving.
    text         the sentences of this gallery (default: Group Characters')
    cover_ratio  cover height / width (default: a character card's portrait; scenes are landscape)
    finish       called once when the window is gone, also if it failed: whatever the task has to
                 tidy up after the moves (it saves its caches here). Optional.
    """

    label: str
    items: Sequence[CharaItem]
    suggest: Callable[[str], list[str]]
    assign: AssignFn
    text: DialogText | None = None
    cover_ratio: float = COVER_RATIO
    finish: Callable[[], None] | None = None
