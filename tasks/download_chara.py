"""
download_chara.py — Download Koikatsu character cards from web sources.

Supports:
  https://db.bepis.moe          — card pages (/view/) and listing pages
  https://koikatsucards.com     — card pages (/contents/) and listing pages

Runs async I/O (httpx + asyncio) inside a regular synchronous run() call so
it integrates cleanly with the rest of KKAFIO's task system.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse

from tasks.base_task import BaseTask
from util.logger import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BEPIS_URL     = "https://db.bepis.moe"
KOIKATSU_URL  = "https://koikatsucards.com"
MAX_CONNECTIONS = 10

HISTORY_FILE = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    / "KKAFIO" / "download_history.json"
)

# ---------------------------------------------------------------------------
# Download history
# ---------------------------------------------------------------------------

def _load_history() -> dict[str, str]:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error("DLOAD", f"Could not load download history: {e}")
        return {}


def _save_history(history: dict[str, str]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------

def _make_client():
    import httpx
    limits = httpx.Limits(
        max_connections=MAX_CONNECTIONS,
        max_keepalive_connections=MAX_CONNECTIONS,
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    return httpx.AsyncClient(
        limits=limits, headers=headers,
        follow_redirects=True, timeout=30,
    )


# ---------------------------------------------------------------------------
# Filename resolution
# ---------------------------------------------------------------------------

def _resolve_filename(response, url: str, default_name: str) -> str:
    cd = response.headers.get("content-disposition", "")
    if cd:
        for part in cd.split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                name = part[9:].strip().strip('"').strip("'")
                if name:
                    return Path(name).name
    path_part = Path(urlparse(url).path).name
    if path_part and path_part not in (".", "/"):
        return path_part
    return default_name


# ---------------------------------------------------------------------------
# Core file downloader
# ---------------------------------------------------------------------------

async def _download_file(
    client,
    url: str,
    directory: Path,
    default_name: str,
    history: dict[str, str],
    skip_downloaded: bool,
) -> tuple[str, Exception | None]:
    if skip_downloaded and url in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[url]).name}")
        return url, None
    try:
        import aiofiles
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            filename = _resolve_filename(response, url, default_name)
            dest = directory / filename
            logger.info("DLOAD", f"Downloading: {filename}")
            async with aiofiles.open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    await f.write(chunk)
        history[url] = str(dest)
        return url, None
    except Exception as exc:
        return url, exc


async def _download_files(
    client,
    file_urls: list[str],
    directory: Path,
    default_name_fn,
    history: dict[str, str],
    skip_downloaded: bool,
) -> tuple[int, int]:
    """Download all URLs concurrently. Returns (succeeded, failed) counts."""
    if not file_urls:
        logger.info("DLOAD", "No files to download.")
        return 0, 0

    logger.info("DLOAD", f"Starting download of {len(file_urls)} file(s)...")

    tasks = [
        _download_file(client, url, directory, default_name_fn(url),
                       history, skip_downloaded)
        for url in file_urls
    ]

    succeeded = 0
    failed    = 0

    for coro in asyncio.as_completed(tasks):
        url, err = await coro
        if err:
            failed += 1
            logger.error("DLOAD", f"Failed: {url} — {err}")
        else:
            succeeded += 1

    return succeeded, failed


# ---------------------------------------------------------------------------
# Bepis scraping
# ---------------------------------------------------------------------------

def _bepis_default_name(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


async def _get_bepis_file_url(client, url: str) -> str:
    from bs4 import BeautifulSoup
    r = await client.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    tag = soup.select_one("a.btn.btn-primary.mr-1.flex-grow-1")
    if tag and tag.get("href"):
        return urljoin(BEPIS_URL, tag["href"])
    return ""


async def _get_bepis_file_urls(client, url: str) -> list[str]:
    from bs4 import BeautifulSoup
    r = await client.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return [
        urljoin(BEPIS_URL, tag["href"])
        for tag in soup.select(".btn.btn-primary.btn-sm")
        if tag.get("href")
    ]


async def _download_bepis(client, url: str, directory: Path,
                           history: dict, skip: bool) -> tuple[int, int]:
    if "/view/" in url:
        logger.info("DLOAD", f"Scraping card page: {url}")
        file_url = await _get_bepis_file_url(client, url)
        if not file_url:
            logger.error("DLOAD", "Could not find download link on page.")
            return 0, 1
        _, err = await _download_file(
            client, file_url, directory, _bepis_default_name(file_url), history, skip)
        return (1, 0) if not err else (0, 1)
    else:
        logger.info("DLOAD", f"Scraping card list: {url}")
        file_urls = await _get_bepis_file_urls(client, url)
        return await _download_files(
            client, file_urls, directory, _bepis_default_name, history, skip)


# ---------------------------------------------------------------------------
# KoikatsuCards scraping
# ---------------------------------------------------------------------------

def _koikatsu_default_name(url: str) -> str:
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return name + ".png"


async def _get_koikatsu_file_url(client, url: str) -> str:
    from bs4 import BeautifulSoup
    r = await client.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    tag = soup.select_one("div.download-menu-panel > a.download-menu-item")
    if tag and tag.get("href"):
        return urljoin(KOIKATSU_URL, tag["href"])

    tags = soup.select("div.tag-group.download-link-group > a.link-pill")
    for tag in tags:
        if tag and "/api/downloads/" in tag.get("href", ""):
            return urljoin(KOIKATSU_URL, tag["href"])
    return ""


async def _get_koikatsu_file_urls(client, url: str) -> list[str]:
    from bs4 import BeautifulSoup
    r = await client.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    sub_pages = [
        urljoin(KOIKATSU_URL, tag["href"])
        for tag in soup.select("a.card")
        if tag.get("href")
    ]
    logger.info("DLOAD", f"Found {len(sub_pages)} card(s), scraping download links...")

    results = await asyncio.gather(
        *[_get_koikatsu_file_url(client, page) for page in sub_pages],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, str) and r]


async def _download_koikatsu(client, url: str, directory: Path,
                              history: dict, skip: bool) -> tuple[int, int]:
    if "/contents/" in url:
        logger.info("DLOAD", f"Scraping card page: {url}")
        file_url = await _get_koikatsu_file_url(client, url)
        if not file_url:
            logger.error("DLOAD", "Could not find download link on page.")
            return 0, 1
        _, err = await _download_file(
            client, file_url, directory, _koikatsu_default_name(file_url), history, skip)
        return (1, 0) if not err else (0, 1)
    else:
        logger.info("DLOAD", f"Scraping card list: {url}")
        file_urls = await _get_koikatsu_file_urls(client, url)
        return await _download_files(
            client, file_urls, directory, _koikatsu_default_name, history, skip)


# ---------------------------------------------------------------------------
# Main task class
# ---------------------------------------------------------------------------

class DownloadChara(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.download_chara
        self.links          : str  = cfg.get("Links", "")
        self.output_dir_str : str  = cfg.get("OutputDir", "")
        self.skip_downloaded: bool = cfg.get("SkipDownloaded", True)

    def run(self) -> None:
        urls = [
            line.strip()
            for line in self.links.splitlines()
            if line.strip() and line.strip().startswith("http")
        ]

        if not urls:
            logger.error("DLOAD", "No URLs found. Enter one URL per line in the Download Links field.")
            return

        output_dir = (
            Path(self.output_dir_str) if self.output_dir_str
            else Path(self.config.config_data["Core"].get(
                "DownloadsPath",
                Path.home() / "Downloads"
            ))
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        self.log_start("DLOAD")
        logger.info("DLOAD", f"Output directory : {output_dir}")
        logger.info("DLOAD", f"Skip downloaded  : {self.skip_downloaded}")
        logger.info("DLOAD", f"URLs to process  : {len(urls)}")

        history = _load_history()
        total_ok = 0
        total_fail = 0

        async def _run_all() -> None:
            nonlocal total_ok, total_fail
            async with _make_client() as client:
                for url in urls:
                    if BEPIS_URL in url:
                        ok, fail = await _download_bepis(
                            client, url, output_dir, history, self.skip_downloaded)
                    elif KOIKATSU_URL in url:
                        ok, fail = await _download_koikatsu(
                            client, url, output_dir, history, self.skip_downloaded)
                    else:
                        logger.error("DLOAD",
                            f"Unsupported URL (must be db.bepis.moe or koikatsucards.com): {url}")
                        fail = 1
                        ok   = 0
                    total_ok   += ok
                    total_fail += fail

        asyncio.run(_run_all())
        _save_history(history)

        self.log_done("DLOAD", moved=total_ok, skipped=total_fail,
                      extra=f"{total_ok + total_fail} total")
