"""
mal_index.py — Look characters and series up in the MyAnimeList index (built by
tools/build_mal_index.py from the Kaggle MAL datasets).

Used by Group Characters and Rename Characters to (1) give the LLM up to MAX_HINTS
candidate characters ("hints") for every card and (2) snap the LLM's series name to one
canonical franchise title. Everything degrades to "no help" when the index
file is missing, so the tasks behave exactly as before without it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path

from kkafio.cards.hint_language import localized
from kkafio.cards.mal_text import name_parts, norm
from kkafio.core.paths import ASSETS_DATA_DIR

INDEX_FILE = "kkafio_mal_index.json"
INDEX_VERSION = 1

MAX_HINTS = 5          # candidates handed to the LLM per card

# The Bangumi merge in tools/build_mal_index.py takes one of several same-named characters
# only if it beats the runner-up's score by this factor.
DOMINANCE = 5

_index_path: Path = ASSETS_DATA_DIR / INDEX_FILE


def set_index_path(path: Path) -> None:
    """Point at another index file (tests / a user-built index) and drop the cache."""
    global _index_path
    _index_path = Path(path)
    _load.cache_clear()
    _title_keys.cache_clear()


@lru_cache(maxsize=1)
def _load() -> dict | None:
    try:
        data = json.loads(_index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("version") == INDEX_VERSION else None


def available() -> bool:
    return _load() is not None


@dataclass(frozen=True)
class Candidate:
    name: str            # in the hint language, "Last, First" form, e.g. "Tohsaka, Rin"
    lastname: str
    firstname: str
    series: tuple[str, ...]   # franchises, most popular first
    score: int           # favorites (popularity)

    @property
    def hint(self) -> str:
        """The candidate as one line for the LLM: "Tohsaka, Rin (Fate/stay night)"."""
        return f"{self.name} ({self.series[0]})" if self.series else self.name


def split_key(key: str) -> tuple[str, str, str]:
    """"last | first | nick | personality | hair" -> (last, first, nick)."""
    parts = [p.strip() for p in key.split(" | ")]
    parts += [""] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def _text(idx: dict, values: list[str], lang: str | None) -> str:
    """One entry of a per-language list (positional, in the order of idx["langs"])."""
    return localized(dict(zip(idx["langs"], values)), lang)


def supports(lang: str) -> bool:
    """True if the index holds any text in `lang` (it falls back to English otherwise)."""
    idx = _load()
    return bool(idx) and lang in idx["langs"]


def lookup(key: str, lang: str | None = None, limit: int = MAX_HINTS) -> tuple[Candidate, ...]:
    """MAL characters whose name matches a make_key() string, most popular first; names and
    series come back in `lang` (English where the index has nothing in it)."""
    idx = _load()
    if not idx:
        return ()
    last, first, nick = split_key(key)
    if last and first:
        forms = {norm(last + first), norm(first + last)}
    else:
        forms = {norm(last or first or nick)}
    forms.discard("")

    seen: dict[int, Candidate] = {}
    for f in forms:
        for ci in idx["names"].get(f, ()):
            if ci in seen:
                continue
            names, series_ids, score = idx["chars"][ci]
            name = _text(idx, names, lang)
            l, fi = name_parts(name)
            seen[ci] = Candidate(name, l, fi,
                                 tuple(_text(idx, idx["series"][i], lang) for i in series_ids), score)
    return tuple(sorted(seen.values(), key=lambda c: -c.score)[:limit])


@lru_cache(maxsize=1)
def _title_keys() -> list[str]:
    idx = _load()
    return list(idx["aliases"]) if idx else []


def canonical_series(name: str, lang: str | None = None, cutoff: float = 0.9) -> str:
    """Snap an LLM-supplied series name (in any language) to the canonical franchise
    title in `lang`.

    Exact (normalised) match first, then a strict fuzzy match; anything unrecognised is
    returned untouched so a series MAL doesn't know still works.
    """
    idx = _load()
    if not idx or not name or not name.strip():
        return name
    k = norm(name)
    hit = idx["aliases"].get(k)
    if hit is None:
        close = get_close_matches(k, _title_keys(), n=1, cutoff=cutoff)
        hit = idx["aliases"][close[0]] if close else None
    return _text(idx, idx["series"][hit], lang) if hit is not None else name
