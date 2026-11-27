"""t.me message links — every file attached to the message.

  https://t.me/<channel>/<message>             a public channel / group message
  https://t.me/c/<chat id>/<message>           a private chat (the account must be a member)
  https://t.me/<channel>/<topic>/<message>     a message inside a forum topic (also .../c/<id>/<topic>/<message>)

Every file attached to the message is downloaded into the output directory. When the message is
one of an album (Telegram sends several files at once as a group of consecutive messages), the
files of the whole album are downloaded, because that is what the person sees as "the message";
add ``?single`` to the link to take only the file of that one message (Telegram's own "Copy link"
adds it for a single item of an album).

Photos are not downloaded (Telegram recompresses them to JPEG, so a card sent as a photo is no
longer a card; cards, scenes and mods are sent as files) and neither are stickers or link
previews. Text-only messages have nothing to download and are reported as failed.

Telegram needs an account, not a login cookie: the first time a Telegram link is met KKAFIO asks
for the API ID / hash from https://my.telegram.org and signs in with a QR code (the same
session as Download Missing Mods). Files go through the teleget9527 downloader when it is
installed (much faster than Telethon alone), otherwise through Telethon.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from kkafio.core import progress
from kkafio.core.logger import logger
from kkafio.services.content_sources.shared.net import safe_filename
from kkafio.services.content_sources.shared.transfer import part_path
from kkafio.services.mod_sources.telegram.mods import _retry_flood_wait

TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.dog")

# A username is 5-32 letters / digits / underscores and starts with a letter; these first path
# segments are other kinds of t.me link, never a channel.
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_NOT_CHANNELS = {"joinchat", "addstickers", "addemoji", "addtheme", "setlanguage", "share",
                 "socks", "proxy", "login", "invoice", "boost", "giftcode", "m", "iv"}

# Albums hold at most 10 items, in consecutive message ids.
_ALBUM_SPAN = 9

# teleget9527 writes in the background: give up on a transfer whose file stops growing this long.
_STALL_SECONDS = 300
_POLL_SECONDS = 1


@dataclass(frozen=True)
class TelegramLink:
    chat: str | int          # "@username", or the marked id (-100...) of a private chat
    message_id: int
    single: bool = False     # ?single: just this message, not its whole album

    @property
    def label(self) -> str:
        """The chat as it appears in a t.me link (used for names and history keys)."""
        if isinstance(self.chat, int):
            return f"c/{str(self.chat)[4:]}"             # -100123 -> c/123
        return self.chat.lstrip("@")


def is_telegram_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in TELEGRAM_HOSTS or host == "www.t.me"


def parse_telegram_url(url: str) -> TelegramLink | None:
    """The message a t.me link points at; None for anything else (a channel without a message
    number, an invite link, a sticker pack, ...)."""
    if not is_telegram_url(url):
        return None
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[0] == "s":                        # t.me/s/<channel>/<id>: the web preview
        parts = parts[1:]
    if len(parts) < 2:
        return None

    if parts[0] == "c":
        if len(parts) < 3 or not parts[1].isdigit():
            return None
        chat: str | int = int(f"-100{parts[1]}")
        numbers = parts[2:]
    else:
        name = parts[0]
        if name.startswith("+") or name.lower() in _NOT_CHANNELS or not _USERNAME_RE.match(name):
            return None
        chat = f"@{name}"
        numbers = parts[1:]

    # <message>, or <topic>/<message>: the message is always the last number.
    if not 1 <= len(numbers) <= 2 or not all(n.isdigit() for n in numbers):
        return None
    single = any(key.lower() == "single" for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
    return TelegramLink(chat, int(numbers[-1]), single)


def telegram_chats(urls: Iterable[str]) -> list[str | int]:
    """The distinct chats of the Telegram message links among `urls`, in order of appearance."""
    chats: list[str | int] = []
    for url in urls:
        link = parse_telegram_url(url)
        if link is not None and link.chat not in chats:
            chats.append(link.chat)
    return chats


# ---------------------------------------------------------------------------
# Reading the message
# ---------------------------------------------------------------------------

async def _resolve_chat(client, chat: str | int):
    """The Telethon entity of `chat`. A private chat can only be found from the account's own
    dialog list, so that is loaded once if the plain lookup fails."""
    try:
        return await _retry_flood_wait(client.get_entity, chat, context=f"resolving chat {chat}")
    except ValueError:
        await _retry_flood_wait(client.get_dialogs, context="loading the dialog list")
        return await _retry_flood_wait(client.get_entity, chat, context=f"resolving chat {chat}")


async def _album(client, entity, message) -> list:
    """`message` plus the rest of its album (the messages with the same ``grouped_id`` next to it)."""
    if not getattr(message, "grouped_id", None):
        return [message]
    ids = list(range(max(1, message.id - _ALBUM_SPAN), message.id + _ALBUM_SPAN + 1))
    nearby = await _retry_flood_wait(client.get_messages, entity, ids=ids,
                                     context=f"reading the album of message {message.id}")
    group = [m for m in nearby if m is not None and m.grouped_id == message.grouped_id]
    return sorted(group, key=lambda m: m.id) or [message]


def _file_name(message, link: TelegramLink) -> str:
    """The name the sender gave the file, or one made from the chat, message and file type."""
    name = None
    for attr in message.document.attributes:
        name = getattr(attr, "file_name", None)
        if name:
            break
    if not name:
        name = f"{link.label.replace('/', '_')}_{message.id}{message.file.ext or ''}"
    return safe_filename(name, f"telegram_{message.id}")


def _free_name(directory: Path, name: str, key: str, history: dict[str, str], message_id: int) -> str:
    """`name`, unless a different file already has it: two messages often both carry e.g.
    ``000.png``, and the second must not overwrite the first. The message id is added then."""
    def taken(candidate: str) -> bool:
        path = directory / candidate
        return path.exists() and history.get(key) != str(path)    # our own earlier copy is fine

    if not taken(name):
        return name
    stem, ext = os.path.splitext(name)
    candidate = f"{stem}_{message_id}{ext}"
    counter = 2
    while taken(candidate):
        candidate = f"{stem}_{message_id}_{counter}{ext}"
        counter += 1
    return candidate


# ---------------------------------------------------------------------------
# Downloading one file
# ---------------------------------------------------------------------------

async def _download_with_teleget(downloader, raw_chat_id: int, message, part: Path, size: int, handle) -> None:
    """Hand the file to teleget9527 and wait until it has all arrived. It writes in the background,
    so the file is watched: done when it reaches `size`, an error if it stops growing."""
    def _on_progress(downloaded: int, total: int, pct: float) -> None:
        handle.update(downloaded, total)

    await downloader.download(
        chat_id=raw_chat_id, msg_id=message.id,
        save_path=str(part.resolve()), progress_callback=_on_progress)

    last_size, last_change = -1, 0.0
    loop = asyncio.get_running_loop()
    while True:
        current = part.stat().st_size if part.exists() else 0
        if current >= size:
            return
        now = loop.time()
        if current != last_size:
            last_size, last_change = current, now
        elif now - last_change > _STALL_SECONDS:
            raise TimeoutError(f"no progress for {_STALL_SECONDS}s ({current} of {size} bytes)")
        await asyncio.sleep(_POLL_SECONDS)


async def _download_message_file(
    client, downloader, raw_chat_id: int | None, link: TelegramLink, message,
    directory: Path, history: dict[str, str], skip: bool,
) -> str:
    """Download the file of one message. Returns "ok", "skipped" or "failed"."""
    document = message.document
    key = f"https://t.me/{link.label}/{message.id}#{document.id}"
    name = _file_name(message, link)

    if skip and key in history:
        logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[key]).name}")
        progress.count("skipped")
        return "skipped"

    size = int(document.size or 0)
    name = _free_name(directory, name, key, history, message.id)
    dest = directory / name
    part = part_path(directory, name, key)
    part.unlink(missing_ok=True)            # neither teleget nor Telethon can continue a part
    logger.info("DLOAD", f"Downloading: {name}")
    handle = progress.begin(name, size)

    try:
        done = False
        if downloader is not None and raw_chat_id is not None:
            try:
                await _download_with_teleget(downloader, raw_chat_id, message, part, size, handle)
                done = True
            except Exception as exc:
                logger.warning("DLOAD", f"  teleget9527 failed for {name} ({exc}) — trying Telethon.")
                part.unlink(missing_ok=True)
                # teleget may still be writing to `part`; Telethon gets a part of its own.
                part = part.with_name(part.name + ".tl")
                part.unlink(missing_ok=True)
        if not done:
            await _retry_flood_wait(
                client.download_media, message, file=str(part),
                progress_callback=lambda cur, total: handle.update(cur, total),
                context=f"downloading {name}")

        if not part.exists() or (size and part.stat().st_size != size):
            have = part.stat().st_size if part.exists() else 0
            raise RuntimeError(f"incomplete download ({have} of {size} bytes)")
        os.replace(part, dest)
        history[key] = str(dest)
        handle.close(ok=True)
        return "ok"
    except Exception as exc:
        part.unlink(missing_ok=True)
        handle.close()
        logger.error("DLOAD", f"  Failed: {name} — {exc}")
        progress.count("failed")
        return "failed"
    finally:
        handle.close()                       # no-op after a success; drops the row on a cancel


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def download_telegram(
    telegram, url: str, directory: Path, history: dict[str, str], skip: bool,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). `telegram` is what ``telegram_session`` yields:
    (credentials, connected Telethon client, teleget9527 downloader or None)."""
    link = parse_telegram_url(url)
    if link is None:
        logger.error("DLOAD",
            f"Unsupported Telegram link (use a message link such as https://t.me/channel/123 "
            f"or https://t.me/c/1234567890/123): {url}")
        return 0, 0, 1
    _tg_data, client, downloader = telegram

    where = f"{link.chat} #{link.message_id}"
    logger.info("DLOAD", f"Telegram message: {where}")

    try:
        entity = await _resolve_chat(client, link.chat)
    except Exception as exc:
        logger.error("DLOAD", f"  Could not open chat {link.chat}: {exc} "
                              f"(a private chat needs this Telegram account to be a member)")
        return 0, 0, 1

    try:
        message = await _retry_flood_wait(
            client.get_messages, entity, ids=link.message_id, context=f"reading message {where}")
    except Exception as exc:
        logger.error("DLOAD", f"  Could not read message {where}: {exc}")
        return 0, 0, 1
    if message is None:
        logger.error("DLOAD", f"  Message {where} was not found (deleted, or not visible to this account).")
        return 0, 0, 1

    try:
        messages = [message] if link.single else await _album(client, entity, message)
    except Exception as exc:
        logger.warning("DLOAD", f"  Could not read the rest of the album ({exc}) — using this message only.")
        messages = [message]

    files = [m for m in messages
             if getattr(m, "document", None) is not None and not getattr(m, "sticker", None)]
    if not files:
        logger.error("DLOAD", f"  Message {where} has no file to download "
                              f"(only text, photos, stickers or a link preview).")
        return 0, 0, 1
    if len(messages) > 1:
        logger.info("DLOAD", f"  Album of {len(messages)} message(s): {len(files)} file(s).")

    raw_chat_id: int | None = None
    if downloader is not None:
        try:
            from telethon.utils import get_peer_id
            raw_chat_id = get_peer_id(entity)       # the marked id teleget9527 needs (see mods.py)
        except Exception as exc:
            logger.warning("DLOAD", f"  Could not get the chat id for teleget9527 ({exc}) — using Telethon.")

    directory.mkdir(parents=True, exist_ok=True)
    ok = skipped = failed = 0
    for file_message in files:                       # one at a time: Telegram throttles parallel transfers
        result = await _download_message_file(
            client, downloader, raw_chat_id, link, file_message, directory, history, skip)
        if result == "ok":
            ok += 1
        elif result == "skipped":
            skipped += 1
        else:
            failed += 1
    return ok, skipped, failed
