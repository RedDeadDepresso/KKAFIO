"""koikatsucards.com — card pages (/contents/) and listing pages.

Needs the ``kkd_session`` cookie (see ``kkafio.services.kkd_session``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urljoin, urlparse

from kkafio.core.logger import logger
from kkafio.services.content_sources import transfer
from kkafio.services.content_sources.net import get_with_retry, set_page

KOIKATSU_URL  = "https://koikatsucards.com"
KK_SCRAPE_CONCURRENCY = 5      # simultaneous koikatsucards card-page fetches


def _koikatsu_default_name(url: str) -> str:
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return name + ".png"


async def _get_koikatsu_file_url(client, url: str) -> str:
    from bs4 import BeautifulSoup
    r = await get_with_retry(client, url)
    soup = BeautifulSoup(r.text, "html.parser")

    tag = soup.select_one("div.download-menu-panel > a.download-menu-item")
    if tag and tag.get("href"):
        return urljoin(KOIKATSU_URL, tag["href"])

    tags = soup.select("div.tag-group.download-link-group > a.link-pill")
    for tag in tags:
        if tag and "/api/downloads/" in tag.get("href", ""):
            return urljoin(KOIKATSU_URL, tag["href"])
    return ""


async def _get_koikatsu_page_urls(client, base_url: str, page: int) -> tuple[list[str], int, bool]:
    """Returns (file_urls, card_failures, listing_empty).

    A card whose page could not be fetched (after retries) or that has no
    download link is counted in card_failures and logged — it is no longer
    silently dropped, and it no longer makes a page look like the end of the
    listing.
    """
    from bs4 import BeautifulSoup
    r = await get_with_retry(client, set_page(base_url, page))
    soup = BeautifulSoup(r.text, "html.parser")
    sub_pages = [
        urljoin(KOIKATSU_URL, tag["href"])
        for tag in soup.select("a.card")
        if tag.get("href")
    ]
    if not sub_pages:
        return [], 0, True
    logger.info("DLOAD", f"  Found {len(sub_pages)} card(s), scraping download links...")

    sem = asyncio.Semaphore(KK_SCRAPE_CONCURRENCY)

    async def _bounded(p: str) -> str:
        async with sem:
            return await _get_koikatsu_file_url(client, p)

    results = await asyncio.gather(*[_bounded(p) for p in sub_pages], return_exceptions=True)

    urls: list[str] = []
    failed = 0
    for sub_page, res in zip(sub_pages, results, strict=True):
        if isinstance(res, Exception):
            failed += 1
            logger.error("DLOAD", f"  Could not read card page {sub_page}: {res}")
        elif not res:
            failed += 1
            logger.warning("DLOAD", f"  No download link on {sub_page}")
        else:
            urls.append(res)
    return urls, failed, False


async def download_koikatsu(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None = None, page_end: int | None = None,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed)."""
    if "/contents/" in url:
        # Single card page
        logger.info("DLOAD", f"Scraping card page: {url}")
        file_url = await _get_koikatsu_file_url(client, url)
        if not file_url:
            logger.error("DLOAD", "Could not find download link on page.")
            return 0, 0, 1
        _, err, was_skipped = await transfer.download_file(
            client, file_url, directory, _koikatsu_default_name(file_url), history, skip)
        if err:
            logger.error("DLOAD", f"  Failed: {file_url} — {err}")
            return 0, 0, 1
        return (0, 1, 0) if was_skipped else (1, 0, 0)

    elif page_start is not None:
        logger.info("DLOAD",
            f"Scraping page {page_start}: {url}" if page_end == page_start else
            f"Scraping pages {page_start}–{'end' if page_end is None else page_end}: {url}")
        return await transfer.download_pages(
            client, url, directory, history, skip,
            page_start, page_end,
            _get_koikatsu_page_urls, _koikatsu_default_name)

    else:
        logger.info("DLOAD", f"Scraping card list: {url}")
        file_urls, card_fail, _empty = await _get_koikatsu_page_urls(client, url, 1)
        ok, skipped, fail = await transfer.download_files(
            client, file_urls, directory, _koikatsu_default_name, history, skip)
        return ok, skipped, fail + card_fail
