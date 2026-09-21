"""
kkd_session.py — Manage the koikatsucards.com kkd_session cookie.

The session is stored in %APPDATA%/KKAFIO/config/kkd_session.json:
  {
    "session": "<cookie value>"
  }

Validity is checked by hitting koikatsucards.com/api/session — if the
response contains {"user": null} the session has expired and the user
is prompted to paste a new one.
"""

from __future__ import annotations

import json
import webbrowser

import httpx

from utils.constants import CONFIG_DIR
from utils.logger import logger
from utils.password_dialog import password_dialog

SESSION_FILE   = CONFIG_DIR / "config" / "kkd_session.json"
SESSION_API    = "https://koikatsucards.com/api/session"
KKD_LOGIN_URL  = "https://koikatsucards.com/login"


def _load() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("KKDSES", f"Could not read kkd_session.json: {e}")
        return {}


def _save(session: str) -> None:
    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(
            json.dumps({"session": session}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("KKDSES", f"Could not save kkd_session.json: {e}")


def _is_valid(session: str) -> bool | None:
    """Check whether the session cookie is accepted by koikatsucards.com.

    Returns True/False when the server actually answered the question (an
    expired/invalid cookie authenticates as {"user": null}, or the endpoint
    returns 401/403), and None when the check itself couldn't be performed
    (DNS/connection failure, timeout, 5xx, unexpected response shape) — the
    caller decides what to do in that case, since a network hiccup is not
    evidence that the cookie is bad.
    """
    # Cookie is scoped to koikatsucards.com only, matching how the cookie is
    # attached everywhere else it's used (see _make_client in
    # download_contents.py) — this call shouldn't send it anywhere else even
    # though this request always goes straight to SESSION_API.
    cookies = httpx.Cookies()
    cookies.set("kkd_session", session, domain="koikatsucards.com")
    try:
        r = httpx.get(
            SESSION_API,
            cookies=cookies,
            timeout=10,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        logger.warning("KKDSES", f"Could not reach koikatsucards.com to validate kkd_session: {e}")
        return None

    if r.status_code in (401, 403):
        return False           # explicitly rejected — definitely invalid
    if r.status_code >= 500:
        logger.warning("KKDSES",
            f"koikatsucards.com returned {r.status_code} while validating kkd_session")
        return None             # server-side issue, not a verdict on the cookie

    try:
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("KKDSES", f"Unexpected response validating kkd_session: {e}")
        return None

    return data.get("user") is not None


def _prompt() -> str | None:
    """Open koikatsucards.com/login and ask the user to paste the cookie."""
    logger.info("KKDSES",
        "Opening koikatsucards.com — log in, then copy the kkd_session cookie value.")
    webbrowser.open(KKD_LOGIN_URL)

    value = password_dialog(
        "koikatsucards.com Session Cookie",
        "Log in to koikatsucards.com, then:\n"
        "1. Open DevTools (F12)\n"
        "2. Go to Application → Cookies → https://koikatsucards.com\n"
        "3. Find 'kkd_session' and copy its Value\n\n"
        "Paste the value below:",
    ).strip()

    return value if value else None


def get_or_prompt() -> str | None:
    """
    Return a valid kkd_session cookie value.

    Loads the stored session and validates it against /api/session.
    If invalid or missing, opens the browser and prompts the user to
    paste a new cookie, then validates the new one before returning.
    Returns None if the user cancels or the new session is also invalid.
    """
    data    = _load()
    session = data.get("session", "")

    if session:
        logger.info("KKDSES", "Validating kkd_session against koikatsucards.com...")
        valid = _is_valid(session)
        if valid is True:
            logger.info("KKDSES", "kkd_session is valid.")
            return session
        if valid is None:
            # Couldn't reach the validation endpoint — that's not evidence
            # the cookie is bad, so use the stored one rather than prompting
            # the user for a cookie we can't confirm is any better.
            logger.warning("KKDSES",
                "Could not validate kkd_session (network issue) — using stored session as-is.")
            return session
        logger.warning("KKDSES", "kkd_session is invalid or expired.")

    # Need a new session
    new_session = _prompt()
    if not new_session:
        logger.error("KKDSES",
            "No kkd_session provided — koikatsucards.com downloads skipped.")
        return None

    logger.info("KKDSES", "Validating new kkd_session...")
    valid = _is_valid(new_session)
    if valid is False:
        logger.error("KKDSES",
            "The provided kkd_session is not valid. "
            "Make sure you are logged in and copied the correct cookie value.")
        return None
    if valid is None:
        logger.warning("KKDSES",
            "Could not confirm the new kkd_session is valid (network issue) — "
            "saving it anyway; downloads will simply fail if it turns out to be wrong.")

    _save(new_session)
    logger.success("KKDSES", "kkd_session saved and validated.")
    return new_session