"""Streaming downloads and paged-listing loops shared by the sources.

``download_file`` fetches one URL into ``<name>.part`` and renames it once the
whole body has arrived; ``download_files`` runs a batch of them with bounded
concurrency; ``download_pages`` walks a listing's pages.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.net import MAX_CONNECTIONS, MAX_CONSECUTIVE_BAD_PAGES, RETRY_ATTEMPTS, is_transient, resolve_filename, retry_delay, strip_page

async def download_file(
    client, url: str, directory: Path, default_name: str,
    history: dict[str, str], skip_downloaded: bool,
) -> tuple[str, Exception | None, bool]:
    """Download one file. Returns (url, error, was_skipped)."""
    if skip_downloaded and url in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[url]).name}")
        return url, None, True

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        # Stream into "<name>.part" and only rename to the real name once the
        # whole body has arrived. A failed/cancelled transfer therefore never
        # leaves a truncated card behind under its final name (where Install
        # Contents would happily pick it up).
        part: Path | None = None
        completed = False
        try:
            import aiofiles
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                filename = resolve_filename(response, url, default_name)
                dest = directory / filename
                part = dest.with_name(dest.name + ".part")
                logger.info("DLOAD", f"Downloading: {filename}")
                async with aiofiles.open(part, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        await f.write(chunk)
            os.replace(part, dest)
            completed = True
            history[url] = str(dest)
            return url, None, False
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS and is_transient(exc):
                delay = retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {url} — {exc}; retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                continue
            return url, exc, False
        finally:
            if part is not None and not completed:
                # Covers errors and cancellation (Stop / Ctrl+C) alike.
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
    return url, RuntimeError("retries exhausted"), False  # not reached


async def download_files(
    client, file_urls: list[str], directory: Path,
    default_name_fn, history: dict[str, str], skip_downloaded: bool,
) -> tuple[int, int, int]:
    """Download all URLs, at most MAX_CONNECTIONS at a time.

    Returns (succeeded, skipped, failed). "Skipped" means already in the
    download history; it is not a failure.
    """
    if not file_urls:
        return 0, 0, 0

    logger.info("DLOAD", f"  Downloading {len(file_urls)} file(s)...")

    # Bound concurrency ourselves. Without this every URL on a page starts at
    # once and the surplus waits inside httpx's connection pool, where that
    # wait counted against the request timeout and could time out large or
    # slow downloads that were merely queued.
    sem = asyncio.Semaphore(MAX_CONNECTIONS)

    async def _bounded(url: str):
        async with sem:
            return await download_file(client, url, directory, default_name_fn(url),
                                        history, skip_downloaded)

    succeeded = skipped = failed = 0
    for coro in asyncio.as_completed([_bounded(u) for u in file_urls]):
        url, err, was_skipped = await coro
        if err:
            failed += 1
            logger.error("DLOAD", f"  Failed: {url} — {err}")
        elif was_skipped:
            skipped += 1
        else:
            succeeded += 1
    return succeeded, skipped, failed


async def download_pages(
    client, base_url: str, directory: Path,
    history: dict, skip: bool,
    page_start: int, page_end: int | None,
    get_page_urls_fn, default_name_fn,
) -> tuple[int, int, int]:
    """Download a range of listing pages. Returns (succeeded, skipped, failed).

    page_end=None → keep going until a page has no cards.
    Supports reverse order when page_start > page_end.

    get_page_urls_fn(client, url, page) returns
    (file_urls, card_failures, listing_empty). A page whose listing has no
    cards ends an open-ended run; a page whose cards all failed to resolve
    does not (it is counted as failed and paging continues).
    """
    total_ok = total_skip = total_fail = 0
    clean_url = strip_page(base_url)
    seen: set[str] = set()
    bad_pages = 0

    if page_end is None:
        page_iter = (p for p in range(page_start, 10 ** 9))
    else:
        step      = 1 if page_end >= page_start else -1
        page_iter = iter(range(page_start, page_end + step, step))

    for page in page_iter:
        logger.info("DLOAD", f"  Page {page}...")
        try:
            file_urls, card_fail, listing_empty = await get_page_urls_fn(client, clean_url, page)
        except Exception as exc:
            # Do NOT treat this as "end of results": we can't tell.
            total_fail += 1
            bad_pages += 1
            logger.error("DLOAD", f"  Page {page} failed: {exc}")
            if bad_pages >= MAX_CONSECUTIVE_BAD_PAGES:
                logger.error("DLOAD",
                    f"  {bad_pages} pages in a row failed — stopping. Re-run later to resume.")
                break
            continue

        total_fail += card_fail
        if listing_empty:
            logger.info("DLOAD", f"  Page {page} is empty — stopping.")
            break

        if not file_urls and card_fail == 0:
            # Results exist but none are downloadable (e.g. pixiv search hits
            # that all fail the size filter) — not an error, keep paging.
            logger.info("DLOAD", f"  Page {page}: nothing to download.")
            bad_pages = 0
            continue
        if not file_urls:
            bad_pages += 1
            logger.warning("DLOAD",
                f"  Page {page}: no downloadable links ({card_fail} card(s) failed).")
            if bad_pages >= MAX_CONSECUTIVE_BAD_PAGES:
                logger.error("DLOAD",
                    f"  {bad_pages} pages in a row failed — stopping. Re-run later to resume.")
                break
            continue
        bad_pages = 0

        # Some sites clamp or wrap out-of-range page numbers and keep serving
        # the last page; without this an open-ended run would never end.
        new_urls = [u for u in file_urls if u not in seen]
        if not new_urls:
            logger.info("DLOAD", f"  Page {page} repeats earlier results — stopping.")
            break
        seen.update(new_urls)

        ok, skipped, fail = await download_files(
            client, new_urls, directory, default_name_fn, history, skip)
        total_ok   += ok
        total_skip += skipped
        total_fail += fail
        history_store.save_history(history)   # checkpoint after every page

    return total_ok, total_skip, total_fail
