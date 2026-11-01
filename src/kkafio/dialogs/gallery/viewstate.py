"""
viewstate.py — What the gallery window remembers between runs: the tab that was open last and,
for each tab, how its cards were sorted (by what, ascending or descending) and how big they were.

Saved in ``<CONFIG_DIR>/config/gallery-view.json`` (on Windows: %APPDATA%/KKAFIO/config), next to
gallery-shortcuts.json but in a file of its own: the shortcuts are written the moment the user
changes one, this is written once when the window closes, and neither should be able to
overwrite the other. A tab is known by its `GalleryTab.key` ("scene", "chara", "outfit"), not by its
translated label, so the language can change. No GUI in here.

Anything unreadable or out of range is ignored and the default is used instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kkafio.core.session_store import SessionStore
from kkafio.dialogs.gallery.model import MODS_ALL, MODS_COMPLETE, MODS_MISSING, SORT_CREATED, SORT_FILENAME, SORT_NAME, SORT_SIZE, SORT_UPDATED

TAG = "GALLERY"
FILE_NAME = "gallery-view.json"
_MODS_FILTERS = frozenset({MODS_ALL, MODS_COMPLETE, MODS_MISSING})
_SORT_KEYS = frozenset({SORT_CREATED, SORT_UPDATED, SORT_NAME, SORT_FILENAME, SORT_SIZE})


@dataclass
class TabView:
    """How one tab shows its cards. `size` is the cover size slider's value (None: the default)."""

    sort: str = SORT_UPDATED
    descending: bool = True
    size: float | None = None
    mods: str = MODS_ALL                    # the mods filter (all cards / all mods installed / missing mods)


class GalleryViewState:
    """The remembered view of every tab. `store` is where it is kept (None: nothing is read or
    written, e.g. in tests)."""

    def __init__(self, store: SessionStore | None = None):
        self.store = store
        self.last_tab = ""
        self.mods_open = True                       # the mod dependencies sidebar, expanded or not
        self.tabs: dict[str, TabView] = {}
        if store is not None:
            self._load()

    @classmethod
    def from_user_config(cls) -> "GalleryViewState":
        from kkafio.core.paths import CONFIG_DIR
        return cls(SessionStore(CONFIG_DIR / "config" / FILE_NAME, TAG))

    def view(self, key: str) -> TabView:
        """The remembered view of the tab `key` (a copy; the defaults for a tab never seen)."""
        v = self.tabs.get(key)
        return TabView(v.sort, v.descending, v.size, v.mods) if v else TabView()

    def initial_index(self, keys: Sequence[str], fallback: int, usable: Sequence[bool]) -> int:
        """The tab to open on: the one that was open last if it is still there and has cards
        (`usable`), else `fallback`."""
        if self.last_tab in keys:
            i = list(keys).index(self.last_tab)
            if usable[i]:
                return i
        return fallback

    def remember(self, last_tab: str, views: dict[str, TabView], mods_open: bool | None = None) -> bool:
        """Keep `last_tab` and the views (a tab that isn't in `views` keeps what it had) and save.
        `mods_open` is whether the mod dependencies sidebar was expanded (None: leave as it was)."""
        self.last_tab = last_tab
        if mods_open is not None:
            self.mods_open = mods_open
        self.tabs.update(views)
        return self.save()

    # -- the file --------------------------------------------------------------------------------

    def _load(self) -> None:
        data = self.store.load()
        last = data.get("last_tab")
        self.last_tab = last if isinstance(last, str) else ""
        mods_open = data.get("mods_open")
        self.mods_open = mods_open if isinstance(mods_open, bool) else True
        tabs = data.get("tabs")
        if not isinstance(tabs, dict):
            return
        for key, raw in tabs.items():
            if not isinstance(raw, dict):
                continue
            sort, descending, size = raw.get("sort"), raw.get("descending"), raw.get("size")
            mods = raw.get("mods")
            self.tabs[str(key)] = TabView(
                sort if sort in _SORT_KEYS else SORT_UPDATED,
                descending if isinstance(descending, bool) else True,
                float(size) if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0 else None,
                mods if mods in _MODS_FILTERS else MODS_ALL)

    def save(self) -> bool:
        if self.store is None:
            return True
        return self.store.save({
            "version": 1, "last_tab": self.last_tab, "mods_open": self.mods_open,
            "tabs": {k: {"sort": v.sort, "descending": v.descending, "size": v.size, "mods": v.mods}
                      for k, v in self.tabs.items()}})
