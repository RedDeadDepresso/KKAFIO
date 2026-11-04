"""Sideloader Modpack index: {mod GUID: [download URL, ...]}.

The indices are published by https://github.com/RedDeadDepresso/kk-mod-scraper (one file per
game type, built from the BetterRepack server's listings). A copy is kept in
CONFIG_DIR/indices/kk_modpack.json and kks_modpack.json; the server is asked at most once an
hour, and a copy is only re-downloaded when its ETag changes (see kkafio.indices.cache). It lets
Download Missing Mods, Archive Cards, Delete Unused Mods and the mods scan tell which GUIDs
the Sideloader Modpack already provides without opening every zipmod in the modpack folders.

Each GUID has one or more URLs, best first: the later ones are other copies or versions of
the same mod, tried when an earlier one fails. The path of a mod inside the modpack folder is
not stored; it is the URL's path below the server's base (see url_to_rel_path).
"""

import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from kkafio.core.config import GameType
from kkafio.core.logger import logger
from kkafio.indices import cache

TAG = "MODPACK"

MODPACK_INDEX_URL_KK  = "https://reddeaddepresso.github.io/kk-mod-scraper/indices/kk_modpack.json"

MODPACK_INDEX_URL_KKS = "https://reddeaddepresso.github.io/kk-mod-scraper/indices/kks_modpack.json"

MODPACK_INDEX_FILE_KK  = "kk_modpack.json"

MODPACK_INDEX_FILE_KKS = "kks_modpack.json"

MODPACK_HOST        = "sideload.betterrepack.com"
MODPACK_PATH_PREFIX = "/download/KKEC/"


def url_to_rel_path(url: str) -> str | None:
    """The path of a modpack URL below the server's base, "/"-separated and percent-decoded
    ("Sideloader Modpack/Author/[a] mod.zipmod"), or None if the URL isn't a usable modpack
    URL: another host, outside the base, or a path that could leave the mods folder."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != MODPACK_HOST:
        return None
    path = unquote(parsed.path)
    if not path.startswith(MODPACK_PATH_PREFIX):
        return None
    rel = path[len(MODPACK_PATH_PREFIX):]
    parts = rel.split("/")
    if len(parts) < 2 or any(p in ("", ".", "..") or "\\" in p for p in parts):
        return None
    return rel


def _normalize_index(raw) -> dict[str, list[str]]:
    """The {guid: [url, ...]} mapping of an index file ({"guids": {...}}).
    Raises ValueError if it isn't in that format."""
    guids = raw.get("guids") if isinstance(raw, dict) else None
    if not isinstance(guids, dict) or not all(isinstance(urls, list) for urls in guids.values()):
        raise ValueError("not a {guid: [url, ...]} index")
    return guids


def _load_cached_index(index_path: Path) -> dict | None:
    """Load the cached index file. None if missing or unreadable."""
    if not index_path.exists():
        return None
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        _normalize_index(raw)
        return raw
    except Exception as e:
        logger.warning(TAG, f"Could not load cached {index_path.name}: {e}")
        return None


_memo: dict[str, dict[str, list[str]] | None] = {}


def clear_cache() -> None:
    """Forget the in-process copies, so the next load checks the cache and server again."""
    _memo.clear()


def load_modpack_index(game_type: str = GameType.KOIKATSU.value, client=None,
                       index_dir: Path | None = None) -> dict[str, list[str]] | None:
    """Return the Sideloader Modpack index {guid: [url, ...]} (best URL first) for the game type.

    Uses kks_modpack.json for KoikatsuSunshine, and kk_modpack.json for all other variants.

    The server is only asked if the cached copy was last checked an hour or more ago, with a
    conditional request (ETag), so an unchanged index costs a 304 and no download. If the
    download fails (e.g. offline), the cached copy is used. Returns None if no index is
    available.

    Remembered (per game type) for the lifetime of the process — the index doesn't change
    mid-run, and this is called once per card by e.g. DeleteCards (scan_mods() →
    load_modpack_index() per file). Not remembered when `client` or `index_dir` is given.
    """
    memoize = client is None and index_dir is None
    if memoize and game_type in _memo:
        return _memo[game_type]
    guids = _fetch_index(game_type, client, index_dir)
    if memoize:
        _memo[game_type] = guids
    return guids


def _fetch_index(game_type: str, client, index_dir: Path | None) -> dict[str, list[str]] | None:
    is_kks = game_type == GameType.KOIKATSU_SUNSHINE.value
    url = MODPACK_INDEX_URL_KKS if is_kks else MODPACK_INDEX_URL_KK
    index_path = (index_dir or cache.default_dir()) / (MODPACK_INDEX_FILE_KKS if is_kks else MODPACK_INDEX_FILE_KK)
    name = index_path.name

    cached = _load_cached_index(index_path)
    if cached is not None and cache.is_fresh(index_path):
        guids = _normalize_index(cached)
        logger.info(TAG, f"Modpack index checked recently: {len(guids)} GUIDs from {name} (cached)")
        return guids
    headers = cache.conditional_headers(index_path) if cached is not None else {}

    own_client = client is None
    if own_client:
        import httpx
        client = httpx.Client(follow_redirects=True, timeout=60)
    try:
        try:
            r = client.get(url, headers=headers)
            if r.status_code == 304 and cached is not None:
                cache.touch(index_path)
                guids = _normalize_index(cached)
                logger.info(TAG, f"Modpack index up to date: {len(guids)} GUIDs from {name} (cached)")
                return guids
            r.raise_for_status()
            raw = r.json()
            guids = _normalize_index(raw)
        except Exception as e:
            if cached is not None:
                logger.warning(TAG, f"Could not check for {name} updates ({e}) — using cached copy.")
                return _normalize_index(cached)
            logger.error(TAG, f"Failed to download {name}: {e}")
            return None

        try:
            cache.save(index_path, raw, r.headers.get("etag"))
        except OSError as e:
            logger.warning(TAG, f"Could not save {name} cache: {e}")

        logger.info(TAG, f"Modpack index downloaded: {len(guids)} GUIDs from {name}")
        return guids
    finally:
        if own_client:
            client.close()
