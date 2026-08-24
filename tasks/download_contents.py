"""
download_contents.py — Download Koikatsu character cards from web sources.

Supports:
  https://db.bepis.moe          — card pages (/view/) and listing pages
  https://koikatsucards.com     — card pages (/contents/) and listing pages

Input line formats (| is the separator — safe because URLs never contain |):
  https://...                   plain URL — single card page or one listing page
  https://... | all             listing: download all pages until empty
  https://... | 3 | 7           listing: pages 3 through 7 inclusive
  https://... | 7 | 3           listing: pages 7 down to 3 (reverse order)
  # comment                     lines starting with # are ignored

Both sites use ?page=N for pagination.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse, urljoin

from tasks.base_task import BaseTask
from utils.logger import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BEPIS_URL     = "https://db.bepis.moe"
BEPIS_API_URL = "https://db.bepis.moe/api/frontend/search?"
KOIKATSU_URL  = "https://koikatsucards.com"
MAX_CONNECTIONS = 10

HISTORY_FILE = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    / "KKAFIO" / "download_history.json"
)

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _set_page(url: str, page: int) -> str:
    """Return url with ?page=<page> set or replaced."""
    parts  = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    params["page"] = str(page)
    return urlunparse(parts._replace(query=urlencode(params)))


def _strip_page(url: str) -> str:
    """Return url with ?page removed."""
    parts  = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()
              if k != "page"}
    return urlunparse(parts._replace(query=urlencode(params)))


# ---------------------------------------------------------------------------
# Line parser
# ---------------------------------------------------------------------------

def _parse_line(raw: str) -> tuple[str, int | None, int | None] | None:
    """Parse one input line.

    Returns (url, page_start, page_end) where:
      page_start=None, page_end=None  → plain URL, no pagination
      page_start=N,    page_end=None  → all pages from N until empty
      page_start=N,    page_end=M     → pages N through M (inclusive, supports reverse)

    Returns None if the line is invalid.
    """
    parts = [p.strip() for p in raw.split("|")]
    url   = parts[0].strip()

    if not url.startswith("http"):
        return None

    if len(parts) == 1:
        return url, None, None

    directive = parts[1].strip().lower()

    if directive == "all":
        return url, 1, None

    try:
        p_start = int(parts[1].strip())
        p_end   = int(parts[2].strip()) if len(parts) > 2 else p_start
        return url, p_start, p_end
    except (ValueError, IndexError):
        logger.error("DLOAD",
            f"Could not parse: {raw!r} — expected 'url | all' or 'url | N | M'")
        return None


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

def _make_client(cookies: dict | None = None):
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
        limits=limits, headers=headers, cookies=cookies or {},
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
    client, url: str, directory: Path, default_name: str,
    history: dict[str, str], skip_downloaded: bool,
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
    client, file_urls: list[str], directory: Path,
    default_name_fn, history: dict[str, str], skip_downloaded: bool,
) -> tuple[int, int]:
    """Download all URLs concurrently. Returns (succeeded, failed)."""
    if not file_urls:
        return 0, 0

    logger.info("DLOAD", f"  Downloading {len(file_urls)} file(s)...")
    tasks = [
        _download_file(client, url, directory, default_name_fn(url),
                       history, skip_downloaded)
        for url in file_urls
    ]
    succeeded = failed = 0
    for coro in asyncio.as_completed(tasks):
        url, err = await coro
        if err:
            failed += 1
            logger.error("DLOAD", f"  Failed: {url} — {err}")
        else:
            succeeded += 1
    return succeeded, failed


async def _download_pages(
    client, base_url: str, directory: Path,
    history: dict, skip: bool,
    page_start: int, page_end: int | None,
    get_page_urls_fn, default_name_fn,
) -> tuple[int, int]:
    """Download a range of listing pages.

    page_end=None → keep going until a page returns no results.
    Supports reverse order when page_start > page_end.
    """
    total_ok = total_fail = 0
    clean_url = _strip_page(base_url)

    if page_end is None:
        page_iter = (p for p in range(page_start, 10 ** 9))
    else:
        step      = 1 if page_end >= page_start else -1
        page_iter = iter(range(page_start, page_end + step, step))

    for page in page_iter:
        logger.info("DLOAD", f"  Page {page}...")
        file_urls = await get_page_urls_fn(client, clean_url, page)
        if not file_urls:
            logger.info("DLOAD", f"  Page {page} is empty — stopping.")
            break
        ok, fail = await _download_files(
            client, file_urls, directory, default_name_fn, history, skip)
        total_ok   += ok
        total_fail += fail

    return total_ok, total_fail


# ---------------------------------------------------------------------------
# Bepis scraping
# ---------------------------------------------------------------------------

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


async def _get_bepis_page_urls(client, base_url: str, page: int) -> list[str]:
    query_string = urlparse(base_url).query
    card_type = _get_bepis_card_type(base_url)
    api_url = f"{BEPIS_API_URL}cardType={card_type}&{query_string}"
    r = await client.get(_set_page(api_url, page))
    r.raise_for_status()
    data = r.json().get("data")
    cards = data.get("cards", []) if data else []
    return [
        f"{BEPIS_URL}/card/full/{card_type}_{_get_bepis_file_card_id(card["id"])}.png"
        for card in cards
    ]


async def _download_bepis(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None = None, page_end: int | None = None,
) -> tuple[int, int]:
    if "/view/" in url:
        # Single card page — pagination args ignored
        logger.info("DLOAD", f"Scraping card page: {url}")
        file_url = await _get_bepis_file_url(url)
        if not file_url:
            logger.error("DLOAD", "Could not find download link on page.")
            return 0, 1
        _, err = await _download_file(
            client, file_url, directory, _bepis_default_name(file_url), history, skip)
        return (1, 0) if not err else (0, 1)

    elif page_start is not None:
        logger.info("DLOAD",
            f"Scraping pages {page_start}–{'end' if page_end is None else page_end}: {url}")
        return await _download_pages(
            client, url, directory, history, skip,
            page_start, page_end,
            _get_bepis_page_urls, _bepis_default_name)

    else:
        logger.info("DLOAD", f"Scraping card list: {url}")
        file_urls = await _get_bepis_page_urls(client, url, 1)
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


async def _get_koikatsu_page_urls(client, base_url: str, page: int) -> list[str]:
    from bs4 import BeautifulSoup
    r = await client.get(_set_page(base_url, page))
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    sub_pages = [
        urljoin(KOIKATSU_URL, tag["href"])
        for tag in soup.select("a.card")
        if tag.get("href")
    ]
    if not sub_pages:
        return []
    logger.info("DLOAD", f"  Found {len(sub_pages)} card(s), scraping download links...")
    results = await asyncio.gather(
        *[_get_koikatsu_file_url(client, p) for p in sub_pages],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, str) and r]


async def _download_koikatsu(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None = None, page_end: int | None = None,
) -> tuple[int, int]:
    if "/contents/" in url:
        # Single card page
        logger.info("DLOAD", f"Scraping card page: {url}")
        file_url = await _get_koikatsu_file_url(client, url)
        if not file_url:
            logger.error("DLOAD", "Could not find download link on page.")
            return 0, 1
        _, err = await _download_file(
            client, file_url, directory, _koikatsu_default_name(file_url), history, skip)
        return (1, 0) if not err else (0, 1)

    elif page_start is not None:
        logger.info("DLOAD",
            f"Scraping pages {page_start}–{'end' if page_end is None else page_end}: {url}")
        return await _download_pages(
            client, url, directory, history, skip,
            page_start, page_end,
            _get_koikatsu_page_urls, _koikatsu_default_name)

    else:
        logger.info("DLOAD", f"Scraping card list: {url}")
        file_urls = await _get_koikatsu_page_urls(client, url, 1)
        return await _download_files(
            client, file_urls, directory, _koikatsu_default_name, history, skip)


# ---------------------------------------------------------------------------
# Main task class
# ---------------------------------------------------------------------------

class DownloadContents(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.download_contents
        self.links           : str  = cfg.get("Links", "")
        self.output_dir_str  : str  = cfg.get("OutputDir", "")
        self.skip_downloaded : bool = cfg.get("SkipDownloaded", True)

    def run(self) -> None:
        # Parse input lines
        urls: list[tuple[str, int | None, int | None]] = []
        for raw_line in self.links.splitlines():
            raw_line = raw_line.strip()
            if not raw_line or raw_line.startswith("#"):
                continue
            parsed = _parse_line(raw_line)
            if parsed:
                urls.append(parsed)

        if not urls:
            logger.error("DLOAD",
                "No valid URLs found. Enter one URL per line in the Download Links field.")
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

        # Resolve kkd_session cookie — prompts if missing or expired
        has_kk_urls = any(KOIKATSU_URL in url_str for url_str, *_ in urls)
        kkd_session = None
        if has_kk_urls:
            import utils.kkd_session as _kkd
            kkd_session = _kkd.get_or_prompt()
            if not kkd_session:
                logger.warning("DLOAD",
                    "No kkd_session — koikatsucards.com downloads will be skipped.")

        history = _load_history()
        total_ok = 0
        total_fail = 0

        kkd_cookies = {"kkd_session": kkd_session} if kkd_session else None

        async def _run_all() -> None:
            nonlocal total_ok, total_fail
            async with _make_client() as bepis_client, \
                       _make_client(cookies=kkd_cookies) as kk_client:
                for url_str, page_start, page_end in urls:
                    if BEPIS_URL in url_str:
                        ok, fail = await _download_bepis(
                            bepis_client, url_str, output_dir, history, self.skip_downloaded,
                            page_start=page_start, page_end=page_end
                        )
                    elif KOIKATSU_URL in url_str:
                        if not kkd_session:
                            logger.skipped("DLOAD",
                                f"Skipping koikatsucards.com URL (no session): {url_str}")
                            total_fail += 1
                            continue
                        ok, fail = await _download_koikatsu(
                            kk_client, url_str, output_dir, history, self.skip_downloaded,
                            page_start=page_start, page_end=page_end
                        )
                    else:
                        logger.error("DLOAD",
                            f"Unsupported URL (must be db.bepis.moe or koikatsucards.com): {url_str}")
                        fail = 1
                        ok   = 0
                    total_ok   += ok
                    total_fail += fail

        asyncio.run(_run_all())
        _save_history(history)

        self.log_done("DLOAD", moved=total_ok, skipped=total_fail,
                      extra=f"{total_ok + total_fail} total")