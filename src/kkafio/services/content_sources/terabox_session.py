"""
terabox_session.py — Manage the TeraBox ``ndus`` login cookie.

The session is stored in %APPDATA%/KKAFIO/sessions/terabox.json:
  {
    "session": "<ndus cookie value>"
  }

Lookup order (same idea as pixiv/session.py):
  1. the TERABOX_NDUS environment variable, if set
  2. the stored session file
  3. a dialog asking the user to paste the cookie

TeraBox has no cheap "am I logged in?" endpoint, so the value is only checked for its shape. A
missing or expired cookie is not fatal: whatever TeraBox lets an anonymous visitor download still
downloads, only the files that need an account fail. Cancelling the dialog therefore means
"continue without a login".
"""

from __future__ import annotations

import re

from kkafio.core.paths import SESSIONS_DIR
from kkafio.services.content_sources.session_flow import SessionSpec, obtain_session
from kkafio.core.session_store import SessionStore

TERABOX_LOGIN_URL = "https://www.terabox.com/login"
COOKIE_NAME       = "ndus"
ENV_VAR           = "TERABOX_NDUS"

# An ndus value is one long URL-safe token.
_SESSION_RE = re.compile(r"^[A-Za-z0-9._~+/=%-]{16,}$")


def clean_session(raw: str) -> str:
    """Tidy up whatever the user pasted: strip whitespace/quotes and pick the ``ndus`` value out of
    a ``name=value`` pair or a whole Cookie header (``a=b; ndus=xyz``)."""
    text = (raw or "").strip().strip("\"'").strip()
    pairs = [p.strip() for p in text.split(";") if p.strip()]
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if sep and name.strip().lower() == COOKIE_NAME:
            return value.strip().strip("\"'").strip()
    # No "ndus=" pair: a bare value is the first (only) piece without a name.
    if pairs and "=" not in pairs[0].rstrip("="):
        return pairs[0].strip("\"'").strip()
    return ""


def looks_valid(session: str) -> bool:
    """True if `session` has the shape of an ndus token."""
    return bool(_SESSION_RE.match(session or ""))


SPEC = SessionSpec(
    tag="TBXSES",
    label="TeraBox ndus cookie",
    store=SessionStore(SESSIONS_DIR / "terabox.json", "TBXSES"),
    env_var=ENV_VAR,
    login_url=TERABOX_LOGIN_URL,
    login_hint="Opening terabox.com — log in, then copy the ndus cookie value.",
    dialog_key="terabox",
    clean=clean_session,
    looks_valid=looks_valid,
    cancel_message=("No ndus cookie provided — continuing without login "
                    "(TeraBox files that need an account will fail)."),
    malformed_message=("That does not look like an ndus cookie. Continuing without login."),
    rejected_message="",            # no site check
)


def get_or_prompt() -> str | None:
    """Return the ndus cookie value, or None to continue without a login.

    Uses the environment variable or the stored session when well-formed; otherwise opens the
    browser and asks the user to paste one. Returns None if the user cancels or pastes something
    that is clearly not an ndus cookie.
    """
    return obtain_session(SPEC)
