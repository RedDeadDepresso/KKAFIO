"""
pawchive/session.py — Manage the pawchive.pw ``session`` cookie.

The session is stored in %APPDATA%/KKAFIO/sessions/pawchive.json:
  {
    "session": "<session cookie value>"
  }

Only needed for the oversized files Pawchive keeps in its temporary storage on
``t1.pawchive.pw``: those are served to logged-in accounts. Everything else on
pawchive.pw downloads without a login, so Download Contents asks for the cookie
only when it is about to download such a file (never up front).

Lookup order (same idea as pixiv/session.py):
  1. the PAWCHIVE_SESSION environment variable, if set
  2. the stored session file
  3. a dialog asking the user to paste the cookie

A stored or pasted cookie is checked against pawchive.pw/account (like
koikatsucards/session.py checks koikatsucards.com/api/session): a redirect or 401 means
the session is invalid or expired and a new one is asked for; any other
answer that is not a clear "yes" (network error, 403, 5xx) is not evidence
that the cookie is bad, so the cookie is used as it is. Cancelling the dialog
means "continue without a session" — the temporary files then fail.
"""

from __future__ import annotations

import re
from importlib import metadata

import httpx

from kkafio.core.logger import logger
from kkafio.core.paths import SESSIONS_DIR
from kkafio.core.session_store import SessionStore
from kkafio.services.content_sources.session_flow import SessionSpec, obtain_session

PAWCHIVE_LOGIN_URL = "https://pawchive.pw/account/login"
ACCOUNT_URL       = "https://pawchive.pw/account"
COOKIE_NAME       = "session"
ENV_VAR           = "PAWCHIVE_SESSION"


def _version() -> str:
    try:
        return metadata.version("kkafio")
    except metadata.PackageNotFoundError:
        return "dev"


# Pawchive serves its API and files to clients that say what they are; a
# browser User-Agent on a non-browser TLS / HTTP2 fingerprint is what gets
# blocked. Used by every pawchive.pw request KKAFIO makes.
USER_AGENT = f"KKAFIO/{_version()} (Download Contents)"

# A bare session value is a long URL-safe token (usually a JWT, "eyJ...").
_SESSION_RE = re.compile(r"^[A-Za-z0-9._~+/=%-]{16,}$")


def clean_session(raw: str) -> str:
    """Tidy up whatever the user pasted: strip whitespace/quotes and pick the
    ``session`` value out of a ``name=value`` pair or a whole Cookie header
    (``a=b; session=xyz``)."""
    text = (raw or "").strip().strip("\"'").strip()
    pairs = [p.strip() for p in text.split(";") if p.strip()]
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if sep and name.strip().lower() == COOKIE_NAME:
            return value.strip().strip("\"'").strip()
    # No "session=" pair: a bare value is the first (only) piece without a name.
    if pairs and "=" not in pairs[0].rstrip("="):
        return pairs[0].strip("\"'").strip()
    return ""


def looks_valid(session: str) -> bool:
    """True if `session` has the shape of a pawchive session token."""
    return bool(_SESSION_RE.match(session or ""))


def _is_valid(session: str) -> bool | None:
    """Check whether pawchive.pw accepts the session cookie.

    True: /account answered 200 for it. False: it redirected to the login page
    or answered 401 — the session is invalid or expired. None: the check could
    not be performed or proves nothing (connection failure, timeout, 403 — which
    may be a bot block rather than a verdict on the cookie —, 5xx, ...).
    """
    # Scoped to pawchive.pw only, like the cookie of the download client.
    cookies = httpx.Cookies()
    cookies.set(COOKIE_NAME, session, domain="pawchive.pw")
    try:
        r = httpx.get(
            ACCOUNT_URL,
            cookies=cookies,
            headers={"User-Agent": USER_AGENT, "Referer": "https://pawchive.pw/"},
            timeout=10,
            follow_redirects=False,
        )
    except httpx.HTTPError as e:
        logger.warning("PAWSES", f"Could not reach pawchive.pw to validate the session: {e}")
        return None

    if r.status_code == 200:
        return True
    if r.status_code in (301, 302, 303, 307, 308, 401):
        return False
    logger.warning("PAWSES",
        f"pawchive.pw returned {r.status_code} while validating the session")
    return None


SPEC = SessionSpec(
    tag="PAWSES",
    label="pawchive session",
    store=SessionStore(SESSIONS_DIR / "pawchive.json", "PAWSES"),
    env_var=ENV_VAR,
    login_url=PAWCHIVE_LOGIN_URL,
    login_hint="Opening pawchive.pw — log in, then copy the session cookie value.",
    dialog_key="pawchive",
    clean=clean_session,
    looks_valid=looks_valid,
    is_valid=_is_valid,
    cancel_message=("No pawchive session provided — oversized temporary files "
                    "(t1.pawchive.pw) will not be downloaded."),
    malformed_message="That does not look like a pawchive session cookie. Continuing without login.",
    rejected_message=("The provided pawchive session is not valid. "
                      "Make sure you are logged in and copied the value of the 'session' cookie."),
)


def get_or_prompt() -> str | None:
    """Return a pawchive.pw ``session`` cookie value, or None to continue without one.

    Uses the environment variable or the stored session when pawchive.pw accepts
    it (or when that can't be checked); otherwise opens the browser and asks the
    user to paste one. Returns None if the user cancels, or pastes something that
    is clearly not a session cookie or that pawchive.pw rejects.
    """
    return obtain_session(SPEC)
