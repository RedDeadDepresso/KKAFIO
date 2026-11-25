"""author.py — The known scene authors, for Group Scenes' Manual mode.

The index is published by https://github.com/RedDeadDepresso/kk-author-scraper as a list,
[{"name": "...", "alias": ["...", ...]}, ...]: the name is the folder an author's scenes are grouped
under, the aliases are other names the author goes by (a handle, a romanisation ...). Typing an alias
suggests, and resolves to, the name.
A copy is kept in CONFIG_DIR/indices/author.json; the server is asked at most once an hour,
and the copy is only re-downloaded when its ETag changes (see kkafio.indices.cache).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.indices import cache

INDEX_FILENAME = "author.json"
AUTHOR_INDEX_URL = f"https://reddeaddepresso.github.io/kk-author-scraper/indices/{INDEX_FILENAME}"

TAG = "AUTHORS"


@dataclass(frozen=True)
class Author:
    name: str
    aliases: tuple[str, ...] = ()


def _load_cached_index(index_path: Path) -> list | None:
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        logger.warning(TAG, f"Cached {index_path.name} is not a JSON list.")
    except Exception as e:
        logger.warning(TAG, f"Could not load cached {index_path.name}: {e}")
    return None


def normalize_index(raw: list) -> list[Author]:
    """The authors in the list, in list order. Names and aliases are stripped; entries without a
    usable name are dropped, and so are aliases that are not non-empty strings."""
    authors: list[Author] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            continue
        aliases = entry.get("alias") or []
        authors.append(Author(entry["name"].strip(),
                              tuple(a.strip() for a in aliases if isinstance(a, str) and a.strip())
                              if isinstance(aliases, list) else ()))
    return authors


def load_authors(client=None, index_dir: Path | None = None) -> list[Author]:
    """Return the known authors.

    The server is only asked if the cached copy was last checked an hour or more ago, with a
    conditional request (ETag), so an unchanged index costs a 304 and no download. If the
    download fails (e.g. offline), the cached copy is used. Returns [] if no index is available.
    """
    index_path = (index_dir or cache.default_dir()) / INDEX_FILENAME
    cached = _load_cached_index(index_path)
    if cached is not None and cache.is_fresh(index_path):
        authors = normalize_index(cached)
        logger.info(TAG, f"Author index checked recently: {len(authors)} author(s) (cached)")
        return authors
    headers = cache.conditional_headers(index_path) if cached is not None else {}

    own_client = client is None
    if own_client:
        import httpx
        client = httpx.Client(follow_redirects=True, timeout=60)
    try:
        try:
            r = client.get(AUTHOR_INDEX_URL, headers=headers)
            if r.status_code == 304 and cached is not None:
                cache.touch(index_path)
                authors = normalize_index(cached)
                logger.info(TAG, f"Author index up to date: {len(authors)} author(s) (cached)")
                return authors
            r.raise_for_status()
            raw = r.json()
            if not isinstance(raw, list):
                raise ValueError("index is not a JSON list")
        except Exception as e:
            if cached is not None:
                logger.warning(TAG, f"Could not check for author index updates ({e}) — using cached copy.")
                return normalize_index(cached)
            logger.error(TAG, f"Failed to download author index: {e} — no author suggestions")
            return []

        try:
            cache.save(index_path, raw, r.headers.get("etag"))
        except OSError as e:
            logger.warning(TAG, f"Could not save author index cache: {e}")

        authors = normalize_index(raw)
        logger.info(TAG, f"Author index downloaded: {len(authors)} author(s)")
        return authors
    finally:
        if own_client:
            client.close()


_shared: list[Author] | None = None


def get_authors() -> list[Author]:
    """The authors, loaded once per run and shared by every caller (the Scene tab, the other tabs'
    author suggestions). An empty result (no index available) is not kept, so the next call tries
    again. `reset_shared()` forgets the kept list."""
    global _shared
    if _shared is None:
        authors = load_authors()
        if not authors:
            return authors
        _shared = authors
    return _shared


def reset_shared() -> None:
    global _shared
    _shared = None


def canonical_name(text: str, authors: list[Author]) -> str:
    """The author's name when `text` is a name or an alias of one (any case), else `text` stripped."""
    q = text.strip().casefold()
    for author in authors:
        if author.name.casefold() == q:
            return author.name
    for author in authors:
        if any(a.casefold() == q for a in author.aliases):
            return author.name
    return text.strip()


def suggest_authors(text: str, authors: list[Author], limit: int = 8) -> list[str]:
    """Names of the authors whose name or alias contains `text` (any case), best first: a name that
    starts with it, an alias that starts with it, then any other containing it; file order within each."""
    q = text.strip().casefold()
    if not q:
        return []
    ranked: list[tuple[int, int, str]] = []
    for n, author in enumerate(authors):
        name = author.name.casefold()
        if name.startswith(q):
            rank = 0
        elif any(a.casefold().startswith(q) for a in author.aliases):
            rank = 1
        elif q in name:
            rank = 2
        elif any(q in a.casefold() for a in author.aliases):
            rank = 3
        else:
            continue
        ranked.append((rank, n, author.name))
    ranked.sort()
    return [name for _r, _n, name in ranked[:limit]]
