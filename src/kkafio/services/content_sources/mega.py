"""mega.nz — files and folders.

Port of HeapLeach's mega extractor. Mega encrypts everything client-side
under the key in the link's #fragment: names, sizes and the file bytes. So
a link must include its key, filenames are decrypted here, and the payload
is AES-CTR decrypted while it streams to disk.

  https://mega.nz/file/<handle>#<key>                  one file
  https://mega.nz/folder/<handle>#<key>                a whole folder
  https://mega.nz/folder/<handle>#<key>/file/<node>    one file of a folder
  https://mega.nz/#!<handle>!<key>, #F!<handle>!<key>  legacy shapes

Download links are minted per request (they are tied to one storage node),
so each is requested right before its file starts. Mega meters free
transfer per IP address; when the quota is used up the API (error -17) or
the storage server (HTTP 509) says how long the wait is, and the file is
reported as failed with that wait rather than retried pointlessly.
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
            parts.insert(0, safe_filename(parent["name"] or handle, handle))
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
        files.append({"handle": h, "name": safe_filename(d["name"] or h, h),
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
            if attempt < RETRY_ATTEMPTS and (is_transient(exc) or isinstance(exc, IOError)):
                delay = retry_delay(exc, attempt)
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


async def download_mega(
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
        name = safe_filename(str(attrs.get("n") or ""), link["handle"])
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
    history_store.save_history(history)
    return ok, skipped, failed
