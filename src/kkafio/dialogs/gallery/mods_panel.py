"""
mods_panel — What the gallery's mod dependencies sidebar knows, with no GUI in it.

For the card the cursor is on, the sidebar lists the GUIDs of the mods the card uses, each marked
installed or missing (like the "Mod dependencies" list of a mod manager). This module answers the
two questions behind it:

  * which GUIDs does a card use?   `CardMods.guids()` runs the tab's parser (`GalleryTab.guids`)
                                   and remembers the answer for as long as the file is unchanged;
  * which of them are installed?   `ModsLookup` scans the mods folder once (the same incremental
                                   cache Archive Cards uses) and resolves GUIDs against it.

Sideloader Modpack mods are resolved through the bundled modpack index, like Archive Cards does:
the index says where the zipmod of a GUID lives, so only that file has to be checked and the
modpack folders are never opened.

Both classes are thread-safe: the window calls them from a worker thread.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from kkafio.core.config import GameType
from kkafio.core.logger import logger
from kkafio.cards.mods.zipmods import local_mods_map, resolve_guid
from kkafio.indices.modpack import load_modpack_index

TAG = "GALLERY"


@dataclass(frozen=True)
class ModRow:
    """One mod of a card. `path` is its zipmod when installed, None when missing — or when the
    mods folder is unknown (`ModsLookup.available` is then False and nothing can be said)."""

    guid: str
    path: Path | None


def google_search_url(guid: str) -> str:
    """The Google search for a GUID (what the search icon of a missing mod opens)."""
    return "https://www.google.com/search?q=" + quote_plus(guid)


def reveal_in_file_manager(path: Path) -> None:
    """Show `path` selected in the system's file manager (the folder opens where there is no way
    to select a file). Failures are logged, never raised: this is a convenience button."""
    try:
        if sys.platform == "win32":
            # explorer wants one "/select,<path>" argument and returns 1 even on success
            subprocess.Popen(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as e:
        logger.warning(TAG, f"Could not show {path} in the file manager: {e}")


def sort_rows(rows: Sequence[ModRow]) -> list[ModRow]:
    """Missing mods first (they are what needs attention), then by GUID."""
    return sorted(rows, key=lambda r: (r.path is not None, r.guid.casefold()))


def summarize(rows: Sequence[ModRow]) -> tuple[int, int]:
    """(installed, missing) of `rows`."""
    installed = sum(1 for r in rows if r.path is not None)
    return installed, len(rows) - installed


class ModsLookup:
    """Which GUIDs are installed in a mods folder.

    `mods_dir` None (or a folder that isn't there) means there is nothing to look in: `available`
    is False and `resolve()` returns every GUID without a path.
    """

    def __init__(self, mods_dir: Path | None, game_type: str = GameType.KOIKATSU.value,
                 use_cache: bool = True):
        self.mods_dir = Path(mods_dir).resolve() if mods_dir is not None else None
        self.game_type = game_type
        self.use_cache = use_cache
        self._lock = threading.Lock()
        self._local: dict[str, Path] | None = None         # GUIDs of the mods outside the modpack
        self._modpack: dict[str, str] | None = None        # the bundled index: GUID -> relative path

    @property
    def available(self) -> bool:
        return self.mods_dir is not None and self.mods_dir.is_dir()

    def _load(self) -> None:
        """Scan the mods folder (once; the first card pays for it)."""
        with self._lock:
            if self._local is not None:
                return
            self._modpack = load_modpack_index(game_type=self.game_type) or {}
            try:
                self._local = local_mods_map(self.mods_dir, include_modpack=False,
                                             use_cache=self.use_cache)
            except OSError as e:
                logger.warning(TAG, f"Could not scan the mods folder {self.mods_dir}: {e}")
                self._local = {}
            logger.info(TAG, f"Mods folder: {len(self._local)} mod(s) found in {self.mods_dir}")

    def find(self, guid: str) -> Path | None:
        """The zipmod of `guid`, or None when it isn't installed."""
        if not self.available:
            return None
        self._load()
        # A Sideloader Modpack mod is checked at its indexed path (the modpack folders are never
        # opened); if it isn't there it may still be installed elsewhere in the mods folder,
        # which the local scan covers.
        return resolve_guid(guid, self._local or {}, self._modpack or {}, self.mods_dir,
                            include_modpack=True)

    def resolve(self, guids: Sequence[str]) -> list[ModRow]:
        return [ModRow(g, self.find(g)) for g in guids]


class CardMods:
    """The mods of cards: parses a card's GUIDs (once per unchanged file) and resolves them.

    `parse` is the tab's `GalleryTab.guids`. The result is sorted with `sort_rows`; a card that
    can't be read raises whatever `parse` raises, so the window can say so.
    """

    def __init__(self, lookup: ModsLookup, max_cached: int = 400):
        self.lookup = lookup
        self._max = max_cached
        self._guids: dict[tuple[Path, int, int], tuple[str, ...]] = {}
        self._lock = threading.Lock()

    def rows(self, path: Path, parse: Callable[[Path], Sequence[str]]) -> list[ModRow]:
        st = path.stat()
        key = (path, st.st_mtime_ns, st.st_size)
        with self._lock:
            cached = self._guids.get(key)
        if cached is None:
            cached = tuple(dict.fromkeys(parse(path)))     # unique, in the card's order
            with self._lock:
                if len(self._guids) >= self._max:
                    self._guids.pop(next(iter(self._guids)))
                self._guids[key] = cached
        return sort_rows(self.lookup.resolve(cached))


def mods_dir_for(configured: str, default: Path | str | None) -> Path | None:
    """The mods folder to look in: the one that was configured, else the game's (None when there
    is neither). A configured folder that doesn't exist is the caller's error to report."""
    if configured.strip():
        return Path(configured)
    return Path(default) if default else None
