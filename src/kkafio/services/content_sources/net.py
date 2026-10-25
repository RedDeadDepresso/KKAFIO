"""Shared HTTP plumbing for the content sources.

Retry / back-off helpers, the async client factory, ``?page=`` URL helpers and
file-name helpers. Nothing in here knows about a particular site.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from kkafio.core.logger import logger

MAX_CONNECTIONS = 10
RETRY_ATTEMPTS = 3             # total tries for a transient network/HTTP error
MAX_CONSECUTIVE_BAD_PAGES = 3  # stop paging after this many failed pages in a row
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


def set_page(url: str, page: int) -> str:
    """Return url with ?page=<page> set or replaced.

    The query is handled as a list of (key, value) pairs so repeated
    parameters (e.g. several ``tag=`` filters) are all preserved.
    """
    parts = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    pairs.append(("page", str(page)))
    return urlunparse(parts._replace(query=urlencode(pairs)))


def strip_page(url: str) -> str:
    """Return url with ?page removed (repeated parameters are preserved)."""
    parts = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    return urlunparse(parts._replace(query=urlencode(pairs)))


def is_transient(exc: Exception) -> bool:
    """True for errors worth retrying: network/timeout errors and 408/425/429/5xx."""
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return isinstance(exc, httpx.TransportError)


def retry_delay(exc: Exception, attempt: int) -> float:
    """Seconds to wait before retry `attempt` (honours Retry-After, capped)."""
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("retry-after", "").strip()
        if retry_after.isdigit():
            return float(min(int(retry_after), 60))
    return float(min(2 ** attempt, 30))


def is_rate_limited(exc: Exception | None) -> bool:
    """True for an HTTP 429 answer."""
    import httpx
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def retry_after_seconds(exc: Exception | None, cap: float = 120.0) -> float | None:
    """The Retry-After of a 429 (seconds or an HTTP date), capped; None if absent."""
    response = getattr(exc, "response", None)
    value = (response.headers.get("retry-after", "") if response is not None else "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(min(int(value), cap))
    try:
        from email.utils import parsedate_to_datetime
        import datetime as _dt
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return max(0.0, min(cap, (when - _dt.datetime.now(_dt.timezone.utc)).total_seconds()))
    except Exception:
        return None


async def get_with_retry(client, url: str, headers: dict | None = None,
                          retry_429: bool = True):
    """GET `url`, raise_for_status, retrying transient failures with backoff.

    With retry_429=False a 429 is raised at once instead of being retried here:
    the caller owns the rate-limit handling (pawchive's shared cooldown).
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = await (client.get(url, headers=headers) if headers else client.get(url))
            r.raise_for_status()
            return r
        except Exception as exc:
            if not retry_429 and is_rate_limited(exc):
                raise
            if attempt < RETRY_ATTEMPTS and is_transient(exc):
                delay = retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {exc} — retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                continue
            raise


def make_client(cookies: dict | None = None, cookie_domain: str | None = None,
                 extra_headers: dict | None = None, http2: bool = True):
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
        follow_redirects=True, http2=http2,
        timeout=httpx.Timeout(30.0, pool=None),
    )


def resolve_filename(response, url: str, default_name: str) -> str:
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


def safe_filename(name: str, fallback: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or fallback
