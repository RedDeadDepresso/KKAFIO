"""
koikatsucards/session.py — Manage the koikatsucards.com kkd_session cookie.

The session is stored in %APPDATA%/KKAFIO/sessions/koikatsucards.json:
  {
    "session": "<cookie value>"
  }

Validity is checked by hitting koikatsucards.com/api/session — if the
response contains {"user": null} the session has expired and the user
is prompted to paste a new one.
"""

from __future__ import annotations

import httpx

from kkafio.core.logger import logger
from kkafio.core.paths import SESSIONS_DIR
from kkafio.core.session_store import SessionStore
from kkafio.services.content_sources.session_flow import SessionSpec, obtain_session

SESSION_API    = "https://koikatsucards.com/api/session"
KKD_LOGIN_URL  = "https://koikatsucards.com/login"


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


SPEC = SessionSpec(
    tag="KKDSES",
    label="kkd_session",
    store=SessionStore(SESSIONS_DIR / "koikatsucards.json", "KKDSES"),
    login_url=KKD_LOGIN_URL,
    login_hint="Opening koikatsucards.com — log in, then copy the kkd_session cookie value.",
    dialog_key="kkd",
    cancel_message="No kkd_session provided — koikatsucards.com downloads skipped.",
    malformed_message="",           # no shape check
    is_valid=_is_valid,
    rejected_message=("The provided kkd_session is not valid. "
                      "Make sure you are logged in and copied the correct cookie value."),
)


def get_or_prompt() -> str | None:
    """Return a valid kkd_session cookie value.

    Loads the stored session and validates it against /api/session. If invalid
    or missing, opens the browser and prompts the user to paste a new cookie,
    then validates the new one before returning. Returns None if the user
    cancels or the new session is also invalid.
    """
    return obtain_session(SPEC)
