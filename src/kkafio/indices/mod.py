"""kk-mod-scraper index: {mod GUID: {"mega": [...], "drive": [...], "telegram": [...]}}.

The index is published by https://github.com/RedDeadDepresso/kk-mod-scraper and tells
Download Missing Mods where a given mod can be downloaded: each list holds links to the file
on that host (Mega, Google Drive, a t.me message). A copy is kept in
CONFIG_DIR/indices/mod.json; the server is asked at most once an hour, and the copy is only
re-downloaded when its ETag changes (see kkafio.indices.cache).
"""

import json
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.indices import cache

SOURCES = ("mega", "drive", "telegram")         # the keys of an entry of the index

# The order Download Missing Mods tries the sources of a mod in, best first:
#   telegram  fastest (parallel MTProto download through the user's own account) and the most
#             reliable: files stay in the archive channel and there is no per-IP quota.
#   drive     fast and needs no account, but popular files hit Drive's download quota
#             ("too many users have viewed or downloaded this file").
#   mega      last: transfers are metered per IP address and stop when the quota is used up, the
#             data is decrypted on the fly (slower), and its links die more often.
SOURCE_ORDER = ("telegram", "drive", "mega")

ModIndex = dict[str, dict[str, list[str]]]

INDEX_FILENAME = "mod.json"
MOD_INDEX_URL = f"https://reddeaddepresso.github.io/kk-mod-scraper/indices/{INDEX_FILENAME}"


def _normalize_index(raw) -> ModIndex:
    """The {guid: {source: [link, ...]}} mapping of a mod.json. Raises ValueError if `raw` isn't a
    JSON object. Entries that aren't objects, sources that aren't lists of links and anything that
    isn't a link are dropped, as are mods left without a link."""
    if not isinstance(raw, dict):
        raise ValueError("index is not a JSON object")
    index: ModIndex = {}
    for guid, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        links = {}
        for source in SOURCES:
            urls = entry.get(source)
            if isinstance(urls, list):
                urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
                if urls:
                    links[source] = urls
        if links:
            index[guid] = links
    return index


def ordered_links(entry: dict[str, list[str]] | None) -> list[tuple[str, str]]:
    """[(source, link), ...] of one mod's entry in the order to try them: by SOURCE_ORDER, and
    in the order of the index within a source."""
    if not entry:
        return []
    return [(source, url) for source in SOURCE_ORDER for url in entry.get(source, [])]


def _load_cached_mod_index(index_path: Path) -> ModIndex | None:
    """Load the cached mod.json. None if missing or unreadable."""
    if not index_path.exists():
        return None
    try:
        index = _normalize_index(json.loads(index_path.read_text(encoding="utf-8")))
    except Exception as e:
        logger.warning("DLMOD", f"Could not load cached {index_path.name}: {e}")
        return None
    # A copy with no usable entry (e.g. one in an older format) is as good as none: download it again.
    return index or None


async def load_mod_index(client, index_dir: Path | None = None) -> ModIndex:
    """Return the {guid: {source: [link, ...]}} index from kk-mod-scraper.

    The server is only asked if the cached copy was last checked an hour or more ago, with a
    conditional request (ETag), so an unchanged index costs a 304 and no download. If the
    download fails, the cached copy is used.

    Returns an empty dict if the index is unavailable.
    """
    index_path = (index_dir or cache.default_dir()) / INDEX_FILENAME
    cached = _load_cached_mod_index(index_path)
    if cached is not None and cache.is_fresh(index_path):
        logger.info("DLMOD", f"mod index checked recently: {len(cached)} GUIDs (cached)")
        return cached
    headers = cache.conditional_headers(index_path) if cached is not None else {}

    try:
        r = await client.get(MOD_INDEX_URL, headers=headers)
        if r.status_code == 304 and cached is not None:
            cache.touch(index_path)
            logger.info("DLMOD", f"mod index up to date: {len(cached)} GUIDs (cached)")
            return cached
        r.raise_for_status()
        raw = r.json()
        index = _normalize_index(raw)
    except Exception as e:
        if cached is not None:
            logger.warning("DLMOD", f"Could not check for mod index updates ({e}) — using cached copy.")
            return cached
        logger.error("DLMOD", f"Failed to download mod index: {e}")
        return {}

    try:
        cache.save(index_path, raw, r.headers.get("etag"))
    except OSError as e:
        logger.warning("DLMOD", f"Could not save mod index cache: {e}")

    logger.info("DLMOD", f"mod index downloaded: {len(index)} GUIDs")
    return index
