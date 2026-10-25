"""gofile.io — folders and single files (/d/ID); subfolders go into subdirectories.

A guest account token from POST /accounts, then GET /contents/<id> signed
with a "website token": a sha256 over the user agent, language, account
token, a 4-hour time bucket and a secret ("salt"). Gofile rotates that secret
every few months, so it is NOT pinned: like HeapLeach, it is read out of
gofile's own script (/js/wt.obf.js), where it is hidden in an RC4-encrypted,
shuffled-base64 string table (see _gofile_secret_from). If that fails, set
the GOFILE_SALT environment variable to the current secret. GOFILE_TOKEN may
hold the token of a real account instead of using a throw-away guest account.

Signing badly is worse than not signing: gofile blocks an address that keeps
doing it. So after an "error-notPremium" rejection nothing signed is sent
for GOFILE_REJECTION_COOLDOWN seconds, and the secret is read again after.

An interrupted download keeps its "<name>.part" file; the next attempt (or
run) continues it with a Range request. A server that ignores Range sends the
whole file again, which replaces the part. The body is checked against the
size the server announced before the part is renamed into place.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from kkafio.core.logger import logger
from kkafio.core.paths import CONFIG_DIR
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.net import RETRY_ATTEMPTS, get_with_retry, is_transient, resolve_filename, retry_delay, safe_filename

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
GOFILE_STATE_FILE  = CONFIG_DIR / "gofile_state.json"   # guest token + last rejection, kept between runs
GOFILE_TOKEN_TTL   = 10800            # reuse a guest account this long (s); gofile allows ~6h
GOFILE_PARALLEL    = 4                # files downloaded at once from gofile
GOFILE_BUSY_BASE   = 5                # first wait when a storage server is busy (s), then doubling
GOFILE_BUSY_MAX    = 60
GOFILE_BUSY_LIMIT  = 6                # busy answers tolerated per file before giving up


def is_gofile_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ("gofile.io", "www.gofile.io")


def parse_gofile_url(url: str) -> tuple[str, str | None] | None:
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
    rejected_at = max(cache["rejected_at"], float(_gofile_load_state().get("rejected_at") or 0))
    since = now - rejected_at
    if rejected_at and since < GOFILE_REJECTION_COOLDOWN:
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


def _gofile_load_state() -> dict:
    try:
        data = json.loads(GOFILE_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _gofile_save_state(**updates) -> None:
    """Merge `updates` into the state file (best effort: it is only an optimisation)."""
    try:
        state = _gofile_load_state()
        state.update(updates)
        GOFILE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = GOFILE_STATE_FILE.with_name(GOFILE_STATE_FILE.name + ".part")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, GOFILE_STATE_FILE)
    except OSError as e:
        logger.warning("DLOAD", f"  Could not save Gofile state: {e}")


def _gofile_signature_rejected() -> None:
    """Drop the secret and start the cooldown (the next try re-reads the script).
    The cooldown is also written to disk, so restarting the app doesn't end it."""
    now = time.time()
    _gofile_secret_cache.update(secret="", until=0.0, rejected_at=now)
    _gofile_save_state(rejected_at=now)


def _gofile_website_token(api_token: str, secret: str) -> str:
    bucket = int(time.time() // GOFILE_TIME_BUCKET)
    raw = f"{GOFILE_USER_AGENT}::{GOFILE_LANG}::{api_token}::{bucket}::{secret}"
    return hashlib.sha256(raw.encode()).hexdigest()


class GofileStatusError(RuntimeError):
    """A Gofile API error status, kept so callers can react to specific ones."""

    def __init__(self, message: str, status: str | None):
        super().__init__(message)
        self.status = status


_GOFILE_STALE_TOKEN_STATUSES = ("error-token", "error-wrongToken")


def _gofile_status_message(status: str | None) -> str:
    return {
        "error-notFound": "Gofile content not found (deleted or expired).",
        "error-passwordRequired":
            "This Gofile folder is password protected — add ?password=... to the link.",
        "error-passwordWrong": "Wrong Gofile folder password.",
        "error-token": "Gofile rejected the account token.",
        "error-wrongToken": "Gofile rejected the account token.",
        "error-notPremium":
            "Gofile rejected the request signature (error-notPremium). The signing secret "
            "read from gofile's script was refused; it will be read again after a short "
            "cooldown. Set the GOFILE_SALT environment variable to override it.",
    }.get(status or "", f"Gofile API error: {status}")


async def _gofile_token(client) -> str:
    """The account token: GOFILE_TOKEN, one already used by this client, a guest
    account kept from an earlier run (they stay valid for hours, and minting
    them in bulk is something gofile watches for), or a fresh guest account.
    It is also set as the accountToken cookie, which gofile's file servers want
    on the download requests."""
    token = os.environ.get("GOFILE_TOKEN") or client.cookies.get("accountToken", domain=GOFILE_DOMAIN)
    if not token:
        state = _gofile_load_state()
        saved = state.get("token")
        if isinstance(saved, str) and saved and time.time() - float(state.get("token_at") or 0) < GOFILE_TOKEN_TTL:
            token = saved
    if not token:
        r = await client.post(f"{GOFILE_API}/accounts")
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            raise RuntimeError(_gofile_status_message(data.get("status")))
        token = (data.get("data") or {}).get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Gofile guest account response carries no token.")
        _gofile_save_state(token=token, token_at=time.time())
    client.cookies.set("accountToken", token, domain=GOFILE_DOMAIN)
    return token


def _gofile_forget_token(client) -> None:
    """Drop a token gofile no longer accepts, so the next _gofile_token mints one."""
    _gofile_save_state(token="", token_at=0)
    try:
        client.cookies.delete("accountToken", domain=GOFILE_DOMAIN)
    except Exception:
        pass


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
        r = await get_with_retry(client, f"{GOFILE_API}{path}?{urlencode(params)}", headers)
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
            raise GofileStatusError(_gofile_status_message(status), status) from exc
        raise
    data = r.json()
    if data.get("status") != "ok":
        if data.get("status") == "error-notPremium":
            _gofile_signature_rejected()
        raise GofileStatusError(_gofile_status_message(data.get("status")), data.get("status"))
    return data.get("data")


async def _gofile_walk(
    client, content_id: str, api_token: str, pw_hash: str | None,
    rel: tuple[str, ...], depth: int, out: list[tuple[tuple[str, ...], list[tuple[str, list[str]]]]],
) -> None:
    """Collect (relative folder, file links) for a folder and its subfolders."""
    files: list[tuple[str, list[str]]] = []
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
        if data.get("type") == "file":
            # A /d/ link can name one file: its entry is the answer, with no "children".
            link, servers = data.get("link"), data.get("servers")
            if not isinstance(link, str) or not link:
                raise RuntimeError("Gofile: this link names a file but carries no download link.")
            out.append((rel, [(link, [x for x in servers if isinstance(x, str) and x]
                                if isinstance(servers, list) else [])]))
            return
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
                servers = entry.get("servers")
                files.append((entry["link"], [x for x in servers if isinstance(x, str) and x]
                              if isinstance(servers, list) else []))
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
                           rel + (safe_filename(sub_name, sub_id),), depth + 1, out)


async def download_gofile(
    client, url: str, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). Files of the linked folder go
    straight into `directory`; files of nested folders into subfolders named
    after them, so same-named files never overwrite each other."""
    parsed = parse_gofile_url(url)
    if parsed is None:
        logger.error("DLOAD", f"Unsupported Gofile link (use gofile.io/d/ID): {url}")
        return 0, 0, 1
    content_id, password = parsed
    logger.info("DLOAD", f"Scraping Gofile: {content_id}")

    api_token = await _gofile_token(client)
    pw_hash = hashlib.sha256(password.encode()).hexdigest() if password else None

    folders: list[tuple[tuple[str, ...], list[tuple[str, list[str]]]]] = []
    try:
        await _gofile_walk(client, content_id, api_token, pw_hash, (), 0, folders)
    except GofileStatusError as e:
        # A token kept from an earlier run may have expired: mint one, once.
        if e.status not in _GOFILE_STALE_TOKEN_STATUSES or os.environ.get("GOFILE_TOKEN"):
            raise
        logger.info("DLOAD", "  Gofile account token expired — creating a new guest account.")
        _gofile_forget_token(client)
        api_token = await _gofile_token(client)
        folders.clear()
        await _gofile_walk(client, content_id, api_token, pw_hash, (), 0, folders)
    logger.info("DLOAD", f"  Found {sum(len(f) for _, f in folders)} file(s).")

    sem = asyncio.Semaphore(GOFILE_PARALLEL)

    async def _one(link: str, servers: list[str], target: Path):
        async with sem:
            return await _download_gofile_file(client, link, servers, target, history, skip)

    jobs = []
    for rel, files in folders:
        if not files:
            continue
        target = directory.joinpath(*rel)
        target.mkdir(parents=True, exist_ok=True)
        jobs += [_one(link, servers, target) for link, servers in files]

    ok = skipped = failed = 0
    for coro in asyncio.as_completed(jobs):
        link, err, was_skipped = await coro
        if err:
            failed += 1
            logger.error("DLOAD", f"  Failed: {link} — {err}")
        elif was_skipped:
            skipped += 1
        else:
            ok += 1
    history_store.save_history(history)
    return ok, skipped, failed


class _GofileBusy(Exception):
    """A storage server answered with a web page instead of the file."""


class _GofileIncomplete(Exception):
    """The body ended before the announced size: worth another (resumed) try."""


class _GofileRestart(Exception):
    """The part cannot be continued and was removed: start again, no retry used."""


def _content_range(response) -> tuple[int, int | None] | None:
    """(first byte, total size or None) of a 206's "Content-Range: bytes a-b/total"."""
    m = re.match(r"\s*bytes\s+(\d+)-\d+/(\d+|\*)", response.headers.get("content-range", ""), re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)), (int(m.group(2)) if m.group(2).isdigit() else None)


def _gofile_mirrors(link: str, servers: list[str]) -> list[str]:
    """The link, then the same path on each other storage server holding the file."""
    parts = urlparse(link)
    out = [link]
    for server in servers:
        mirror = urlunparse(parts._replace(netloc=f"{server}.gofile.io"))
        if mirror not in out:
            out.append(mirror)
    return out


async def _download_gofile_file(
    client, link: str, servers: list[str], directory: Path,
    history: dict[str, str], skip_downloaded: bool,
) -> tuple[str, Exception | None, bool]:
    """Download one gofile file. Returns (link, error, was_skipped).

    A busy storage server doesn't refuse: it redirects to gofile's web page,
    which arrives as a 200 of HTML. That must not be saved as the file (and
    recorded as downloaded). Instead the next mirror is tried after a growing
    wait; those waits don't use up the retry budget, but are capped.

    A failed attempt keeps its ".part" file; the next attempt resumes it.
    """
    if skip_downloaded and link in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[link]).name}")
        return link, None, True

    mirrors = _gofile_mirrors(link, servers)
    default_name = _gofile_default_name(link)
    part = directory / (safe_filename(default_name, "gofile-file") + ".part")   # same on every mirror
    busy = 0
    attempt = 1
    while attempt <= RETRY_ATTEMPTS:
        url = mirrors[busy % len(mirrors)]
        try:
            import aiofiles
            offset = part.stat().st_size if part.exists() else 0
            headers = {"Referer": "https://gofile.io/", "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            async with client.stream("GET", url, headers=headers) as response:
                if offset and response.status_code == 416:     # the part is not a prefix of the file
                    part.unlink(missing_ok=True)
                    raise _GofileRestart()
                response.raise_for_status()
                filename = resolve_filename(response, url, default_name)
                if ("text/html" in response.headers.get("content-type", "").lower()
                        and Path(filename).suffix.lower() not in (".html", ".htm")):
                    raise _GofileBusy()

                # The bytes. 206 continues the part; a plain 200 is the whole file.
                total: int | None = None
                length = response.headers.get("content-length", "")
                if offset and response.status_code == 206:
                    span = _content_range(response)
                    if span is None or span[0] != offset:
                        part.unlink(missing_ok=True)
                        raise _GofileIncomplete("server answered a different range than asked; restarting")
                    total = span[1] or (offset + int(length) if length.isdigit() else None)
                else:
                    offset = 0
                    total = int(length) if length.isdigit() else None
                dest = directory / filename
                if offset:
                    logger.info("DLOAD", f"Resuming: {filename} from {offset} bytes")
                else:
                    logger.info("DLOAD", f"Downloading: {filename}")
                written = offset
                async with aiofiles.open(part, "ab" if offset else "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        written += len(chunk)
                        await f.write(chunk)
            if total is not None and written != total:
                if written > total:
                    part.unlink(missing_ok=True)    # more than the file holds: unusable
                raise _GofileIncomplete(f"incomplete download ({written} of {total} bytes)")
            os.replace(part, dest)
            history[link] = str(dest)
            return link, None, False
        except _GofileRestart:
            continue                                # the part is gone; this try was not used up
        except _GofileBusy:
            busy += 1
            if busy > GOFILE_BUSY_LIMIT:
                return link, RuntimeError(
                    "gofile's storage server kept answering with a web page instead of the file "
                    "(it is busy); try again later"), False
            wait = min(GOFILE_BUSY_BASE * 2 ** (busy - 1), GOFILE_BUSY_MAX)
            logger.warning("DLOAD",
                f"  {default_name} — storage server busy; waiting {wait}s then trying "
                f"{'another server' if len(mirrors) > 1 else 'again'} ({busy}/{GOFILE_BUSY_LIMIT})")
            await asyncio.sleep(wait)
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS and (is_transient(exc) or isinstance(exc, _GofileIncomplete)):
                delay = retry_delay(exc, attempt)
                logger.warning("DLOAD",
                    f"  {link} — {exc}; retrying in {delay:.0f}s ({attempt}/{RETRY_ATTEMPTS - 1})")
                await asyncio.sleep(delay)
                attempt += 1
                continue
            if getattr(getattr(exc, "response", None), "status_code", None) in (404, 410):
                part.unlink(missing_ok=True)        # the file is gone: nothing to resume later
            return link, exc, False
    return link, RuntimeError("retries exhausted"), False  # not reached


def _gofile_default_name(url: str) -> str:
    return Path(unquote(urlparse(url).path)).name
