"""
char_index.py — The character lookup Group Characters and Rename Characters use.

Combines the gacha-game index (gacha_index, higher priority) with the MyAnimeList index
(mal_index). Both degrade to "no help" when unavailable, so this does too.
"""

from __future__ import annotations

from kkafio.cards import gacha_index, mal_index
from kkafio.cards.mal_index import Candidate, Match

__all__ = ["Candidate", "Match", "available", "supports", "refresh", "lookup", "canonical_series"]


def available() -> bool:
    return gacha_index.available() or mal_index.available()


def supports(lang: str) -> bool:
    """True if either index holds text in `lang`; otherwise its hints come back in English."""
    return gacha_index.supports(lang) or mal_index.supports(lang)


def refresh(tag: str) -> None:
    """Update the downloadable (gacha) index. Never raises; offline just uses the cache."""
    gacha_index.refresh(tag)


def lookup(key: str, lang: str | None = None) -> Match:
    """Best match for a make_key() string, with names and series in `lang`. Priority:

        gacha confident > MAL confident > gacha ambiguous > MAL ambiguous / none

    A confident answer is final, so gacha gets the first word and wins whenever it is
    sure. A gacha guess that is only ambiguous does not override a MAL match that is sure.
    """
    g = gacha_index.lookup(key, lang)
    if g.tier == "confident":
        return g
    m = mal_index.lookup(key, lang)
    if m.tier == "confident":
        return m
    return g if g.tier == "ambiguous" else m


def canonical_series(name: str, lang: str | None = None) -> str:
    """Snap a series name (in any language) to its canonical title in `lang`: gacha titles
    first, then MAL franchises; anything neither knows is returned untouched."""
    return gacha_index.snap_series(name, lang) or mal_index.canonical_series(name, lang)
