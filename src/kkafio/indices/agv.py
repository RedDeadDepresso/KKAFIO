"""
agv.py — Look characters and series up in the AGV index (anime and visual novel / JRPG
games, built from the Bangumi Archive by https://github.com/RedDeadDepresso/agv-character-scraper
and published on GitHub Pages).

The index is downloaded on demand and cached as agv_character_index.json in CONFIG_DIR
(the per-user KKAFIO folder, e.g. %APPDATA%/KKAFIO). It is only downloaded again when the
repo's latest commit differs from the one recorded in agv_character_index_last_commit.txt
(same scheme as the kkc mod index and the scene index).

Used by Group Characters and Rename Characters to (1) give the LLM up to MAX_HINTS
candidate characters ("hints") for every card and (2) snap the LLM's series name to one
canonical franchise title. Everything degrades to "no help" when the index
file is missing, so the tasks behave exactly as before without it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path

from kkafio.cards.chara.hint_language import DEFAULT_LANGUAGE, localized
from kkafio.indices.text import name_parts, norm
from kkafio.core.logger import logger

INDEX_URL = "https://reddeaddepresso.github.io/agv-character-scraper/agv_character_index.json"
COMMITS_API = "https://api.github.com/repos/RedDeadDepresso/agv-character-scraper/commits"
INDEX_FILE = "agv_character_index.json"
COMMIT_FILE = "agv_character_index_last_commit.txt"
INDEX_VERSION = 1

MAX_HINTS = 5          # candidates handed to the LLM per card

# A first candidate counts as unambiguous (see chara) if its score is at least this many
# times the runner-up's.
DOMINANCE = 5

_index_path: Path | None = None


def set_index_path(path: Path | None) -> None:
    """Point at another index file (tests / a user-built index), or back at the cached
    download (None), and drop the cache. The commit file lives next to the index."""
    global _index_path
    _index_path = Path(path) if path is not None else None
    _load.cache_clear()
    _title_keys.cache_clear()
    _series_popularity.cache_clear()


def _path() -> Path:
    if _index_path is not None:
        return _index_path
    from kkafio.core.paths import CONFIG_DIR
    return CONFIG_DIR / INDEX_FILE


@lru_cache(maxsize=1)
def _load() -> dict | None:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == INDEX_VERSION else None


def available() -> bool:
    return _load() is not None


@dataclass(frozen=True)
class Candidate:
    name: str            # in the hint language, "Last, First" form, e.g. "Tohsaka, Rin"
    lastname: str
    firstname: str
    series: tuple[str, ...]   # franchises, most popular first
    score: int           # favorites (popularity)
    birth_month: int | None = None   # optional: 1-12, when the index has it
    birth_day: int | None = None     # optional: 1-31, when the index has it
    series_native: bool = True       # False: the first series has no title in the requested language
                                     # (`series` then holds its fallback-language title)

    @property
    def hint(self) -> str:
        """The candidate as one line for the LLM: "Tohsaka, Rin (Fate/stay night)"."""
        return f"{self.name} ({self.series[0]})" if self.series else self.name


def optional_int(value) -> int | None:
    """An optional integer index field (birthMonth / birthDay): None when absent or not an integer."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
    """Anime, games, vtubers index characters whose name matches a make_key() string, most popular first; names and
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
            names, series_ids, score, *birth = idx["chars"][ci]      # birth: optional [month, day]
            name = _text(idx, names, lang)
            l, fi = name_parts(name)
            month, day = (optional_int(v) for v in (birth + [None, None])[:2])
            native = bool(series_ids) and bool(
                dict(zip(idx["langs"], idx["series"][series_ids[0]])).get(lang or DEFAULT_LANGUAGE))
            seen[ci] = Candidate(name, l, fi,
                                 tuple(_text(idx, idx["series"][i], lang) for i in series_ids), score,
                                 month, day, native)
    return tuple(sorted(seen.values(), key=lambda c: -c.score)[:limit])


@lru_cache(maxsize=1)
def _title_keys() -> list[str]:
    idx = _load()
    return list(idx["aliases"]) if idx else []


def canonical_series(name: str, lang: str | None = None, cutoff: float = 0.9) -> str:
    """Snap an LLM-supplied series name (in any language) to the canonical franchise
    title in `lang`.

    Exact (normalised) match first, then a strict fuzzy match; anything unrecognised is
    returned untouched so a series the index doesn't know still works. A series the index
    has no title for in `lang` is returned untouched too (never a title in another language).
    """
    idx = _load()
    if not idx or not name or not name.strip():
        return name
    k = norm(name)
    hit = idx["aliases"].get(k)
    if hit is None:
        close = get_close_matches(k, _title_keys(), n=1, cutoff=cutoff)
        hit = idx["aliases"][close[0]] if close else None
    if hit is None:
        return name
    # The index may have no title in `lang` (_text would then fall back to another language,
    # e.g. Japanese for an English answer): the LLM's own name is the better result then.
    return dict(zip(idx["langs"], idx["series"][hit])).get(lang or DEFAULT_LANGUAGE) or name


@lru_cache(maxsize=1)
def _series_popularity() -> tuple[int, ...]:
    """Per series id: the summed favorites of its characters (how well known the series is)."""
    idx = _load()
    if not idx:
        return ()
    total = [0] * len(idx["series"])
    for _names, series_ids, score, *_birth in idx["chars"]:
        for sid in series_ids:
            if 0 <= sid < len(total):
                total[sid] += score if isinstance(score, int) else 0
    return tuple(total)


def suggest_series(query: str, lang: str | None = None, limit: int = 8) -> list[str]:
    """Series titles (in `lang`, English where the index has none) that match what the user has
    typed so far, for autocompletion.

    A title matches when the normalised query (see text.norm) is a prefix of, or contained in,
    any of the series' titles in any language, so "fate" and "フェイト" both find Fate/stay night.
    Prefix matches come first, then the better known series (sum of its characters' favorites).
    An empty query, or no index, gives [].
    """
    idx = _load()
    q = norm(query)
    if not idx or not q:
        return []
    rank: dict[int, int] = {}                  # series id -> 0 (prefix) / 1 (contained)
    for key, sid in idx["aliases"].items():
        if q in key:
            r = 0 if key.startswith(q) else 1
            if rank.get(sid, 2) > r:
                rank[sid] = r
    popularity = _series_popularity()
    out: list[str] = []
    seen: set[str] = set()
    for sid in sorted(rank, key=lambda i: (rank[i], -(popularity[i] if i < len(popularity) else 0), i)):
        title = _text(idx, idx["series"][sid], lang)
        if title and title.casefold() not in seen:
            seen.add(title.casefold())
            out.append(title)
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------------------
# Download / cache
# ---------------------------------------------------------------------------

def _fetch_latest_commit(client) -> str | None:
    """SHA of the repo's latest commit, or None if it couldn't be determined."""
    try:
        r = client.get(
            COMMITS_API,
            params={"per_page": 1},
            headers={"Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        data = r.json()
        return data[0]["sha"].strip() if data else None
    except Exception as e:
        logger.warning("AGVIDX", f"Could not fetch latest agv-character-scraper commit: {e}")
        return None


def refresh(tag: str = "AGVIDX", timeout: float = 30.0, client=None) -> bool:
    """Bring the cached index up to date. Returns True if a usable index exists afterwards.

    The cached copy is kept only if the commit saved next to it matches the repo's latest
    commit and it loads; otherwise the index is downloaded again and the commit updated.
    If the latest commit can't be checked (offline, rate limit), the cached copy is used.
    Never raises.
    """
    path = _path()
    commit_path = path.with_name(COMMIT_FILE)

    own_client = client is None
    try:
        if own_client:
            import httpx
            client = httpx.Client(follow_redirects=True, timeout=timeout)

        latest = _fetch_latest_commit(client)
        saved = ""
        try:
            saved = commit_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass

        if latest and saved == latest and available():
            logger.info(tag, "Anime, games, vtubers character index is up to date (cached).")
            return True
        if latest is None:
            if available():
                logger.warning(tag, "Could not check for anime, games, vtubers index updates — using the cached copy.")
            return available()
        logger.info(tag, "Anime, games, vtubers character index is new or outdated — downloading...")

        try:
            r = client.get(INDEX_URL)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
                raise ValueError("unsupported index version "
                                 f"{data.get('version') if isinstance(data, dict) else '?'}")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
            commit_path.write_text(latest, encoding="utf-8")
        except Exception as e:
            logger.warning(tag, f"Could not download the anime, games, vtubers character index: {e}"
                                + (" — using the cached copy." if available() else ""))
            return available()

        _load.cache_clear()
        _title_keys.cache_clear()
        _series_popularity.cache_clear()
        idx = _load()
        logger.info(tag, f"Anime, games, vtubers character index downloaded: {len(idx['chars']) if idx else 0} characters.")
        return available()
    except Exception as e:      # never let an index problem break a task
        logger.warning(tag, f"Anime, games, vtubers character index update failed: {e}")
        return available()
    finally:
        if own_client and client is not None:
            client.close()
