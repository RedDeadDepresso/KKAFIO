"""
pixiv/session.py — Manage the pixiv.net PHPSESSID cookie.

The session is stored in %APPDATA%/KKAFIO/config/pixiv_session.json:
  {
    "session": "<PHPSESSID cookie value>"
  }

Lookup order (same idea as megaloader's Pixiv plugin):
  1. the PIXIV_PHPSESSID environment variable, if set
  2. the stored session file
  3. a dialog asking the user to paste the cookie

Unlike koikatsucards.com there is no cheap, documented "am I logged in?"
endpoint, so the value is only checked for the expected shape
(``<user id>_<random string>``). A wrong or expired cookie is not fatal: public
artworks still download without a login, only R-18 / restricted works need it.
Cancelling the dialog therefore means "continue without a session".
"""

from __future__ import annotations

import json
import os
import re
import webbrowser

from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.core.paths import CONFIG_DIR
from kkafio.dialogs.password_dialog import password_dialog

SESSION_FILE    = CONFIG_DIR / "config" / "pixiv_session.json"
PIXIV_LOGIN_URL = "https://accounts.pixiv.net/login"
ENV_VAR         = "PIXIV_PHPSESSID"

# A PHPSESSID looks like "12345678_AbCdEf0123...": the numeric user id, an
# underscore, then a long random token.
_SESSION_RE = re.compile(r"^\d+_[A-Za-z0-9]{16,}$")


def clean_session(raw: str) -> str:
    """Tidy up whatever the user pasted: strip whitespace/quotes and a leading
    ``PHPSESSID=`` (people often copy the whole ``name=value`` pair)."""
    value = (raw or "").strip().strip("\"'").strip()
    if value.lower().startswith("phpsessid="):
        value = value[len("phpsessid="):].strip().strip("\"'").strip()
    # "PHPSESSID=abc; other=cookie" — keep only the first pair's value.
    return value.split(";", 1)[0].strip()


def looks_valid(session: str) -> bool:
    """True if `session` has the shape of a pixiv PHPSESSID."""
    return bool(_SESSION_RE.match(session or ""))


def _load() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("PXVSES", f"Could not read pixiv_session.json: {e}")
        return {}


def _save(session: str) -> None:
    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(
            json.dumps({"session": session}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("PXVSES", f"Could not save pixiv_session.json: {e}")


def _prompt() -> str | None:
    """Open accounts.pixiv.net/login and ask the user to paste the cookie."""
    logger.info("PXVSES",
        "Opening pixiv.net — log in, then copy the PHPSESSID cookie value.")
    webbrowser.open(PIXIV_LOGIN_URL)

    value = clean_session(password_dialog(
        t("dialog.password.pixiv_title"),
        t("dialog.password.pixiv_body"),
    ))
    return value or None


def get_or_prompt() -> str | None:
    """
    Return a PHPSESSID cookie value, or None to continue without a login.

    Uses the environment variable or the stored session when present and
    well-formed; otherwise opens the browser and asks the user to paste one.
    Returns None if the user cancels or pastes something that is clearly not a
    PHPSESSID (public artworks can still be downloaded in that case).
    """
    env_value = clean_session(os.environ.get(ENV_VAR, ""))
    if env_value:
        if looks_valid(env_value):
            logger.info("PXVSES", f"Using PHPSESSID from {ENV_VAR}.")
            return env_value
        logger.warning("PXVSES", f"{ENV_VAR} does not look like a PHPSESSID — ignoring it.")

    session = clean_session(_load().get("session", ""))
    if session:
        if looks_valid(session):
            logger.info("PXVSES", "Using stored pixiv PHPSESSID.")
            return session
        logger.warning("PXVSES", "Stored pixiv session is malformed — asking for a new one.")

    new_session = _prompt()
    if not new_session:
        logger.warning("PXVSES",
            "No PHPSESSID provided — continuing without login "
            "(R-18 / restricted pixiv works will not be accessible).")
        return None

    if not looks_valid(new_session):
        logger.error("PXVSES",
            "That does not look like a PHPSESSID (expected something like "
            "'12345678_AbCdEf...'). Continuing without login.")
        return None

    _save(new_session)
    logger.success("PXVSES", "pixiv PHPSESSID saved.")
    return new_session
