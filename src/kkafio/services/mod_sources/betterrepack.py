"""BetterRepack: the Sideloader Modpack download server (plain HTTP).

Also hosts the shared HTTP client Download Missing Mods uses for it and for the
kkc mod index (see kkafio.indices.kkc_mod).
"""

import asyncio
import os
from pathlib import Path

from kkafio.core.logger import logger


BETTERREPACK_BASE     = "https://sideload.betterrepack.com/download/KKEC"

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
