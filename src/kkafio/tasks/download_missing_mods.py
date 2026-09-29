"""
download_missing_mods.py — Find and download mods that characters reference
but are not present in the input / output mods directories.

Strategy
--------
1. Build / load the mods cache of the input mods directory and of the output
   mods directory (both default to the game's mods directory when left
   blank; if they resolve to the same folder it is only scanned once).
2. Build / load the chara GUID cache (all GUIDs referenced by chara cards).
3. missing_local = referenced_guids - input_mods_guids - output_mods_guids
4. Sideloader Modpack mode:
     Skip     — ignore modpack GUIDs entirely (only download local-only mods)
     OnlyUsed — download missing GUIDs that are in the modpack index or on
                the KKC mod index / Telegram Chat Links
     All      — also download every GUID in the modpack index not installed
5. For each GUID to download:
     a) In modpack index → BetterRepack (httpx, no auth)
     b) Not in index, Telegram Source is KoikatsuCards/Both → look up the
        GUID in kkc_mod_index.json (kkc-mod-scraper; cached in CONFIG_DIR and
        refreshed when the repo's latest commit changes), then download the
        linked t.me message directly from Telegram
     c) Not in index (or the KKC mod index had no link / the download
        failed), Telegram Source is ChatLinks/Both → search each
        configured Telegram Chat Links entry (channel, group, or forum
        topic) via Telegram's server-side document search, and download
        the first result whose filename ends in .zipmod or .zip
     d) Otherwise → log as unresolved
6. Everything is downloaded into the OUTPUT mods directory (which also
   receives the report file).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from kkafio.tasks.base_task import BaseTask
from kkafio.cards.chara_ops import (
    build_mods_cache,
    collect_chara_guids,
    collect_coord_guids,
    collect_scene_guids,
    guid_from_zipmod,
    load_modpack_index,
)
from kkafio.core.config import GameType
from kkafio.core.logger import logger
import kkafio.services.telegram_config as tg_cfg

BETTERREPACK_BASE     = "https://sideload.betterrepack.com/download/KKEC"
KKC_INDEX_URL         = "https://reddeaddepresso.github.io/kkc-mod-scraper/kkc_mod_index.json"
KKC_COMMITS_API       = "https://api.github.com/repos/RedDeadDepresso/kkc-mod-scraper/commits"
MAX_CONNECTIONS       = 4


# ---------------------------------------------------------------------------
# HTTP client (for BetterRepack + kkc-mod-scraper index)
# ---------------------------------------------------------------------------

def _make_http_client(cookies: dict | None = None):
    import httpx
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=MAX_CONNECTIONS,
            max_keepalive_connections=MAX_CONNECTIONS,
        ),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
        cookies=cookies or {},
        follow_redirects=True,
        timeout=60,
        http2=True,
    )


# ---------------------------------------------------------------------------
# BetterRepack download
# ---------------------------------------------------------------------------

async def _download_betterrepack(
    client, guid: str, rel_path: str, mods_dir: Path,
    guid_str_map: dict[str, str],
) -> bool | str:
    """Download one modpack zipmod. Returns True (downloaded), "skipped"
    (already present and intact) or False (failed)."""
    import zipfile

    url  = f"{BETTERREPACK_BASE}/{rel_path.replace(chr(92), '/')}"
    dest = mods_dir / rel_path.replace("\\", os.sep).replace("/", os.sep)

    # Only trust an existing file if it is a complete zip. Earlier versions
    # streamed straight into the final name, so an interrupted download could
    # leave a truncated .zipmod behind that would otherwise be "skipped" forever.
    if dest.exists():
        if await asyncio.to_thread(zipfile.is_zipfile, dest):
            return "skipped"
        logger.warning("DLMOD", f"Existing file is incomplete/corrupt, re-downloading: {dest.name}")

    logger.info("DLMOD", f"BetterRepack ↓ {dest.name}")
    # Download to "<name>.part" and rename only once the body is complete.
    part = dest.with_name(dest.name + ".part")
    completed = False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            import aiofiles
            async with aiofiles.open(part, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    await f.write(chunk)
        os.replace(part, dest)
        completed = True
        logger.success("DLMOD", f"Downloaded: {dest.name}")
        guid_str_map[guid] = str(dest)
        return True
    except Exception as e:
        logger.error("DLMOD", f"BetterRepack failed [{guid}]: {e}")
        return False
    finally:
        if not completed:
            # Errors and cancellation (Stop / Ctrl+C) alike.
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# kkc-mod-scraper index (GUID -> Telegram link)
# ---------------------------------------------------------------------------

async def _fetch_latest_index_commit(client) -> str | None:
    """Return the SHA of the latest commit of the kkc-mod-scraper repo, or
    None if it couldn't be determined."""
    try:
        r = await client.get(
            KKC_COMMITS_API,
            params={"per_page": 1},
            headers={"Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        data = r.json()
        return data[0]["sha"].strip() if data else None
    except Exception as e:
        logger.warning("DLMOD", f"Could not fetch latest kkc-mod-scraper commit: {e}")
        return None


def _load_cached_kkc_index(index_path: Path) -> dict[str, str] | None:
    """Load the cached kkc_mod_index.json. None if missing or unreadable."""
    import json
    if not index_path.exists():
        return None
    try:
        with index_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("DLMOD", "Cached kkc_mod_index.json is not a JSON object.")
    except Exception as e:
        logger.warning("DLMOD", f"Could not load cached kkc_mod_index.json: {e}")
    return None


async def _load_kkc_mod_index(client) -> dict[str, str]:
    """Return the {guid: t.me link} index from kkc-mod-scraper.

    The cached copy (CONFIG_DIR/config/kkc_mod_index.json) is used only if
    the commit recorded in kkc_mod_index_last_commit.txt matches the repo's
    latest commit and the cached file loads successfully. Otherwise the
    index is downloaded again and the commit file is updated.

    Returns an empty dict if the index is unavailable.
    """
    import json
    from kkafio.core.constants import CONFIG_DIR

    cfg_dir     = CONFIG_DIR / "config"
    index_path  = cfg_dir / "kkc_mod_index.json"
    commit_path = cfg_dir / "kkc_mod_index_last_commit.txt"

    latest_commit = await _fetch_latest_index_commit(client)

    saved_commit = ""
    try:
        if commit_path.exists():
            saved_commit = commit_path.read_text(encoding="utf-8").strip()
    except OSError:
        pass

    if latest_commit and saved_commit == latest_commit:
        cached = _load_cached_kkc_index(index_path)
        if cached is not None:
            logger.info("DLMOD", f"kkc mod index up to date: {len(cached)} GUIDs (cached)")
            return cached
        logger.info("DLMOD", "kkc mod index cache missing or unreadable — downloading...")
    elif latest_commit is None:
        # Can't tell whether the cache is current; use it rather than fail.
        cached = _load_cached_kkc_index(index_path)
        if cached is not None:
            logger.warning("DLMOD",
                "Could not check for kkc mod index updates — using cached copy.")
            return cached
    else:
        logger.info("DLMOD", "kkc mod index is new or outdated — downloading...")

    try:
        r = await client.get(KKC_INDEX_URL)
        r.raise_for_status()
        index = r.json()
        if not isinstance(index, dict):
            raise ValueError("index is not a JSON object")
    except Exception as e:
        logger.error("DLMOD", f"Failed to download kkc mod index: {e}")
        return {}

    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        tmp = index_path.with_name(index_path.name + ".part")
        tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, index_path)
        if latest_commit:
            commit_path.write_text(latest_commit, encoding="utf-8")
    except OSError as e:
        logger.warning("DLMOD", f"Could not save kkc mod index cache: {e}")

    logger.info("DLMOD", f"kkc mod index downloaded: {len(index)} GUIDs")
    return index


# ---------------------------------------------------------------------------
# Telethon download
# ---------------------------------------------------------------------------

def _parse_tme_link(tg_link: str) -> tuple[str, int] | None:
    """
    Parse a t.me link and return (chat_identifier, message_id).

    Supported formats:
      https://t.me/KK_archive_modlibrary/6157   → ("@KK_archive_modlibrary", 6157)
      https://t.me/c/1234567890/6157             → (-1001234567890, 6157)
    """
    m = re.match(r"https?://t\.me/(?:c/(\d+)|([A-Za-z0-9_]+))/(\d+)", tg_link)
    if not m:
        return None
    numeric_id, username, msg_id = m.group(1), m.group(2), m.group(3)
    chat = int(f"-100{numeric_id}") if numeric_id else f"@{username}"
    return chat, int(msg_id)


# ---------------------------------------------------------------------------
# Telegram Chat Links — direct server-side search fallback
# ---------------------------------------------------------------------------

DEFAULT_TELEGRAM_CHAT_LINKS = (
    "# private - need to join it\n"
    "# invite link: https://t.me/+td__jctzxic0NGVi\n"
    "https://t.me/c/2549022984\n"
    "\n"
    "# public - no need to join them\n"
    "https://t.me/KK_archive_modlibrary\n"
    "https://t.me/KKDOC\n"
    "https://t.me/koikatu_card_download\n"
    "https://t.me/kknowcc"
)

_TELEGRAM_SOURCE_LABELS = {
    "No":            "No",
    "KoikatsuCards": "koikatsucards.com",
    "ChatLinks":     "Telegram Chat Links",
    "Both":          "koikatsucards.com + Telegram Chat Links",
}


def _telegram_source_label(source: str) -> str:
    return _TELEGRAM_SOURCE_LABELS.get(source, source)


# A chat/channel/group link, optionally pointing at a specific forum topic:
#   https://t.me/kknowcc                → username, no topic
#   https://t.me/somepublicforum/123    → username, topic 123
#   https://t.me/c/2549022984           → private/numeric id, no topic
#   https://t.me/c/2549022984/299       → private/numeric id, topic 299
_CHAT_LINK_RE = re.compile(
    r"^https?://t\.me/(?:c/(?P<numeric_id>\d+)|(?P<username>[A-Za-z0-9_]+))"
    r"(?:/(?P<topic_id>\d+))?/?$"
)


def _parse_chat_link_for_search(link: str) -> tuple[str | int, int | None] | None:
    """Parse a single Telegram Chat Links entry into (chat, topic_id).

    `chat` is either an "@username" string or a signed numeric channel ID
    (both of which Telethon's get_input_entity() accepts directly).
    `topic_id` is None for a plain channel/group/chat, or the forum topic's
    message ID if the link points at a specific topic thread.
    """
    m = _CHAT_LINK_RE.match(link)
    if not m:
        return None
    numeric_id, username, topic_str = m.group("numeric_id"), m.group("username"), m.group("topic_id")
    chat = int(f"-100{numeric_id}") if numeric_id else f"@{username}"
    topic_id = int(topic_str) if topic_str else None
    return chat, topic_id


def _parse_chat_links(raw: str) -> list[tuple[str | int, int | None]]:
    """Parse the multi-line Telegram Chat Links textbox into an ordered list
    of (chat, topic_id) tuples.

    Blank lines and lines starting with '#' are ignored entirely; a
    trailing '# comment' after a link on the same line is stripped before
    parsing, so the shipped default value's "# you need to be part of this
    chat" notes don't need to be removed by the user.
    """
    links: list[tuple[str | int, int | None]] = []
    for raw_line in raw.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parsed = _parse_chat_link_for_search(line)
        if parsed:
            links.append(parsed)
        else:
            logger.warning("DLMOD", f"Could not parse Telegram chat link: {raw_line.strip()}")
    return links


async def _retry_flood_wait(coro_fn, *args, context: str, max_wait: int = 300, **kwargs):
    """Call `coro_fn(*args, **kwargs)`, retrying once (after actually waiting)
    if Telegram responds with a FloodWaitError.

    `FloodWaitError.seconds` is the exact time this account must wait before
    the *same kind* of request will succeed again — retrying immediately, or
    waiting less than that, tends to trip another (often longer) flood-wait
    rather than clearing it, which is how a single rate-limit turns into a
    much longer restriction over the course of a run with many GUIDs/chats.

    If the required wait exceeds `max_wait`, we give up and re-raise instead
    of blocking the whole DownloadMissingMods run for an unbounded amount of
    time; callers already treat a raised/caught exception here as "try the
    next candidate/chat", which is the right behavior in that case too.

    Only used for the plain Telethon calls in this module (chat search,
    entity resolution, the no-teleget9527 download fallback) — teleget9527
    already handles FloodWaitError internally for its own `.download()` calls.
    """
    from telethon.errors import FloodWaitError

    try:
        return await coro_fn(*args, **kwargs)
    except FloodWaitError as e:
        wait_s = e.seconds + 1  # small margin, same convention Telethon itself uses
        if wait_s > max_wait:
            logger.warning("DLMOD",
                f"    Telegram flood-wait on {context}: {wait_s}s required, "
                f"exceeds the {max_wait}s cap — skipping instead of blocking the run.")
            raise
        logger.warning("DLMOD",
            f"    Telegram is rate-limiting {context} — waiting {wait_s}s before retrying...")
        await asyncio.sleep(wait_s)
        return await coro_fn(*args, **kwargs)


async def _search_chat_for_zipmod(client, chat: str | int, topic_id: int | None, guid: str) -> list:
    """Search a Telegram chat (channel, group, or forum topic) for
    documents matching `guid`, using Telegram's server-side search — this is
    the same raw API call Telegram Desktop itself sends, so it works
    instantly without downloading/scanning message history client-side.

    Returns every matching Telethon Message (in the order Telegram's search
    returned them) whose attached document's filename ends with
    '.zipmod' or '.zip' (some mods are shared without ever being renamed
    to '.zipmod' — the downloaded file's manifest.xml is what actually
    gets verified afterward, this filter just narrows the candidates), or
    an empty list if nothing matched (including if the chat can't be
    resolved / searched at all, e.g. not a member).

    A text search for a GUID can turn up several results in the same chat
    (re-uploads, unrelated files that happen to mention the GUID in a
    caption, etc.), so the caller tries them in order — downloading one,
    checking its actual in-zip GUID, and moving on to the next candidate if
    it turns out to be a mismatch — rather than trusting the first hit.
    """
    from telethon.tl.functions.messages import SearchRequest
    from telethon.tl.types import InputMessagesFilterDocument

    try:
        peer = await _retry_flood_wait(
            client.get_input_entity, chat, context=f"resolving chat {chat}")
    except Exception as e:
        logger.warning("DLMOD", f"    Could not resolve chat {chat}: {e}")
        return []

    try:
        result = await _retry_flood_wait(
            client, SearchRequest(
                peer=peer,
                q=guid,                                  # Text search term
                filter=InputMessagesFilterDocument(),     # Server-side DOCUMENT filter
                top_msg_id=topic_id or 0,                 # Server-side TOPIC filter, 0 if none
                min_date=None,
                max_date=None,
                offset_id=0,
                add_offset=0,
                limit=100,
                max_id=0,
                min_id=0,
                hash=0,
            ),
            context=f"searching {chat} for {guid}")
    except Exception as e:
        logger.warning("DLMOD", f"    Search failed in {chat}: {e}")
        return []

    matches = []
    for message in getattr(result, "messages", []):
        doc = getattr(message, "document", None)
        if not doc:
            continue
        file_name = None
        for attr in doc.attributes:
            fn = getattr(attr, "file_name", None)
            if fn:
                file_name = fn
                break
        if file_name and file_name.lower().endswith(".zipmod"):
            matches.append(message)

    return matches


async def _search_chat_links_and_download(
    guid: str,
    chat_links: list[tuple[str | int, int | None]],
    mods_dir: Path,
    tg_data: dict,
    guid_str_map: dict[str, str],
    client,
    downloader=None,
    rel_path: str | None = None,
) -> tuple[bool, bool | str]:
    """Search each configured Telegram Chat Links entry, in order, for a
    .zipmod attachment matching `guid`. Moves on to the next chat if the
    current one has no match (not a member, chat doesn't exist, or nothing
    found).

    A chat's server-side text search can return several .zipmod candidates
    for one GUID (re-uploads, unrelated files whose caption happens to
    mention the GUID, GUID collisions in filenames, etc). Every candidate
    is downloaded and its *actual* in-zip GUID (from manifest.xml) is
    checked against the one we're looking for — the search result's
    filename/caption match is not trusted on its own. A mismatch is
    deleted immediately and the next candidate is tried, first within the
    same chat, then in the next configured chat link, until a verified
    match is downloaded or every candidate in every chat has been
    exhausted. At most 5 candidates are actually downloaded per chat (per
    GUID) — a chat search can return up to 100 hits, and downloading every
    one of them just to check a manifest is unnecessary MTProto traffic
    once a chat's real match is very unlikely to be candidate #6+; the
    remaining candidates in that chat are skipped and the next chat link
    (if any) is tried instead. Files already on disk that verify by GUID
    (the `dest.exists()` check above) don't count against this cap, since
    no download happens for those.

    `client` is a single already-connected TelegramClient shared across
    every GUID in this DownloadMissingMods run (see the caller) — this
    used to open and disconnect a brand new TelegramClient on every single
    call (i.e. once per GUID that reaches chat-link search), which for a
    Chat Links list with several entries meant a fresh MTProto
    connect/auth handshake per chat per GUID.

    Returns (found_source, result):
      found_source — True if any chat had at least one candidate .zipmod
                     (matching by filename search), regardless of whether
                     any of them turned out to have the right GUID or
                     downloaded successfully
      result       — True (downloaded and GUID-verified), "skipped"
                     (already present with the correct GUID), or False (no
                     chat had a verified match, or every download failed)
    """
    if not chat_links:
        return False, False

    MAX_DOWNLOADS_PER_CHAT = 5
    found_source = False

    for chat, topic_id in chat_links:
        where = f"{chat}" + (f" (topic {topic_id})" if topic_id else "")
        logger.info("DLMOD", f"    Searching {where} for {guid}...")

        messages = await _search_chat_for_zipmod(client, chat, topic_id, guid)
        if not messages:
            continue  # no candidates here — try the next chat link

        found_source = True
        downloads_this_chat = 0

        for message in messages:
            file_name = None
            for attr in message.document.attributes:
                fn = getattr(attr, "file_name", None)
                if fn:
                    file_name = fn
                    break
            if not file_name:
                file_name = f"{guid}.zipmod"

            dest = (mods_dir / Path(rel_path).parent / file_name) if rel_path else (mods_dir / file_name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            file_size = message.document.size

            if dest.exists() and dest.stat().st_size == file_size:
                # A same-size file is already here (e.g. from a previous
                # run) — still verify its GUID rather than trusting the
                # size match, since a wrong file the same size as this
                # candidate would otherwise be "skipped" forever.
                existing_guid = await asyncio.to_thread(guid_from_zipmod, dest)
                if existing_guid == guid:
                    return True, "skipped"
                logger.warning("DLMOD",
                    f"    Existing file has wrong GUID ({existing_guid!r} != {guid!r}), "
                    f"deleting and re-downloading: {dest.name}")
                dest.unlink(missing_ok=True)

            if downloads_this_chat >= MAX_DOWNLOADS_PER_CHAT:
                logger.warning("DLMOD",
                    f"    Hit the {MAX_DOWNLOADS_PER_CHAT}-download cap for {where} "
                    f"searching {guid} — skipping remaining candidates in this chat.")
                break  # move on to the next configured chat link

            logger.info("DLMOD", f"    Found in {where} — downloading {file_name}")
            downloads_this_chat += 1

            # Use teleget9527 if available (reuse the passed-in downloader instance),
            # matching _download_via_teleget's exact pattern.
            if downloader is not None:
                try:
                    entity = await _retry_flood_wait(
                        client.get_entity, chat, context=f"resolving entity for {chat}")
                    # See the [FIX-2026-09-29-ENTITY-MARK] note in
                    # _download_via_teleget above — teleget9527 needs
                    # Telethon's marked peer-ID form, not the bare
                    # Channel.id, or a bare-int lookup on the daemon's
                    # copy of the session raises "Could not find the
                    # input entity for PeerUser(...)".
                    from telethon.utils import get_peer_id
                    raw_chat_id = get_peer_id(entity)

                    def _on_progress(downloaded: int, total: int, pct: float,
                                     _name: str = file_name) -> None:
                        if total > 0:
                            mb_done  = downloaded // 1024 // 1024
                            mb_total = total      // 1024 // 1024
                            logger.info("DLMOD",
                                f"    {_name}: {mb_done}/{mb_total} MB ({pct:.0f}%)")

                    await downloader.download(
                        chat_id=raw_chat_id,
                        msg_id=message.id,
                        save_path=str(dest.resolve()),
                        progress_callback=_on_progress,
                    )

                    # Poll until file reaches expected size (up to 1 hour)
                    for _ in range(3600):
                        await asyncio.sleep(1)
                        if dest.exists() and dest.stat().st_size >= file_size:
                            break
                except Exception as e:
                    logger.error("DLMOD",
                        f"    teleget9527 download failed [{guid}] from {where}: {e}")
                    dest.unlink(missing_ok=True)
                    continue  # try the next candidate

            else:
                # Fallback: plain Telethon download_media (teleget9527 isn't
                # involved on this path, so its own flood-wait handling
                # doesn't apply here — retry it ourselves)
                try:
                    await _retry_flood_wait(
                        client.download_media, message, file=str(dest),
                        context=f"downloading {file_name} from {where}")
                except Exception as e:
                    logger.error("DLMOD", f"    Download failed [{guid}] from {where}: {e}")
                    dest.unlink(missing_ok=True)
                    continue  # try the next candidate

            if not dest.exists() or dest.stat().st_size != file_size:
                logger.error("DLMOD", f"    Download incomplete [{guid}] from {where}")
                dest.unlink(missing_ok=True)
                continue  # try the next candidate

            # Verify the downloaded zipmod's actual GUID (from manifest.xml)
            # matches the one we asked for. Telegram search is text-based —
            # a filename or caption can mention a GUID without the archive
            # actually containing that mod — so don't trust the match until
            # this checks out.
            downloaded_guid = await asyncio.to_thread(guid_from_zipmod, dest)
            if downloaded_guid != guid:
                logger.warning("DLMOD",
                    f"    GUID mismatch for {file_name} — expected {guid}, "
                    f"got {downloaded_guid!r}. Deleting and trying the next result...")
                dest.unlink(missing_ok=True)
                continue  # try the next candidate, same chat

            logger.success("DLMOD", f"Downloaded: {file_name}")
            guid_str_map[guid] = str(dest)
            return True, True

        # No candidate in this chat had the right GUID — move on to the
        # next configured chat link.

    return found_source, False  # nothing verified in any chat/candidate


# ---------------------------------------------------------------------------
# Telethon session management
# ---------------------------------------------------------------------------

async def _ensure_session(tg_data: dict) -> bool:
    """
    Ensure a valid Telethon .session file exists in the session directory.
    If the session file is missing or the user is not authorised, walk them
    through phone + code (+ optional 2FA) dialogs and save the result.
    Returns True if authorised, False if the user cancelled or sign-in
    ultimately failed.
    """
    from telethon import TelegramClient
    from telethon.errors import (
        SessionPasswordNeededError,
        PasswordHashInvalidError,
        PhoneCodeInvalidError,
        PhoneCodeExpiredError,
        PhoneNumberInvalidError,
        FloodWaitError,
    )
    from kkafio.system.password_dialog import password_dialog
    from kkafio.core.constants import CONFIG_DIR

    session_dir  = CONFIG_DIR / "config" / "tg_session"
    session_dir.mkdir(parents=True, exist_ok=True)
    # Name the session file "kkafio" so TGDownloader finds it as "kkafio.session"
    session_file = session_dir / "kkafio"   # Telethon appends .session

    client = TelegramClient(str(session_file), tg_data["api_id"], tg_data["api_hash"])
    await client.connect()

    # Every return path below used to call client.disconnect() itself —
    # meaning any exception NOT explicitly caught (a wrong verification
    # code, an invalid phone number, a Telegram flood-wait, ...) skipped
    # disconnecting entirely and crashed the whole DownloadMissingMods task
    # with an unhandled exception on top of that. A try/finally around
    # everything after connect() guarantees the connection is always
    # closed, and every Telethon call below that can plausibly fail on
    # ordinary user input (wrong code, mistyped phone number, wrong 2FA
    # password, rate limiting) is now caught and reported as a normal
    # "sign-in failed" instead of an unhandled crash.
    try:
        if await client.is_user_authorized():
            return True

        logger.info("DLMOD", "Telegram session not found or expired — starting sign-in...")

        phone = password_dialog(
            "Telegram Sign-in",
            "Enter your Telegram phone number (with country code, e.g. +447911123456):",
            mask=False,
        ).strip()
        if not phone:
            logger.error("DLMOD", "Phone number not provided.")
            return False

        try:
            await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            logger.error("DLMOD", f"'{phone}' is not a valid phone number.")
            return False
        except FloodWaitError as e:
            logger.error("DLMOD",
                f"Telegram is rate-limiting sign-in attempts — try again in {e.seconds}s.")
            return False

        # A verification code is easy to mistype or let expire while
        # switching to the Telegram app to read it, so this specifically
        # gets a few retries rather than failing the whole task on the
        # first mistake.
        max_code_attempts = 3
        for attempt in range(1, max_code_attempts + 1):
            code = password_dialog(
                "Telegram Verification Code",
                f"A verification code was sent to {phone}.\nEnter the code:",
                mask=False,
            ).strip()
            if not code:
                logger.error("DLMOD", "Verification code not provided.")
                return False

            try:
                await client.sign_in(phone, code)
                break
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
                if attempt < max_code_attempts:
                    logger.warning("DLMOD",
                        f"Verification code was {'invalid' if isinstance(e, PhoneCodeInvalidError) else 'expired'} "
                        f"— try again ({attempt}/{max_code_attempts}).")
                    continue
                logger.error("DLMOD",
                    f"Verification code still incorrect after {max_code_attempts} attempts — giving up.")
                return False
            except SessionPasswordNeededError:
                pw = password_dialog(
                    "Telegram Two-Factor Password",
                    "Your account has Two-Factor Authentication enabled.\nEnter your 2FA password:",
                )
                if not pw:
                    logger.error("DLMOD", "2FA password not provided.")
                    return False
                try:
                    await client.sign_in(password=pw)
                except PasswordHashInvalidError:
                    logger.error("DLMOD", "2FA password was incorrect.")
                    return False
                break

        logger.success("DLMOD", "Telegram sign-in successful. Session saved.")
        return True
    finally:
        try:
            await client.disconnect()
        except Exception as e:
            logger.debug("DLMOD", f"Telegram disconnect failed (ignored): {e}")


# Hardcoded numeric ID for the KK_archive_modlibrary public channel, in
# Telethon's "marked" peer-ID form (see the [FIX-2026-09-29-ENTITY-MARK]
# note below for why it must be marked, not the bare channel ID).
KK_ARCHIVE_CHAT_ID = -1003881428951


async def _download_via_teleget(
    guid: str,
    tg_link: str,
    mods_dir: Path,
    tg_data: dict,
    guid_str_map: dict[str, str],
    client,
    downloader=None,
    rel_path: str | None = None,
) -> bool:
    """
    Download the file attached to a Telegram message using teleget9527.

    `client` is a single already-connected TelegramClient shared across
    every GUID in this DownloadMissingMods run (see the caller), used here
    for the metadata lookup and, if teleget9527 isn't installed, the actual
    download too. This function does not connect or disconnect it —
    previously this opened and closed two fresh TelegramClient connections
    per call (one for metadata, one for the Telethon-fallback download),
    meaning a batch of, say, 50 missing mods made ~100 separate MTProto
    connect/auth round trips to Telegram for what only ever needed one.

    If rel_path is provided (from the modpack index), the file is saved to
    mods_dir / rel_path preserving the Sideloader Modpack subfolder structure.
    Otherwise the file is saved directly into mods_dir.
    """
    parsed = _parse_tme_link(tg_link)
    if not parsed:
        logger.error("DLMOD", f"Cannot parse Telegram link: {tg_link}")
        return False
    chat_identifier, message_id = parsed

    # Fetch message metadata (filename, size) via the shared client.
    #
    # [FIX-2026-09-13-ENTITY-RESOLUTION] Previously this called
    # get_messages(KK_ARCHIVE_CHAT_ID, ids=message_id) — using the
    # hardcoded raw numeric chat ID and silently discarding chat_identifier
    # (the "@username" / "-100..." value _parse_tme_link already extracted
    # from the link). Telethon can only resolve a bare numeric peer ID into
    # a usable InputPeer if it already has that entity's access_hash cached
    # in the session file; on a cold cache (e.g. a freshly created session
    # that has never interacted with this chat before) this raises:
    #   ValueError: Could not find the input entity for PeerUser(user_id=...)
    # chat_identifier, by contrast, is the "@KK_archive_modlibrary" username
    # form for links like this one, which Telethon can resolve via a live
    # API call even with no prior cache — so use it instead of the raw ID
    # whenever we have it.
    message = await _retry_flood_wait(
        client.get_messages, chat_identifier, ids=message_id,
        context=f"fetching message metadata from {chat_identifier}")

    # teleget9527 wants the chat ID of the chat the link actually points at,
    # in Telethon's "marked" peer-ID form (the same shape as chat_id in
    # tg_downloader.py's own docstring example, e.g. -1001234567890) — not
    # the bare Channel.id Telethon exposes on the entity object.
    #
    # [FIX-2026-09-29-ENTITY-MARK] Previously this used `entity.id` — for a
    # Channel/supergroup, `.id` is always the *unmarked* bare numeric ID
    # (e.g. 3881428951). teleget9527's daemon passes chat_id straight to
    # Telethon's own get_messages()/get_input_entity() with no resolution of
    # its own, and Telethon always interprets a bare positive int as a User
    # ID — it does not consult the entity cache to disambiguate a plain int,
    # regardless of what's cached there. On a *copy* of the session handed
    # to a separate daemon subprocess this reliably raised:
    #   ValueError: Could not find the input entity for PeerUser(user_id=...)
    # even right after warming the entity cache for exactly this channel,
    # because the bare ID is inherently ambiguous to Telethon no matter what
    # is cached — it needs the sign/prefix to know it's a channel at all.
    # get_peer_id() (add_mark=True by default) converts the entity into
    # that unambiguous marked form.
    raw_chat_id: int | None = None
    try:
        from telethon.utils import get_peer_id
        entity = await _retry_flood_wait(
            client.get_entity, chat_identifier, context=f"resolving entity for {chat_identifier}")
        raw_chat_id = get_peer_id(entity)
    except Exception as e:
        if str(chat_identifier).lower() == "@kk_archive_modlibrary":
            raw_chat_id = KK_ARCHIVE_CHAT_ID   # known channel; safe fallback
        else:
            logger.warning("DLMOD",
                f"Could not resolve chat {chat_identifier} for teleget9527: {e}")

    if message is None or message.document is None:
        logger.error("DLMOD", f"No file in message {message_id} [{guid}]")
        return False

    file_name = f"{guid}.zipmod"
    for attr in message.document.attributes:
        fn = getattr(attr, "file_name", None)
        if fn:
            file_name = fn
            break

    # If rel_path is given (from modpack index), preserve the subfolder structure.
    # Replace only the filename portion so the directory matches BetterRepack's layout.
    if rel_path:
        dest = mods_dir / Path(rel_path).parent / file_name
    else:
        dest = mods_dir / file_name
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size == message.document.size:
        return "skipped"

    logger.info("DLMOD", f"Telegram ↓ {file_name}")
    file_size = message.document.size

    # Use teleget9527 if available (reuse the passed-in downloader instance)
    if downloader is not None:
        try:
            def _on_progress(downloaded: int, total: int, pct: float) -> None:
                if total > 0:
                    mb_done  = downloaded // 1024 // 1024
                    mb_total = total      // 1024 // 1024
                    logger.info("DLMOD",
                        f"  {file_name}: {mb_done}/{mb_total} MB ({pct:.0f}%)")

            if raw_chat_id is None:
                raise RuntimeError(f"cannot resolve chat {chat_identifier}")
            await downloader.download(
                chat_id=raw_chat_id,
                msg_id=message_id,
                save_path=str(dest.resolve()),
                progress_callback=_on_progress,
            )

            # Poll until file reaches expected size (up to 1 hour)
            for _ in range(3600):
                await asyncio.sleep(1)
                if dest.exists() and dest.stat().st_size >= file_size:
                    break

        except Exception as e:
            logger.error("DLMOD", f"teleget9527 download failed [{guid}]: {e}")
            return False

    else:
        # Fallback: Telethon download_media on the shared client (not
        # covered by teleget9527's own flood-wait handling, since teleget9527
        # isn't involved on this path)
        try:
            await _retry_flood_wait(
                client.download_media, message, file=str(dest), workers=4,
                context=f"downloading {file_name} [{guid}]")
        except Exception as e:
            logger.error("DLMOD", f"Telethon download failed [{guid}]: {e}")
            return False

    if not dest.exists() or dest.stat().st_size != message.document.size:
        logger.error("DLMOD", f"Download incomplete [{guid}]: {dest}")
        return False

    logger.success("DLMOD", f"Downloaded: {file_name}")
    guid_str_map[guid] = str(dest)
    return True


class DownloadMissingMods(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.download_missing_mods
        self.input_mods_dir_str  : str  = cfg.get("InputModsDir",        "")
        self.output_mods_dir_str : str  = cfg.get("OutputModsDir",       "")
        self.chara_dir_str       : str  = cfg.get("CharaDir",             "")
        self.scene_dir_str       : str  = cfg.get("SceneDir",             "")
        self.coord_dir_str       : str  = cfg.get("CoordDir",             "")
        self.content_types       : list[str] = cfg.get("ContentTypes",    ["Chara", "Scene", "Coord"])
        self.use_cache           : bool = cfg.get("UseCache",             True)
        self.modpack_mode        : str  = cfg.get("SideloaderModpack",    "OnlyUsed")
        self.telegram_source     : str  = cfg.get("TelegramSource",       "No")  # No | KoikatsuCards | ChatLinks | Both
        self.telegram_chat_links_raw : str = cfg.get("TelegramChatLinks", DEFAULT_TELEGRAM_CHAT_LINKS)

    @staticmethod
    def _write_readme(
        input_mods_dir: Path,
        output_mods_dir: Path,
        chara_guids: set[str],
        scene_guids: set[str],
        coord_guids: set[str],
        local_guids: set[str],
        modpack_index: dict[str, str],
        to_download: set[str],
        from_betterrepack: dict[str, str],
        downloaded_br: set[str],
        from_telegram: list[str],
        downloaded_tg: set[str],
        unresolved: list[str],
        failed_guids: set[str],
        ok: int,
        fail: int,
        modpack_mode: str,
        telegram_source: str,
        generated: str,
    ) -> None:
        """Write a report to output_mods_dir summarising the download run."""
        referenced_guids = chara_guids | scene_guids | coord_guids
        missing_all = referenced_guids - local_guids

        modpack_covered = referenced_guids & set(modpack_index.keys())
        if modpack_mode == "Skip":
            covered_count = len(local_guids & referenced_guids)
        else:
            covered_count = len((local_guids | modpack_covered) & referenced_guids)

        lines: list[str] = [
            "KKAFIO — Download Missing Mods Report",
            f"Generated        : {generated}",
            f"Input mods dir   : {input_mods_dir}",
            f"Output mods dir  : {output_mods_dir}",
            f"Sideloader mode  : {modpack_mode}",
            f"Telegram source  : {telegram_source}",
            "",
            "=" * 60,
            "",
            f"Character card mod references : {len(chara_guids)}",
            f"Scene mod references          : {len(scene_guids)}",
            f"Coordinate mod references     : {len(coord_guids)}",
            f"Already installed / covered   : {covered_count}",
            f"Missing total                 : {len(missing_all)}",
            f"Queued for download           : {len(to_download)}",
            f"  — from BetterRepack         : {len(from_betterrepack)}",
            f"  — from Telegram             : {len(from_telegram)}",
            f"  — unresolvable              : {len(unresolved)}",
            "",
            f"Downloaded successfully : {ok}",
            f"Failed                  : {fail}",
            "",
        ]

        if unresolved:
            lines += [
                "=" * 60,
                "Unresolvable mods (not in modpack index, no Telegram source found):",
                "These mods could not be downloaded automatically.",
                "Search for them manually on the KKC mod index or game modding communities.",
                "",
            ]
            for guid in sorted(unresolved):
                lines.append(f"  ! {guid}")
            lines.append("")

        if failed_guids:
            lines += [
                "=" * 60,
                f"Failed downloads ({len(failed_guids)}):",
                "These mods were found but could not be downloaded.",
                "Check your internet connection and try again.",
                "",
            ]
            for guid in sorted(failed_guids):
                lines.append(f"  ✗ {guid}")
            lines.append("")

        # Only list mods that were actually downloaded (not skipped/already present)
        actually_downloaded_br = {guid: rel for guid, rel in from_betterrepack.items()
                                   if guid in downloaded_br}
        actually_downloaded_tg = [guid for guid in from_telegram if guid in downloaded_tg]

        if actually_downloaded_br:
            lines.append("Downloaded from BetterRepack:")
            for guid, rel in sorted(actually_downloaded_br.items()):
                lines.append(f"  + {guid}")
                lines.append(f"    {rel}")
            lines.append("")

        if actually_downloaded_tg:
            lines.append("Downloaded via Telegram:")
            for guid in sorted(actually_downloaded_tg):
                lines.append(f"  + {guid}")
            lines.append("")

        if modpack_mode == "Skip":
            modpack_missing = referenced_guids & set(modpack_index.keys()) - local_guids
            if modpack_missing:
                lines += [
                    "=" * 60,
                    f"Sideloader Modpack mods skipped ({len(modpack_missing)}) — mode is 'Skip':",
                    "These mods are part of the Sideloader Modpack and were not downloaded.",
                    "Install the Sideloader Modpack from: https://dl.betterrepack.com/",
                    "",
                ]
                for guid in sorted(modpack_missing):
                    lines.append(f"  ~ {guid} ({modpack_index[guid]})")
                lines.append("")

        readme_path = output_mods_dir / "kkafio_missing_mods_report.txt"
        try:
            readme_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("DLMOD", f"Report saved: {readme_path}")
        except Exception as e:
            logger.warning("DLMOD", f"Could not write report: {e}")

    def run(self) -> None:
        game_path = self.config.game_path

        # ── Resolve directories ───────────────────────────────────────────
        # Input and output mods dirs each fall back to the game's mods dir
        # when left blank.
        default_mods_dir = game_path["mods"] if "mods" in game_path else None

        def _resolve_mods_dir(value: str, label: str) -> Path | None:
            if value.strip():
                return Path(value.strip())
            if default_mods_dir is not None:
                return Path(default_mods_dir)
            logger.error("DLMOD",
                f"{label} mods directory not set and not resolvable from game path.")
            return None

        input_mods_dir  = _resolve_mods_dir(self.input_mods_dir_str,  "Input")
        output_mods_dir = _resolve_mods_dir(self.output_mods_dir_str, "Output")
        if input_mods_dir is None or output_mods_dir is None:
            return

        if not input_mods_dir.exists():
            logger.error("DLMOD", f"Input mods directory does not exist: {input_mods_dir}")
            return

        # The output dir only receives downloads, so create it if needed.
        if not output_mods_dir.exists():
            try:
                output_mods_dir.mkdir(parents=True, exist_ok=True)
                logger.info("DLMOD", f"Created output mods directory: {output_mods_dir}")
            except OSError as e:
                logger.error("DLMOD",
                    f"Output mods directory does not exist and could not be created: "
                    f"{output_mods_dir} ({e})")
                return

        try:
            same_mods_dir = input_mods_dir.resolve() == output_mods_dir.resolve()
        except OSError:
            same_mods_dir = input_mods_dir == output_mods_dir

        if not self.content_types:
            logger.error("DLMOD",
                "No content types selected (Characters/Scenes/Coordinates) — nothing to scan.")
            return

        scan_chara = "Chara" in self.content_types
        scan_scene = "Scene" in self.content_types
        scan_coord = "Coord" in self.content_types

        chara_dirs: list[Path] = []
        if scan_chara:
            if self.chara_dir_str:
                chara_dirs = [Path(self.chara_dir_str)]
            else:
                chara_dirs = [
                    d for d in [game_path.get("charaFemale"), game_path.get("charaMale")]
                    if d is not None and d.exists()
                ]
            if not chara_dirs:
                logger.error("DLMOD", "Chara directory not set and not resolvable from game path.")
                return

        scene_dirs: list[Path] = []
        if scan_scene:
            if self.scene_dir_str:
                scene_dirs = [Path(self.scene_dir_str)]
            else:
                scene_dirs = [
                    d for d in [game_path.get("scene")]
                    if d is not None and d.exists()
                ]

        coord_dirs: list[Path] = []
        if scan_coord:
            if self.coord_dir_str:
                coord_dirs = [Path(self.coord_dir_str)]
            else:
                coord_dirs = [
                    d for d in [game_path.get("coordinate")]
                    if d is not None and d.exists()
                ]

        self.log_start("DLMOD")
        if same_mods_dir:
            logger.info("DLMOD", f"Mods dir  : {output_mods_dir} (input and output)")
        else:
            logger.info("DLMOD", f"Input mods dir  : {input_mods_dir}")
            logger.info("DLMOD", f"Output mods dir : {output_mods_dir}")
        if scan_chara:
            for d in chara_dirs:
                logger.info("DLMOD", f"Chara dir : {d}")
        else:
            logger.info("DLMOD", "Chara dir : skipped (Characters not selected)")
        if scan_scene:
            if scene_dirs:
                for d in scene_dirs:
                    logger.info("DLMOD", f"Scene dir : {d}")
            else:
                logger.info("DLMOD", "Scene dir : not set / Studio not installed — skipping scenes")
        else:
            logger.info("DLMOD", "Scene dir : skipped (Scenes not selected)")
        if scan_coord:
            if coord_dirs:
                for d in coord_dirs:
                    logger.info("DLMOD", f"Coord dir : {d}")
            else:
                logger.info("DLMOD", "Coord dir : not set / not resolvable — skipping coordinates")
        else:
            logger.info("DLMOD", "Coord dir : skipped (Coordinates not selected)")
        logger.info("DLMOD", f"Modpack   : {self.modpack_mode}")

        # ── Step 1: mods caches (input + output) ──────────────────────────
        # guid_str_map deliberately EXCLUDES the Sideloader Modpack subtree
        # (include_modpack=False) and only covers the OUTPUT dir — it is the
        # set new downloads get written into ("Local GUIDs: N" describes the
        # user's own non-modpack mods there).
        guid_str_map = build_mods_cache(output_mods_dir, include_modpack=False, use_cache=self.use_cache)
        logger.info("DLMOD", f"Output dir GUIDs: {len(guid_str_map)}")

        # But "is this GUID already installed at all" (used below to decide
        # what's actually missing) needs the FULL picture — including mods
        # inside a Sideloader Modpack folder, which is where BetterRepack's
        # own installer puts everything — from BOTH directories:
        #   missing = referenced - input GUIDs - output GUIDs
        # Otherwise "OnlyUsed" would re-download every modpack GUID the user
        # already has, and "All" would try to re-download the whole modpack.
        # The cache file is shared between include_modpack scopes, so this
        # doesn't re-hash anything the call above already covered.
        output_guid_map = build_mods_cache(output_mods_dir, include_modpack=True, use_cache=self.use_cache)
        output_guids: set[str] = set(output_guid_map.keys())

        if same_mods_dir:
            input_guids: set[str] = set(output_guids)
        else:
            input_guid_map = build_mods_cache(input_mods_dir, include_modpack=True, use_cache=self.use_cache)
            input_guids = set(input_guid_map.keys())
            logger.info("DLMOD", f"Input dir GUIDs : {len(input_guids)}")

        all_local_guids: set[str] = input_guids | output_guids

        # ── Step 2: modpack index ─────────────────────────────────────────
        game_type     = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        modpack_index = load_modpack_index(game_type=game_type) or {}
        if modpack_index:
            logger.info("DLMOD", f"Modpack index loaded: {len(modpack_index)} GUIDs")
        else:
            logger.warning("DLMOD",
                "kkafio_modpack_index_kk/kks.json not found — "
                "BetterRepack downloads unavailable.")

        # ── Step 3: chara + scene + coord GUIDs ─────────────────────────────
        chara_guids: set[str] = set()
        if scan_chara:
            chara_guids = collect_chara_guids(chara_dirs, self.use_cache)
            logger.info("DLMOD", f"Chara references: {len(chara_guids)} unique GUIDs")

        scene_guids: set[str] = set()
        if scan_scene and scene_dirs:
            scene_guids = collect_scene_guids(scene_dirs, self.use_cache)
            logger.info("DLMOD", f"Scene references: {len(scene_guids)} unique GUIDs")

        coord_guids: set[str] = set()
        if scan_coord and coord_dirs:
            coord_guids = collect_coord_guids(coord_dirs, self.use_cache)
            logger.info("DLMOD", f"Coord references: {len(coord_guids)} unique GUIDs")

        referenced_guids = chara_guids | scene_guids | coord_guids

        # ── Step 4: decide what to download ──────────────────────────────
        # all_local_guids = input dir GUIDs | output dir GUIDs (both including
        # any Sideloader Modpack subtree) — see Step 1 — so a mod already
        # present in either directory isn't treated as missing.
        missing_local: set[str] = referenced_guids - all_local_guids

        match self.modpack_mode:
            case "Skip":
                to_download = {g for g in missing_local if g not in modpack_index}
            case "OnlyUsed":
                to_download = missing_local
            case "All":
                to_download = missing_local | (set(modpack_index.keys()) - all_local_guids)
            case _:
                to_download = missing_local

        logger.info("DLMOD",
            f"Missing local: {len(missing_local)} | "
            f"To download ({self.modpack_mode}): {len(to_download)}")

        if not to_download:
            logger.success("DLMOD", "Nothing to download.")
            return

        # ── Step 5: partition ─────────────────────────────────────────────
        use_koikatsucards = self.telegram_source in ("KoikatsuCards", "Both")
        use_chat_links    = self.telegram_source in ("ChatLinks", "Both")
        use_telegram      = use_koikatsucards or use_chat_links
        if not use_telegram:
            logger.info("DLMOD",
                "Telegram Source is 'No' — "
                "only BetterRepack mods will be downloaded.")

        from_betterrepack: dict[str, str] = {}
        from_telegram    : list[str]      = []
        unresolved       : list[str]      = []

        for guid in sorted(to_download):
            if guid in modpack_index:
                from_betterrepack[guid] = modpack_index[guid]
            elif use_telegram:
                from_telegram.append(guid)
            else:
                unresolved.append(guid)

        logger.info("DLMOD",
            f"  BetterRepack: {len(from_betterrepack)} | "
            f"Telegram: {len(from_telegram)} | "
            f"Unresolved: {len(unresolved)}")

        if unresolved:
            logger.warning("DLMOD",
                f"{len(unresolved)} GUID(s) unresolvable:")
            for guid in unresolved:
                logger.warning("DLMOD", f"  {guid}")

        # ── Step 6: download ──────────────────────────────────────────────
        ok = fail = 0
        failed_guids:  set[str]  = set()
        downloaded_br:  set[str] = set()
        downloaded_tg:  set[str] = set()

        async def _run_all() -> None:
            nonlocal ok, fail, failed_guids, downloaded_br, downloaded_tg
            async with _make_http_client() as br_client:

                # BetterRepack — concurrent
                br_failed     = {}   # guid -> rel_path for failed BR downloads
                downloaded_br = set()  # actually downloaded (not skipped)

                if from_betterrepack:
                    logger.info("DLMOD",
                        f"Downloading {len(from_betterrepack)} mod(s) from BetterRepack...")
                    results = await asyncio.gather(*[
                        _download_betterrepack(br_client, guid, rel, output_mods_dir, guid_str_map)
                        for guid, rel in from_betterrepack.items()
                    ], return_exceptions=True)
                    for guid, result in zip(from_betterrepack, results, strict=True):
                        if result is True:
                            ok += 1
                            downloaded_br.add(guid)
                        elif result == "skipped":
                            pass  # already existed — don't count or report
                        else:
                            fail += 1
                            if isinstance(result, Exception):
                                logger.error("DLMOD", f"Exception [{guid}]: {result}")
                            # Queue for Telegram fallback if enabled
                            if use_telegram:
                                br_failed[guid] = from_betterrepack[guid]
                                logger.info("DLMOD",
                                    f"  [{guid}] will be retried via Telegram")
                            else:
                                failed_guids.add(guid)

                # Merge BetterRepack failures into Telegram queue
                telegram_queue: list[tuple[str, str | None]] = [
                    (guid, None) for guid in from_telegram
                ] + [
                    (guid, rel) for guid, rel in br_failed.items()
                ]

                # Telegram — sequential
                if telegram_queue:
                    kkc_index: dict[str, str] = {}
                    if use_koikatsucards:
                        kkc_index = await _load_kkc_mod_index(br_client)

                    # Load/prompt for credentials once before the loop
                    tg_data = tg_cfg.get_or_prompt()
                    if tg_data is None:
                        logger.error("DLMOD",
                            "Telegram credentials not provided — "
                            f"skipping {len(telegram_queue)} mod(s).")
                        # br_failed's `fail += 1` already happened in the
                        # BetterRepack loop above (this used to double-count
                        # it here on top of that). from_telegram was never
                        # counted anywhere yet, so it still needs it. Both
                        # groups are added to failed_guids here too — the
                        # BetterRepack loop deliberately leaves a br_failed
                        # guid out of failed_guids while Telegram is still
                        # queued to retry it, and neither group was ever
                        # added to the itemized failure list otherwise, so
                        # without this the final "failed" count and the
                        # per-GUID list shown in the report would disagree.
                        fail += len(from_telegram)
                        failed_guids.update(from_telegram)
                        failed_guids.update(br_failed)
                    else:
                        # Ensure session file exists before starting downloads
                        authorised = await _ensure_session(tg_data)
                        if not authorised:
                            logger.error("DLMOD",
                                "Telegram sign-in failed — "
                                f"skipping {len(telegram_queue)} mod(s).")
                            fail += len(from_telegram)
                            failed_guids.update(from_telegram)
                            failed_guids.update(br_failed)
                        else:
                            teleget_downloader = None

                            # One TelegramClient, connected once and reused for
                            # every GUID in this batch (both the KKC-index
                            # metadata/fallback-download path and the Telegram
                            # Chat Links search/download path below) — this used
                            # to open and close a brand new connection per GUID
                            # per path, which for a batch of N missing mods meant
                            # up to ~2N-3N separate MTProto connect/auth round
                            # trips to Telegram for what only ever needed one.
                            from telethon import TelegramClient as _TelegramClient
                            from kkafio.core.constants import CONFIG_DIR as _TG_CFG_DIR
                            _tg_session_dir = _TG_CFG_DIR / "config" / "tg_session"
                            tg_client = _TelegramClient(
                                str(_tg_session_dir / "kkafio"),
                                tg_data["api_id"], tg_data["api_hash"],
                            )
                            await tg_client.connect()

                            if use_koikatsucards:
                                # [FIX-2026-09-13-ENTITY-CACHE-WARMUP] Resolve the
                                # KK_archive_modlibrary channel by username *once*,
                                # using the primary session, before the daemon
                                # subprocess is started below.
                                #
                                # Why this is needed: every download further down
                                # this pipeline (both the per-GUID metadata lookup
                                # in _download_via_teleget, and teleget9527's own
                                # daemon-side get_messages(request.chat_id, ...)
                                # call) references the channel via the raw numeric
                                # KK_ARCHIVE_CHAT_ID constant, not its username.
                                # Telethon can only turn a bare numeric peer ID
                                # into a usable InputPeer if it already has that
                                # entity's access_hash cached in the session file
                                # (populated by an earlier get_entity/get_dialogs
                                # call, or by the account having already interacted
                                # with the chat via the Telegram app itself). On a
                                # brand-new session that has never touched this
                                # channel, resolving the raw ID directly raises:
                                #   ValueError: Could not find the input entity
                                #   for PeerUser(user_id=...)
                                #
                                # The daemon's own session is a one-time copy of
                                # this primary session, taken when
                                # teleget_downloader.start() launches it just
                                # below — so if the cache isn't warmed *before*
                                # that copy happens, the daemon inherits a cold
                                # cache and hits the exact same error internally,
                                # just deeper in the pipeline and harder to
                                # diagnose. Resolving by username here (which
                                # Telethon can do via a fresh API call even with
                                # no prior cache) warms the primary session's
                                # cache first, so the copy the daemon receives is
                                # already warm, and every later raw-numeric-ID
                                # lookup — in this file and inside teleget9527 —
                                # succeeds on the very first attempt.
                                try:
                                    # Warm the *shared* client's entity cache — it
                                    # stays connected for the rest of this batch,
                                    # so this also directly benefits every
                                    # Telegram metadata lookup below, not
                                    # just the daemon's copied session.
                                    await tg_client.get_entity("KK_archive_modlibrary")
                                    logger.info("DLMOD",
                                        "Warmed entity cache for KK_archive_modlibrary")
                                except Exception as warm_err:
                                    logger.warning("DLMOD",
                                        f"Could not pre-warm channel entity cache "
                                        f"(non-fatal, downloads may still fail on a "
                                        f"cold cache): {warm_err}")

                            # Create TGDownloader once and reuse across all downloads,
                            # for both the KKC-index path and the Telegram Chat
                            # Links path.
                            from kkafio.core.constants import CONFIG_DIR as _CFG_DIR
                            _session_dir = _CFG_DIR / "config" / "tg_session"
                            try:
                                from tg_downloader import TGDownloader

                                # [FIX-2026-09-14-SUPPRESS-TELEGET-CONSOLE-LOGS]
                                # The download daemon runs as a separate
                                # multiprocessing child process, re-executing
                                # this frozen app fresh — kkafio_cli.py's own
                                # __main__ logic (including the console-log
                                # suppression it applies to *this* process,
                                # see suppress_teleget_console_logs()) never
                                # runs there at all, since
                                # multiprocessing.freeze_support() intercepts
                                # before reaching it. daemon_console_log_level
                                # is a separate teleget9527 config key
                                # (added specifically to support this) that
                                # controls only the daemon's console handler,
                                # independent of its file handler — unlike
                                # daemon_log_level, which controls both
                                # together. Setting it to CRITICAL effectively
                                # silences the daemon's console output
                                # entirely (nothing below CRITICAL is ever
                                # emitted there) while its log file keeps
                                # full INFO/DEBUG detail for troubleshooting.
                                # Errors from this task are already reported
                                # to the user through KKAFIO's own error
                                # handling, which points them at the log
                                # file, so there's no need to also mirror
                                # teleget's own console output for
                                # visibility. Only applied in frozen/packaged
                                # builds — keep full console detail for
                                # developers running from source.
                                _teleget_config = (
                                    {
                                        "daemon_log_level": "INFO",
                                        "daemon_console_log_level": "CRITICAL",
                                    }
                                    if getattr(sys, "frozen", False)
                                    else None
                                )

                                teleget_downloader = TGDownloader(
                                    api_id=tg_data["api_id"],
                                    api_hash=tg_data["api_hash"],
                                    session_dir=str(_session_dir.resolve()),
                                    config=_teleget_config,
                                )
                                await teleget_downloader.start("kkafio")
                                logger.info("DLMOD", "teleget9527 downloader started")
                            except ImportError:
                                logger.info("DLMOD",
                                    "teleget9527 not installed, using Telethon fallback "
                                    "(install with: pip install teleget9527[fast])")

                            chat_links: list[tuple[str | int, int | None]] = []
                            if use_chat_links:
                                chat_links = _parse_chat_links(self.telegram_chat_links_raw)
                                if not chat_links:
                                    logger.warning("DLMOD",
                                        "Telegram Chat Links is enabled but no valid "
                                        "chat links are configured — nothing to search.")

                            source_label = _telegram_source_label(self.telegram_source)
                            logger.info("DLMOD",
                                f"Processing {len(telegram_queue)} mod(s) via {source_label}...")

                            try:
                                for guid, rel_path in telegram_queue:
                                    found_source = False
                                    success: bool | str = False

                                    if use_koikatsucards:
                                        tg_link = kkc_index.get(guid, "")
                                        if tg_link:
                                            found_source = True
                                            logger.info("DLMOD", f"  Link: {tg_link}")
                                            # Pass rel_path so the file is saved to the
                                            # same subfolder as the modpack index
                                            success = await _download_via_teleget(
                                                guid, tg_link, output_mods_dir, tg_data,
                                                guid_str_map, tg_client,
                                                downloader=teleget_downloader,
                                                rel_path=rel_path,
                                            )
                                        else:
                                            logger.warning("DLMOD",
                                                f"  {guid} — not in kkc mod index")

                                    if success not in (True, "skipped") and use_chat_links and chat_links:
                                        if use_koikatsucards:
                                            logger.info("DLMOD",
                                                f"  Trying Telegram Chat Links for {guid}...")
                                        chat_found, success = await _search_chat_links_and_download(
                                            guid, chat_links, output_mods_dir, tg_data,
                                            guid_str_map, tg_client,
                                            downloader=teleget_downloader,
                                            rel_path=rel_path,
                                        )
                                        found_source = found_source or chat_found

                                    if success == "skipped":
                                        pass  # already existed — don't count or report
                                    elif success is True:
                                        failed_guids.discard(guid)
                                        downloaded_tg.add(guid)
                                        if guid in br_failed:
                                            fail -= 1
                                        ok += 1
                                    elif found_source:
                                        # A source was identified somewhere but the
                                        # download itself failed
                                        failed_guids.add(guid)
                                        if guid not in br_failed:
                                            fail += 1
                                    else:
                                        # No source found anywhere that was tried.
                                        # A br_failed guid was already counted
                                        # into `fail` back in the BetterRepack
                                        # loop (deferred there pending this
                                        # Telegram retry) — since it's ending up
                                        # here as unresolved rather than a
                                        # confirmed failure, undo that count so
                                        # the final "failed: N, unresolved: M"
                                        # summary doesn't count the same guid
                                        # in both buckets.
                                        if guid in br_failed:
                                            fail -= 1
                                            failed_guids.discard(guid)
                                        unresolved.append(guid)
                            finally:
                                if teleget_downloader is not None:
                                    try:
                                        await teleget_downloader.shutdown()
                                    except Exception as e:
                                        # teleget9527's ShutdownRequest bug — harmless, downloads are done
                                        logger.debug("DLMOD", f"teleget9527 shutdown error (ignored): {e}")
                                try:
                                    await tg_client.disconnect()
                                except Exception as e:
                                    logger.debug("DLMOD", f"Telegram disconnect failed (ignored): {e}")

        asyncio.run(_run_all())

        logger.line()

        # Write the report to the output mods dir summarising what was downloaded
        self._write_readme(
            input_mods_dir    = input_mods_dir,
            output_mods_dir   = output_mods_dir,
            chara_guids       = chara_guids,
            scene_guids       = scene_guids,
            coord_guids       = coord_guids,
            # Union of both directories (incl. any Sideloader Modpack
            # subtree), same as missing_local above.
            local_guids       = all_local_guids,
            modpack_index     = modpack_index,
            to_download       = to_download,
            from_betterrepack = from_betterrepack,
            downloaded_br     = downloaded_br,
            from_telegram     = from_telegram,
            downloaded_tg     = downloaded_tg,
            unresolved        = unresolved,
            failed_guids      = failed_guids,
            ok                = ok,
            fail              = fail,
            modpack_mode      = self.modpack_mode,
            telegram_source   = _telegram_source_label(self.telegram_source),
            generated         = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        logger.success("DLMOD",
            f"Done — downloaded: {ok}, failed: {fail}, "
            f"unresolved: {len(unresolved)}")