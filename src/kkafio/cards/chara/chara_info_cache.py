"""
chara_info_cache.py — Remembers what was read off each chara card between runs.

Group Characters and Rename Characters both need a card's identity (last name, first name,
nickname, personality, hair colour, birthday) for every PNG, and getting it means reading the
whole file and fully parsing the character data — tens of MB per card. Group Characters even
does that twice per run (once to export, once to move). This cache stores, per file, the raw
fields read the last time (see chara_key.CharaInfo):

    {"<path>": {"fp": [mtime, size],
                "info": {"last", "first", "nick", "pers", "hair": [r, g, b] | null,
                         "birth": [month, day]} | null}}

The key string ("last | first | nick | personality | hair colour") is not stored: `key_of(info)`
builds it from the fields, so changing how keys are built needs no re-read of any card.
`info` is null for a PNG that isn't a chara card, so those aren't re-read either. An entry that
is missing a field, or whose fields are malformed, counts as a miss and the card is read again.
An entry is only trusted while the file's [mtime, size] still match, so any edit to a card makes
it be read again.

One cache file per scanned folder (CACHE_FILE, same layout and atomic write as the
other incremental caches). Both tasks share it, and both keep it in step with what
they do to the files: a moved or renamed card's entry follows it to its new path,
and a card whose metadata was rewritten gets its new fields and fingerprint — so the
next run doesn't have to read any of them again.

Not to be confused with kkafio_rename_cache.json, which holds the LLM's
translations (key -> names) and is always used.
"""

from __future__ import annotations

import os
from pathlib import Path

from kkafio.cards.cache_io import atomic_write_json, file_fp
from kkafio.cards.chara.chara_key import CharaInfo
from kkafio.core.logger import logger

# Not the old kkafio_chara_key_cache.json (which stored finished key strings): that file is
# simply never read again.
CACHE_FILE = "kkafio_chara_info_cache.json"

# Bump when the entry layout changes in a way CharaInfo.from_json can't tell from a missing or
# malformed field: caches written by an older version are then ignored, not trusted. A new
# CharaInfo field doesn't need it; entries without the field are read again by themselves.
CACHE_VERSION = 1


def _load(folder: Path) -> dict[str, dict]:
    import json
    try:
        data = json.loads((folder / CACHE_FILE).read_text(encoding="utf-8"))
        if data.get("version") == CACHE_VERSION and data.get("dir") == str(folder):
            files = data.get("files", {})
            return files if isinstance(files, dict) else {}
    except FileNotFoundError:
        pass                                  # first run — nothing cached yet
    except Exception as e:
        logger.debug("CACHE", f"Ignoring unreadable {folder / CACHE_FILE}: {e}")
    return {}


class CharaInfoCache:
    """The chara info cache for a set of chara folders ("roots").

    With `enabled=False` it does nothing at all (every lookup misses, nothing is
    read or written), so callers don't need to special-case the option.

    `lookup` / `put` / `moved` / `updated` / `forget` may be called from worker
    threads; the entries are plain dict operations, which are atomic.
    """

    def __init__(self, roots: list[Path], enabled: bool = True, tag: str = "CACHE"):
        self.roots = [Path(r) for r in roots]
        self.enabled = enabled
        self.tag = tag
        self.hits = 0
        self.misses = 0
        self._dirty = False
        self._data: dict[str, dict] = {}
        if enabled:
            for root in self.roots:
                self._data.update(_load(root))

    # -- reading ------------------------------------------------------------

    def lookup(self, path: Path, fp: list[int] | None = None) -> tuple[bool, CharaInfo | None]:
        """(True, info) if `path` is cached and unchanged — `info` is None for a PNG that
        isn't a chara card. (False, None) if it has to be read: not cached, changed since, or
        cached without every field CharaInfo has now."""
        if not self.enabled:
            return False, None
        entry = self._data.get(str(path))
        if (entry is not None and "info" in entry
                and entry.get("fp") == (fp if fp is not None else file_fp(path))):
            raw = entry["info"]
            info = None if raw is None else CharaInfo.from_json(raw)
            if raw is None or info is not None:
                self.hits += 1
                return True, info
        self.misses += 1
        return False, None

    # -- recording what was learned -------------------------------------------

    def put(self, path: Path, fp: list[int], info: CharaInfo | None) -> None:
        """Record what was read from `path` (`info` None: not a chara card)."""
        if self.enabled:
            self._data[str(path)] = {"fp": list(fp),
                                     "info": info.to_json() if info is not None else None}
            self._dirty = True

    def updated(self, path: Path, info: CharaInfo) -> None:
        """`path` was rewritten by us (metadata update): record the card's new fields under
        its new fingerprint."""
        self.put(path, file_fp(path), info)

    def moved(self, old: Path, new: Path) -> None:
        """`old` was moved/renamed to `new`: its entry follows it. A move keeps mtime and
        size, so the entry stays valid; if the fingerprint did change, it's dropped
        (re-read next time) rather than trusted."""
        if not self.enabled:
            return
        entry = self._data.pop(str(old), None)
        if entry is None:
            return
        self._dirty = True
        if entry.get("fp") == file_fp(new):
            self._data[str(new)] = entry

    def forget(self, path: Path) -> None:
        if self.enabled and self._data.pop(str(path), None) is not None:
            self._dirty = True

    # -- saving ---------------------------------------------------------------

    def summary(self) -> str:
        return f"{self.hits} reused, {self.misses} read"

    def save(self) -> None:
        """Write the cache back (one file per root) if anything changed. Entries of
        files that no longer exist are dropped."""
        if not self.enabled or not self._dirty:
            return
        per_root: dict[Path, dict[str, dict]] = {root: {} for root in self.roots}
        for path_str, entry in self._data.items():
            if not os.path.exists(path_str):
                continue
            path = Path(path_str)
            for root in self.roots:
                if path.is_relative_to(root):
                    per_root[root][path_str] = entry
                    break
        for root, files in per_root.items():
            try:
                atomic_write_json(root / CACHE_FILE,
                                  {"version": CACHE_VERSION, "dir": str(root), "files": files})
            except Exception as e:
                logger.warning(self.tag, f"Could not save {root / CACHE_FILE}: {e}")
        self._data = {k: v for files in per_root.values() for k, v in files.items()}
        self._dirty = False
