"""drive.google.com — public files and folders.

Files: ported from HeapLeach. Drive serves bytes from drive.usercontent
.google.com/download; anything too big to be virus-scanned first returns an
HTML confirmation page whose form carries a per-visit token, which has to be
submitted to get the bytes.

Folders: HeapLeach leaves these out (listing one through the API needs a
key). The public "embedded folder view" lists a shared folder without any
key, so that is read instead: files are downloaded, subfolders followed.
Google Docs/Sheets/Slides inside a folder are not files and are skipped.

  https://drive.google.com/file/d/<id>/view     https://drive.google.com/open?id=<id>
  https://drive.google.com/uc?id=<id>           https://drive.google.com/drive/folders/<id>

Only items shared as "anyone with the link" can be fetched.

An interrupted download keeps its "drive-<id>.part" file; the next attempt (or
run) continues it with a Range request. A server that ignores Range sends the
whole file again, which replaces the part. Bytes are checked against the size
the server announced before the part is renamed into place.
"""

from __future__ import annotations

import asyncio
import os
import re
from html.parser import HTMLParser as _HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.net import RETRY_ATTEMPTS, get_with_retry, is_transient, retry_delay, safe_filename

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


class _Incomplete(Exception):
    """The body ended before the announced size: worth another (resumed) try."""


def is_gdrive_url(url: str) -> bool:
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host in ("drive.google.com", "drive.usercontent.google.com"):
        return True
    return host == "docs.google.com" and "/uc" in parts.path


def parse_gdrive_url(url: str) -> tuple[str, str] | None:
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


def _content_range(response) -> tuple[int, int | None] | None:
    """(first byte, total size or None) of a 206's "Content-Range: bytes a-b/total"."""
    m = re.match(r"\s*bytes\s+(\d+)-\d+/(\d+|\*)", response.headers.get("content-range", ""), re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)), (int(m.group(2)) if m.group(2).isdigit() else None)


async def _download_gdrive_file(
    client, file_id: str, name_hint: str, directory: Path,
    history: dict[str, str], skip_downloaded: bool,
) -> tuple[str, Exception | None, bool]:
    """Download one drive file. Returns (history key, error, was_skipped).

    A failed attempt keeps "drive-<id>.part"; the next attempt resumes it.
    """
    hkey = f"gdrive:{file_id}"
    if skip_downloaded and hkey in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[hkey]).name}")
        return hkey, None, True

    part = directory / f"drive-{file_id}.part"      # the name is only known once Drive answers
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            import aiofiles
            offset = part.stat().st_size if part.exists() else 0
            url = f"{GDRIVE_DOWNLOAD}?{urlencode({'id': file_id, 'export': 'download', 'confirm': 't'})}"
            page_name = ""
            done = False
            for _step in range(4):             # the file itself, or its confirmation page(s)
                headers = {"Referer": GDRIVE_ROOT + "/", "Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 404:
                        part.unlink(missing_ok=True)
                        raise GDriveError("Google Drive: the file does not exist or is not shared publicly")
                    if offset and response.status_code == 416:     # the part is not a prefix of the file
                        part.unlink(missing_ok=True)
                        offset = 0
                        continue
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

                    # The bytes. 206 continues the part; a plain 200 is the whole file.
                    total: int | None = None
                    length = response.headers.get("content-length", "")
                    if offset and response.status_code == 206:
                        span = _content_range(response)
                        if span is None or span[0] != offset:
                            part.unlink(missing_ok=True)
                            raise _Incomplete("server answered a different range than asked; restarting")
                        total = span[1] or (offset + int(length) if length.isdigit() else None)
                    else:
                        offset = 0
                        total = int(length) if length.isdigit() else None
                    name = (name_hint or _gdrive_filename(response) or page_name
                            or f"drive-{file_id}")
                    dest = directory / safe_filename(name, f"drive-{file_id}")
                    if offset:
                        logger.info("DLOAD", f"Resuming: {dest.name} from {offset} bytes")
                    else:
                        logger.info("DLOAD", f"Downloading: {dest.name}")
                    written = offset
                    async with aiofiles.open(part, "ab" if offset else "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            written += len(chunk)
                            await f.write(chunk)
                    done = True
                    break
            if not done:
                raise GDriveError("Google Drive: stuck on the confirmation page")
            if total is not None and written != total:
                if written > total:
                    part.unlink(missing_ok=True)    # more than the file holds: unusable
                raise _Incomplete(f"incomplete download ({written} of {total} bytes)")
            os.replace(part, dest)
            history[hkey] = str(dest)
            return hkey, None, False
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS and (is_transient(exc) or isinstance(exc, _Incomplete)):
                delay = retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {file_id} — {exc}; retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                continue
            return hkey, exc, False
    return hkey, RuntimeError("retries exhausted"), False  # not reached


async def _gdrive_walk(
    client, folder_id: str, rel: tuple[str, ...], depth: int,
    out: list[tuple[tuple[str, ...], list[tuple[str, str]]]],
) -> None:
    """Collect (relative folder, [(file id, name)]) for a folder and its subfolders."""
    r = await get_with_retry(client, f"{GDRIVE_FOLDER}?{urlencode({'id': folder_id})}",
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
            name = safe_filename(title or f"drive-{fid}", f"drive-{fid}")
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
        await _gdrive_walk(client, sub_id, rel + (safe_filename(sub_name, sub_id),), depth + 1, out)


async def download_gdrive(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). A folder link downloads every file,
    nested folders into subfolders; a file link downloads that one file."""
    parsed = parse_gdrive_url(url)
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
    history_store.save_history(history)
    return ok, skipped, failed
