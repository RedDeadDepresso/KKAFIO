"""mediafire.com — single files and folders.

  https://www.mediafire.com/file/<key>/<name>/file     one file (also /file_premium/, /view/, /download/<key>
                                                       and the old https://www.mediafire.com/?<key>)
  https://www.mediafire.com/folder/<key>/<name>        a folder: every file in it is downloaded
  https://download<n>.mediafire.com/...                a direct link: downloaded as it is

MediaFire has no download API for files: a file link is a web page, and the real
(temporary) download link is read from its download button. A folder is listed with
MediaFire's public folder API, which needs no key or login; its subfolders become
subfolders of the output directory, like the folders of Gofile and Mega.
"""

from __future__ import annotations

import asyncio
import base64
import html
import re
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse

from kkafio.core import progress
from kkafio.core.logger import logger
from kkafio.services.content_sources.shared import history as history_store
from kkafio.services.content_sources.shared.net import MAX_CONNECTIONS, get_with_retry, safe_filename
from kkafio.services.content_sources.shared.transfer import download_file

MEDIAFIRE_ROOT = "https://www.mediafire.com"
MEDIAFIRE_FOLDER_API = f"{MEDIAFIRE_ROOT}/api/1.5/folder/get_content.php"
MEDIAFIRE_CHUNK_SIZE = 1000
MEDIAFIRE_MAX_DEPTH = 8                      # nested folders followed (a safety net against loops)

_KEY = r"[A-Za-z0-9]{6,20}"
_FILE_PATH_RE   = re.compile(rf"^/(?:file|file_premium|view|download)/({_KEY})(?:/([^/]*))?", re.I)
_FOLDER_PATH_RE = re.compile(rf"^/folder/({_KEY})(?:/|$)", re.I)
_OLD_QUERY_RE   = re.compile(rf"^({_KEY})$")

_BUTTON_RE    = re.compile(r"<a\b[^>]*\bid=[\"']downloadButton[\"'][^>]*>", re.I | re.S)
_SCRAMBLED_RE = re.compile(r"data-scrambled-url=[\"']([^\"']+)[\"']", re.I)
_HREF_RE      = re.compile(r"\bhref=[\"']([^\"']*)[\"']", re.I)
_DIRECT_RE    = re.compile(r"https?://download\d*\.mediafire\.com/[^\s\"'<>]+", re.I)


def is_mediafire_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "mediafire.com" or host.endswith(".mediafire.com")


def parse_mediafire_url(url: str) -> tuple[str, ...] | None:
    """("file", page url, name), ("folder", key) or ("direct", url); None if `url` is
    neither (the home page, an account page, ...)."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if not (host == "mediafire.com" or host.endswith(".mediafire.com")):
        return None
    path = unquote(parts.path)
    if host not in ("mediafire.com", "www.mediafire.com"):
        # download1234.mediafire.com/<token>/<key>/<name>: already the file itself.
        if host.startswith("download") and len([s for s in path.split("/") if s]) >= 2:
            return "direct", f"https://{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")
        return None
    m = _FOLDER_PATH_RE.match(path)
    if m:
        return "folder", m.group(1)
    m = _FILE_PATH_RE.match(path)
    if m:
        name = m.group(2) if m.group(2) and m.group(2) != "file" else ""
        return "file", f"{MEDIAFIRE_ROOT}/file/{m.group(1)}/file", name
    if path in ("", "/") and _OLD_QUERY_RE.match(parts.query):
        return "file", f"{MEDIAFIRE_ROOT}/file/{parts.query}/file", ""
    return None


def mediafire_direct_link(page: str) -> str | None:
    """The download link of a MediaFire file page, else None (file deleted or blocked)."""
    button = _BUTTON_RE.search(page)
    if button:
        tag = button.group(0)
        scrambled = _SCRAMBLED_RE.search(tag)
        if scrambled:
            try:
                raw = scrambled.group(1)
                decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8").strip()
            except Exception:
                decoded = ""
            if decoded.lower().startswith(("http://", "https://")):
                return decoded
        href = _HREF_RE.search(tag)
        if href:
            link = html.unescape(href.group(1)).strip()
            if link.lower().startswith(("http://", "https://")):
                return link
    found = _DIRECT_RE.search(page)
    return html.unescape(found.group(0)) if found else None


def _default_name(page_url: str, name: str = "") -> str:
    return safe_filename(name, "") or Path(unquote(urlparse(page_url).path)).parent.name or "mediafire-file"


async def _download_one(
    client, page_url: str, directory: Path, name: str,
    history: dict, skip: bool, sem: asyncio.Semaphore,
) -> str:
    """"ok", "skipped" or "failed" for one file page (or direct link)."""
    async with sem:
        if skip and page_url in history:
            logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[page_url]).name}")
            progress.count("skipped")
            return "skipped"
        direct = page_url
        if urlparse(page_url).hostname.lower() in ("mediafire.com", "www.mediafire.com"):
            try:
                response = await get_with_retry(client, page_url)
                direct = mediafire_direct_link(response.text)
            except Exception as exc:
                logger.error("DLOAD", f"  MediaFire page failed: {page_url} — {exc}")
                progress.count("failed")
                return "failed"
            if not direct:
                logger.error("DLOAD", f"  No download link on {page_url} (deleted or blocked?)")
                progress.count("failed")
                return "failed"
        directory.mkdir(parents=True, exist_ok=True)
        _, err, _ = await download_file(client, direct, directory, name or _default_name(page_url), history, False)
        if err:
            logger.error("DLOAD", f"  Failed: {page_url} — {err}")
            return "failed"
        if direct != page_url and direct in history:
            history[page_url] = history.pop(direct)
        return "ok"


async def _folder_api(client, folder_key: str, content_type: str, chunk: int) -> dict:
    query = urlencode({
        "folder_key": folder_key, "content_type": content_type, "chunk": chunk,
        "chunk_size": MEDIAFIRE_CHUNK_SIZE, "response_format": "json",
    })
    response = await get_with_retry(client, f"{MEDIAFIRE_FOLDER_API}?{query}")
    body = (response.json() or {}).get("response") or {}
    if body.get("result") != "Success":
        raise RuntimeError(body.get("message") or body.get("error") or "folder not available")
    return body.get("folder_content") or {}


async def _folder_items(client, folder_key: str, content_type: str, field: str) -> list[dict]:
    items: list[dict] = []
    chunk = 1
    while True:
        content = await _folder_api(client, folder_key, content_type, chunk)
        items += [i for i in content.get(field) or [] if isinstance(i, dict)]
        if str(content.get("more_chunks", "no")).lower() != "yes":
            return items
        chunk += 1


async def _folder_walk(client, key: str, rel: tuple[str, ...], depth: int,
                       out: list[tuple[tuple[str, ...], str, str]]) -> None:
    """Collect (relative folder, file page url, file name) of the folder and its subfolders."""
    for item in await _folder_items(client, key, "files", "files"):
        link = ((item.get("links") or {}).get("normal_download") or "").strip()
        quickkey = item.get("quickkey")
        if not link and quickkey:
            link = f"{MEDIAFIRE_ROOT}/file/{quickkey}/file"
        if link:
            out.append((rel, link, safe_filename(str(item.get("filename") or ""), "")))
    if depth >= MEDIAFIRE_MAX_DEPTH:
        return
    for sub in await _folder_items(client, key, "folders", "folders"):
        sub_key = sub.get("folderkey")
        if sub_key:
            await _folder_walk(client, str(sub_key), rel + (safe_filename(str(sub.get("name") or ""), str(sub_key)),),
                               depth + 1, out)


async def download_mediafire(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). A file link downloads that file; a folder link
    downloads every file of the folder (subfolders into subfolders of `directory`)."""
    parsed = parse_mediafire_url(url)
    if parsed is None:
        logger.error("DLOAD",
            f"Unsupported MediaFire link (use mediafire.com/file/<id> or mediafire.com/folder/<id>): {url}")
        return 0, 0, 1

    jobs: list[tuple[Path, str, str]] = []
    if parsed[0] == "folder":
        logger.info("DLOAD", f"Scraping MediaFire folder: {parsed[1]}")
        found: list[tuple[tuple[str, ...], str, str]] = []
        try:
            await _folder_walk(client, parsed[1], (), 0, found)
        except Exception as exc:
            logger.error("DLOAD", f"  MediaFire folder {parsed[1]} failed: {exc}")
            return 0, 0, 1
        if not found:
            logger.error("DLOAD", f"  No files found in MediaFire folder {parsed[1]} (deleted or empty?).")
            return 0, 0, 1
        logger.info("DLOAD", f"  Found {len(found)} file(s).")
        jobs = [(directory.joinpath(*rel), link, name) for rel, link, name in found]
    elif parsed[0] == "file":
        logger.info("DLOAD", f"MediaFire file: {parsed[1]}")
        jobs = [(directory, parsed[1], safe_filename(parsed[2], ""))]
    else:
        logger.info("DLOAD", f"MediaFire direct link: {parsed[1]}")
        jobs = [(directory, parsed[1], "")]

    sem = asyncio.Semaphore(MAX_CONNECTIONS)
    results = await asyncio.gather(*(
        _download_one(client, link, target, name, history, skip, sem) for target, link, name in jobs))
    history_store.save_history(history)
    return results.count("ok"), results.count("skipped"), results.count("failed")
