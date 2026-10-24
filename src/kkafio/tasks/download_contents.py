"""
download_contents.py — Download Koikatsu character cards from web sources.

Supports:
  https://db.bepis.moe          — card pages (/view/) and listing pages
  https://koikatsucards.com     — card pages (/contents/) and listing pages
  https://www.pixiv.net         — artworks (/artworks/ID), galleries
                                  (/users/ID, /users/ID/illustrations) and
                                  searches (/search?q=...); only
                                  images that look like KK cards/scenes are kept
  https://gofile.io             — folders (/d/ID); every file is downloaded,
                                  subfolders into subdirectories; a password can
                                  be given as ?password=...
  https://drive.google.com      — public files (/file/d/ID, open?id=ID, uc?id=ID) and
                                  folders (/drive/folders/ID; subfolders go into
                                  subdirectories; Google Docs/Sheets are skipped).
                                  Must be shared as "anyone with the link"
  https://mega.nz               — files (/file/ID#KEY) and folders (/folder/ID#KEY);
                                  the #KEY part is required (it decrypts the
                                  names and the data); subfolders go into
                                  subdirectories

Input line formats (| is the separator — safe because URLs never contain |):
  https://...                   plain URL — single card page or one listing page
  https://... | all             listing: download all pages until empty
  https://... | 5               listing: just page 5 (replaces any ?page= in the link)
  https://... | 3 | 7           listing: pages 3 through 7 inclusive
  https://... | 7 | 3           listing: pages 7 down to 3 (reverse order)
  # comment                     lines starting with # are ignored

Both card sites use ?page=N for pagination. Pixiv galleries are paged like the
site (48 works per page, ?p=N): "url | 2", "url | 1 | 3" and "url | all" work,
as does a link that already ends in ?p=2; with neither, the whole gallery is read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse, urljoin

from kkafio.tasks.base_task import BaseTask
from kkafio.core.logger import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BEPIS_URL     = "https://db.bepis.moe"
BEPIS_API_URL = "https://db.bepis.moe/api/frontend/search?"
KOIKATSU_URL  = "https://koikatsucards.com"
PIXIV_URL     = "https://www.pixiv.net"
PIXIV_COOKIE_DOMAIN = ".pixiv.net"
MAX_CONNECTIONS = 10
KK_SCRAPE_CONCURRENCY = 5      # simultaneous koikatsucards card-page fetches
RETRY_ATTEMPTS = 3             # total tries for a transient network/HTTP error
MAX_CONSECUTIVE_BAD_PAGES = 3  # stop paging after this many failed pages in a row
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Use the same per-platform config directory as everything else (config.json,
# telegram.json) instead of a separate, Windows-only APPDATA
# lookup — the old fallback (`Path.home() / "AppData" / "Roaming"`) doesn't
# make sense on Linux/macOS at all, so history silently ended up somewhere
# that isn't even where the rest of KKAFIO's own data lives.
from kkafio.core.paths import CONFIG_DIR

HISTORY_FILE = CONFIG_DIR / "download_history.json"

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _set_page(url: str, page: int) -> str:
    """Return url with ?page=<page> set or replaced.

    The query is handled as a list of (key, value) pairs so repeated
    parameters (e.g. several ``tag=`` filters) are all preserved.
    """
    parts = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    pairs.append(("page", str(page)))
    return urlunparse(parts._replace(query=urlencode(pairs)))


def _strip_page(url: str) -> str:
    """Return url with ?page removed (repeated parameters are preserved)."""
    parts = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    return urlunparse(parts._replace(query=urlencode(pairs)))


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

def _is_transient(exc: Exception) -> bool:
    """True for errors worth retrying: network/timeout errors and 408/425/429/5xx."""
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return isinstance(exc, httpx.TransportError)


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Seconds to wait before retry `attempt` (honours Retry-After, capped)."""
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("retry-after", "").strip()
        if retry_after.isdigit():
            return float(min(int(retry_after), 60))
    return float(min(2 ** attempt, 30))


async def _get_with_retry(client, url: str, headers: dict | None = None):
    """GET `url`, raise_for_status, retrying transient failures with backoff."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = await (client.get(url, headers=headers) if headers else client.get(url))
            r.raise_for_status()
            return r
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS and _is_transient(exc):
                delay = _retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {exc} — retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                continue
            raise


# ---------------------------------------------------------------------------
# Line parser
# ---------------------------------------------------------------------------

def _parse_line(raw: str) -> tuple[str, int | None, int | None] | None:
    """Parse one input line.

    Returns (url, page_start, page_end) where:
      page_start=None, page_end=None  → plain URL, no pagination
      page_start=N,    page_end=None  → all pages from N until empty
      page_start=N,    page_end=N     → only page N ("url | N"); any ?page= already
                                        in the url is replaced by N
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
            f"Could not parse: {raw!r} — expected 'url | all', 'url | N' or 'url | N | M'")
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
    """Write the history atomically (temp file + rename) so a crash or Stop
    in the middle of a save can't leave a truncated/corrupt history file.

    Written compactly (no indent) rather than indent=2: this is now
    checkpointed after every single URL/page (see the callers below), and
    it only grows over time as more downloads happen, so keeping it small
    and skipping the pretty-printing overhead actually matters here.
    """
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_name(HISTORY_FILE.name + ".tmp")
    tmp.write_text(
        json.dumps(history, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(tmp, HISTORY_FILE)


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------

def _make_client(cookies: dict | None = None, cookie_domain: str | None = None,
                 extra_headers: dict | None = None):
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
    if extra_headers:
        headers.update(extra_headers)
    # A plain dict of cookies is attached to EVERY request this client makes,
    # regardless of host. koikatsucards.com's download-menu links can be
    # absolute URLs to a different host (a CDN, say), and this client also
    # follows redirects, so without domain scoping the kkd_session cookie
    # would be sent to whatever host a card's download link happens to
    # point to. httpx.Cookies with an explicit domain is scoped by
    # cookiejar matching and is only ever attached to requests to that host.
    cookie_jar: httpx.Cookies | dict = {}
    if cookies:
        cookie_jar = httpx.Cookies()
        for name, value in cookies.items():
            cookie_jar.set(name, value, domain=cookie_domain or "")

    return httpx.AsyncClient(
        limits=limits, headers=headers, cookies=cookie_jar,
        follow_redirects=True, http2=True,
        timeout=httpx.Timeout(30.0, pool=None),
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
    path_part = Path(unquote(urlparse(url).path)).name
    if path_part and path_part not in (".", "/"):
        return path_part
    return default_name


# ---------------------------------------------------------------------------
# Core file downloader
# ---------------------------------------------------------------------------

async def _download_file(
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
                filename = _resolve_filename(response, url, default_name)
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
            if attempt < RETRY_ATTEMPTS and _is_transient(exc):
                delay = _retry_delay(exc, attempt)
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


async def _download_files(
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
            return await _download_file(client, url, directory, default_name_fn(url),
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


async def _download_pages(
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
    clean_url = _strip_page(base_url)
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

        ok, skipped, fail = await _download_files(
            client, new_urls, directory, default_name_fn, history, skip)
        total_ok   += ok
        total_skip += skipped
        total_fail += fail
        _save_history(history)   # checkpoint after every page

    return total_ok, total_skip, total_fail


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


async def _get_bepis_page_urls(client, base_url: str, page: int) -> tuple[list[str], int, bool]:
    """Returns (file_urls, card_failures, listing_empty)."""
    query_string = urlparse(base_url).query
    card_type = _get_bepis_card_type(base_url)
    api_url = f"{BEPIS_API_URL}cardType={card_type}&{query_string}"
    r = await _get_with_retry(client, _set_page(api_url, page))
    data = r.json().get("data")
    cards = data.get("cards", []) if data else []
    urls = [
        f"{BEPIS_URL}/card/full/{card_type}_{_get_bepis_file_card_id(card["id"])}.png"
        for card in cards
    ]
    return urls, 0, not cards


async def _download_bepis(
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
        _, err, was_skipped = await _download_file(
            client, file_url, directory, _bepis_default_name(file_url), history, skip)
        if err:
            logger.error("DLOAD", f"  Failed: {file_url} — {err}")
            return 0, 0, 1
        return (0, 1, 0) if was_skipped else (1, 0, 0)

    elif page_start is not None:
        logger.info("DLOAD",
            f"Scraping page {page_start}: {url}" if page_end == page_start else
            f"Scraping pages {page_start}–{'end' if page_end is None else page_end}: {url}")
        return await _download_pages(
            client, url, directory, history, skip,
            page_start, page_end,
            _get_bepis_page_urls, _bepis_default_name)

    else:
        logger.info("DLOAD", f"Scraping card list: {url}")
        file_urls, card_fail, _empty = await _get_bepis_page_urls(client, url, 1)
        ok, skipped, fail = await _download_files(
            client, file_urls, directory, _bepis_default_name, history, skip)
        return ok, skipped, fail + card_fail


# ---------------------------------------------------------------------------
# KoikatsuCards scraping
# ---------------------------------------------------------------------------

def _koikatsu_default_name(url: str) -> str:
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return name + ".png"


async def _get_koikatsu_file_url(client, url: str) -> str:
    from bs4 import BeautifulSoup
    r = await _get_with_retry(client, url)
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
    r = await _get_with_retry(client, _set_page(base_url, page))
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


async def _download_koikatsu(
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
        _, err, was_skipped = await _download_file(
            client, file_url, directory, _koikatsu_default_name(file_url), history, skip)
        if err:
            logger.error("DLOAD", f"  Failed: {file_url} — {err}")
            return 0, 0, 1
        return (0, 1, 0) if was_skipped else (1, 0, 0)

    elif page_start is not None:
        logger.info("DLOAD",
            f"Scraping page {page_start}: {url}" if page_end == page_start else
            f"Scraping pages {page_start}–{'end' if page_end is None else page_end}: {url}")
        return await _download_pages(
            client, url, directory, history, skip,
            page_start, page_end,
            _get_koikatsu_page_urls, _koikatsu_default_name)

    else:
        logger.info("DLOAD", f"Scraping card list: {url}")
        file_urls, card_fail, _empty = await _get_koikatsu_page_urls(client, url, 1)
        ok, skipped, fail = await _download_files(
            client, file_urls, directory, _koikatsu_default_name, history, skip)
        return ok, skipped, fail + card_fail


# ---------------------------------------------------------------------------
# Pixiv scraping
# ---------------------------------------------------------------------------
#
# Same approach as megaloader's Pixiv plugin: the site's own /ajax JSON API,
# authenticated with the PHPSESSID cookie, and the i.pximg.net originals
# fetched with a pixiv Referer. The /pages endpoint reports the width/height
# of every image, so the size filter runs before anything is downloaded.

PIXIV_META_CONCURRENCY = 4     # simultaneous /ajax requests (pixiv rate-limits)
PIXIV_PAGE_SIZE        = 48    # works per page on pixiv's gallery tabs (?p=N)
PIXIV_CHUNK_SIZE       = PIXIV_PAGE_SIZE   # artworks resolved, then downloaded, per round

# KK scene thumbnails are exactly 320x180; character/coordinate cards are
# 252x352 — but a re-saved card may be scaled, so any image with that aspect
# ratio (within a small tolerance for rounding) is accepted.
PIXIV_SCENE_SIZE = (320, 180)
PIXIV_CARD_SIZE  = (252, 352)
PIXIV_RATIO_TOLERANCE = 0.01   # relative

_LANG_PREFIX = re.compile(r"^[a-z]{2}(?:[_-][a-z]{2,4})?$", re.IGNORECASE)


def _is_pixiv_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ("www.pixiv.net", "pixiv.net")


def _parse_pixiv_url(url: str) -> tuple[str, str, str] | None:
    """Classify a pixiv link.

    Returns ("artwork", artwork_id, "") or ("user", user_id, scope) where scope
    is "artworks" (illustrations + manga), "illustrations" or "manga", or
    ("search", keyword, scope) for a search / tag page.
    A bare /users/ID means /users/ID/artworks. Returns None for anything else.
    """
    parts = urlparse(url)
    segs = [s for s in parts.path.split("/") if s]
    if segs and _LANG_PREFIX.match(segs[0]) and segs[0].lower() not in ("users", "artworks"):
        segs = segs[1:]                      # drop the /en/ language prefix

    if len(segs) >= 2 and segs[0] == "artworks" and segs[1].isdigit():
        return "artwork", segs[1], ""
    if len(segs) >= 2 and segs[0] == "users" and segs[1].isdigit():
        scope = segs[2] if len(segs) >= 3 else "artworks"
        if scope in ("artworks", "illustrations", "manga"):
            return "user", segs[1], scope
        return None                          # /bookmarks, /following, ...

    query = dict(parse_qsl(parts.query))

    # Search: /search?q=KEYWORD&type=artwork|illust|manga&...
    if segs == ["search"] and query.get("q"):
        scope = {"artwork": "artworks", "illust": "illustrations", "manga": "manga"}.get(
            query.get("type", "artwork"))
        return ("search", query["q"], scope) if scope else None
    # Tag page: /tags/KEYWORD[/artworks|illustrations|manga]
    if len(segs) >= 2 and segs[0] == "tags":
        scope = segs[2] if len(segs) >= 3 else "artworks"
        if scope in ("artworks", "illustrations", "manga"):
            return "search", unquote(segs[1]), scope
        return None

    # Legacy links
    if segs and segs[0] == "member.php" and query.get("id", "").isdigit():
        return "user", query["id"], "artworks"
    if segs and segs[0] == "member_illust.php" and query.get("illust_id", "").isdigit():
        return "artwork", query["illust_id"], ""
    return None


def _pixiv_url_page(url: str) -> int | None:
    """The ?p=N page number of a pixiv gallery link, or None."""
    value = dict(parse_qsl(urlparse(url).query)).get("p", "")
    return int(value) if value.isdigit() and int(value) >= 1 else None


def _pixiv_select_page_ids(ids: list[str], page_start: int, page_end: int | None) -> list[str]:
    """Pick the works shown on pages page_start..page_end (inclusive, either
    direction; None = through the last page) of a gallery whose works are
    listed newest first, PIXIV_PAGE_SIZE per page like pixiv's own ?p=N."""
    pages = max(1, -(-len(ids) // PIXIV_PAGE_SIZE))
    last = pages if page_end is None else page_end
    step = 1 if last >= page_start else -1
    selected: list[str] = []
    for page in range(page_start, last + step, step):
        if page < 1 or page > pages:
            logger.warning("DLOAD",
                f"  Page {page} is outside this gallery ({pages} page(s)) — skipped.")
            continue
        selected += ids[(page - 1) * PIXIV_PAGE_SIZE: page * PIXIV_PAGE_SIZE]
    return selected


def _normalize_pixiv_url(url: str) -> str | None:
    """Canonical form of a pixiv link: users/ID → users/ID/artworks."""
    parsed = _parse_pixiv_url(url)
    if parsed is None:
        return None
    kind, ident, scope = parsed
    if kind == "artwork":
        return f"{PIXIV_URL}/en/artworks/{ident}"
    if kind == "search":
        return url
    return f"{PIXIV_URL}/en/users/{ident}/{scope}"


def _pixiv_size_matches(width, height) -> bool:
    """True for exactly 320x180, or any image with the 252x352 aspect ratio."""
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0:
        return False
    if (w, h) == PIXIV_SCENE_SIZE:
        return True
    target = PIXIV_CARD_SIZE[0] / PIXIV_CARD_SIZE[1]
    return abs(w / h - target) <= target * PIXIV_RATIO_TOLERANCE


def _pixiv_default_name(url: str) -> str:
    return Path(urlparse(url).path).name


async def _pixiv_api(client, endpoint: str, params: dict | None = None):
    """Call pixiv's /ajax JSON API and return its "body"."""
    url = f"{PIXIV_URL}/ajax{endpoint}"
    if params:
        url += "?" + urlencode(params)
    r = await _get_with_retry(client, url)
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data.get("message") or "Pixiv API returned an error")
    return data.get("body")


async def _pixiv_artwork_images(client, artwork_id: str) -> tuple[list[str], int]:
    """Return (original image URLs that pass the size filter, number rejected)."""
    pages = await _pixiv_api(client, f"/illust/{artwork_id}/pages")
    urls: list[str] = []
    rejected = 0
    for page in pages or []:
        original = (page.get("urls") or {}).get("original")
        if not original or not isinstance(original, str):
            continue
        if _pixiv_size_matches(page.get("width"), page.get("height")):
            urls.append(original)
        else:
            rejected += 1
    return urls, rejected


async def _pixiv_user_artwork_ids(client, user_id: str, scope: str) -> list[str]:
    """All artwork IDs of a user, newest first."""
    body = await _pixiv_api(client, f"/user/{user_id}/profile/all") or {}

    def _ids(section) -> list[str]:
        # pixiv sends [] (not {}) when a section is empty
        return list(section.keys()) if isinstance(section, dict) else []

    ids: list[str] = []
    if scope in ("artworks", "illustrations"):
        ids += _ids(body.get("illusts"))
    if scope in ("artworks", "manga"):
        ids += _ids(body.get("manga"))
    return sorted(set(ids), key=int, reverse=True)


# Web search params -> /ajax/search path + "type" value.
_PIXIV_SEARCH_ENDPOINT = {
    "artworks":      ("artworks",      "all"),
    "illustrations": ("illustrations", "illust"),
    "manga":         ("manga",         "manga"),
}
# Params the request builder sets itself; everything else in the link (the
# filters: s_mode, mode, order, ai_type, scd/ecd, wlt/wgt, tool, ...) is kept.
_PIXIV_SEARCH_OWN_PARAMS = {"q", "word", "p", "page", "type", "lang"}


def _pixiv_search_request(url: str, page: int) -> tuple[str, list[tuple[str, str]]]:
    """Build the /ajax/search endpoint + params for a search link and page,
    carrying over every filter query parameter of the link unchanged."""
    kind, keyword, scope = _parse_pixiv_url(url)
    path, type_ = _PIXIV_SEARCH_ENDPOINT[scope]
    pairs = [(k, v) for k, v in parse_qsl(urlparse(url).query, keep_blank_values=True)
             if k not in _PIXIV_SEARCH_OWN_PARAMS]
    present = {k for k, _ in pairs}
    for key, default in (("order", "date_d"), ("mode", "all"), ("s_mode", "s_tag")):
        if key not in present:
            pairs.append((key, default))
    pairs += [("word", keyword), ("type", type_), ("p", str(page)), ("lang", "en")]
    return f"/search/{path}/{quote(keyword, safe='')}", pairs


def _pixiv_search_items(body) -> list[dict]:
    """The work list of a search response (its key depends on the endpoint)."""
    if isinstance(body, dict):
        for key in ("illustManga", "illust", "manga", "artworks"):
            section = body.get(key)
            if isinstance(section, dict) and isinstance(section.get("data"), list):
                return [w for w in section["data"]
                        if isinstance(w, dict) and str(w.get("id", "")).isdigit()]
    return []


async def _get_pixiv_search_page_urls(client, url: str, page: int) -> tuple[list[str], int, bool]:
    """Returns (file_urls, card_failures, listing_empty) for one search page.

    The search results already carry the first image's size, so a single-page
    work that fails the filter is dropped without another request; every other
    work is checked through /pages like a gallery work.
    """
    endpoint, params = _pixiv_search_request(url, page)
    body = await _pixiv_api(client, endpoint, params)
    works = _pixiv_search_items(body)
    if not works:
        return [], 0, True

    to_check: list[str] = []
    for w in works:
        single = w.get("pageCount") == 1
        if single and not _pixiv_size_matches(w.get("width"), w.get("height")):
            continue
        to_check.append(str(w["id"]))
    logger.info("DLOAD",
        f"  {len(works)} work(s) on page {page}, {len(to_check)} to check "
        f"({len(works) - len(to_check)} dropped by size).")

    sem = asyncio.Semaphore(PIXIV_META_CONCURRENCY)

    async def _resolve(artwork_id: str):
        async with sem:
            return await _pixiv_artwork_images(client, artwork_id)

    results = await asyncio.gather(*[_resolve(a) for a in to_check], return_exceptions=True)
    urls: list[str] = []
    failed = 0
    for artwork_id, res in zip(to_check, results, strict=True):
        if isinstance(res, BaseException):
            if not isinstance(res, Exception):
                raise res                          # Cancelled / KeyboardInterrupt
            failed += 1
            logger.error("DLOAD", f"  Could not read artwork {artwork_id}: {res}")
        else:
            urls += res[0]
    return urls, failed, False


async def _download_pixiv_search(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None, page_end: int | None,
) -> tuple[int, int, int]:
    """Search results are paged (60 per page) like the other listing sources:
    ``| N`` / ``| N | M`` / ``| all`` pick the pages; without a directive the
    link's own ``?p=N`` (else page 1) is read — a search can be huge, so
    "everything" has to be asked for with ``| all``."""
    if page_start is None:
        page_start = page_end = _pixiv_url_page(url) or 1
    logger.info("DLOAD",
        f"Scraping pixiv search (page {page_start}): {url}" if page_end == page_start else
        f"Scraping pixiv search (pages {page_start}–{'end' if page_end is None else page_end}): {url}")
    return await _download_pages(
        client, url, directory, history, skip, page_start, page_end,
        _get_pixiv_search_page_urls, _pixiv_default_name)


async def _download_pixiv(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None = None, page_end: int | None = None,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed).

    For a user gallery the page directive (``| N``, ``| N | M``, ``| all``) or,
    without one, the link's own ``?p=N`` picks which pages are read; with
    neither the whole gallery is read. Artwork links ignore it.
    """
    parsed = _parse_pixiv_url(url)
    if parsed is None:
        logger.error("DLOAD",
            f"Unsupported pixiv link (use /artworks/ID, /users/ID or a /search link): {url}")
        return 0, 0, 1
    kind, ident, scope = parsed

    if kind == "search":
        return await _download_pixiv_search(
            client, url, directory, history, skip, page_start, page_end)

    if kind == "artwork":
        logger.info("DLOAD", f"Scraping pixiv artwork: {ident}")
        artwork_ids = [ident]
    else:
        # Print the normalised link so users/ID visibly became users/ID/artworks.
        logger.info("DLOAD", f"Scraping pixiv gallery: {_normalize_pixiv_url(url)}")
        artwork_ids = await _pixiv_user_artwork_ids(client, ident, scope)
        logger.info("DLOAD", f"  Found {len(artwork_ids)} artwork(s).")
        if page_start is None:
            link_page = _pixiv_url_page(url)       # ...?p=2 means just page 2
            if link_page is not None:
                page_start = page_end = link_page
        if page_start is not None:
            artwork_ids = _pixiv_select_page_ids(artwork_ids, page_start, page_end)
            logger.info("DLOAD",
                f"  Page {page_start}" if page_end == page_start else
                f"  Pages {page_start}–{'end' if page_end is None else page_end}"
                f": {len(artwork_ids)} artwork(s) selected.")

    sem = asyncio.Semaphore(PIXIV_META_CONCURRENCY)

    async def _resolve(artwork_id: str):
        async with sem:
            return await _pixiv_artwork_images(client, artwork_id)

    total_ok = total_skip = total_fail = 0
    kept = filtered = 0
    seen: set[str] = set()

    for i in range(0, len(artwork_ids), PIXIV_CHUNK_SIZE):
        chunk = artwork_ids[i:i + PIXIV_CHUNK_SIZE]
        results = await asyncio.gather(*[_resolve(a) for a in chunk], return_exceptions=True)

        file_urls: list[str] = []
        for artwork_id, res in zip(chunk, results, strict=True):
            if isinstance(res, BaseException):
                if not isinstance(res, Exception):
                    raise res                      # Cancelled / KeyboardInterrupt
                total_fail += 1
                logger.error("DLOAD", f"  Could not read artwork {artwork_id}: {res}")
                continue
            urls, rejected = res
            filtered += rejected
            kept += len(urls)
            file_urls += [u for u in urls if u not in seen]
            seen.update(urls)

        if len(artwork_ids) > PIXIV_CHUNK_SIZE:
            logger.info("DLOAD",
                f"  Checked {min(i + PIXIV_CHUNK_SIZE, len(artwork_ids))}"
                f"/{len(artwork_ids)} artwork(s)...")

        ok, skipped, fail = await _download_files(
            client, file_urls, directory, _pixiv_default_name, history, skip)
        total_ok   += ok
        total_skip += skipped
        total_fail += fail
        _save_history(history)       # checkpoint after every chunk

    logger.info("DLOAD",
        f"  {kept} image(s) matched 320x180 / 252x352-ratio, "
        f"{filtered} filtered out by size.")
    return total_ok, total_skip, total_fail


# ---------------------------------------------------------------------------
# Gofile
# ---------------------------------------------------------------------------
#
# A guest account token from POST /accounts, then GET /contents/<id> signed
# with a "website token": a sha256 over the user agent, language, account
# token, a 4-hour time bucket and a secret ("salt"). Gofile rotates that secret
# every few months, so it is NOT pinned: like HeapLeach, it is read out of
# gofile's own script (/js/wt.obf.js), where it is hidden in an RC4-encrypted,
# shuffled-base64 string table (see _gofile_secret_from). If that fails, set
# the GOFILE_SALT environment variable to the current secret. GOFILE_TOKEN may hold the token of a real account
# instead of using a throw-away guest account.
#
# Signing badly is worse than not signing: gofile blocks an address that keeps
# doing it. So after an "error-notPremium" rejection nothing signed is sent
# for GOFILE_REJECTION_COOLDOWN seconds, and the secret is read again after.

GOFILE_API         = "https://api.gofile.io"
GOFILE_DOMAIN      = ".gofile.io"
GOFILE_SCRIPT_URL  = "https://gofile.io/js/wt.obf.js"
GOFILE_SECRET_TTL      = 3600          # reuse a recovered secret this long (s)
GOFILE_REJECTION_COOLDOWN = 900        # stop signing this long after a rejection (s)
GOFILE_TIME_BUCKET = 14400            # seconds
GOFILE_LANG        = "en-US"
# The website token hashes this exact user agent, so the client must send it.
GOFILE_USER_AGENT  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
GOFILE_PAGE_SIZE   = 1000
GOFILE_MAX_DEPTH   = 8                # nested folder levels followed


def _is_gofile_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ("gofile.io", "www.gofile.io")


def _parse_gofile_url(url: str) -> tuple[str, str | None] | None:
    """(content_id, password) of a gofile.io/d/ID or /f/ID link, else None.

    A folder password can be given as ``?password=...`` or ``#password=...``.
    """
    parts = urlparse(url)
    m = re.match(r"^/(?:d|f)/([\w-]+)/?$", parts.path)
    if not m:
        return None
    opts = dict(parse_qsl(parts.query)) | dict(parse_qsl(parts.fragment))
    return m.group(1), opts.get("password") or None


# --- Recovering the secret from gofile's script (port of HeapLeach's gofilewt.go)

_GOFILE_ALPHABET_RE   = re.compile(r"'([A-Za-z0-9+/]{64}=)'")
_GOFILE_TABLE_RE      = re.compile(r"\[((?:'[A-Za-z0-9+/=]*'\s*,\s*){8,}'[A-Za-z0-9+/=]*')\]")
_GOFILE_TABLE_ITEM_RE = re.compile(r"'([A-Za-z0-9+/=]*)'")
_GOFILE_CALL_RE       = re.compile(r"\(\s*0x([0-9a-fA-F]+)\s*,\s*'([^']{1,32})'\s*\)")
_GOFILE_HEX_ESCAPE_RE = re.compile(r"\\x([0-9a-fA-F]{2})")
_GOFILE_SECRET_RE     = re.compile(r"^[0-9a-f]{10,40}$")


def _gofile_rc4(text: str, key: str) -> str:
    """RC4 over characters (code points), as the script does it."""
    if not key:
        return ""
    k = [ord(c) for c in key]
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + k[i % len(k)]) % 256
        state[i], state[j] = state[j], state[i]
    out = []
    i = j = 0
    for ch in text:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        out.append(chr(ord(ch) ^ state[(state[i] + state[j]) % 256]))
    return "".join(out)


def _gofile_base64(encoded: str, alphabet: str) -> bytes:
    """Base64 under the script's shuffled alphabet, six bits at a time."""
    out = bytearray()
    acc = bits = 0
    for ch in encoded:
        value = alphabet.find(ch)
        if value < 0 or value >= 64:
            continue                      # padding / foreign character
        acc = (acc << 6 | value) & 0xFFFFFF
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    if not out:
        raise ValueError("entry decoded to nothing")
    return bytes(out)


def _gofile_decode_entry(encoded: str, key: str, alphabet: str) -> str:
    # Decoded to text first, then enciphered over characters — same order as
    # the script, so multi-byte characters don't turn into noise.
    return _gofile_rc4(_gofile_base64(encoded, alphabet).decode("utf-8"), key)


def _gofile_printable(text: str) -> bool:
    return bool(text) and all(0x20 <= ord(c) <= 0x7e for c in text)


def _gofile_secret_from(script: str) -> str:
    """Extract the signing secret from wt.obf.js, or raise ValueError.

    Refuses to guess: the secret is returned only if the whole script decodes
    under one table rotation and exactly one decoded string looks like a secret.
    """
    script = _GOFILE_HEX_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), script)

    m = _GOFILE_ALPHABET_RE.search(script)
    if not m:
        raise ValueError("no base64 alphabet in the signing script")
    alphabet = m.group(1)

    table: list[str] = []
    for tm in _GOFILE_TABLE_RE.finditer(script):
        items = _GOFILE_TABLE_ITEM_RE.findall(tm.group(1))
        if len(items) > len(table):
            table = items                 # the longest table is the real one
    if not table:
        raise ValueError("no string table in the signing script")

    calls: list[tuple[int, str]] = []
    for cm in _GOFILE_CALL_RE.finditer(script):
        call = (int(cm.group(1), 16), cm.group(2))
        if call not in calls:
            calls.append(call)
    if not calls:
        raise ValueError("no decodable references in the signing script")

    # The table is rotated by the script's startup loop; try every rotation and
    # keep the one where every reference decodes to readable text.
    decoded: list[str] | None = None
    for offset in range(len(table)):
        attempt: list[str] = []
        for index, key in calls:
            try:
                text = _gofile_decode_entry(table[(index + offset) % len(table)], key, alphabet)
            except (ValueError, UnicodeDecodeError):
                break
            if not _gofile_printable(text):
                break
            attempt.append(text)
        else:
            decoded = attempt
            break
    if decoded is None:
        raise ValueError("the signing script did not decode; its obfuscation has changed")

    candidates = list(dict.fromkeys(
        t for t in decoded
        if _GOFILE_SECRET_RE.match(t) and len(set(t)) > 1))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("the signing script holds nothing shaped like a secret")
    raise ValueError(f"the signing script holds {len(candidates)} strings shaped like a secret")


_gofile_secret_cache: dict = {"secret": "", "until": 0.0, "rejected_at": 0.0,
                              "error": "", "error_until": 0.0}


async def _gofile_ensure_secret(client) -> str:
    """The secret to sign with: the GOFILE_SALT env override, else the one read
    from gofile's script (cached). Raises RuntimeError if neither is available."""
    cache = _gofile_secret_cache
    now = time.time()
    since = now - cache["rejected_at"]
    if cache["rejected_at"] and since < GOFILE_REJECTION_COOLDOWN:
        raise RuntimeError(
            "Gofile refused the last request as wrongly signed, and repeating that "
            f"gets this address blocked — waiting {GOFILE_REJECTION_COOLDOWN - since:.0f}s "
            "before trying again (or set the GOFILE_SALT environment variable).")

    override = os.environ.get("GOFILE_SALT")
    if override:
        return override

    if cache["secret"] and now < cache["until"]:
        return cache["secret"]

    # A failed read is held briefly so a failing script isn't re-fetched per request.
    if cache.get("error") and now < cache["error_until"]:
        raise RuntimeError(cache["error"])

    try:
        r = await client.get(GOFILE_SCRIPT_URL, headers={"Referer": "https://gofile.io/"})
        r.raise_for_status()
        cache["secret"] = _gofile_secret_from(r.text)
        cache["until"] = now + GOFILE_SECRET_TTL
        cache["error"] = ""
        logger.info("DLOAD", "  Read Gofile signing secret from its script.")
    except Exception as e:
        cache["error"] = (
            f"Could not read Gofile's signing secret from its script ({e}). "
            "Set the GOFILE_SALT environment variable to the current secret.")
        cache["error_until"] = now + 60
        raise RuntimeError(cache["error"]) from e
    return cache["secret"]


def _gofile_signature_rejected() -> None:
    """Drop the secret and start the cooldown (the next try re-reads the script)."""
    _gofile_secret_cache.update(secret="", until=0.0, rejected_at=time.time())


def _gofile_website_token(api_token: str, secret: str) -> str:
    bucket = int(time.time() // GOFILE_TIME_BUCKET)
    raw = f"{GOFILE_USER_AGENT}::{GOFILE_LANG}::{api_token}::{bucket}::{secret}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _gofile_status_message(status: str | None) -> str:
    return {
        "error-notFound": "Gofile content not found (deleted or expired).",
        "error-passwordRequired":
            "This Gofile folder is password protected — add ?password=... to the link.",
        "error-passwordWrong": "Wrong Gofile folder password.",
        "error-notPremium":
            "Gofile rejected the request signature (error-notPremium). The signing secret "
            "read from gofile's script was refused; it will be read again after a short "
            "cooldown. Set the GOFILE_SALT environment variable to override it.",
    }.get(status or "", f"Gofile API error: {status}")


def _gofile_safe_name(name: str, fallback: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or fallback


async def _gofile_token(client) -> str:
    """The account token: GOFILE_TOKEN, a token already used by this client,
    or a fresh guest account. It is also set as the accountToken cookie, which
    gofile's file servers want on the download requests."""
    token = os.environ.get("GOFILE_TOKEN") or client.cookies.get("accountToken", domain=GOFILE_DOMAIN)
    if not token:
        r = await client.post(f"{GOFILE_API}/accounts")
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            raise RuntimeError(_gofile_status_message(data.get("status")))
        token = (data.get("data") or {}).get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Gofile guest account response carries no token.")
    client.cookies.set("accountToken", token, domain=GOFILE_DOMAIN)
    return token


async def _gofile_api(client, path: str, params: dict, api_token: str):
    """GET a signed gofile API endpoint and return its "data"."""
    secret = await _gofile_ensure_secret(client)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "X-Website-Token": _gofile_website_token(api_token, secret),
        "X-BL": GOFILE_LANG,
        "Origin": "https://gofile.io",
        "Referer": "https://gofile.io/",
    }
    try:
        r = await _get_with_retry(client, f"{GOFILE_API}{path}?{urlencode(params)}", headers)
    except Exception as exc:
        # Gofile reports "not found" / "password" problems as HTTP errors with
        # a JSON {"status": "error-..."} body — turn those into a clear message.
        try:
            status = exc.response.json().get("status")          # type: ignore[attr-defined]
        except Exception:
            status = None
        if status == "error-notPremium":
            _gofile_signature_rejected()
        if status:
            raise RuntimeError(_gofile_status_message(status)) from exc
        raise
    data = r.json()
    if data.get("status") != "ok":
        if data.get("status") == "error-notPremium":
            _gofile_signature_rejected()
        raise RuntimeError(_gofile_status_message(data.get("status")))
    return data.get("data")


async def _gofile_walk(
    client, content_id: str, api_token: str, pw_hash: str | None,
    rel: tuple[str, ...], depth: int, out: list[tuple[tuple[str, ...], list[str]]],
) -> None:
    """Collect (relative folder, file links) for a folder and its subfolders."""
    files: list[str] = []
    subfolders: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    page = 1
    while True:
        params = {"contentFilter": "", "sortField": "name", "sortDirection": "1",
                  "pageSize": str(GOFILE_PAGE_SIZE), "page": str(page)}
        if pw_hash:
            params["password"] = pw_hash
        data = await _gofile_api(client, f"/contents/{content_id}", params, api_token)
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected Gofile response (expected an object).")
        children = data.get("children")
        children = children if isinstance(children, dict) else {}

        new = 0
        for child_id, entry in children.items():
            if not isinstance(entry, dict) or child_id in seen_ids:
                continue
            seen_ids.add(child_id)
            new += 1
            kind = entry.get("type")
            if kind == "file" and isinstance(entry.get("link"), str) and entry["link"]:
                files.append(entry["link"])
            elif kind == "folder":
                sub_id = str(entry.get("id") or child_id)
                subfolders.append((sub_id, str(entry.get("name") or sub_id)))
        if len(children) < GOFILE_PAGE_SIZE or new == 0 or page >= 100:
            break
        page += 1

    if not seen_ids and not pw_hash:
        logger.warning("DLOAD", "  No files found (a password may be required).")
    out.append((rel, files))

    for sub_id, sub_name in subfolders:
        if depth >= GOFILE_MAX_DEPTH:
            logger.warning("DLOAD", f"  Folder nesting too deep, skipping: {sub_name}")
            continue
        await _gofile_walk(client, sub_id, api_token, pw_hash,
                           rel + (_gofile_safe_name(sub_name, sub_id),), depth + 1, out)


async def _download_gofile(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). Files of the linked folder go
    straight into `directory`; files of nested folders into subfolders named
    after them, so same-named files never overwrite each other."""
    parsed = _parse_gofile_url(url)
    if parsed is None:
        logger.error("DLOAD", f"Unsupported Gofile link (use gofile.io/d/ID): {url}")
        return 0, 0, 1
    content_id, password = parsed
    logger.info("DLOAD", f"Scraping Gofile: {content_id}")

    api_token = await _gofile_token(client)
    pw_hash = hashlib.sha256(password.encode()).hexdigest() if password else None

    folders: list[tuple[tuple[str, ...], list[str]]] = []
    await _gofile_walk(client, content_id, api_token, pw_hash, (), 0, folders)
    logger.info("DLOAD", f"  Found {sum(len(f) for _, f in folders)} file(s).")

    total_ok = total_skip = total_fail = 0
    for rel, file_urls in folders:
        if not file_urls:
            continue
        target = directory.joinpath(*rel)
        target.mkdir(parents=True, exist_ok=True)
        ok, skipped, fail = await _download_files(
            client, file_urls, target, _gofile_default_name, history, skip)
        total_ok   += ok
        total_skip += skipped
        total_fail += fail
        _save_history(history)
    return total_ok, total_skip, total_fail


def _gofile_default_name(url: str) -> str:
    return Path(unquote(urlparse(url).path)).name


# ---------------------------------------------------------------------------
# Mega
# ---------------------------------------------------------------------------
#
# Port of HeapLeach's mega extractor. Mega encrypts everything client-side
# under the key in the link's #fragment: names, sizes and the file bytes. So
# a link must include its key, filenames are decrypted here, and the payload
# is AES-CTR decrypted while it streams to disk.
#
#   https://mega.nz/file/<handle>#<key>                  one file
#   https://mega.nz/folder/<handle>#<key>                a whole folder
#   https://mega.nz/folder/<handle>#<key>/file/<node>    one file of a folder
#   https://mega.nz/#!<handle>!<key>, #F!<handle>!<key>  legacy shapes
#
# Download links are minted per request (they are tied to one storage node),
# so each is requested right before its file starts. Mega meters free
# transfer per IP address; when the quota is used up the API (error -17) or
# the storage server (HTTP 509) says how long the wait is, and the file is
# reported as failed with that wait rather than retried pointlessly.

MEGA_API            = "https://g.api.mega.co.nz/cs"
MEGA_ROOT           = "https://mega.nz"
MEGA_FILE_KEY_LEN   = 32
MEGA_FOLDER_KEY_LEN = 16
MEGA_MAX_DEPTH      = 8
MEGA_PARALLEL       = 1                 # files at once (mega meters free transfer per address)
MEGA_TYPE_FILE, MEGA_TYPE_FOLDER, MEGA_TYPE_ROOT = 0, 1, 2

_mega_seq = 0


class MegaError(RuntimeError):
    """A Mega failure with a message fit to show the user."""


_MEGA_API_ERRORS = {
    -2:  "the api rejected the request as malformed",
    -3:  "the api is rate limiting or temporarily overloaded",
    -4:  "the api is rate limiting or temporarily overloaded",
    -6:  "access denied for this link",
    -8:  "this link has expired",
    -9:  "content not found (deleted, or the link is mistyped)",
    -11: "access denied for this link",
    -14: "the key in the link does not decrypt this content",
    -15: "the session expired",
    -16: "the account that shared this has been suspended",
    -17: "download quota exceeded — mega limits how much one address may "
         "pull in a period, and the wait is measured in hours",
    -18: "the content is temporarily unavailable",
}


def _mega_api_message(code: int) -> str:
    return f"Mega: {_MEGA_API_ERRORS.get(code, f'the api returned error {code}')}"


def _is_mega_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz")


def _mega_b64(s: str) -> bytes:
    """Mega's unpadded URL-safe base64."""
    import base64
    s = s.strip().replace("-", "+").replace("_", "/").replace(",", "")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _new_mega_link(folder: bool, handle: str, key: str, node: str) -> dict:
    if not handle:
        raise MegaError("Mega: link carries no content id.")
    if not key:
        raise MegaError(
            "Mega: link carries no key — mega encrypts everything, and the part after "
            "the # is what decrypts it, so a link quoted without it cannot be opened.")
    try:
        raw = _mega_b64(key)
    except Exception as e:
        raise MegaError(f"Mega: link key is malformed: {e}") from e
    want = MEGA_FOLDER_KEY_LEN if folder else MEGA_FILE_KEY_LEN
    if len(raw) != want:
        raise MegaError(f"Mega: link key decodes to {len(raw)} bytes, expected {want}.")
    return {"folder": folder, "handle": handle, "key": raw, "node": node}


def _parse_mega_link(url: str) -> dict:
    """{"folder", "handle", "key" (bytes), "node"} of a public mega link.

    Raises MegaError for a link that isn't a file/folder link or lacks its key.
    """
    parts = urlparse(url)
    segs = [x for x in parts.path.split("/") if x]
    frag = parts.fragment.strip()

    if len(segs) >= 2:
        if segs[0] in ("file", "embed"):
            return _new_mega_link(False, segs[1], frag, "")
        if segs[0] == "folder":
            key, _, rest = frag.partition("/")
            node = ""
            kind, _, handle = rest.partition("/")
            if kind == "file":          # "/folder/<h>" would select a subtree: ignored
                node = handle
            return _new_mega_link(True, segs[1], key, node)

    if frag:                            # #!<handle>!<key>  /  #F!<handle>!<key>[!<node>]
        folder = False
        if frag.startswith("F"):
            folder, frag = True, frag[1:]
        bits = frag.removeprefix("!").split("!")
        if len(bits) >= 2:
            node = bits[2] if folder and len(bits) >= 3 else ""
            return _new_mega_link(folder, bits[0], bits[1], node)

    raise MegaError(f"Mega: {url} is not a file or folder link.")


# --- cryptography ---------------------------------------------------------

def _mega_file_key(k: bytes) -> tuple[bytes, bytes]:
    """(AES key, CTR nonce) folded out of a 32-byte file key."""
    if len(k) != MEGA_FILE_KEY_LEN:
        raise MegaError(f"Mega: file key is {len(k)} bytes, expected {MEGA_FILE_KEY_LEN}.")
    key = bytes(a ^ b for a, b in zip(k[:16], k[16:32]))
    return key, k[16:24]


def _mega_unwrap_key(wrapped: bytes, share: bytes) -> bytes:
    """Decrypt a node's key (AES-ECB) with the key of the share holding it."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if len(share) != MEGA_FOLDER_KEY_LEN:
        raise ValueError("share key has the wrong length")
    if not wrapped or len(wrapped) % 16:
        raise ValueError("wrapped key is not a whole number of blocks")
    dec = Cipher(algorithms.AES(share), modes.ECB()).decryptor()
    return dec.update(wrapped) + dec.finalize()


def _mega_decrypt_attrs(encoded: str, key: bytes) -> dict:
    """Decrypt a node's attributes (AES-CBC, zero IV, "MEGA" prefix) → dict."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    raw = _mega_b64(encoded)
    if not raw or len(raw) % 16:
        raise ValueError("attributes are not a whole number of blocks")
    dec = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
    plain = (dec.update(raw) + dec.finalize()).rstrip(b"\x00")
    if not plain.startswith(b"MEGA"):
        raise ValueError("attributes did not decrypt: wrong key for this node")
    attrs = json.loads(plain[4:].decode("utf-8", errors="replace"))
    return attrs if isinstance(attrs, dict) else {}


def _mega_ctr_decryptor(key: bytes, nonce: bytes):
    """Streaming AES-CTR decryptor (counter starts at 0 after the 8-byte nonce)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.CTR(nonce + bytes(8))).decryptor()


def _mega_safe_name(name: str, fallback: str) -> str:
    return _gofile_safe_name(name, fallback)


# --- API --------------------------------------------------------------------

def _mega_error_code(value) -> int | None:
    """A bare negative number is how mega signals failure where a result would be."""
    if isinstance(value, int) and not isinstance(value, bool) and value < 0:
        return value
    return None


async def _mega_api(client, cmd: dict, folder: str = "") -> dict:
    """POST one command and return its single result object."""
    global _mega_seq
    _mega_seq += 1
    url = f"{MEGA_API}?id={_mega_seq}" + (f"&n={quote(folder)}" if folder else "")
    r = await client.post(url, json=[cmd], headers={"Origin": MEGA_ROOT, "Referer": MEGA_ROOT + "/"})
    r.raise_for_status()
    raw = r.json()
    code = _mega_error_code(raw)
    if code is None and isinstance(raw, list) and raw:
        code = _mega_error_code(raw[0])
    if code is not None:
        raise MegaError(_mega_api_message(code))
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], dict):
        raise MegaError(f"Mega: unexpected api response: {str(raw)[:120]}")
    result = raw[0]
    code = _mega_error_code(result.get("e"))
    if code is not None:
        if code == -17 and result.get("tl"):
            raise MegaError(_mega_api_message(code) + f" (about {int(result['tl'])}s left)")
        raise MegaError(_mega_api_message(code))
    return result


async def _mega_link_for(client, handle: str, folder: str = "") -> dict:
    """A fresh storage link ({"g": url, "s": size, "at": attrs}) for a file:
    a node of the `folder` share, or a public file when `folder` is empty."""
    cmd: dict = {"a": "g", "g": 1, "ssl": 2}
    cmd["n" if folder else "p"] = handle
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            dl = await _mega_api(client, cmd, folder)
            break
        except MegaError as e:
            # -3 / -4: congestion or per-address request limit — worth asking again.
            if attempt < RETRY_ATTEMPTS and "rate limiting" in str(e):
                await asyncio.sleep(min(2 ** attempt, 30))
                continue
            raise
    url = dl.get("g")
    if not isinstance(url, str) or not url:
        raise MegaError("Mega: the api returned no download link.")
    dl["g"] = url.replace("http://", "https://", 1)
    return dl


# --- listing ---------------------------------------------------------------

def _mega_decrypt_node(node: dict, share: bytes) -> tuple[bytes | None, str]:
    """(node key, name) — tries every wrapped key; the one whose attributes
    carry mega's "MEGA" prefix is the right one."""
    k = node.get("k")
    for entry in (k.split("/") if isinstance(k, str) else []):
        wrapped = entry.partition(":")[2] if ":" in entry else entry
        try:
            key = _mega_unwrap_key(_mega_b64(wrapped), share)
            attr_key = _mega_file_key(key)[0] if len(key) == MEGA_FILE_KEY_LEN else key
            attrs = _mega_decrypt_attrs(str(node.get("a") or ""), attr_key)
        except Exception:
            continue
        return key, str(attrs.get("n") or "")
    return None, ""


def _mega_list_folder(nodes: list[dict], share: bytes, only_node: str = "") -> tuple[str, list[dict]]:
    """(folder title, files) for a folder listing. Each file is
    {"handle", "name", "dir" (tuple of folder names), "size", "key", "nonce"}."""
    dec: dict[str, dict] = {}
    for n in nodes:
        if not isinstance(n, dict) or not n.get("h"):
            continue
        key, name = _mega_decrypt_node(n, share)
        dec[n["h"]] = {"node": n, "key": key, "name": name}

    title = "mega"
    for d in dec.values():
        if d["node"].get("t") != MEGA_TYPE_FILE and d["name"] and \
                (not d["node"].get("p") or d["node"]["p"] not in dec):
            title = d["name"]
            break

    def dir_of(d: dict) -> tuple[str, ...]:
        parts: list[str] = []
        handle = d["node"].get("p")
        while handle and len(parts) < MEGA_MAX_DEPTH:
            parent = dec.get(handle)
            if parent is None or parent["node"].get("t") == MEGA_TYPE_ROOT \
                    or parent["node"].get("p") not in dec:
                break                      # the share root, or outside the listing
            parts.insert(0, _mega_safe_name(parent["name"] or handle, handle))
            handle = parent["node"].get("p")
        return tuple(parts)

    files = []
    for h, d in dec.items():
        n = d["node"]
        if n.get("t") != MEGA_TYPE_FILE or d["key"] is None:
            continue
        if only_node and h != only_node:
            continue
        try:
            key, nonce = _mega_file_key(d["key"])
        except MegaError:
            continue
        files.append({"handle": h, "name": _mega_safe_name(d["name"] or h, h),
                      "dir": dir_of(d), "size": int(n.get("s") or 0),
                      "key": key, "nonce": nonce})
    return title, files


# --- download --------------------------------------------------------------

async def _download_mega_file(
    client, file: dict, share_handle: str, directory: Path,
    history: dict[str, str], skip_downloaded: bool,
) -> tuple[str, Exception | None, bool]:
    """Download + decrypt one mega file. Returns (history key, error, was_skipped)."""
    hkey = f"mega:{share_handle or '-'}:{file['handle']}"
    if skip_downloaded and hkey in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[hkey]).name}")
        return hkey, None, True

    dest = directory / file["name"]
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        part: Path | None = None
        completed = False
        try:
            import aiofiles
            dl = await _mega_link_for(client, file["handle"], folder=share_handle)
            decryptor = _mega_ctr_decryptor(file["key"], file["nonce"])
            written = 0
            async with client.stream("GET", dl["g"]) as response:
                if response.status_code == 509:
                    left = (response.headers.get("X-MEGA-Time-Left") or "").strip()
                    raise MegaError(_mega_api_message(-17) + (f" (about {left}s left)" if left.isdigit() else ""))
                response.raise_for_status()
                part = dest.with_name(dest.name + ".part")
                logger.info("DLOAD", f"Downloading: {file['name']}")
                async with aiofiles.open(part, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        plain = decryptor.update(chunk)
                        written += len(plain)
                        await f.write(plain)
                    tail = decryptor.finalize()
                    if tail:
                        written += len(tail)
                        await f.write(tail)
            expected = file["size"] or int(dl.get("s") or 0)
            if expected and written != expected:
                raise IOError(f"incomplete download ({written} of {expected} bytes)")
            os.replace(part, dest)
            completed = True
            history[hkey] = str(dest)
            return hkey, None, False
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS and (_is_transient(exc) or isinstance(exc, IOError)):
                delay = _retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {file['name']} — {exc}; retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                continue
            return hkey, exc, False
        finally:
            if part is not None and not completed:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
    return hkey, RuntimeError("retries exhausted"), False  # not reached


async def _download_mega(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). A folder link downloads every file,
    nested folders into subfolders; a file link downloads that one file."""
    try:
        link = _parse_mega_link(url)
    except MegaError as e:
        logger.error("DLOAD", str(e))
        return 0, 0, 1

    logger.info("DLOAD", f"Scraping Mega {'folder' if link['folder'] else 'file'}: {link['handle']}")
    if link["folder"]:
        listing = await _mega_api(client, {"a": "f", "c": 1, "r": 1}, folder=link["handle"])
        nodes = listing.get("f")
        if not isinstance(nodes, list) or not nodes:
            raise MegaError("Mega: folder is empty.")
        title, files = _mega_list_folder(nodes, link["key"], link["node"])
        if not files:
            raise MegaError("Mega: the selected file is not in this folder."
                            if link["node"] else "Mega: folder holds no readable files.")
        share = link["handle"]
    else:
        key, nonce = _mega_file_key(link["key"])
        info = await _mega_api(client, {"a": "g", "p": link["handle"]})
        try:
            attrs = _mega_decrypt_attrs(str(info.get("at") or ""), key)
        except Exception as e:
            raise MegaError(f"Mega: {e}") from e
        name = _mega_safe_name(str(attrs.get("n") or ""), link["handle"])
        files = [{"handle": link["handle"], "name": name, "dir": (),
                  "size": int(info.get("s") or 0), "key": key, "nonce": nonce}]
        share = ""
    logger.info("DLOAD", f"  Found {len(files)} file(s).")

    sem = asyncio.Semaphore(MEGA_PARALLEL)

    async def _one(f: dict):
        target = directory.joinpath(*f["dir"])
        target.mkdir(parents=True, exist_ok=True)
        async with sem:
            return f, await _download_mega_file(client, f, share, target, history, skip)

    ok = skipped = failed = 0
    for coro in asyncio.as_completed([_one(f) for f in files]):
        f, (hkey, err, was_skipped) = await coro
        if err:
            failed += 1
            logger.error("DLOAD", f"  Failed: {f['name']} — {err}")
        elif was_skipped:
            skipped += 1
        else:
            ok += 1
    _save_history(history)
    return ok, skipped, failed


# ---------------------------------------------------------------------------
# Google Drive (public links)
# ---------------------------------------------------------------------------
#
# Files: ported from HeapLeach. Drive serves bytes from drive.usercontent
# .google.com/download; anything too big to be virus-scanned first returns an
# HTML confirmation page whose form carries a per-visit token, which has to be
# submitted to get the bytes.
#
# Folders: HeapLeach leaves these out (listing one through the API needs a
# key). The public "embedded folder view" lists a shared folder without any
# key, so that is read instead: files are downloaded, subfolders followed.
# Google Docs/Sheets/Slides inside a folder are not files and are skipped.
#
#   https://drive.google.com/file/d/<id>/view     https://drive.google.com/open?id=<id>
#   https://drive.google.com/uc?id=<id>           https://drive.google.com/drive/folders/<id>
#
# Only items shared as "anyone with the link" can be fetched.

GDRIVE_ROOT      = "https://drive.google.com"
GDRIVE_DOWNLOAD  = "https://drive.usercontent.google.com/download"
GDRIVE_FOLDER    = "https://drive.google.com/embeddedfolderview"
GDRIVE_MAX_DEPTH = 8
GDRIVE_PARALLEL  = 2

_GDRIVE_ID_RE     = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_GDRIVE_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_GDRIVE_FILE_RE   = re.compile(r"/file/d/([A-Za-z0-9_-]+)")


class GDriveError(RuntimeError):
    """A Google Drive failure with a message fit to show the user (not retried)."""


def _is_gdrive_url(url: str) -> bool:
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host in ("drive.google.com", "drive.usercontent.google.com"):
        return True
    return host == "docs.google.com" and "/uc" in parts.path


def _parse_gdrive_url(url: str) -> tuple[str, str] | None:
    """("file" | "folder", id) for a public drive link, or None."""
    parts = urlparse(url)
    segs = [x for x in parts.path.split("/") if x]
    if "folders" in segs:
        i = segs.index("folders")
        if i + 1 < len(segs) and _GDRIVE_ID_RE.match(segs[i + 1]):
            return "folder", segs[i + 1]
    query_id = dict(parse_qsl(parts.query)).get("id", "")
    if query_id and _GDRIVE_ID_RE.match(query_id):
        return "file", query_id
    for i, seg in enumerate(segs):                       # /file/d/<id>/view and /d/<id>
        if seg == "d" and i + 1 < len(segs) and _GDRIVE_ID_RE.match(segs[i + 1]):
            return "file", segs[i + 1]
    return None


def _gdrive_refusal(doc: str) -> str:
    """Drive words some refusals as a page rather than a status."""
    if "Too many users have viewed or downloaded" in doc:
        return "this file has hit its download quota; it becomes available again after a day or so"
    if "You need access" in doc or "Request access" in doc:
        return "this is not shared publicly (anyone-with-the-link access is required)"
    if "does not exist" in doc or "Not Found" in doc[:2000]:
        return "the file does not exist or is not shared publicly"
    return ""


from html.parser import HTMLParser as _HTMLParser


class _GDriveConfirmParser(_HTMLParser):
    """Reads the large-file confirmation page: its form and the shown filename."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.action = ""
        self.fields: dict[str, str] = {}
        self.name = ""
        self._in_form = False
        self._in_name_span = False
        self._in_name_link = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "form" and a.get("id") == "download-form":
            self._in_form = True
            self.action = a.get("action") or ""
        elif tag == "input" and self._in_form and a.get("name"):
            self.fields[a["name"]] = a.get("value") or ""
        elif tag == "span" and "uc-name-size" in classes:
            self._in_name_span = True
        elif tag == "a" and self._in_name_span and not self.name:
            self._in_name_link = True

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False
        elif tag == "a":
            self._in_name_link = False
        elif tag == "span":
            self._in_name_span = False

    def handle_data(self, data):
        if self._in_name_link:
            self.name += data


class _GDriveFolderParser(_HTMLParser):
    """Reads a folder listing: entries of {"id", "href", "title"}."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries: list[dict] = []
        self._cur: dict | None = None
        self._depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "div":
            if self._cur is None:
                if "flip-entry" in classes and (a.get("id") or "").startswith("entry-"):
                    self._cur = {"id": a["id"][len("entry-"):], "href": "", "title": ""}
                    self._depth = 1
            else:
                self._depth += 1
                if "flip-entry-title" in classes:
                    self._in_title = True
        elif tag == "a" and self._cur is not None and not self._cur["href"]:
            self._cur["href"] = a.get("href") or ""

    def handle_endtag(self, tag):
        if tag == "div" and self._cur is not None:
            self._in_title = False
            self._depth -= 1
            if self._depth == 0:
                self.entries.append(self._cur)
                self._cur = None

    def handle_data(self, data):
        if self._in_title and self._cur is not None:
            self._cur["title"] += data


def _gdrive_filename(response) -> str:
    """Filename from Content-Disposition, preferring the RFC 5987 form (filename*=)
    that drive uses for non-ASCII names."""
    cd = response.headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*(?:[\w-]+)'[^']*'([^;]+)", cd, re.IGNORECASE)
    if m:
        return Path(unquote(m.group(1).strip().strip('"'))).name
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.IGNORECASE)
    return Path(m.group(1).strip()).name if m else ""


async def _download_gdrive_file(
    client, file_id: str, name_hint: str, directory: Path,
    history: dict[str, str], skip_downloaded: bool,
) -> tuple[str, Exception | None, bool]:
    """Download one drive file. Returns (history key, error, was_skipped)."""
    hkey = f"gdrive:{file_id}"
    if skip_downloaded and hkey in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[hkey]).name}")
        return hkey, None, True

    referer = {"Referer": GDRIVE_ROOT + "/"}
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        part: Path | None = None
        completed = False
        try:
            import aiofiles
            url = f"{GDRIVE_DOWNLOAD}?{urlencode({'id': file_id, 'export': 'download', 'confirm': 't'})}"
            page_name = ""
            for _step in range(3):             # the file itself, or its confirmation page(s)
                async with client.stream("GET", url, headers=referer) as response:
                    if response.status_code == 404:
                        raise GDriveError("Google Drive: the file does not exist or is not shared publicly")
                    response.raise_for_status()
                    if "text/html" in response.headers.get("content-type", "").lower():
                        doc = (await response.aread()).decode("utf-8", errors="replace")
                        msg = _gdrive_refusal(doc)
                        if msg:
                            raise GDriveError(f"Google Drive: {msg}")
                        form = _GDriveConfirmParser()
                        form.feed(doc)
                        if not form.action and not form.fields:
                            raise GDriveError("Google Drive: unexpected page instead of the file")
                        page_name = form.name.strip() or page_name
                        fields = dict(form.fields)
                        fields.setdefault("id", file_id)
                        fields.setdefault("confirm", "t")
                        url = f"{form.action or GDRIVE_DOWNLOAD}?{urlencode(fields)}"
                        continue
                    name = (name_hint or _gdrive_filename(response) or page_name
                            or f"drive-{file_id}")
                    dest = directory / _gofile_safe_name(name, f"drive-{file_id}")
                    part = dest.with_name(dest.name + ".part")
                    logger.info("DLOAD", f"Downloading: {dest.name}")
                    async with aiofiles.open(part, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            await f.write(chunk)
                    break
            else:
                raise GDriveError("Google Drive: stuck on the confirmation page")
            os.replace(part, dest)
            completed = True
            history[hkey] = str(dest)
            return hkey, None, False
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS and _is_transient(exc):
                delay = _retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {file_id} — {exc}; retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                continue
            return hkey, exc, False
        finally:
            if part is not None and not completed:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
    return hkey, RuntimeError("retries exhausted"), False  # not reached


async def _gdrive_walk(
    client, folder_id: str, rel: tuple[str, ...], depth: int,
    out: list[tuple[tuple[str, ...], list[tuple[str, str]]]],
) -> None:
    """Collect (relative folder, [(file id, name)]) for a folder and its subfolders."""
    r = await _get_with_retry(client, f"{GDRIVE_FOLDER}?{urlencode({'id': folder_id})}",
                              headers={"Referer": GDRIVE_ROOT + "/"})
    parser = _GDriveFolderParser()
    parser.feed(r.text)
    if not parser.entries:
        msg = _gdrive_refusal(r.text)
        if msg:
            raise GDriveError(f"Google Drive: {msg}")

    files: list[tuple[str, str]] = []
    subfolders: list[tuple[str, str]] = []
    used: set[str] = set()
    for e in parser.entries:
        title = " ".join(e["title"].split())
        fm, dm = _GDRIVE_FILE_RE.search(e["href"]), _GDRIVE_FOLDER_RE.search(e["href"])
        if dm:
            subfolders.append((dm.group(1), title or dm.group(1)))
        elif fm:
            fid = fm.group(1)
            name = _gofile_safe_name(title or f"drive-{fid}", f"drive-{fid}")
            if name.casefold() in used:        # drive allows duplicate names in a folder
                stem, dot, ext = name.rpartition(".")
                name = f"{stem} ({fid[:6]}).{ext}" if dot and stem else f"{name} ({fid[:6]})"
            used.add(name.casefold())
            files.append((fid, name))
        else:
            logger.warning("DLOAD", f"  Skipping (not a downloadable file, e.g. a Google Doc): {title or e['id']}")
    out.append((rel, files))

    for sub_id, sub_name in subfolders:
        if depth >= GDRIVE_MAX_DEPTH:
            logger.warning("DLOAD", f"  Folder nesting too deep, skipping: {sub_name}")
            continue
        await _gdrive_walk(client, sub_id, rel + (_gofile_safe_name(sub_name, sub_id),), depth + 1, out)


async def _download_gdrive(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). A folder link downloads every file,
    nested folders into subfolders; a file link downloads that one file."""
    parsed = _parse_gdrive_url(url)
    if parsed is None:
        logger.error("DLOAD", f"Unsupported Google Drive link (no file or folder id): {url}")
        return 0, 0, 1
    kind, item_id = parsed
    logger.info("DLOAD", f"Scraping Google Drive {kind}: {item_id}")

    if kind == "file":
        folders = [((), [(item_id, "")])]
    else:
        folders = []
        await _gdrive_walk(client, item_id, (), 0, folders)
        logger.info("DLOAD", f"  Found {sum(len(f) for _, f in folders)} file(s).")

    sem = asyncio.Semaphore(GDRIVE_PARALLEL)

    async def _one(fid: str, name: str, target: Path):
        async with sem:
            return name or fid, await _download_gdrive_file(client, fid, name, target, history, skip)

    jobs = []
    for rel, files in folders:
        if not files:
            continue
        target = directory.joinpath(*rel)
        target.mkdir(parents=True, exist_ok=True)
        jobs += [_one(fid, name, target) for fid, name in files]

    ok = skipped = failed = 0
    for coro in asyncio.as_completed(jobs):
        label, (hkey, err, was_skipped) = await coro
        if err:
            failed += 1
            logger.error("DLOAD", f"  Failed: {label} — {err}")
        elif was_skipped:
            skipped += 1
        else:
            ok += 1
    _save_history(history)
    return ok, skipped, failed


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
            import kkafio.services.kkd_session as _kkd
            kkd_session = _kkd.get_or_prompt()
            if not kkd_session:
                logger.warning("DLOAD",
                    "No kkd_session — koikatsucards.com downloads will be skipped.")

        # Resolve the pixiv PHPSESSID — optional: without it only public
        # artworks are reachable, so cancelling the dialog is not fatal.
        pixiv_session = None
        if any(_is_pixiv_url(url_str) for url_str, *_ in urls):
            import kkafio.services.pixiv_session as _pxv
            pixiv_session = _pxv.get_or_prompt()

        history = _load_history()
        total_ok = 0
        total_skip = 0
        total_fail = 0

        kkd_cookies = {"kkd_session": kkd_session} if kkd_session else None
        pixiv_cookies = {"PHPSESSID": pixiv_session} if pixiv_session else None

        async def _run_all() -> None:
            nonlocal total_ok, total_skip, total_fail
            # The PHPSESSID is scoped to .pixiv.net, so it is never sent on to
            # i.pximg.net (the image host) or anywhere else; the image host only
            # needs the pixiv Referer, which the client sends on every request.
            async with _make_client() as bepis_client, \
                       _make_client(cookies=kkd_cookies,
                                    cookie_domain="koikatsucards.com") as kk_client, \
                       _make_client(cookies=pixiv_cookies,
                                    cookie_domain=PIXIV_COOKIE_DOMAIN,
                                    extra_headers={"Referer": PIXIV_URL + "/"}) as pixiv_client, \
                       _make_client(extra_headers={"User-Agent": GOFILE_USER_AGENT}) as gofile_client, \
                       _make_client() as mega_client, \
                       _make_client() as gdrive_client:
                for url_str, page_start, page_end in urls:
                    ok = skipped = fail = 0
                    try:
                        if BEPIS_URL in url_str:
                            ok, skipped, fail = await _download_bepis(
                                bepis_client, url_str, output_dir, history, self.skip_downloaded,
                                page_start=page_start, page_end=page_end
                            )
                        elif KOIKATSU_URL in url_str:
                            if not kkd_session:
                                logger.skipped("DLOAD",
                                    f"Skipping koikatsucards.com URL (no session): {url_str}")
                                total_fail += 1
                                continue
                            ok, skipped, fail = await _download_koikatsu(
                                kk_client, url_str, output_dir, history, self.skip_downloaded,
                                page_start=page_start, page_end=page_end
                            )
                        elif _is_pixiv_url(url_str):
                            ok, skipped, fail = await _download_pixiv(
                                pixiv_client, url_str, output_dir, history, self.skip_downloaded,
                                page_start=page_start, page_end=page_end)
                        elif _is_gofile_url(url_str):
                            if page_start is not None:
                                logger.info("DLOAD",
                                    "  Page directive is ignored for Gofile links.")
                            ok, skipped, fail = await _download_gofile(
                                gofile_client, url_str, output_dir, history, self.skip_downloaded)
                        elif _is_mega_url(url_str):
                            if page_start is not None:
                                logger.info("DLOAD",
                                    "  Page directive is ignored for Mega links.")
                            ok, skipped, fail = await _download_mega(
                                mega_client, url_str, output_dir, history, self.skip_downloaded)
                        elif _is_gdrive_url(url_str):
                            if page_start is not None:
                                logger.info("DLOAD",
                                    "  Page directive is ignored for Google Drive links.")
                            ok, skipped, fail = await _download_gdrive(
                                gdrive_client, url_str, output_dir, history, self.skip_downloaded)
                        else:
                            logger.error("DLOAD",
                                f"Unsupported URL (must be db.bepis.moe, koikatsucards.com, pixiv.net, gofile.io, mega.nz or drive.google.com): {url_str}")
                            fail = 1
                    except Exception as exc:
                        # One bad URL must not abort the remaining ones.
                        logger.error("DLOAD", f"Failed to process {url_str}: {exc}")
                        fail += 1
                    total_ok   += ok
                    total_skip += skipped
                    total_fail += fail
                    # Persist after every URL so an interrupted run keeps
                    # everything downloaded so far.
                    _save_history(history)

        try:
            asyncio.run(_run_all())
        finally:
            # Also runs on Stop / Ctrl+C (KeyboardInterrupt) and on errors, so
            # "Skip already downloaded" still works on the next run.
            try:
                _save_history(history)
            except Exception as e:
                logger.error("DLOAD", f"Could not save download history: {e}")

        self.log_done("DLOAD", moved=total_ok, skipped=total_skip,
                      extra=f"{total_fail} failed, {total_ok + total_skip + total_fail} total")