"""Finding and downloading mods from Telegram chats (via teleget): flood-wait retries, chat search,
session bootstrap, and the per-GUID download.
"""

import asyncio
from pathlib import Path

from kkafio.cards.mods import guid_from_zipmod
from kkafio.core.logger import logger
from kkafio.services.telegram_links import parse_tme_link


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


async def search_chat_links_and_download(
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
            # matching download_via_teleget's exact pattern.
            if downloader is not None:
                try:
                    entity = await _retry_flood_wait(
                        client.get_entity, chat, context=f"resolving entity for {chat}")
                    # See the [FIX-2026-09-29-ENTITY-MARK] note in
                    # download_via_teleget above — teleget9527 needs
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

async def ensure_session(tg_data: dict) -> bool:
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


async def download_via_teleget(
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
    parsed = parse_tme_link(tg_link)
    if not parsed:
        logger.error("DLMOD", f"Cannot parse Telegram link: {tg_link}")
        return False
    chat_identifier, message_id = parsed

    # Fetch message metadata (filename, size) via the shared client.
    #
    # [FIX-2026-09-13-ENTITY-RESOLUTION] Previously this called
    # get_messages(KK_ARCHIVE_CHAT_ID, ids=message_id) — using the
    # hardcoded raw numeric chat ID and silently discarding chat_identifier
    # (the "@username" / "-100..." value parse_tme_link already extracted
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
