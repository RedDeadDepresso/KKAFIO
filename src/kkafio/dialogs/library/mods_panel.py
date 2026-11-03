"""
mods_panel — What the library's mod dependencies sidebar knows, with no GUI in it.

For the card the cursor is on, the sidebar lists the GUIDs of the mods the card uses, each marked
installed or missing (like the "Mod dependencies" list of a mod manager). This module answers the
two questions behind it:

  * which GUIDs does a card use?   `CardMods.guids()` runs the tab's parser (`LibraryTab.guids`)
                                   and remembers the answer for as long as the file is unchanged;
  * which of them are installed?   `ModsLookup` scans the mods folder(s) once (the same incremental
                                   cache Archive Cards uses) and resolves GUIDs against it.

Sideloader Modpack mods are resolved through the bundled modpack index, like Archive Cards does:
the index says where the zipmod of a GUID lives, so only that file has to be checked and the
modpack folders are never opened.

Both classes are thread-safe: the window calls them from a worker thread.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from kkafio.core.config import GameType
from kkafio.core.logger import logger
from kkafio.cards.mods.zipmods import local_mods_map, resolve_guid
from kkafio.indices.modpack import load_modpack_index

TAG = "LIBRARY"


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
            # explorer wants ONE argument of the form  /select,"C:\a b\c.zipmod"  (quotes around
            # the path only). As a list, Python would quote the whole argument whenever the path
            # has a space, explorer wouldn't understand it and would open a default folder, so
            # the command line is built by hand. (It also returns 1 even on success.)
            subprocess.Popen(f'explorer /select,"{os.path.normpath(str(path))}"')
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

    `mods_dir` is the main folder (the installed mods folder); `extra_dirs` are further folders (the
    downloaded mods folder) whose mods count as installed too — a GUID is installed when any of the
    folders has it, the main folder being looked in first. With no folder at all (or none that
    exists) there is nothing to look in: `available` is False and `resolve()` returns every GUID
    without a path.
    """

    def __init__(self, mods_dir: Path | None, game_type: str = GameType.KOIKATSU.value,
                 use_cache: bool = True, extra_dirs: Sequence[Path | None] = ()):
        self.mods_dir = Path(mods_dir).resolve() if mods_dir is not None else None
        # every folder to look in, resolved, without repeats (installed and downloaded may be the same)
        self.mods_dirs: list[Path] = []
        for d in (mods_dir, *extra_dirs):
            if d is None:
                continue
            d = Path(d).resolve()
            if d not in self.mods_dirs:
                self.mods_dirs.append(d)
        if self.mods_dir is None and self.mods_dirs:
            self.mods_dir = self.mods_dirs[0]
        self.game_type = game_type
        self.use_cache = use_cache
        self._lock = threading.Lock()
        self._local: dict[Path, dict[str, Path]] | None = None   # per folder: GUIDs of the mods outside the modpack
        self._modpack: dict[str, list[str]] | None = None  # the bundled index: GUID -> download URLs

    @property
    def available(self) -> bool:
        return any(d.is_dir() for d in self.mods_dirs)

    def _load(self) -> None:
        """Scan the mods folder (once; the first card pays for it)."""
        with self._lock:
            if self._local is not None:
                return
            self._modpack = load_modpack_index(game_type=self.game_type) or {}
            local: dict[Path, dict[str, Path]] = {}
            for folder in self.mods_dirs:
                if not folder.is_dir():
                    continue
                try:
                    local[folder] = local_mods_map(folder, include_modpack=False,
                                                   use_cache=self.use_cache)
                except OSError as e:
                    logger.warning(TAG, f"Could not scan the mods folder {folder}: {e}")
                    local[folder] = {}
                logger.info(TAG, f"Mods folder: {len(local[folder])} mod(s) found in {folder}")
            self._local = local

    def find(self, guid: str) -> Path | None:
        """The zipmod of `guid`, or None when it isn't installed."""
        if not self.available:
            return None
        self._load()
        # A Sideloader Modpack mod is checked at its indexed path (the modpack folders are never
        # opened); if it isn't there it may still be installed elsewhere in the mods folder,
        # which the local scan covers.
        for folder, local in (self._local or {}).items():
            found = resolve_guid(guid, local, self._modpack or {}, folder, include_modpack=True)
            if found is not None:
                return found
        return None

    def resolve(self, guids: Sequence[str]) -> list[ModRow]:
        return [ModRow(g, self.find(g)) for g in guids]


class CardMods:
    """The mods of cards: finds a card's GUIDs (once per unchanged file) and resolves them.

    A card's GUIDs come, in order, from the in-memory cache (keyed by path, mtime and size), from
    the tab's on-disk GUID index when the window has handed one over with `set_index`, and
    finally from `parse` (the tab's `LibraryTab.guids`) reading the card file. The result is
    sorted with `sort_rows`; a card that can't be read raises whatever `parse` raises, so the
    window can say so.
    """

    def __init__(self, lookup: ModsLookup, max_cached: int = 400):
        self.lookup = lookup
        self._max = max_cached
        self._guids: dict[tuple[Path, int, int], tuple[str, ...]] = {}
        # (time_ns the index was read from disk, {str(path): GUIDs}), one per tab that has read it
        self._indexes: list[tuple[int, Mapping[str, Sequence[str]]]] = []
        self._lock = threading.Lock()
        self._loaded: dict[Callable, Mapping[str, Sequence[str]]] = {}    # loader -> what it returned
        self._load_lock = threading.Lock()              # one index read at a time, each only once

    def index_for(self, load: Callable[[], Mapping[str, Sequence[str]]]) -> Mapping[str, Sequence[str]]:
        """The index `load()` returns (the tab's `LibraryTab.guid_index`), read once however many
        callers ask — the filter scan and the sidebar share it. A caller that arrives while it is
        being read waits for that read rather than starting another. An index that can't be read
        is empty, and not tried again; the cards are then read one by one."""
        with self._load_lock:
            if load in self._loaded:
                return self._loaded[load]
            read_at = time.time_ns()
            try:
                index = load()
            except Exception as e:
                logger.warning(TAG, f"Could not read the GUID cache, reading the cards one by one: {e}")
                index = {}
            self._loaded[load] = index
        if index:
            self.set_index(index, read_at)
        return index

    def set_index(self, index: Mapping[str, Sequence[str]], read_at_ns: int | None = None) -> None:
        """Use `index` ({str(path): GUIDs}, the on-disk per-file GUID cache) so a card it lists
        isn't parsed again. `read_at_ns` is the time.time_ns() taken *before* the index was read:
        a card file modified after that is not trusted to the index and is parsed instead."""
        stamp = time.time_ns() if read_at_ns is None else read_at_ns
        with self._lock:
            self._indexes.append((stamp, index))

    def _from_index(self, path: Path, mtime_ns: int) -> tuple[str, ...] | None:
        key = str(path)
        with self._lock:
            indexes = list(self._indexes)
        for read_at, index in indexes:
            known = index.get(key)
            if known is not None and mtime_ns <= read_at:
                return tuple(dict.fromkeys(known))
        return None

    def rows(self, path: Path, parse: Callable[[Path], Sequence[str]]) -> list[ModRow]:
        st = path.stat()
        key = (path, st.st_mtime_ns, st.st_size)
        with self._lock:
            cached = self._guids.get(key)
        if cached is None:
            cached = self._from_index(path, st.st_mtime_ns)
            if cached is None:
                cached = tuple(dict.fromkeys(parse(path)))     # unique, in the card's order
            with self._lock:
                if len(self._guids) >= self._max:
                    self._guids.pop(next(iter(self._guids)))
                self._guids[key] = cached
        return sort_rows(self.lookup.resolve(cached))

    def rows_for(self, guids: Sequence[str]) -> list[ModRow]:
        """The rows of a card whose GUIDs are already known (e.g. from the on-disk per-file GUID
        cache), so the card file isn't read again."""
        return sort_rows(self.lookup.resolve(tuple(dict.fromkeys(guids))))


def mods_dir_for(configured: str, default: Path | str | None) -> Path | None:
    """The mods folder to look in: the one that was configured, else the game's (None when there
    is neither). A configured folder that doesn't exist is the caller's error to report."""
    if configured.strip():
        return Path(configured)
    return Path(default) if default else None
