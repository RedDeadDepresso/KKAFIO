"""Connecting to Telegram once for a whole task run: credentials, sign-in, one shared Telethon
client, and (if installed) the teleget9527 downloader. Used by Download Missing Mods and
Download Contents.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from contextlib import asynccontextmanager

import kkafio.services.mod_sources.telegram.config as tg_cfg
from kkafio.core.logger import logger
from kkafio.services.mod_sources.telegram.mods import ensure_session

# The channel every Download Missing Mods run needs to be able to resolve by its marked id.
WARM_DEFAULT = "KK_archive_modlibrary"


async def warm_entity(client, chat: str | int, *, tag: str) -> bool:
    """Make `client` (and so the session file) know `chat`; True if it could be resolved.

    A public chat resolves from its username alone. A private one (a ``t.me/c/<id>`` link, i.e. a
    bare marked id) can only be resolved from the account's own dialog list, so on failure the
    dialogs are loaded once and the lookup is retried. Failure is not fatal here: the caller
    reports the chat when it actually tries to use it.
    """
    try:
        await client.get_entity(chat)
        logger.info(tag, f"Warmed entity cache for {chat}")
        return True
    except ValueError:
        pass                                    # not in the cache: try the account's dialogs
    except Exception as e:
        logger.warning(tag, f"Could not pre-warm entity cache for {chat} (non-fatal): {e}")
        return False
    try:
        await client.get_dialogs()
        await client.get_entity(chat)
        logger.info(tag, f"Warmed entity cache for {chat} (from the dialog list)")
        return True
    except Exception as e:
        logger.warning(tag, f"Could not resolve chat {chat} — is this account a member of it? ({e})")
        return False


@asynccontextmanager
async def telegram_session(
    enabled: bool = True, *, tag: str = "DLMOD",
    warm: Iterable[str | int] = (WARM_DEFAULT,),
):
    """Connect to Telegram for this run. Yields (tg_data, client, downloader), or None when
    `enabled` is false or Telegram can't be used (no credentials, sign-in failed): the other
    sources keep working without it.

    One TelegramClient is connected once and shared by everything that talks to Telegram in the
    run; it (and the teleget9527 downloader, if installed) is closed when the block ends.

    `tag` is the logger tag of the calling task. `warm` lists the chats ("@name" or marked ids)
    whose entities are resolved before the downloader starts, so the session copy it works
    from already knows them (see the [FIX-2026-09-13-ENTITY-CACHE-WARMUP] note below).
    """
    if not enabled:
        yield None
        return

    tg_data = tg_cfg.get_or_prompt()
    if tg_data is None:
        logger.error(tag, "Telegram credentials not provided — skipping Telegram.")
        yield None
        return
    # Ensure session file exists before starting downloads
    if not await ensure_session(tg_data):
        logger.error(tag, "Telegram sign-in failed — skipping Telegram.")
        yield None
        return

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
    from kkafio.core.paths import TELEGRAM_SESSION_DIR as _TG_SESSION_DIR
    _tg_session_dir = _TG_SESSION_DIR
    tg_client = _TelegramClient(
        str(_tg_session_dir / "kkafio"),
        tg_data["api_id"], tg_data["api_hash"],
    )
    await tg_client.connect()

    # [FIX-2026-09-13-ENTITY-CACHE-WARMUP] Resolve the
    # KK_archive_modlibrary channel by username *once*,
    # using the primary session, before the daemon
    # subprocess is started below.
    #
    # Why this is needed: every download further down
    # this pipeline (both the per-GUID metadata lookup
    # in download_via_teleget, and teleget9527's own
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
    for chat in warm:
        await warm_entity(tg_client, chat, tag=tag)

    # Create TGDownloader once and reuse across all downloads,
    # for both the KKC-index path and the Telegram Chat
    # Links path.
    from kkafio.core.paths import TELEGRAM_SESSION_DIR as _CFG_TG_DIR
    _session_dir = _CFG_TG_DIR
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
        logger.info(tag, "teleget9527 downloader started")
    except ImportError:
        logger.info(tag,
            "teleget9527 not installed, using Telethon fallback "
            "(install with: pip install teleget9527[fast])")
    try:
        yield tg_data, tg_client, teleget_downloader
    finally:
        if teleget_downloader is not None:
            try:
                await teleget_downloader.shutdown()
            except Exception as e:
                # teleget9527's ShutdownRequest bug — harmless, downloads are done
                logger.debug(tag, f"teleget9527 shutdown error (ignored): {e}")
        try:
            await tg_client.disconnect()
        except Exception as e:
            logger.debug(tag, f"Telegram disconnect failed (ignored): {e}")
