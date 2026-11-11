"""pixeldrain.com — single files and lists (also the pixeldrain.net / pixeldrain.dev mirrors).

  https://pixeldrain.com/u/<id>          one file (also /api/file/<id>)
  https://pixeldrain.com/l/<id>          a list: every file in it is downloaded (also /api/list/<id>)

Pixeldrain's API needs no key or login for public files: a file is a plain GET of
/api/file/<id> (the name comes from its Content-Disposition), and a list is read from
/api/list/<id>. A "#item=N" on a list link is ignored: the whole list is downloaded.
Anonymous downloads can be refused (rate limit / captcha required for popular files);
that shows up as a failed download.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.net import get_with_retry, safe_filename
from kkafio.services.content_sources.transfer import download_files

PIXELDRAIN_ROOT  = "https://pixeldrain.com"
PIXELDRAIN_HOSTS = ("pixeldrain.com", "pixeldrain.net", "pixeldrain.dev")

_ID = r"[A-Za-z0-9_-]{4,24}"
_FILE_RE = re.compile(rf"^/(?:u|api/file)/({_ID})/?$")
_LIST_RE = re.compile(rf"^/(?:l|api/list)/({_ID})/?$")


def is_pixeldrain_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in PIXELDRAIN_HOSTS)


def parse_pixeldrain_url(url: str) -> tuple[str, str] | None:
    """("file", id) or ("list", id); None for anything else (home page, user pages, ...)."""
    if not is_pixeldrain_url(url):
        return None
    path = unquote(urlparse(url).path)
    m = _FILE_RE.match(path)
    if m:
        return "file", m.group(1)
    m = _LIST_RE.match(path)
    if m:
        return "list", m.group(1)
    return None


def pixeldrain_file_url(file_id: str) -> str:
    return f"{PIXELDRAIN_ROOT}/api/file/{file_id}?download"


async def download_pixeldrain(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). A file link downloads that file; a list link
    downloads every file of the list."""
    parsed = parse_pixeldrain_url(url)
    if parsed is None:
        logger.error("DLOAD",
            f"Unsupported Pixeldrain link (use pixeldrain.com/u/<id> or pixeldrain.com/l/<id>): {url}")
        return 0, 0, 1
    kind, ident = parsed

    names: dict[str, str] = {}
    if kind == "file":
        logger.info("DLOAD", f"Pixeldrain file: {ident}")
        file_urls = [pixeldrain_file_url(ident)]
        names[file_urls[0]] = ident
    else:
        logger.info("DLOAD", f"Scraping Pixeldrain list: {ident}")
        try:
            body = (await get_with_retry(client, f"{PIXELDRAIN_ROOT}/api/list/{ident}")).json() or {}
        except Exception as exc:
            logger.error("DLOAD", f"  Pixeldrain list {ident} failed: {exc}")
            return 0, 0, 1
        if body.get("success") is False:
            logger.error("DLOAD", f"  Pixeldrain list {ident}: {body.get('message') or body.get('value') or 'not available'}")
            return 0, 0, 1
        file_urls = []
        for item in body.get("files") or []:
            if isinstance(item, dict) and item.get("id"):
                link = pixeldrain_file_url(str(item["id"]))
                file_urls.append(link)
                names[link] = safe_filename(str(item.get("name") or ""), str(item["id"]))
        if not file_urls:
            logger.error("DLOAD", f"  No files found in Pixeldrain list {ident} (deleted or empty?).")
            return 0, 0, 1
        logger.info("DLOAD", f"  Found {len(file_urls)} file(s).")

    directory.mkdir(parents=True, exist_ok=True)
    result = await download_files(client, file_urls, directory, lambda u: names.get(u, "pixeldrain-file"),
                                  history, skip)
    history_store.save_history(history)
    return result
