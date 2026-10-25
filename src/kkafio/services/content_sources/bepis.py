"""db.bepis.moe — card pages (/view/) and listing pages (through its search API)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from kkafio.core.logger import logger
from kkafio.services.content_sources import transfer
from kkafio.services.content_sources.net import get_with_retry, set_page

BEPIS_URL     = "https://db.bepis.moe"
BEPIS_API_URL = "https://db.bepis.moe/api/frontend/search?"


def _bepis_default_name(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def _get_bepis_card_type(url: str) -> str:
    card_type = ""
    if "koikatsu" in url:
        card_type = "KK"
    elif "kkscenes" in url:
        card_type = "KKSCENE"
    elif "kkclothing" in url:
        card_type = "KKCLOTHING"
    return card_type


def _get_bepis_file_card_id(card_id: str | int) -> str:
    card_id = str(card_id)
    length = len(card_id)
    if length < 6:
        return "0" * (6 - length) + card_id
    return card_id


async def _get_bepis_file_url(url: str) -> str:
    card_type = _get_bepis_card_type(url)
    card_id = url.split("/")[-1]
    file_card_id = _get_bepis_file_card_id(card_id)
    return f"{BEPIS_URL}/card/full/{card_type}_{file_card_id}.png"


async def _get_bepis_page_urls(client, base_url: str, page: int) -> tuple[list[str], int, bool]:
    """Returns (file_urls, card_failures, listing_empty)."""
    query_string = urlparse(base_url).query
    card_type = _get_bepis_card_type(base_url)
    api_url = f"{BEPIS_API_URL}cardType={card_type}&{query_string}"
    r = await get_with_retry(client, set_page(api_url, page))
    data = r.json().get("data")
    cards = data.get("cards", []) if data else []
    urls = [
        f"{BEPIS_URL}/card/full/{card_type}_{_get_bepis_file_card_id(card["id"])}.png"
        for card in cards
    ]
    return urls, 0, not cards


async def download_bepis(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None = None, page_end: int | None = None,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed)."""
    if "/view/" in url:
        # Single card page — pagination args ignored
        logger.info("DLOAD", f"Scraping card page: {url}")
        file_url = await _get_bepis_file_url(url)
        if not file_url:
            logger.error("DLOAD", "Could not find download link on page.")
            return 0, 0, 1
        _, err, was_skipped = await transfer.download_file(
            client, file_url, directory, _bepis_default_name(file_url), history, skip)
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
            _get_bepis_page_urls, _bepis_default_name)

    else:
        logger.info("DLOAD", f"Scraping card list: {url}")
        file_urls, card_fail, _empty = await _get_bepis_page_urls(client, url, 1)
        ok, skipped, fail = await transfer.download_files(
            client, file_urls, directory, _bepis_default_name, history, skip)
        return ok, skipped, fail + card_fail
