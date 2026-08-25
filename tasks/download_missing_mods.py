"""
download_missing_mods.py — Find and download mods that characters reference
but are not present in the local mods directory.

Strategy
--------
1. Build / load the local mods cache (non-modpack zipmods only).
2. Build / load the chara GUID cache (all GUIDs referenced by chara cards).
3. missing_local = chara_guids - local_mods_guids
4. Sideloader Modpack mode:
     Skip     — ignore modpack GUIDs entirely (only download local-only mods)
     OnlyUsed — download missing GUIDs that are in the modpack index or on
                koikatsucards.com
     All      — also download every GUID in the modpack index not installed
5. For each GUID to download:
     a) In modpack index → BetterRepack (httpx, no auth)
     b) Not in index, Telethon configured → look up on koikatsucards.com,
        parse the t.me link, download the file directly from Telegram
     c) Otherwise → log as unresolved
6. Each successful download immediately updates and saves the mods cache.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from tasks.base_task import BaseTask
from utils.chara_ops import (
    build_mods_cache,
    load_mods_cache,
    load_modpack_index,
    parse_chara_guids,
    save_mods_cache,
)
from utils.classifier import CardType, get_card_type
from utils.logger import logger
import utils.telegram_config as tg_cfg

BETTERREPACK_BASE     = "https://sideload.betterrepack.com/download/KKEC"
KOIKATSUCARDS_MOD_LIB = "https://koikatsucards.com/mod_library"
CHARA_CACHE_FILE      = "kkafio_chara_guid_cache.json"
MAX_CONNECTIONS       = 4


# ---------------------------------------------------------------------------
# Chara GUID cache
# ---------------------------------------------------------------------------

def _dir_mtime(d: Path) -> float:
    try:
        return d.stat().st_mtime
    except Exception:
        return 0.0


def _load_chara_cache(chara_dirs: list[Path]) -> set[str] | None:
    key = "|".join(str(d) for d in chara_dirs)
    cache_path = chara_dirs[0].parent / CHARA_CACHE_FILE
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("chara_dirs") != key:
            return None
        stored_mtimes = data.get("mtimes", {})
        for d in chara_dirs:
            if abs(stored_mtimes.get(str(d), 0) - _dir_mtime(d)) > 1:
                return None
        return set(data["guids"])
    except Exception:
        return None


def _save_chara_cache(chara_dirs: list[Path], guids: set[str]) -> None:
    key = "|".join(str(d) for d in chara_dirs)
    cache_path = chara_dirs[0].parent / CHARA_CACHE_FILE
    try:
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump({
                "chara_dirs": key,
                "mtimes":     {str(d): _dir_mtime(d) for d in chara_dirs},
                "guids":      sorted(guids),
            }, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _collect_chara_guids(chara_dirs: list[Path], use_cache: bool) -> set[str]:
    if use_cache:
        cached = _load_chara_cache(chara_dirs)
        if cached is not None:
            logger.info("DLMOD", f"Chara cache loaded: {len(cached)} GUIDs")
            return cached

    logger.info("DLMOD", "Scanning character cards for mod GUIDs...")
    guids: set[str] = set()
    for chara_dir in chara_dirs:
        if not chara_dir.exists():
            continue
        for png in chara_dir.rglob("*.png"):
            try:
                raw = png.read_bytes()
                if get_card_type(raw) in (CardType.KK, CardType.KKSP, CardType.KKS):
                    for guid in parse_chara_guids(png):
                        if guid:
                            guids.add(guid)
            except Exception:
                pass

    if use_cache:
        _save_chara_cache(chara_dirs, guids)
        logger.info("DLMOD", f"Chara cache saved: {len(guids)} GUIDs")
    else:
        logger.info("DLMOD", f"Chara scan complete: {len(guids)} GUIDs")
    return guids


# ---------------------------------------------------------------------------
# HTTP client (for BetterRepack + koikatsucards.com scraping)
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
    )


# ---------------------------------------------------------------------------
# BetterRepack download
# ---------------------------------------------------------------------------

async def _download_betterrepack(
    client, guid: str, rel_path: str, mods_dir: Path,
    guid_str_map: dict[str, str],
) -> bool:
    url  = f"{BETTERREPACK_BASE}/{rel_path.replace(chr(92), '/')}"
    dest = mods_dir / rel_path.replace("/", os.sep)

    if dest.exists():
        logger.skipped("DLMOD", f"{dest.name} already exists, skipping")
        return True

    logger.info("DLMOD", f"BetterRepack ↓ {dest.name}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            import aiofiles
            async with aiofiles.open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    await f.write(chunk)
        logger.success("DLMOD", f"Downloaded: {dest.name}")
        guid_str_map[guid] = str(dest)
        save_mods_cache(mods_dir, guid_str_map)
        return True
    except Exception as e:
        logger.error("DLMOD", f"BetterRepack failed [{guid}]: {e}")
        return False


# ---------------------------------------------------------------------------
# koikatsucards.com scraping
# ---------------------------------------------------------------------------

async def _get_telegram_link(client, guid: str) -> str:
    """Scrape koikatsucards.com/mod_library for the Telegram t.me link."""
    from bs4 import BeautifulSoup
    url = f"{KOIKATSUCARDS_MOD_LIB}?q={guid}&pageSize=50"
    try:
        r = await client.get(url)
        r.raise_for_status()
    except Exception as e:
        logger.error("DLMOD", f"koikatsucards.com request failed [{guid}]: {e}")
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for anchor in soup.select("a.mod-library-item-link"):
        code = anchor.select_one("code")
        if code and code.text.strip() == guid:
            href = anchor.get("href", "")
            if href:
                return href
    return ""


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
# Telethon session management
# ---------------------------------------------------------------------------

async def _ensure_session(tg_data: dict) -> bool:
    """
    Ensure a valid Telethon .session file exists in the session directory.
    If the session file is missing or the user is not authorised, walk them
    through phone + code (+ optional 2FA) dialogs and save the result.
    Returns True if authorised, False if the user cancelled.
    """
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from utils.password_dialog import password_dialog
    from utils.constants import CONFIG_DIR

    session_dir  = CONFIG_DIR / "config" / "tg_session"
    session_dir.mkdir(parents=True, exist_ok=True)
    # Name the session file "kkafio" so TGDownloader finds it as "kkafio.session"
    session_file = session_dir / "kkafio"   # Telethon appends .session

    client = TelegramClient(str(session_file), tg_data["api_id"], tg_data["api_hash"])
    await client.connect()

    if await client.is_user_authorized():
        await client.disconnect()
        return True

    logger.info("DLMOD", "Telegram session not found or expired — starting sign-in...")

    phone = password_dialog(
        "Telegram Sign-in",
        "Enter your Telegram phone number (with country code, e.g. +447911123456):",
    )
    if not phone:
        logger.error("DLMOD", "Phone number not provided.")
        await client.disconnect()
        return False

    await client.send_code_request(phone)

    code = password_dialog(
        "Telegram Verification Code",
        f"A verification code was sent to {phone}.\nEnter the code:",
    )
    if not code:
        logger.error("DLMOD", "Verification code not provided.")
        await client.disconnect()
        return False

    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        pw = password_dialog(
            "Telegram Two-Factor Password",
            "Your account has Two-Factor Authentication enabled.\nEnter your 2FA password:",
        )
        if not pw:
            logger.error("DLMOD", "2FA password not provided.")
            await client.disconnect()
            return False
        await client.sign_in(password=pw)

    await client.disconnect()
    logger.success("DLMOD", "Telegram sign-in successful. Session saved.")
    return True


# Hardcoded numeric ID for the KK_archive_modlibrary public channel.
# This never changes for a given channel so no runtime resolution is needed.
KK_ARCHIVE_CHAT_ID = 3881428951


async def _download_via_teleget(
    guid: str,
    tg_link: str,
    mods_dir: Path,
    tg_data: dict,
    guid_str_map: dict[str, str],
    downloader=None,
) -> bool:
    """
    Download the file attached to a Telegram message using teleget9527
    (TeleBackup SDK) for maximum parallel throughput.

    Pass an already-started TGDownloader instance via `downloader` so it is
    reused across multiple calls — avoids the per-download startup/shutdown
    overhead and the ShutdownRequest bug in teleget9527.

    Falls back to standard Telethon download_media if teleget9527 is not
    installed.
    """
    from utils.constants import CONFIG_DIR
    from telethon import TelegramClient

    parsed = _parse_tme_link(tg_link)
    if not parsed:
        logger.error("DLMOD", f"Cannot parse Telegram link: {tg_link}")
        return False
    _, message_id = parsed

    session_dir = CONFIG_DIR / "config" / "tg_session"

    # Fetch message metadata (filename, size) via a lightweight Telethon call
    meta_client = TelegramClient(
        str(session_dir / "kkafio"), tg_data["api_id"], tg_data["api_hash"]
    )
    await meta_client.connect()
    message = await meta_client.get_messages(KK_ARCHIVE_CHAT_ID, ids=message_id)
    await meta_client.disconnect()

    if message is None or message.document is None:
        logger.error("DLMOD", f"No file in message {message_id} [{guid}]")
        return False

    file_name = f"{guid}.zipmod"
    for attr in message.document.attributes:
        fn = getattr(attr, "file_name", None)
        if fn:
            file_name = fn
            break

    dest = mods_dir / file_name

    if dest.exists() and dest.stat().st_size == message.document.size:
        logger.skipped("DLMOD", f"{file_name} already complete, skipping")
        return True

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

            await downloader.download(
                chat_id=KK_ARCHIVE_CHAT_ID,
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
        # Fallback: Telethon download_media with parallel workers
        try:
            dl_client = TelegramClient(
                str(session_dir / "kkafio"), tg_data["api_id"], tg_data["api_hash"]
            )
            await dl_client.connect()
            await dl_client.download_media(message, file=str(dest), workers=4)
            await dl_client.disconnect()
        except Exception as e:
            logger.error("DLMOD", f"Telethon download failed [{guid}]: {e}")
            return False

    if not dest.exists() or dest.stat().st_size != message.document.size:
        logger.error("DLMOD", f"Download incomplete [{guid}]: {dest}")
        return False

    logger.success("DLMOD", f"Downloaded: {file_name}")
    guid_str_map[guid] = str(dest)
    save_mods_cache(mods_dir, guid_str_map)
    return True


class DownloadMissingMods(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.download_missing_mods
        self.mods_dir_str        : str  = cfg.get("ModsDir",             "")
        self.chara_dir_str       : str  = cfg.get("CharaDir",             "")
        self.use_cache           : bool = cfg.get("UseCache",             True)
        self.modpack_mode        : str  = cfg.get("SideloaderModpack",    "OnlyUsed")
        self.download_from_tg    : bool = cfg.get("DownloadFromTelegram", False)

    def run(self) -> None:
        game_path = self.config.game_path

        # ── Resolve directories ───────────────────────────────────────────
        if self.mods_dir_str:
            mods_dir = Path(self.mods_dir_str)
        elif "mods" in game_path:
            mods_dir = game_path["mods"]
        else:
            logger.error("DLMOD", "Mods directory not set and not resolvable from game path.")
            return

        if not mods_dir.exists():
            logger.error("DLMOD", f"Mods directory does not exist: {mods_dir}")
            return

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

        self.log_start("DLMOD")
        logger.info("DLMOD", f"Mods dir  : {mods_dir}")
        for d in chara_dirs:
            logger.info("DLMOD", f"Chara dir : {d}")
        logger.info("DLMOD", f"Modpack   : {self.modpack_mode}")

        # ── Step 1: mods cache ────────────────────────────────────────────
        guid_str_map = load_mods_cache(mods_dir) if self.use_cache else None
        if guid_str_map is None:
            logger.info("DLMOD", "Building mods cache...")
            guid_str_map = build_mods_cache(mods_dir, include_modpack=False)
            logger.info("DLMOD", f"Mods cache built: {len(guid_str_map)} local GUIDs")
        else:
            logger.info("DLMOD", f"Mods cache loaded: {len(guid_str_map)} local GUIDs")

        local_guids: set[str] = set(guid_str_map.keys())

        # ── Step 2: modpack index ─────────────────────────────────────────
        modpack_index = load_modpack_index(mods_dir) or {}
        if modpack_index:
            logger.info("DLMOD", f"Modpack index loaded: {len(modpack_index)} GUIDs")
        else:
            logger.warning("DLMOD",
                "kkafio_modpack_index.json not found — "
                "BetterRepack downloads unavailable.")

        # ── Step 3: chara GUIDs ───────────────────────────────────────────
        chara_guids = _collect_chara_guids(chara_dirs, self.use_cache)
        logger.info("DLMOD", f"Chara references: {len(chara_guids)} unique GUIDs")

        # ── Step 4: decide what to download ──────────────────────────────
        missing_local: set[str] = chara_guids - local_guids

        match self.modpack_mode:
            case "Skip":
                to_download = {g for g in missing_local if g not in modpack_index}
            case "OnlyUsed":
                to_download = missing_local
            case "All":
                to_download = missing_local | (set(modpack_index.keys()) - local_guids)
            case _:
                to_download = missing_local

        logger.info("DLMOD",
            f"Missing local: {len(missing_local)} | "
            f"To download ({self.modpack_mode}): {len(to_download)}")

        if not to_download:
            logger.success("DLMOD", "Nothing to download.")
            return

        # ── Step 5: partition ─────────────────────────────────────────────
        use_telethon = self.download_from_tg
        if not use_telethon:
            logger.info("DLMOD",
                "Download from Telegram is disabled — "
                "only BetterRepack mods will be downloaded.")

        from_betterrepack: dict[str, str] = {}
        from_telegram    : list[str]      = []
        unresolved       : list[str]      = []

        for guid in sorted(to_download):
            if guid in modpack_index:
                from_betterrepack[guid] = modpack_index[guid]
            elif use_telethon:
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

        async def _run_all() -> None:
            nonlocal ok, fail
            async with _make_http_client() as br_client, \
                       _make_http_client() as kk_client:

                # BetterRepack — concurrent
                if from_betterrepack:
                    logger.info("DLMOD",
                        f"Downloading {len(from_betterrepack)} mod(s) from BetterRepack...")
                    results = await asyncio.gather(*[
                        _download_betterrepack(br_client, guid, rel, mods_dir, guid_str_map)
                        for guid, rel in from_betterrepack.items()
                    ], return_exceptions=True)
                    for guid, result in zip(from_betterrepack, results):
                        if result is True:
                            ok += 1
                        else:
                            fail += 1
                            if isinstance(result, Exception):
                                logger.error("DLMOD", f"Exception [{guid}]: {result}")

                # Telegram via Telethon — sequential
                if from_telegram:
                    # Load/prompt for credentials once before the loop
                    tg_data = tg_cfg.get_or_prompt()
                    if tg_data is None:
                        logger.error("DLMOD",
                            "Telegram credentials not provided — "
                            f"skipping {len(from_telegram)} mod(s).")
                        fail += len(from_telegram)
                    else:
                        # Ensure session file exists before starting downloads
                        authorised = await _ensure_session(tg_data)
                        if not authorised:
                            logger.error("DLMOD",
                                "Telegram sign-in failed — "
                                f"skipping {len(from_telegram)} mod(s).")
                            fail += len(from_telegram)
                        else:
                            logger.info("DLMOD",
                                f"Looking up {len(from_telegram)} mod(s) on "
                                "koikatsucards.com + Telegram...")

                            # Create TGDownloader once and reuse across all downloads
                            from utils.constants import CONFIG_DIR as _CFG_DIR
                            _session_dir = _CFG_DIR / "config" / "tg_session"
                            teleget_downloader = None
                            try:
                                from tg_downloader import TGDownloader
                                teleget_downloader = TGDownloader(
                                    api_id=tg_data["api_id"],
                                    api_hash=tg_data["api_hash"],
                                    session_dir=str(_session_dir.resolve()),
                                )
                                await teleget_downloader.start("kkafio")
                                logger.info("DLMOD", "teleget9527 downloader started")
                            except ImportError:
                                logger.info("DLMOD",
                                    "teleget9527 not installed, using Telethon fallback "
                                    "(install with: pip install teleget9527[fast])")

                            try:
                                for guid in from_telegram:
                                    logger.info("DLMOD", f"  Looking up: {guid}")
                                    tg_link = await _get_telegram_link(kk_client, guid)
                                    if not tg_link:
                                        logger.warning("DLMOD",
                                            f"  {guid} — not found on koikatsucards.com")
                                        fail += 1
                                        continue
                                    logger.info("DLMOD", f"  Link: {tg_link}")
                                    success = await _download_via_teleget(
                                        guid, tg_link, mods_dir, tg_data,
                                        guid_str_map, teleget_downloader,
                                    )
                                    if success:
                                        ok += 1
                                    else:
                                        fail += 1
                            finally:
                                if teleget_downloader is not None:
                                    try:
                                        await teleget_downloader.shutdown()
                                    except Exception:
                                        pass  # suppress ShutdownRequest bug in teleget9527

        asyncio.run(_run_all())

        logger.line()
        logger.success("DLMOD",
            f"Done — downloaded: {ok}, failed: {fail}, "
            f"unresolved: {len(unresolved)}")