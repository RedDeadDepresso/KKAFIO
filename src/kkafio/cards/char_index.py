"""
char_index.py — The character lookup Group Characters and Rename Characters use.

Combines the gacha-game index (gacha_index, higher priority) with the MyAnimeList index
(mal_index). Both degrade to "no help" when unavailable, so this does too.
"""

from __future__ import annotations

from itertools import zip_longest

from kkafio.cards import gacha_index, mal_index
from kkafio.cards.mal_index import DOMINANCE, MAX_HINTS, Candidate

__all__ = ["Candidate", "MAX_HINTS", "available", "supports", "refresh", "lookup", "hints", "canonical_series"]


def available() -> bool:
    return gacha_index.available() or mal_index.available()


def supports(lang: str) -> bool:
    """True if either index holds text in `lang`; otherwise its hints come back in English."""
    return gacha_index.supports(lang) or mal_index.supports(lang)


def refresh(tag: str) -> None:
    """Update the downloadable (gacha) index. Never raises; offline just uses the cache."""
    gacha_index.refresh(tag)


def _dominant(cands: list[Candidate]) -> bool:
    """True if the first candidate is the only one, or beats the next by DOMINANCE x score."""
    return bool(cands) and (len(cands) == 1 or cands[0].score >= DOMINANCE * max(cands[1].score, 1))


def lookup(key: str, lang: str | None = None) -> tuple[Candidate, ...]:
    """Up to MAX_HINTS candidates for a make_key() string, names and series in `lang`.

    The two indexes' scores are not comparable, so they are merged rather than sorted: an
    index whose best candidate clearly dominates its own others goes first (gacha before
    MAL), then the rest alternate gacha / MAL, each index's list most popular first. That
    way neither index can crowd the other out of the list. The same name + series from
    both indexes is listed once.
    """
    lists = [list(gacha_index.lookup(key, lang)), list(mal_index.lookup(key, lang))]
    ordered: list[Candidate] = [lst.pop(0) for lst in lists if _dominant(lst)]
    ordered += [c for pair in zip_longest(*lists) for c in pair if c is not None]

    out: list[Candidate] = []
    seen: set[str] = set()
    for cand in ordered:
        k = cand.hint.casefold()
        if k not in seen:
            seen.add(k)
            out.append(cand)
    return tuple(out[:MAX_HINTS])


def hints(key: str, lang: str | None = None) -> list[str]:
    """`lookup` as the strings handed to the LLM, e.g. ["Tohsaka, Rin (Fate/stay night)"]."""
    return [c.hint for c in lookup(key, lang)]


def canonical_series(name: str, lang: str | None = None) -> str:
    """Snap a series name (in any language) to its canonical title in `lang`: gacha titles
    first, then MAL franchises; anything neither knows is returned untouched."""
    return gacha_index.snap_series(name, lang) or mal_index.canonical_series(name, lang)
