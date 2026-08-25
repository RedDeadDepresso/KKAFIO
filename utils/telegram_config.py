"""
telegram_config.py — Load and save Telegram API credentials.

Credentials are stored in %APPDATA%/KKAFIO/config/telegram.json:
  {
    "api_id":   12345678,
    "api_hash": "abcdef...",
    "session":  "1BVtsO..."   # Telethon StringSession, written after first auth
  }

The file is never touched by MXU so there is no sync conflict.
"""

from __future__ import annotations

import json
import webbrowser

from utils.constants import TELEGRAM_CONFIG
from utils.logger import logger
from utils.password_dialog import password_dialog


def load() -> dict:
    """Return stored credentials or an empty dict."""
    try:
        return json.loads(TELEGRAM_CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("TGCFG", f"Could not read telegram.json: {e}")
        return {}


def save(data: dict) -> None:
    """Persist credentials dict to telegram.json."""
    try:
        TELEGRAM_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        TELEGRAM_CONFIG.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("TGCFG", f"Could not save telegram.json: {e}")


def is_complete(data: dict) -> bool:
    """Return True if api_id and api_hash are set."""
    return bool(data.get("api_id") and data.get("api_hash"))


def prompt_for_credentials() -> dict:
    """
    Show dialogs to collect api_id and api_hash from the user.
    Opens https://my.telegram.org in the browser automatically so the user
    can copy the values directly.

    Returns a dict with api_id and api_hash (session still empty at this point).
    """
    logger.info("TGCFG",
        "Telegram API credentials not configured. "
        "Opening https://my.telegram.org — log in, click "
        "'API development tools', and copy your api_id and api_hash.")

    webbrowser.open("https://my.telegram.org/apps")

    api_id_str = password_dialog(
        "Telegram API ID",
        "Enter your Telegram API ID\n"
        "(found on https://my.telegram.org → API development tools):",
    ).strip()

    if not api_id_str or not api_id_str.isdigit():
        logger.error("TGCFG", "Invalid or empty API ID — Telegram download skipped.")
        return {}

    api_hash = password_dialog(
        "Telegram API Hash",
        "Enter your Telegram API Hash\n"
        "(found on https://my.telegram.org → API development tools):",
    ).strip()

    if not api_hash:
        logger.error("TGCFG", "Empty API Hash — Telegram download skipped.")
        return {}

    return {"api_id": int(api_id_str), "api_hash": api_hash, "session": ""}


def get_or_prompt() -> dict | None:
    """
    Load credentials from telegram.json. If api_id or api_hash are missing,
    show the collection dialogs. Returns None if the user cancels.
    """
    data = load()

    if not data.get("api_id") or not data.get("api_hash"):
        data = prompt_for_credentials()
        if not data:
            return None
        save(data)

    return data



