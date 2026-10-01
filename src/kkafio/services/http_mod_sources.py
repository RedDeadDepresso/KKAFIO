"""Mod sources reachable over plain HTTP: the BetterRepack server, and the koikatsucards.com
GUID -> mod index that tells us which Telegram message holds a given mod.
"""

import asyncio
import os
from pathlib import Path

from kkafio.core.logger import logger


BETTERREPACK_BASE     = "https://sideload.betterrepack.com/download/KKEC"

KKC_INDEX_URL         = "https://reddeaddepresso.github.io/kkc-mod-scraper/kkc_mod_index.json"

KKC_COMMITS_API       = "https://api.github.com/repos/RedDeadDepresso/kkc-mod-scraper/commits"

MAX_CONNECTIONS       = 4


# ---------------------------------------------------------------------------
# HTTP client (for BetterRepack + kkc-mod-scraper index)
# ---------------------------------------------------------------------------

def make_http_client(cookies: dict | None = None):
    import httpx
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=MAX_CONNECTIONS,
            max_keepalive_connections=MAX_CONNECTIONS,
        ),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
        cookies=cookies or {},
        follow_redirects=True,
        timeout=60,
        http2=True,
    )


# ---------------------------------------------------------------------------
# BetterRepack download
# ---------------------------------------------------------------------------

async def download_betterrepack(
    client, guid: str, rel_path: str, mods_dir: Path,
    guid_str_map: dict[str, str],
) -> bool | str:
    """Download one modpack zipmod. Returns True (downloaded), "skipped"
    (already present and intact) or False (failed)."""
    import zipfile

    url  = f"{BETTERREPACK_BASE}/{rel_path.replace(chr(92), '/')}"
    dest = mods_dir / rel_path.replace("\\", os.sep).replace("/", os.sep)

    # Only trust an existing file if it is a complete zip. Earlier versions
    # streamed straight into the final name, so an interrupted download could
    # leave a truncated .zipmod behind that would otherwise be "skipped" forever.
    if dest.exists():
        if await asyncio.to_thread(zipfile.is_zipfile, dest):
            return "skipped"
        logger.warning("DLMOD", f"Existing file is incomplete/corrupt, re-downloading: {dest.name}")

    logger.info("DLMOD", f"BetterRepack ↓ {dest.name}")
    # Download to "<name>.part" and rename only once the body is complete.
    part = dest.with_name(dest.name + ".part")
    completed = False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            import aiofiles
            async with aiofiles.open(part, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    await f.write(chunk)
        os.replace(part, dest)
        completed = True
        logger.success("DLMOD", f"Downloaded: {dest.name}")
        guid_str_map[guid] = str(dest)
        return True
    except Exception as e:
        logger.error("DLMOD", f"BetterRepack failed [{guid}]: {e}")
        return False
    finally:
        if not completed:
            # Errors and cancellation (Stop / Ctrl+C) alike.
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# kkc-mod-scraper index (GUID -> Telegram link)
# ---------------------------------------------------------------------------

async def _fetch_latest_index_commit(client) -> str | None:
    """Return the SHA of the latest commit of the kkc-mod-scraper repo, or
    None if it couldn't be determined."""
    try:
        r = await client.get(
            KKC_COMMITS_API,
            params={"per_page": 1},
            headers={"Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        data = r.json()
        return data[0]["sha"].strip() if data else None
    except Exception as e:
        logger.warning("DLMOD", f"Could not fetch latest kkc-mod-scraper commit: {e}")
        return None


def _load_cached_kkc_index(index_path: Path) -> dict[str, str] | None:
    """Load the cached kkc_mod_index.json. None if missing or unreadable."""
    import json
    if not index_path.exists():
        return None
    try:
        with index_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("DLMOD", "Cached kkc_mod_index.json is not a JSON object.")
    except Exception as e:
        logger.warning("DLMOD", f"Could not load cached kkc_mod_index.json: {e}")
    return None


async def load_kkc_mod_index(client) -> dict[str, str]:
    """Return the {guid: t.me link} index from kkc-mod-scraper.

    The cached copy (CONFIG_DIR/config/kkc_mod_index.json) is used only if
    the commit recorded in kkc_mod_index_last_commit.txt matches the repo's
    latest commit and the cached file loads successfully. Otherwise the
    index is downloaded again and the commit file is updated.

    Returns an empty dict if the index is unavailable.
    """
    import json
    from kkafio.core.constants import CONFIG_DIR

    cfg_dir     = CONFIG_DIR / "config"
    index_path  = cfg_dir / "kkc_mod_index.json"
    commit_path = cfg_dir / "kkc_mod_index_last_commit.txt"

    latest_commit = await _fetch_latest_index_commit(client)

    saved_commit = ""
    try:
        if commit_path.exists():
            saved_commit = commit_path.read_text(encoding="utf-8").strip()
    except OSError:
        pass

    if latest_commit and saved_commit == latest_commit:
        cached = _load_cached_kkc_index(index_path)
        if cached is not None:
            logger.info("DLMOD", f"kkc mod index up to date: {len(cached)} GUIDs (cached)")
            return cached
        logger.info("DLMOD", "kkc mod index cache missing or unreadable — downloading...")
    elif latest_commit is None:
        # Can't tell whether the cache is current; use it rather than fail.
        cached = _load_cached_kkc_index(index_path)
        if cached is not None:
            logger.warning("DLMOD",
                "Could not check for kkc mod index updates — using cached copy.")
            return cached
    else:
        logger.info("DLMOD", "kkc mod index is new or outdated — downloading...")

    try:
        r = await client.get(KKC_INDEX_URL)
        r.raise_for_status()
        index = r.json()
        if not isinstance(index, dict):
            raise ValueError("index is not a JSON object")
    except Exception as e:
        logger.error("DLMOD", f"Failed to download kkc mod index: {e}")
        return {}

    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        tmp = index_path.with_name(index_path.name + ".part")
        tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, index_path)
        if latest_commit:
            commit_path.write_text(latest_commit, encoding="utf-8")
    except OSError as e:
        logger.warning("DLMOD", f"Could not save kkc mod index cache: {e}")

    logger.info("DLMOD", f"kkc mod index downloaded: {len(index)} GUIDs")
    return index
