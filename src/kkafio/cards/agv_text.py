"""
agv_text.py — Pure text helpers used by kkafio.cards.agv_index (which reads the MyAnimeList index).
The agv-character-scraper repo (which writes the index) holds an identical copy — keep the
two in sync so both sides normalise names byte-for-byte the same way.
"""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[\W_]+")


def norm(s: str) -> str:
    """Case/width/punctuation-insensitive form used for every lookup.

    NFKC folds full-width forms, casefold lowercases, and the regex drops spaces and
    punctuation while keeping letters of any script (kanji/kana survive).
    """
    return _NON_WORD.sub("", unicodedata.normalize("NFKC", s or "").casefold())


def name_parts(raw: str) -> tuple[str, str]:
    """MAL display name -> (lastname, firstname).

    "Tohsaka, Rin" -> ("Tohsaka", "Rin"); "Saber" -> ("", "Saber");
    "Rin Tohsaka" (no comma) -> ("Tohsaka", "Rin").
    """
    raw = (raw or "").strip()
    if "," in raw:
        last, first = (p.strip() for p in raw.split(",", 1))
        return last, first
    words = raw.split()
    if len(words) <= 1:
        return "", raw
    return " ".join(words[1:]), words[0]
