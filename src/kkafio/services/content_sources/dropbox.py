"""dropbox.com — shared files and shared folders (also the dl.dropboxusercontent.com direct links).

  https://www.dropbox.com/scl/fi/<id>/<name>?rlkey=<key>&dl=0   one file (new style link)
  https://www.dropbox.com/s/<id>/<name>?dl=0                    one file (old style link; also /s/raw/<id>/...)
  https://www.dropbox.com/scl/fo/<id>/<key>?rlkey=<key>&dl=0    a folder: its files are downloaded
  https://www.dropbox.com/sh/<id>/<key>                         a folder (old style link; also /sc/...)
  https://dl.dropboxusercontent.com/...                         a direct link: downloaded as it is

Dropbox needs no account or API key for a shared link: the same link with "dl=1" instead of
"dl=0" (or none) serves the file itself, and for a folder it serves a zip of the whole folder,
which is unpacked into the output directory (its subfolders are kept). Any other parameter of the
link (rlkey, st, ...) is kept, as the new style links do not work without their rlkey.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from kkafio.core import progress
from kkafio.core.logger import logger
from kkafio.services.content_sources.shared import history as history_store
from kkafio.services.content_sources.shared.net import safe_filename
from kkafio.services.content_sources.shared.transfer import download_files

DROPBOX_HOSTS = ("dropbox.com", "dropboxusercontent.com")

_ID = r"[A-Za-z0-9_-]{6,}"
_FILE_RE   = re.compile(rf"^/(?:s/raw|s|scl/fi)/({_ID})(?:/([^/]+))?/?$")
_FOLDER_RE = re.compile(rf"^/(?:sh|sc|scl/fo)/({_ID})(?:/[^/]*)?/?$")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_dropbox_url(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in DROPBOX_HOSTS)


def _download_link(parts) -> str:
    """The same link with dl=1 (and no raw=1 / fragment), the way Dropbox serves the file itself."""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in ("dl", "raw")]
    query.append(("dl", "1"))
    return urlunparse(("https", parts.netloc, parts.path, "", urlencode(query), ""))


def parse_dropbox_url(url: str) -> tuple[str, str, str] | None:
    """("file", download link, file name), ("folder", download link, "") or ("direct", link, file name);
    None for anything else (the home page, a "dropbox.com/home/..." path of an account, ...)."""
    if not is_dropbox_url(url):
        return None
    parts = urlparse(url.strip())
    path = unquote(parts.path)
    if _host(url).endswith("dropboxusercontent.com"):
        # dl.dropboxusercontent.com/<...>/<name>: already the file itself.
        segments = [s for s in path.split("/") if s]
        if not segments:
            return None
        direct = urlunparse(("https", parts.netloc, parts.path, "", parts.query, ""))
        return "direct", direct, segments[-1]
    m = _FILE_RE.match(path)
    if m:
        return "file", _download_link(parts), m.group(2) or ""
    if _FOLDER_RE.match(path):
        return "folder", _download_link(parts), ""
    return None


def _unpack(archive: Path, directory: Path) -> int:
    """Extract `archive` into `directory`, refusing entries that would land outside it. Returns the
    number of files written."""
    root = directory.resolve()
    written = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (directory / info.filename).resolve()
            if root != target and root not in target.parents:
                logger.warning("DLOAD", f"  Skipping unsafe path in Dropbox folder: {info.filename}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            written += 1
    return written


async def download_dropbox(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). A file link downloads that file; a folder link downloads
    the folder as a zip and unpacks it into `directory`."""
    parsed = parse_dropbox_url(url)
    if parsed is None:
        logger.error("DLOAD",
            f"Unsupported Dropbox link (use a dropbox.com shared file or folder link): {url}")
        progress.count("failed")
        return 0, 0, 1
    kind, link, name = parsed
    directory.mkdir(parents=True, exist_ok=True)

    if kind in ("file", "direct"):
        logger.info("DLOAD", f"Dropbox file: {name or link}")
        label = safe_filename(name, "dropbox-file")
        result = await download_files(client, [link], directory, lambda u: label, history, skip)
        history_store.save_history(history)
        return result

    # A folder: Dropbox zips it on the fly.
    logger.info("DLOAD", f"Dropbox folder: {urlparse(link).path}")
    if skip and link in history:
        logger.info("DLOAD", "Skipping (already downloaded): Dropbox folder")
        progress.count("skipped")
        return 0, 1, 0
    scratch = directory / f".dropbox-{abs(hash(link)) % 10**8}.tmp"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        ok, _, failed = await download_files(
            client, [link], scratch, lambda u: "dropbox-folder.zip", {}, False)
        archives = [p for p in scratch.iterdir() if p.is_file()]
        if failed or not ok or not archives:
            return 0, 0, 1
        try:
            count = await asyncio.to_thread(_unpack, archives[0], directory)
        except (zipfile.BadZipFile, OSError) as exc:
            logger.error("DLOAD", f"  Could not unpack the Dropbox folder: {exc}")
            progress.count("failed")
            return 0, 0, 1
        if not count:
            logger.error("DLOAD", "  The Dropbox folder is empty.")
            progress.count("failed")
            return 0, 0, 1
        logger.info("DLOAD", f"  Unpacked {count} file(s).")
        history[link] = str(directory)
        history_store.save_history(history)
        return count, 0, 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
