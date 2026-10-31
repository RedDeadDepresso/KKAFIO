"""mega.nz — files and folders.

Port of HeapLeach's mega extractor. Mega encrypts everything client-side
under the key in the link's #fragment: names, sizes and the file bytes. So
a link must include its key, filenames are decrypted here, and the payload
is AES-CTR decrypted while it streams to disk.

  https://mega.nz/file/<handle>#<key>                  one file
  https://mega.nz/folder/<handle>#<key>                a whole folder
  https://mega.nz/folder/<handle>#<key>/file/<node>    one file of a folder
  https://mega.nz/folder/<handle>#<key>/folder/<node>  one subfolder of a folder (and what is in it)
  https://mega.nz/#!<handle>!<key>, #F!<handle>!<key>  legacy shapes

Download links are minted per request (they are tied to one storage node),
so each is requested right before its file starts. Mega meters free
transfer per IP address; when the quota is used up the API (error -17) or
the storage server (HTTP 509) says how long the wait is, and the file is
reported as failed with that wait rather than retried pointlessly; the
remaining files of the run are then not attempted either.

An interrupted download keeps its "<name>.<key id>.part" file and the next
attempt (or run) continues it with a Range request: AES-CTR is seekable, so
the counter is simply set to the resume offset (rounded down to a 16-byte
block). A server that ignores the Range header gets the file from the start.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse

from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.net import RETRY_ATTEMPTS, is_transient, retry_delay, safe_filename

MEGA_API            = "https://g.api.mega.co.nz/cs"
MEGA_ROOT           = "https://mega.nz"
MEGA_FILE_KEY_LEN   = 32
MEGA_FOLDER_KEY_LEN = 16
MEGA_MAX_DEPTH      = 8
MEGA_PARALLEL       = 3                 # files at once (mega meters free transfer per address)
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


def is_mega_url(url: str) -> bool:
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
    """{"folder", "handle", "key" (bytes), "node"} of a public mega link. "node" is the file or
    subfolder of a folder link the link points at ("" for everything).

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
            if kind in ("file", "folder"):      # a file, or a subfolder (its whole subtree)
                node = handle.strip("/")
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


def _mega_ctr_decryptor(key: bytes, nonce: bytes, offset: int = 0):
    """Streaming AES-CTR decryptor positioned at byte `offset` of the file.

    The counter (the 8 bytes after the nonce) counts 16-byte blocks, so a
    resume offset must be a multiple of 16.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if offset < 0 or offset % 16:
        raise ValueError("resume offset must be a multiple of 16 bytes")
    return Cipher(algorithms.AES(key), modes.CTR(nonce + (offset // 16).to_bytes(8, "big"))).decryptor()


def _mega_part_path(dest: Path, key: bytes) -> Path:
    """Partial-download file of `dest`. The key id in its name keeps a part of
    one file from being continued as another file's content (names repeat)."""
    return dest.with_name(f"{dest.name}.{key[:4].hex()}.part")


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
    {"handle", "name", "dir" (tuple of folder names), "size", "key", "nonce"}.

    `only_node` limits the result to one file, or to every file under one subfolder; the
    subfolder's own name is then the first element of each file's "dir" (so it lands in a
    folder of that name), and the folders above it are left out."""
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
            parts.insert(0, safe_filename(parent["name"] or handle, handle))
            handle = parent["node"].get("p")
        return tuple(parts)

    def is_under(d: dict, ancestor: str) -> bool:
        seen: set[str] = set()
        handle = d["node"].get("p")
        while handle and handle in dec and handle not in seen:
            if handle == ancestor:
                return True
            seen.add(handle)
            handle = dec[handle]["node"].get("p")
        return False

    selected = dec.get(only_node) if only_node else None
    subtree = selected is not None and selected["node"].get("t") != MEGA_TYPE_FILE
    # Folders above the selected subfolder (the share root is never part of a "dir").
    strip = len(dir_of(selected)) if subtree else 0

    files = []
    for h, d in dec.items():
        n = d["node"]
        if n.get("t") != MEGA_TYPE_FILE or d["key"] is None:
            continue
        if only_node and (not is_under(d, only_node) if subtree else h != only_node):
            continue
        try:
            key, nonce = _mega_file_key(d["key"])
        except MegaError:
            continue
        files.append({"handle": h, "name": safe_filename(d["name"] or h, h),
                      "dir": dir_of(d)[strip:], "size": int(n.get("s") or 0),
                      "key": key, "nonce": nonce})
    return title, files


# --- download --------------------------------------------------------------

async def _download_mega_file(
    client, file: dict, share_handle: str, directory: Path,
    history: dict[str, str], skip_downloaded: bool,
) -> tuple[str, Exception | None, bool]:
    """Download + decrypt one mega file. Returns (history key, error, was_skipped).

    A failed attempt keeps its ".part" file; the next attempt resumes it.
    """
    hkey = f"mega:{share_handle or '-'}:{file['handle']}"
    if skip_downloaded and hkey in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[hkey]).name}")
        return hkey, None, True

    dest = directory / file["name"]
    part = _mega_part_path(dest, file["key"])
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            import aiofiles
            dl = await _mega_link_for(client, file["handle"], folder=share_handle)
            expected = file["size"] or int(dl.get("s") or 0)

            # Resume from the whole 16-byte blocks already on disk.
            offset = 0
            if part.exists():
                have = part.stat().st_size
                offset = have - have % 16
                if expected and offset >= expected:
                    offset = 0                  # not a prefix of this file: start over
                if offset != have:
                    with open(part, "r+b") as fh:
                        fh.truncate(offset)

            headers = {"Range": f"bytes={offset}-"} if offset else {}
            async with client.stream("GET", dl["g"], headers=headers) as response:
                if response.status_code == 509:
                    left = (response.headers.get("X-MEGA-Time-Left") or "").strip()
                    raise MegaError(_mega_api_message(-17) + (f" (about {left}s left)" if left.isdigit() else ""))
                if offset and response.status_code == 416:      # range not satisfiable
                    part.unlink(missing_ok=True)
                    raise IOError("resume offset rejected by the server; restarting")
                response.raise_for_status()
                if offset and response.status_code != 206:      # Range ignored: whole file follows
                    offset = 0
                decryptor = _mega_ctr_decryptor(file["key"], file["nonce"], offset)
                written = offset
                if offset:
                    logger.info("DLOAD", f"Resuming: {file['name']} from {offset} bytes")
                else:
                    logger.info("DLOAD", f"Downloading: {file['name']}")
                async with aiofiles.open(part, "ab" if offset else "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        plain = decryptor.update(chunk)
                        written += len(plain)
                        await f.write(plain)
                    tail = decryptor.finalize()
                    if tail:
                        written += len(tail)
                        await f.write(tail)
            if expected and written != expected:
                if written > expected:
                    part.unlink(missing_ok=True)    # more than the file holds: unusable
                raise IOError(f"incomplete download ({written} of {expected} bytes)")
            os.replace(part, dest)
            history[hkey] = str(dest)
            return hkey, None, False
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS and (is_transient(exc) or isinstance(exc, IOError)):
                delay = retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {file['name']} — {exc}; retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                continue
            return hkey, exc, False
    return hkey, RuntimeError("retries exhausted"), False  # not reached


async def download_mega(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). A folder link downloads every file,
    nested folders into subfolders (one with a /file/ or /folder/ part: only that file or
    subfolder); a file link downloads that one file."""
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
            raise MegaError("Mega: the selected file or folder is not in this link, or holds no readable files."
                            if link["node"] else "Mega: folder holds no readable files.")
        share = link["handle"]
    else:
        key, nonce = _mega_file_key(link["key"])
        info = await _mega_api(client, {"a": "g", "p": link["handle"]})
        try:
            attrs = _mega_decrypt_attrs(str(info.get("at") or ""), key)
        except Exception as e:
            raise MegaError(f"Mega: {e}") from e
        name = safe_filename(str(attrs.get("n") or ""), link["handle"])
        files = [{"handle": link["handle"], "name": name, "dir": (),
                  "size": int(info.get("s") or 0), "key": key, "nonce": nonce}]
        share = ""
    logger.info("DLOAD", f"  Found {len(files)} file(s).")

    sem = asyncio.Semaphore(MEGA_PARALLEL)
    quota_hit = asyncio.Event()             # one file ran into the quota: the rest would too

    async def _one(f: dict):
        target = directory.joinpath(*f["dir"])
        target.mkdir(parents=True, exist_ok=True)
        async with sem:
            if quota_hit.is_set():
                return f, (f"mega:{share or '-'}:{f['handle']}",
                           MegaError("Mega: not attempted, the download quota is already used up"), False)
            result = await _download_mega_file(client, f, share, target, history, skip)
            if result[1] is not None and "quota exceeded" in str(result[1]):
                quota_hit.set()
            return f, result

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
    history_store.save_history(history)
    return ok, skipped, failed
