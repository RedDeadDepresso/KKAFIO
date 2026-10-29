"""kkc-mod-scraper index: {mod GUID: t.me message link}.

The index is published by https://github.com/RedDeadDepresso/kkc-mod-scraper and tells
Download Missing Mods which Telegram message holds a given mod. A copy is kept in
CONFIG_DIR/indices/kkc_mod.json and only re-downloaded when its ETag changes
(see kkafio.indices.cache).
"""

import json
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.indices import cache

KKC_INDEX_URL = "https://reddeaddepresso.github.io/kkc-mod-scraper/kkc_mod_index.json"

INDEX_FILENAME = "kkc_mod.json"


def _load_cached_kkc_index(index_path: Path) -> dict[str, str] | None:
    """Load the cached kkc_mod.json. None if missing or unreadable."""
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        logger.warning("DLMOD", f"Cached {index_path.name} is not a JSON object.")
    except Exception as e:
        logger.warning("DLMOD", f"Could not load cached {index_path.name}: {e}")
    return None


async def load_kkc_mod_index(client, index_dir: Path | None = None) -> dict[str, str]:
    """Return the {guid: t.me link} index from kkc-mod-scraper.

    Uses a conditional request (ETag) against the cached copy, so an unchanged index
    costs a 304 and no download. If the download fails, the cached copy is used.

    Returns an empty dict if the index is unavailable.
    """
    index_path = (index_dir or cache.default_dir()) / INDEX_FILENAME
    cached = _load_cached_kkc_index(index_path)
    headers = cache.conditional_headers(index_path) if cached is not None else {}

    try:
        r = await client.get(KKC_INDEX_URL, headers=headers)
        if r.status_code == 304 and cached is not None:
            logger.info("DLMOD", f"kkc mod index up to date: {len(cached)} GUIDs (cached)")
            return cached
        r.raise_for_status()
        index = r.json()
        if not isinstance(index, dict):
            raise ValueError("index is not a JSON object")
    except Exception as e:
        if cached is not None:
            logger.warning("DLMOD", f"Could not check for kkc mod index updates ({e}) — using cached copy.")
            return cached
        logger.error("DLMOD", f"Failed to download kkc mod index: {e}")
        return {}

    try:
        cache.save(index_path, index, r.headers.get("etag"))
    except OSError as e:
        logger.warning("DLMOD", f"Could not save kkc mod index cache: {e}")

    logger.info("DLMOD", f"kkc mod index downloaded: {len(index)} GUIDs")
    return index
