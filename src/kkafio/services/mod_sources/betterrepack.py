"""BetterRepack: the Sideloader Modpack download server (plain HTTP).

Also hosts the shared HTTP client Download Missing Mods uses for it and for the
kkc mod index (see kkafio.indices.kkc_mod).
"""

import asyncio
import os
from pathlib import Path

from kkafio.cards.mods.zipmods import guid_from_zipmod
from kkafio.core import progress
from kkafio.core.logger import logger
from kkafio.indices.modpack import url_to_rel_path


MAX_CONNECTIONS       = 4


# ---------------------------------------------------------------------------
# HTTP client (for BetterRepack + the kkc mod index)
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

# What one download attempt leads to (see _download_one).
_DONE, _NEXT_URL, _GIVE_UP = "done", "next", "give up"


async def _download_one(client, guid: str, url: str, dest: Path) -> str:
    """Download `url` to `dest`. Returns _DONE, _NEXT_URL (this file is unusable: the server
    has no such file, or what came back isn't the mod, so another URL may work) or _GIVE_UP
    (a connection, server or disk problem that another URL of the same server would only
    repeat)."""
    import httpx

    logger.info("DLMOD", f"BetterRepack ↓ {dest.name}")
    # Download to "<name>.part" and rename only once the body is complete and checked.
    part = dest.with_name(dest.name + ".part")
    outcome = _GIVE_UP
    handle = None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            import aiofiles
            handle = progress.begin(dest.name, progress.content_length(r))
            received = 0
            async with aiofiles.open(part, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    await f.write(chunk)
                    received += len(chunk)
                    handle.update(received)

        found = await asyncio.to_thread(guid_from_zipmod, part)
        if found is None:
            logger.warning("DLMOD", f"BetterRepack [{guid}]: {dest.name} is not a valid zipmod")
            outcome = _NEXT_URL
        elif found.casefold() != guid.casefold():
            logger.warning("DLMOD",
                f"BetterRepack [{guid}]: {dest.name} contains a different mod ({found})")
            outcome = _NEXT_URL
        else:
            os.replace(part, dest)
            logger.success("DLMOD", f"Downloaded: {dest.name}")
            outcome = _DONE
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        logger.error("DLMOD", f"BetterRepack failed [{guid}]: HTTP {status} for {dest.name}")
        outcome = _NEXT_URL if 400 <= status < 500 else _GIVE_UP
    except Exception as e:
        logger.error("DLMOD", f"BetterRepack failed [{guid}]: {e}")
    finally:
        if handle is not None:
            handle.close()      # the task counts the outcome per mod
        if outcome != _DONE:
            # Errors and cancellation (Stop / Ctrl+C) alike.
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
    return outcome


async def download_betterrepack(
    client, guid: str, urls: list[str], mods_dir: Path,
    guid_str_map: dict[str, str],
) -> bool | str:
    """Download one modpack zipmod, trying its URLs in order (best first). The file is saved
    under mods_dir at the path of its URL, keeping the Sideloader Modpack folder structure.

    Returns True (downloaded), "skipped" (already present and intact) or False (failed).
    The next URL is only tried after a failure that is about that file (HTTP 4xx, a download
    that isn't a zipmod or has another GUID); connection and server errors end the attempt."""
    import zipfile

    for url in urls:
        rel = url_to_rel_path(url)
        if rel is None:
            logger.warning("DLMOD", f"BetterRepack [{guid}]: ignoring unusable URL {url}")
            continue
        dest = mods_dir / rel

        # Only trust an existing file if it is a complete zip. Earlier versions
        # streamed straight into the final name, so an interrupted download could
        # leave a truncated .zipmod behind that would otherwise be "skipped" forever.
        if dest.exists():
            if await asyncio.to_thread(zipfile.is_zipfile, dest):
                return "skipped"
            logger.warning("DLMOD", f"Existing file is incomplete/corrupt, re-downloading: {dest.name}")

        outcome = await _download_one(client, guid, url, dest)
        if outcome == _DONE:
            guid_str_map[guid] = str(dest)
            return True
        if outcome == _GIVE_UP:
            return False
    return False
