"""
download_contents.py — Download Koikatsu character cards from web sources.

Supports:
  https://db.bepis.moe          — card pages (/view/) and listing pages
  https://koikatsucards.com     — card pages (/contents/) and listing pages
  https://www.pixiv.net         — artworks (/artworks/ID) and whole galleries
                                  (/users/ID, /users/ID/illustrations); only
                                  images that look like KK cards/scenes are kept

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
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse, urljoin

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


async def _get_with_retry(client, url: str):
    """GET `url`, raise_for_status, retrying transient failures with backoff."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = await client.get(url)
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
    is "artworks" (illustrations + manga), "illustrations" or "manga".
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

    # Legacy links
    query = dict(parse_qsl(parts.query))
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
            f"Unsupported pixiv link (use /artworks/ID or /users/ID): {url}")
        return 0, 0, 1
    kind, ident, scope = parsed

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
                                    extra_headers={"Referer": PIXIV_URL + "/"}) as pixiv_client:
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
                        else:
                            logger.error("DLOAD",
                                f"Unsupported URL (must be db.bepis.moe, koikatsucards.com or pixiv.net): {url_str}")
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