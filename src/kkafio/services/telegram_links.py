"""Parsing Telegram links and the user's list of chats to search (pure string handling, no network)."""

import re

from kkafio.core.logger import logger


# ---------------------------------------------------------------------------
# Telethon download
# ---------------------------------------------------------------------------

def parse_tme_link(tg_link: str) -> tuple[str, int] | None:
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

telegram_source_labelS = {
    "No":            "No",
    "KoikatsuCards": "koikatsucards.com",
    "ChatLinks":     "Telegram Chat Links",
    "Both":          "koikatsucards.com + Telegram Chat Links",
}


def telegram_source_label(source: str) -> str:
    return telegram_source_labelS.get(source, source)


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


def parse_chat_links(raw: str) -> list[tuple[str | int, int | None]]:
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
