"""pepper-scene-index: the {xxhash: author} index of Studio scenes.

The index is published by https://github.com/RedDeadDepresso/pepper-scene-index.
A copy is kept in CONFIG_DIR/config and only re-downloaded when the repo's
latest commit differs from the one recorded in
pepper_scene_index_last_commit.txt (same scheme as the kkc mod index).
"""

import json
import os
from pathlib import Path

from kkafio.core.logger import logger

SCENE_INDEX_URL = "https://reddeaddepresso.github.io/pepper-scene-index/pepper-scene-index.json"
SCENE_INDEX_COMMITS_API = "https://api.github.com/repos/RedDeadDepresso/pepper-scene-index/commits"

INDEX_FILENAME = "pepper_scene_index.json"
COMMIT_FILENAME = "pepper_scene_index_last_commit.txt"

TAG = "SCENEIDX"


def _fetch_latest_commit(client) -> str | None:
    """SHA of the repo's latest commit, or None if it couldn't be determined."""
    try:
        r = client.get(
            SCENE_INDEX_COMMITS_API,
            params={"per_page": 1},
            headers={"Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        data = r.json()
        return data[0]["sha"].strip() if data else None
    except Exception as e:
        logger.warning(TAG, f"Could not fetch latest pepper-scene-index commit: {e}")
        return None


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


def normalize_index(raw: dict) -> dict[str, str]:
    """{xxhash: author} with lower-cased hashes and plain-string authors.

    Entries whose author can't be read (empty, not a string, or an object
    without an "author"/"name" field) are dropped.
    """
    index: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            value = value.get("author") or value.get("name")
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            index[key.strip().lower()] = value.strip()
    return index


def load_scene_index(client=None, config_dir: Path | None = None) -> dict[str, str]:
    """Return the {xxhash: author} scene index.

    The cached copy is used only if the commit saved next to it matches the
    repo's latest commit and it loads; otherwise the index is downloaded again
    and the saved commit updated. If the latest commit can't be checked (e.g.
    offline), a cached copy is used rather than failing. Returns {} if no
    index is available.
    """
    if config_dir is None:
        from kkafio.core.paths import CONFIG_DIR
        config_dir = CONFIG_DIR / "config"
    index_path = config_dir / INDEX_FILENAME
    commit_path = config_dir / COMMIT_FILENAME

    own_client = client is None
    if own_client:
        import httpx
        client = httpx.Client(follow_redirects=True, timeout=60)
    try:
        latest = _fetch_latest_commit(client)

        saved = ""
        try:
            if commit_path.exists():
                saved = commit_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass

        if latest and saved == latest:
            cached = _load_cached_index(index_path)
            if cached is not None:
                index = normalize_index(cached)
                logger.info(TAG, f"Scene index up to date: {len(index)} scene(s) (cached)")
                return index
            logger.info(TAG, "Scene index cache missing or unreadable — downloading...")
        elif latest is None:
            cached = _load_cached_index(index_path)
            if cached is not None:
                logger.warning(TAG, "Could not check for scene index updates — using cached copy.")
                return normalize_index(cached)
        else:
            logger.info(TAG, "Scene index is new or outdated — downloading...")

        try:
            r = client.get(SCENE_INDEX_URL)
            r.raise_for_status()
            raw = r.json()
            if not isinstance(raw, dict):
                raise ValueError("index is not a JSON object")
        except Exception as e:
            logger.error(TAG, f"Failed to download scene index: {e}")
            return {}

        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            tmp = index_path.with_name(index_path.name + ".part")
            tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, index_path)
            if latest:
                commit_path.write_text(latest, encoding="utf-8")
        except OSError as e:
            logger.warning(TAG, f"Could not save scene index cache: {e}")

        index = normalize_index(raw)
        logger.info(TAG, f"Scene index downloaded: {len(index)} scene(s)")
        return index
    finally:
        if own_client:
            client.close()
