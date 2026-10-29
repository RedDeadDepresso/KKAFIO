"""
pixiv/session.py — Manage the pixiv.net PHPSESSID cookie.

The session is stored in %APPDATA%/KKAFIO/sessions/pixiv.json:
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

import re

from kkafio.core.paths import SESSIONS_DIR
from kkafio.core.session_store import SessionStore
from kkafio.services.content_sources.session_flow import SessionSpec, obtain_session

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


SPEC = SessionSpec(
    tag="PXVSES",
    label="pixiv PHPSESSID",
    store=SessionStore(SESSIONS_DIR / "pixiv.json", "PXVSES"),
    env_var=ENV_VAR,
    login_url=PIXIV_LOGIN_URL,
    login_hint="Opening pixiv.net — log in, then copy the PHPSESSID cookie value.",
    dialog_key="pixiv",
    clean=clean_session,
    looks_valid=looks_valid,
    cancel_message=("No PHPSESSID provided — continuing without login "
                    "(R-18 / restricted pixiv works will not be accessible)."),
    malformed_message=("That does not look like a PHPSESSID (expected something like "
                       "'12345678_AbCdEf...'). Continuing without login."),
    rejected_message="",            # no site check
)


def get_or_prompt() -> str | None:
    """Return a PHPSESSID cookie value, or None to continue without a login.

    Uses the environment variable or the stored session when well-formed;
    otherwise opens the browser and asks the user to paste one. Returns None if
    the user cancels or pastes something that is clearly not a PHPSESSID
    (public artworks can still be downloaded in that case).
    """
    return obtain_session(SPEC)
