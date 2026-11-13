"""Streaming downloads and paged-listing loops shared by the sources.

``download_file`` fetches one URL into a ``.part`` file (continued with a Range request after an
interruption) and renames it once the whole body has arrived; ``download_files`` runs a batch of them with bounded
concurrency; ``download_pages`` walks a listing's pages.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path

from kkafio.core import progress
from kkafio.core.logger import logger
from kkafio.services.content_sources.shared import history as history_store
from kkafio.services.content_sources.shared.net import (
    MAX_CONNECTIONS, MAX_CONSECUTIVE_BAD_PAGES, RETRY_ATTEMPTS, is_transient, resolve_filename, retry_delay,
    safe_filename, strip_page,
)

MAX_RESUMES = 25        # extra tries given to a transfer that keeps making progress before it drops


class _Incomplete(Exception):
    """The body ended before the announced size: worth another (resumed) try."""


def _content_range(response) -> tuple[int, int | None] | None:
    """(first byte, total size or None) of a 206's "Content-Range: bytes a-b/total"."""
    m = re.match(r"\s*bytes\s+(\d+)-\d+/(\d+|\*)", response.headers.get("content-range", ""), re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)), (int(m.group(2)) if m.group(2).isdigit() else None)


def _unsatisfied_total(response) -> int | None:
    """The total size of a 416's "Content-Range: bytes */total", if it says."""
    m = re.match(r"\s*bytes\s+\*/(\d+)", response.headers.get("content-range", ""), re.IGNORECASE)
    return int(m.group(1)) if m else None


def part_path(directory: Path, name: str, key: str) -> Path:
    """The ``.part`` file of a download. Its name is known before the request (so an interrupted
    transfer can be continued) and tied to `key` (the URL, or whatever identifies the file when its
    URL is temporary), so two different files that share a name never share a part."""
    tag = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return directory / f"{safe_filename(name, 'download')}.{tag}.part"


async def download_file(
    client, url: str, directory: Path, default_name: str,
    history: dict[str, str], skip_downloaded: bool,
    *, filename: str | None = None, headers: dict[str, str] | None = None,
    resume_key: str | None = None, count_failure: bool = True,
) -> tuple[str, Exception | None, bool]:
    """Download one file. Returns (url, error, was_skipped).

    `filename` fixes the name on disk (otherwise it comes from the response / URL); `headers` are
    sent with this transfer only.

    The body is streamed into a ``.part`` file that is renamed to the real name once it is complete,
    so a truncated card never sits under its final name (where Install Contents would pick it up).
    An interrupted transfer keeps its part: the next attempt (or run) continues it with a Range
    request. A server that ignores Range sends the whole file, which replaces the part; a part that
    is not a prefix of the file (416) is dropped and the file fetched again. An attempt that gained
    bytes does not use up a retry, so a big file on a bad connection keeps going. `resume_key`
    identifies the file across runs when its URL changes every time (default: the URL).
    ``count_failure=False`` leaves a final failure uncounted, for a caller that retries it.
    """
    if skip_downloaded and url in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[url]).name}")
        progress.count("skipped")
        return url, None, True

    part = part_path(directory, filename or default_name, resume_key or url)
    attempt = 1
    resumes = 0
    while attempt <= RETRY_ATTEMPTS:
        handle = None
        start = written = 0
        try:
            import aiofiles
            offset = part.stat().st_size if part.exists() else 0
            req_headers = {"Accept-Encoding": "identity", **(headers or {})}      # byte counts must be exact
            if offset:
                req_headers["Range"] = f"bytes={offset}-"
            async with client.stream("GET", url, headers=req_headers) as response:
                complete_already = False
                if offset and response.status_code == 416:
                    if _unsatisfied_total(response) == offset:
                        complete_already = True         # the part is the whole file; it was never renamed
                    else:
                        part.unlink(missing_ok=True)    # the part is not a prefix of the file
                        continue                        # start again; this try is not used up
                else:
                    response.raise_for_status()
                name = filename or resolve_filename(response, url, default_name)
                dest = directory / name

                total: int | None = None
                length = response.headers.get("content-length", "")
                if complete_already:
                    total = offset
                elif offset and response.status_code == 206:
                    span = _content_range(response)
                    if span is None or span[0] != offset:
                        part.unlink(missing_ok=True)
                        raise _Incomplete("server answered a different range than asked; restarting")
                    total = span[1] or (offset + int(length) if length.isdigit() else None)
                else:                                   # a plain 200: the whole file, Range or not
                    offset = 0
                    total = int(length) if length.isdigit() else None
                start = written = offset
                logger.info("DLOAD", f"{'Resuming' if offset else 'Downloading'}: {name}"
                                     + (f" from {offset} bytes" if offset else ""))
                handle = progress.begin(name, total)
                handle.update(written)
                if not complete_already:
                    async with aiofiles.open(part, "ab" if offset else "wb") as f:
                        # No re-chunking: what has arrived is written at once, so a dropped
                        # connection loses nothing that was received.
                        async for chunk in response.aiter_bytes():
                            await f.write(chunk)
                            written += len(chunk)
                            handle.update(written)
            if total is not None and written != total:
                if written > total:
                    part.unlink(missing_ok=True)        # more than the file holds: unusable
                raise _Incomplete(f"incomplete download ({written} of {total} bytes)")
            os.replace(part, dest)
            history[url] = str(dest)
            handle.close(ok=True)
            return url, None, False
        except Exception as exc:
            retryable = is_transient(exc) or isinstance(exc, _Incomplete)
            if retryable and written > start and resumes < MAX_RESUMES:
                resumes += 1                            # it moved forward: continue without using a try
                logger.warning("DLOAD", f"  {url} — {exc}; resuming from {part.stat().st_size} bytes")
                await asyncio.sleep(retry_delay(exc, 1))
                continue
            if retryable and attempt < RETRY_ATTEMPTS:
                delay = retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {url} — {exc}; retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                attempt += 1
                continue
            if getattr(getattr(exc, "response", None), "status_code", None) in (404, 410):
                part.unlink(missing_ok=True)            # the file is gone: nothing to resume later
            if count_failure:
                progress.count("failed")
            return url, exc, False
        finally:
            if handle is not None:
                handle.close()          # no-op after a success; drops the row on a retry / cancel
            # A failed or cancelled (Stop / Ctrl+C) transfer keeps its part for the next attempt.
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