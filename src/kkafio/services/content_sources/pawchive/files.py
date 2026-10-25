"""Downloading a single pawchive file (with the PNG size filter)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.services.content_sources.card_size import kk_size_matches
from kkafio.services.content_sources.net import RETRY_ATTEMPTS, is_rate_limited, is_transient, retry_after_seconds, retry_delay
from kkafio.services.content_sources.pawchive.access import PAWCHIVE_MAX_RATE_LIMITED, PawchiveThrottle
from kkafio.services.content_sources.pawchive.parsing import pawchive_item_key

_PAWCHIVE_HEAD_BYTES = 32                       # bytes needed to read a PNG's size


def _pawchive_png_matches(path: Path) -> bool:
    """True when the PNG at `path` is 320x180 or has the 252x352 ratio. The
    size is read with imagesize, which only looks at the file header."""
    import imagesize
    try:
        width, height = imagesize.get(str(path))
    except Exception:
        return False
    return kk_size_matches(width, height)


async def _download_pawchive_url(
    client, url: str, dest: Path, name: str, check: bool,
) -> tuple[Exception | None, str]:
    """Fetch one URL of a pawchive file into `dest`. Returns (error, status)
    with status "ok" or "filtered" (a PNG that is not a KK card/scene size;
    aborted after its first chunk, nothing is kept)."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        # Same ".part" scheme as transfer.download_file: a failed, cancelled or
        # filtered transfer never leaves anything under the final name.
        part: Path | None = None
        completed = False
        try:
            import aiofiles
            verdict: bool | None = None        # None until the PNG header is checked
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                part = dest.with_name(dest.name + ".part")
                received = 0
                if not check:
                    logger.info("DLOAD", f"Downloading: {name}")
                async with aiofiles.open(part, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        await f.write(chunk)
                        if check and verdict is None:
                            received += len(chunk)
                            if received >= _PAWCHIVE_HEAD_BYTES:
                                await f.flush()
                                verdict = _pawchive_png_matches(part)
                                if not verdict:
                                    break
                                logger.info("DLOAD", f"Downloading: {name}")
                    if check and verdict is None:      # whole file shorter than a header
                        await f.flush()
                        verdict = _pawchive_png_matches(part)
            if check and not verdict:
                return None, "filtered"
            os.replace(part, dest)
            completed = True
            return None, "ok"
        except Exception as exc:
            if is_rate_limited(exc):
                return exc, "ok"       # the caller applies the shared cooldown
            if attempt < RETRY_ATTEMPTS and is_transient(exc):
                delay = retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {url} — {exc}; retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                continue
            return exc, "ok"
        finally:
            if part is not None and not completed:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
    return RuntimeError("retries exhausted"), "ok"   # not reached


def pawchive_already_downloaded(item: dict, history: dict[str, str], skip_downloaded: bool) -> bool:
    return bool(skip_downloaded) and pawchive_item_key(item) in history


async def download_pawchive_file(
    client, item: dict, directory: Path, name: str,
    history: dict[str, str], skip_downloaded: bool,
    throttle: PawchiveThrottle | None = None,
) -> tuple[str, Exception | None, str]:
    """Download one pawchive file. Returns (url, error, status) with status
    "ok", "skipped" (already in the history) or "filtered" (a PNG that is not
    a KK card/scene size; nothing is kept).

    The candidate URLs of the file are tried in order: a host that fails (403,
    404, unreachable, ...) hands over to the next one, and only the last
    failure is reported. A 429 is different: it starts the shared cooldown (see
    PawchiveThrottle), and the same URL is tried again once it is over -- up
    to PAWCHIVE_MAX_RATE_LIMITED times in a row -- instead of failing the file.
    """
    throttle = throttle or PawchiveThrottle()
    url = item["url"]
    key = pawchive_item_key(item)
    if skip_downloaded and key in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[key]).name}")
        return url, None, "skipped"

    dest = directory / name
    check = bool(item["check_size"])
    urls = item.get("urls") or [url]
    err: Exception | None = None
    for i, candidate in enumerate(urls):
        limited = 0
        while True:
            await throttle.gate()
            err, status = await _download_pawchive_url(client, candidate, dest, name, check)
            if not is_rate_limited(err):
                break
            limited += 1
            if limited > PAWCHIVE_MAX_RATE_LIMITED:
                break
            throttle.on_rate_limited(retry_after_seconds(err), limited)
        if err is None:
            throttle.on_success()
            if status == "ok":
                history[key] = str(dest)
            return url, None, status
        if i + 1 < len(urls):
            logger.warning("DLOAD", f"  {candidate} — {err}; trying {urls[i + 1]}")
    return url, err, "ok"
