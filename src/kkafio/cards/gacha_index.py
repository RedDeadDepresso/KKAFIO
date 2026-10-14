"""
gacha_index.py — Look characters and series up in the gacha-game index.

The index is built from popular gacha characters' wiki pages by a separate repo and
published on GitHub Pages (see docs/06-gacha-index-format.md for the file format). It is
downloaded on demand and cached in CONFIG_DIR/config, the same way kkc_mod_index.json is.

It has the same shape of API as kkafio.cards.mal_index (available / lookup /
canonical_series) so kkafio.cards.char_index can consult both, gacha first. Everything
degrades to "no help" when the index can't be downloaded and isn't cached.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path

from kkafio.cards.hint_language import DEFAULT_LANGUAGE, localized
from kkafio.cards.mal_index import DOMINANCE, Candidate, Match, split_key
from kkafio.cards.mal_text import name_parts, norm
from kkafio.core.logger import logger

INDEX_URL = "https://reddeaddepresso.github.io/gacha-character-scraper/gacha_character_index.json"
INDEX_FILE = "kkafio_gacha_index.json"
INDEX_VERSION = 1
SOURCE = "gacha"

_index_path: Path | None = None
_NONE = Match("none", source=SOURCE)


def set_index_path(path: Path | None) -> None:
    """Point at another index file (tests) or back at the cache (None); drops the cache."""
    global _index_path
    _index_path = Path(path) if path is not None else None
    _load.cache_clear()


def _path() -> Path:
    if _index_path is not None:
        return _index_path
    from kkafio.core.paths import CONFIG_DIR
    return CONFIG_DIR / "config" / INDEX_FILE


def _per_language(value) -> dict[str, str]:
    """A title / name object as {language code: text}."""
    return {str(k).lower().replace("-", "_"): str(v) for k, v in value.items() if v}


@dataclass(frozen=True)
class _Char:
    names: dict[str, str]                  # language -> "Last, First" (or a mononym)
    series: tuple[int, ...]                # indexes into _Built.series, most associated first
    score: int
    mononym: bool                          # known by a single name (in the default language)


@dataclass(frozen=True)
class _Built:
    chars: tuple[_Char, ...]
    names: dict[str, tuple[int, ...]]      # norm(name form / alias) -> char indexes
    series: tuple[dict[str, str], ...]     # language -> title
    series_alias: dict[str, int]           # norm(title / alias) -> series index
    alias_keys: tuple[str, ...]


def _build(data: dict) -> _Built:
    titles = tuple(_per_language(s["title"]) for s in data["series"])
    series_alias: dict[str, int] = {}
    for i, s in enumerate(data["series"]):
        for form in (*titles[i].values(), *s.get("aliases", ())):
            k = norm(form)
            if k:
                series_alias.setdefault(k, i)

    chars: list[_Char] = []
    names: dict[str, list[int]] = {}
    for ci, c in enumerate(data["chars"]):
        by_lang = _per_language(c["name"])
        chars.append(_Char(by_lang, tuple(int(i) for i in c["series"]), int(c.get("score", 0)),
                           not name_parts(localized(by_lang, DEFAULT_LANGUAGE))[0]))
        forms: set[str] = set()
        for name in by_lang.values():
            last, first = name_parts(name)
            forms |= {norm(name), norm(last + first), norm(first + last)}
        forms |= {norm(a) for a in c.get("aliases", ())}
        forms.discard("")
        for f in forms:
            names.setdefault(f, []).append(ci)
    return _Built(tuple(chars), {k: tuple(v) for k, v in names.items()},
                  titles, series_alias, tuple(series_alias))


@lru_cache(maxsize=1)
def _load() -> _Built | None:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
            return None
        return _build(data)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def available() -> bool:
    return _load() is not None


def supports(lang: str) -> bool:
    """True if any title or name in the index is in `lang` (it falls back to English otherwise)."""
    idx = _load()
    return bool(idx) and (any(lang in s for s in idx.series) or any(lang in c.names for c in idx.chars))


def _candidate(idx: _Built, c: _Char, lang: str | None) -> Candidate:
    name = localized(c.names, lang)
    last, first = name_parts(name)
    return Candidate(name, last, first, tuple(localized(idx.series[i], lang) for i in c.series), c.score)


def lookup(key: str, lang: str | None = None) -> Match:
    """Find gacha characters for a make_key() string; names and series come back in `lang`
    (English where the index has nothing in it).

    "confident" needs a single candidate (or one dominating the next by DOMINANCE x
    score) AND either both name fields on the card or a character the index itself knows
    by a single name ("Ganyu"). A one-field card name ("Rin") against a character that
    has a family name is only ever a hint.
    """
    idx = _load()
    if not idx:
        return _NONE
    last, first, nick = split_key(key)
    full = bool(last and first)
    forms = {norm(last + first), norm(first + last)} if full else {norm(last or first or nick)}
    forms.discard("")

    seen: dict[int, _Char] = {}
    for f in forms:
        for ci in idx.names.get(f, ()):
            seen.setdefault(ci, idx.chars[ci])
    if not seen:
        return _NONE
    ranked = sorted(seen.values(), key=lambda c: -c.score)
    unique = len(ranked) == 1 or ranked[0].score >= DOMINANCE * max(ranked[1].score, 1)
    cands = tuple(_candidate(idx, c, lang) for c in ranked[:3])
    if unique and (full or ranked[0].mononym):
        return Match("confident", cands[:1], SOURCE)
    return Match("ambiguous", cands, SOURCE)


def snap_series(name: str, lang: str | None = None, cutoff: float = 0.9) -> str | None:
    """Canonical gacha series title, in `lang`, for an LLM-supplied name in any language;
    None if not recognised."""
    idx = _load()
    if not idx or not name or not name.strip():
        return None
    k = norm(name)
    hit = idx.series_alias.get(k)
    if hit is None:
        close = get_close_matches(k, idx.alias_keys, n=1, cutoff=cutoff)
        hit = idx.series_alias[close[0]] if close else None
    return localized(idx.series[hit], lang) if hit is not None else None


# ---------------------------------------------------------------------------
# Download / cache
# ---------------------------------------------------------------------------

def _fetch(url: str, headers: dict[str, str], timeout: float):
    import httpx
    return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)


def refresh(tag: str = "GACHA", timeout: float = 6.0) -> bool:
    """Bring the cached index up to date. Returns True if a usable index exists afterwards.

    Uses a conditional request (ETag), so an unchanged index costs a 304 and no download.
    Any network problem just leaves the cached copy (if any) in use.
    """
    path = _path()
    etag_path = path.with_name(path.name + ".etag")
    headers: dict[str, str] = {}
    if available():
        try:
            etag = etag_path.read_text(encoding="utf-8").strip()
            if etag:
                headers["If-None-Match"] = etag
        except OSError:
            pass

    try:
        r = _fetch(INDEX_URL, headers, timeout)
    except Exception as e:
        logger.warning(tag, f"Could not check for a gacha index update: {e}"
                            + (" — using the cached copy." if available() else ""))
        return available()

    if r.status_code == 304:
        logger.info(tag, "Gacha index is up to date (cached).")
        return available()
    if r.status_code != 200:
        logger.warning(tag, f"Gacha index download failed: HTTP {r.status_code}")
        return available()

    try:
        data = r.json()
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
            raise ValueError(f"unsupported index version {data.get('version') if isinstance(data, dict) else '?'}")
        _build(data)      # reject a malformed file before it replaces a good cache
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        etag = r.headers.get("etag")
        if etag:
            etag_path.write_text(etag, encoding="utf-8")
        else:
            etag_path.unlink(missing_ok=True)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
        logger.warning(tag, f"Could not use the downloaded gacha index: {e}")
        return available()

    _load.cache_clear()
    idx = _load()
    logger.info(tag, f"Gacha index downloaded: {len(idx.chars) if idx else 0} characters.")
    return available()
