"""pepper-scene-index: the {xxhash: {"author", "year"}} index of Studio scenes.

The index is published by https://github.com/RedDeadDepresso/pepper-scene-hasher.
A copy is kept in CONFIG_DIR/indices/scene.json and only re-downloaded when its ETag
changes (see kkafio.indices.cache).
"""

import json
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.indices import cache

SCENE_INDEX_URL = "https://reddeaddepresso.github.io/pepper-scene-hasher/pepper-scene-index.json"

INDEX_FILENAME = "scene.json"

TAG = "SCENEIDX"


def _load_cached_index(index_path: Path) -> dict | None:
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        logger.warning(TAG, f"Cached {index_path.name} is not a JSON object.")
    except Exception as e:
        logger.warning(TAG, f"Could not load cached {index_path.name}: {e}")
    return None


def _parse_year(value) -> int | None:
    """A usable year from an index value, or None (null, missing, bool, non-numeric)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def normalize_index(raw: dict) -> dict[str, dict]:
    """{xxhash: {"author": str, "year": int | None}} with lower-cased hashes.

    The index maps each hash to {"author": "...", "year": 2025}, where year may
    be null. For leniency, a bare author string is also accepted (year None), and
    an object may use "name" instead of "author". Entries whose author can't be
    read (empty, not a string) are dropped.
    """
    index: dict[str, dict] = {}
    for key, value in raw.items():
        year = None
        if isinstance(value, dict):
            year = _parse_year(value.get("year"))
            value = value.get("author") or value.get("name")
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            index[key.strip().lower()] = {"author": value.strip(), "year": year}
    return index


def load_scene_index(client=None, index_dir: Path | None = None) -> dict[str, dict]:
    """Return the {xxhash: {"author": str, "year": int | None}} scene index.

    Uses a conditional request (ETag) against the cached copy, so an unchanged index
    costs a 304 and no download. If the download fails (e.g. offline), the cached copy
    is used. Returns {} if no index is available.
    """
    index_path = (index_dir or cache.default_dir()) / INDEX_FILENAME
    cached = _load_cached_index(index_path)
    headers = cache.conditional_headers(index_path) if cached is not None else {}

    own_client = client is None
    if own_client:
        import httpx
        client = httpx.Client(follow_redirects=True, timeout=60)
    try:
        try:
            r = client.get(SCENE_INDEX_URL, headers=headers)
            if r.status_code == 304 and cached is not None:
                index = normalize_index(cached)
                logger.info(TAG, f"Scene index up to date: {len(index)} scene(s) (cached)")
                return index
            r.raise_for_status()
            raw = r.json()
            if not isinstance(raw, dict):
                raise ValueError("index is not a JSON object")
        except Exception as e:
            if cached is not None:
                logger.warning(TAG, f"Could not check for scene index updates ({e}) — using cached copy.")
                return normalize_index(cached)
            logger.error(TAG, f"Failed to download scene index: {e}")
            return {}

        try:
            cache.save(index_path, raw, r.headers.get("etag"))
        except OSError as e:
            logger.warning(TAG, f"Could not save scene index cache: {e}")

        index = normalize_index(raw)
        logger.info(TAG, f"Scene index downloaded: {len(index)} scene(s)")
        return index
    finally:
        if own_client:
            client.close()
