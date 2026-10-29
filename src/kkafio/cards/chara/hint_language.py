"""
hint_language.py — Which language the character-index hints (series / names) are in.

Group Characters and Rename Characters let the user pick:

    "no"      no hints at all (the character indexes are not used)
    "detect"  work it out from the prompt (see `detect`)
    <code>    one of the languages in assets/i18n/ (``en_us``, ``ja_jp`` ...)

The indexes store each series title / character name per language; `localized` picks
the requested one and falls back to English, then to whatever is there.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping

from kkafio.core.logger import logger

NO = "no"
DETECT = "detect"
DEFAULT_LANGUAGE = "en_us"

# Language code -> the English names a prompt may use for it (matched as whole words,
# case-insensitively). A code that is in assets/i18n/ but not listed here can still be
# picked by hand; it just can't be detected.
LANGUAGE_NAMES: dict[str, tuple[str, ...]] = {
    "en_us": ("english",),
    "zh_cn": ("simplified chinese", "chinese", "mandarin"),
    "zh_tw": ("traditional chinese",),
    "ja_jp": ("japanese",),
    "ko_kr": ("korean",),
    "ru_ru": ("russian",),
}

# Characters only one language uses.
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# Han characters that differ between the two Chinese scripts (a sample is enough).
_TRADITIONAL = frozenset("們個這來說時國學會對與為從過還動長開關麼點邊體種經實無後書愛話認讀請謝戀獨環兒")
_SIMPLIFIED = frozenset("们个这来说时国学会对与为从过还动长开关么点边体种经实无后书爱话认读请谢恋独环儿")

# One mention of a language's name counts as this many of its characters.
_NAME_WEIGHT = 20

_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted({n for names in LANGUAGE_NAMES.values() for n in names},
                             key=len, reverse=True)) + r")\b",
    re.IGNORECASE)
_NAME_TO_CODE = {n: code for code, names in LANGUAGE_NAMES.items() for n in names}


# ---------------------------------------------------------------------------
# Which languages exist
# ---------------------------------------------------------------------------

def available_languages() -> list[str]:
    """Every language in assets/i18n/ (English first, then alphabetical)."""
    from kkafio.core.paths import ASSETS_DIR
    try:
        codes = {p.stem.lower() for p in (ASSETS_DIR / "i18n").glob("*.json")}
    except OSError:
        codes = set()
    codes.add(DEFAULT_LANGUAGE)
    return sorted(codes, key=lambda c: (c != DEFAULT_LANGUAGE, c))


def choices() -> list[str]:
    """Every value of the setting: ``no``, ``detect``, then the languages."""
    return [NO, DETECT, *available_languages()]


def _normalise(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


# ---------------------------------------------------------------------------
# Detecting the language of a prompt
# ---------------------------------------------------------------------------

def detect(prompt: str) -> str | None:
    """The hint language a prompt asks for, or None if it names none.

    Looks for (a) characters specific to a language — Hangul, kana, Cyrillic, Han (told
    apart as Traditional or Simplified by a few characters that differ) — and (b) the
    English name of a language ("Korean", "Traditional Chinese"). English only counts as
    a language when nothing else is found, because a prompt written in English says
    "English" about the *answer*, which is what the other languages are alternatives to.
    """
    prompt = prompt or ""
    langs = set(available_languages())
    score: Counter[str] = Counter()

    score["ko_kr"] += len(_HANGUL.findall(prompt))
    score["ru_ru"] += len(_CYRILLIC.findall(prompt))
    kana = len(_KANA.findall(prompt))
    han = _HAN.findall(prompt)
    if kana:
        score["ja_jp"] += kana + len(han)         # kanji next to kana is Japanese
    elif han:
        trad = sum(c in _TRADITIONAL for c in han)
        simp = sum(c in _SIMPLIFIED for c in han)
        score["zh_tw" if trad > simp else "zh_cn"] += len(han)

    for m in _NAME_RE.finditer(prompt):
        score[_NAME_TO_CODE[m.group(1).lower()]] += _NAME_WEIGHT

    others = [(n, c) for c, n in score.items() if c != DEFAULT_LANGUAGE and c in langs and n > 0]
    if others:
        return max(others)[1]
    return DEFAULT_LANGUAGE if score[DEFAULT_LANGUAGE] and DEFAULT_LANGUAGE in langs else None


def resolve(setting: object, prompt: str, tag: str) -> str | None:
    """The language to use for hints, or None for no hints at all.

    `setting` is the option's value: ``no``, ``detect`` (the default, and what an empty or
    unknown value falls back to) or a language code.
    """
    value = _normalise(setting) or DETECT
    if value == NO:
        logger.info(tag, "Hints language: none — the character indexes are not used.")
        return None
    if value != DETECT:
        if value in available_languages():
            logger.info(tag, f"Hints language: {value}")
            return value
        logger.warning(tag, f"Unknown hints language '{setting}' — detecting it from the prompt instead.")
    found = detect(prompt)
    if found is None:
        logger.info(tag, f"Hints language: {DEFAULT_LANGUAGE} (the prompt names no language)")
        return DEFAULT_LANGUAGE
    logger.info(tag, f"Hints language: {found} (detected from the prompt)")
    return found


# ---------------------------------------------------------------------------
# Picking a language's text out of an index entry
# ---------------------------------------------------------------------------

def localized(values: Mapping[str, str], lang: str | None) -> str:
    """`values` maps a language code to text. The requested language if it has some,
    else English, else the first non-empty one, else ""."""
    for code in (lang or DEFAULT_LANGUAGE, DEFAULT_LANGUAGE):
        text = values.get(code)
        if text:
            return text
    return next((v for v in values.values() if v), "")
