"""
mal_index.py — Look characters and series up in the MyAnimeList index (built by
tools/build_mal_index.py from the Kaggle MAL datasets).

Used by Group Characters and Rename Characters to (1) answer unambiguous characters
locally, (2) pre-fill a suggestion for ambiguous ones, and (3) snap the LLM's series
name to one canonical franchise title. Everything degrades to "no help" when the index
file is missing, so the tasks behave exactly as before without it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path

from kkafio.cards.mal_text import name_parts, norm
from kkafio.core.paths import ASSETS_DATA_DIR

INDEX_FILE = "kkafio_mal_index.json"
INDEX_VERSION = 2

# A single hit is "confident" only if it beats the runner-up's score by this factor.
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
    name: str            # as stored by MAL, e.g. "Tohsaka, Rin"
    lastname: str
    firstname: str
    series: tuple[str, ...]   # franchises, most popular first
    score: int           # Members of the character's most popular anime (popularity proxy)


@dataclass(frozen=True)
class Match:
    tier: str                           # "confident" | "ambiguous" | "none"
    candidates: tuple[Candidate, ...] = ()
    source: str = "mal"                # which index answered: "gacha" | "mal"

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


_NONE = Match("none")


def split_key(key: str) -> tuple[str, str, str]:
    """"last | first | nick | personality | hair" -> (last, first, nick)."""
    parts = [p.strip() for p in key.split(" | ")]
    parts += [""] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def lookup(key: str) -> Match:
    """Find MAL characters for a make_key() string.

    "confident" needs BOTH name fields on the card and a single candidate (or one that
    dominates the next by DOMINANCE x score). A one-field name ("Rin") can never be
    confident — it's a hint at best.
    """
    idx = _load()
    if not idx:
        return _NONE
    last, first, nick = split_key(key)
    full = bool(last and first)
    if full:
        forms = {norm(last + first), norm(first + last)}
    else:
        forms = {norm(last or first or nick)}
    forms.discard("")

    seen: dict[int, Candidate] = {}
    for f in forms:
        for ci in idx["names"].get(f, ()):
            if ci in seen:
                continue
            name, series_ids, score = idx["chars"][ci]
            l, fi = name_parts(name)
            seen[ci] = Candidate(name, l, fi, tuple(idx["series"][i] for i in series_ids), score)
    if not seen:
        return _NONE
    cands = sorted(seen.values(), key=lambda c: -c.score)
    if full and (len(cands) == 1 or cands[0].score >= DOMINANCE * max(cands[1].score, 1)):
        return Match("confident", tuple(cands[:1]))
    return Match("ambiguous", tuple(cands[:3]))


@lru_cache(maxsize=1)
def _title_keys() -> list[str]:
    idx = _load()
    return list(idx["aliases"]) if idx else []


def canonical_series(name: str, cutoff: float = 0.9) -> str:
    """Snap an LLM-supplied series name to the canonical franchise title.

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
    return idx["series"][hit] if hit is not None else name
