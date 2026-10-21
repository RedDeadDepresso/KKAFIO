"""
char_index.py — The character lookup Group Characters and Rename Characters use.

Combines the gacha-game index (gacha_index, higher priority) with the anime, games, vtubers index
(agv_index). Both degrade to "no help" when unavailable, so this does too.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import zip_longest

from kkafio.cards import gacha_index, agv_index
from kkafio.cards.agv_index import DOMINANCE, MAX_HINTS, Candidate

__all__ = ["Candidate", "MAX_HINTS", "RESOLVE_CANDIDATES", "available", "supports", "refresh", "lookup",
           "decide", "narrow", "hints", "canonical_series"]

RESOLVE_CANDIDATES = 50      # how many index matches Resolve Locally weighs (the LLM hints only show 5)


def available() -> bool:
    return gacha_index.available() or agv_index.available()


def supports(lang: str) -> bool:
    """True if either index holds text in `lang`; otherwise its hints come back in English."""
    return gacha_index.supports(lang) or agv_index.supports(lang)


def refresh(tag: str) -> None:
    """Update the downloadable (gacha and anime, games, vtubers) indexes. Never raises; offline just uses the caches."""
    gacha_index.refresh(tag)
    agv_index.refresh(tag)


def _dominant(cands: list[Candidate]) -> bool:
    """True if the first candidate is the only one, or beats the next by DOMINANCE x score."""
    return bool(cands) and (len(cands) == 1 or cands[0].score >= DOMINANCE * max(cands[1].score, 1))


def lookup(key: str, lang: str | None = None, limit: int = MAX_HINTS) -> tuple[Candidate, ...]:
    """Up to `limit` (MAX_HINTS) candidates for a make_key() string, names and series in `lang`.

    The two indexes' scores are not comparable, so they are merged rather than sorted: an
    index whose best candidate clearly dominates its own others goes first (gacha before
    the anime, games, vtubers index), then the rest alternate gacha / anime, games, vtubers, each index's list most popular first. That
    way neither index can crowd the other out of the list. The same name + series from
    both indexes is listed once.
    """
    lists = [list(gacha_index.lookup(key, lang, limit)), list(agv_index.lookup(key, lang, limit))]
    ordered: list[Candidate] = [lst.pop(0) for lst in lists if _dominant(lst)]
    ordered += [c for pair in zip_longest(*lists) for c in pair if c is not None]

    out: list[Candidate] = []
    where: dict[str, int] = {}
    for cand in ordered:
        k = cand.hint.casefold()
        if k not in where:
            where[k] = len(out)
            out.append(cand)
        elif out[where[k]].birth_month is None and cand.birth_month is not None:
            # the same character from the other index: keep the birthday if only that one has it
            out[where[k]] = replace(out[where[k]], birth_month=cand.birth_month, birth_day=cand.birth_day)
    return tuple(out[:limit])


def decide(cands, birth: tuple[int | None, int | None]) -> Candidate | None:
    """The one candidate that is the card's character, or None if it can't be told.

    A single candidate is the character whatever the birthday; among several it is the one whose
    birthMonth and birthDay are the card's (none, or more than one, matching decides nothing)."""
    if len(cands) == 1:
        return cands[0]
    matched = [c for c in cands if birth[0] is not None and birth == (c.birth_month, c.birth_day)]
    return matched[0] if len(matched) == 1 else None


def narrow(key: str, births, lang: str | None = None) -> list[Candidate]:
    """The candidates that are the character of every card with this key (`births`: their
    (birthMonth, birthDay)s), or [] if any of those cards can't be decided."""
    cands = lookup(key, lang, limit=RESOLVE_CANDIDATES)
    out: list[Candidate] = []
    for birth in births:
        c = decide(cands, birth) if cands else None
        if c is None:
            return []
        if c not in out:
            out.append(c)
    return out


def hints(key: str, lang: str | None = None) -> list[str]:
    """`lookup` as the strings handed to the LLM, e.g. ["Tohsaka, Rin (Fate/stay night)"]."""
    return [c.hint for c in lookup(key, lang)]


def canonical_series(name: str, lang: str | None = None) -> str:
    """Snap a series name (in any language) to its canonical title in `lang`: gacha titles
    first, then the anime, games, vtubers index's series; anything neither knows is returned untouched."""
    return gacha_index.snap_series(name, lang) or agv_index.canonical_series(name, lang)
