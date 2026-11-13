"""catbox.moe — single files and albums (also Litterbox's temporary files).

  https://files.catbox.moe/<name>.<ext>     one file
  https://litter.catbox.moe/<name>.<ext>    one Litterbox file (expires on its own)
  https://catbox.moe/c/<id>                 an album: every file in it is downloaded

Catbox serves files without a key or a login, so a file link is a plain GET.
An album is a web page: its file links are read from the HTML (there is no
API for it) and downloaded into the output directory like the files of a
single link. Every file of an album is downloaded, whatever its type, the way
Gofile and Mega folders are.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from kkafio.core.logger import logger
from kkafio.services.content_sources.shared import history as history_store
from kkafio.services.content_sources.shared.net import get_with_retry
from kkafio.services.content_sources.shared.transfer import download_files

CATBOX_ROOT        = "https://catbox.moe"
CATBOX_FILE_HOSTS  = ("files.catbox.moe", "litter.catbox.moe")

_CATBOX_ALBUM_RE = re.compile(r"^/c/([A-Za-z0-9_-]+)/?$")
_CATBOX_NAME_RE  = re.compile(r"^[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}$")
# A file link inside an album page: absolute, protocol-relative or http(s).
_CATBOX_PAGE_FILE_RE = re.compile(
    r"(?:https?:)?//((?:files|litter)\.catbox\.moe)/([A-Za-z0-9_-]+\.[A-Za-z0-9]{1,10})")


def is_catbox_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "catbox.moe" or host.endswith(".catbox.moe")


def parse_catbox_url(url: str) -> tuple[str, str] | None:
    """("file", https url) for a file link, ("album", id) for an album, else None."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    path = unquote(parts.path)
    if host in CATBOX_FILE_HOSTS:
        name = path.lstrip("/")
        if "/" not in name and _CATBOX_NAME_RE.match(name):
            return "file", f"https://{host}/{name}"
        return None
    if host in ("catbox.moe", "www.catbox.moe"):
        m = _CATBOX_ALBUM_RE.match(path)
        if m:
            return "album", m.group(1)
    return None


def catbox_page_files(html: str) -> list[str]:
    """The file links of an album page, in page order and without repeats."""
    seen: dict[str, None] = {}
    for host, name in _CATBOX_PAGE_FILE_RE.findall(html):
        seen.setdefault(f"https://{host}/{name}", None)
    return list(seen)


def _default_name(url: str) -> str:
    return Path(unquote(urlparse(url).path)).name or "catbox-file"


async def download_catbox(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). A file link downloads that one
    file; an album link downloads every file of the album."""
    parsed = parse_catbox_url(url)
    if parsed is None:
        logger.error("DLOAD",
            f"Unsupported Catbox link (use files.catbox.moe/<file> or catbox.moe/c/<album>): {url}")
        return 0, 0, 1
    kind, ident = parsed

    if kind == "file":
        logger.info("DLOAD", f"Catbox file: {ident}")
        file_urls = [ident]
    else:
        logger.info("DLOAD", f"Scraping Catbox album: {ident}")
        response = await get_with_retry(client, f"{CATBOX_ROOT}/c/{ident}")
        file_urls = catbox_page_files(response.text)
        if not file_urls:
            logger.error("DLOAD", f"  No files found in Catbox album {ident} (deleted or empty?).")
            return 0, 0, 1
        logger.info("DLOAD", f"  Found {len(file_urls)} file(s).")

    directory.mkdir(parents=True, exist_ok=True)
    result = await download_files(client, file_urls, directory, _default_name, history, skip)
    history_store.save_history(history)
    return result
