"""
pawchive/session.py — Manage the pawchive.pw ``session`` cookie.

The session is stored in %APPDATA%/KKAFIO/config/pawchive_session.json:
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

import json
import os
import re
import webbrowser
from importlib import metadata

import httpx

from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.core.paths import CONFIG_DIR
from kkafio.dialogs.password_dialog import password_dialog

SESSION_FILE      = CONFIG_DIR / "config" / "pawchive_session.json"
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


def _load() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("PAWSES", f"Could not read pawchive_session.json: {e}")
        return {}


def _save(session: str) -> None:
    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(
            json.dumps({"session": session}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("PAWSES", f"Could not save pawchive_session.json: {e}")


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


def _prompt() -> str | None:
    """Open pawchive.pw/account/login and ask the user to paste the cookie."""
    logger.info("PAWSES",
        "Opening pawchive.pw — log in, then copy the session cookie value.")
    webbrowser.open(PAWCHIVE_LOGIN_URL)

    value = clean_session(password_dialog(
        t("dialog.password.pawchive_title"),
        t("dialog.password.pawchive_body"),
    ))
    return value or None


def get_or_prompt() -> str | None:
    """
    Return a pawchive.pw ``session`` cookie value, or None to continue without one.

    Uses the environment variable or the stored session when it is accepted by
    pawchive.pw (or when that can't be checked); otherwise opens the browser and
    asks the user to paste one. Returns None if the user cancels, or pastes
    something that is clearly not a session cookie or that pawchive.pw rejects.
    """
    candidates: list[tuple[str, str]] = []
    env_value = clean_session(os.environ.get(ENV_VAR, ""))
    if env_value:
        candidates.append((f"{ENV_VAR}", env_value))
    stored = clean_session(_load().get("session", ""))
    if stored:
        candidates.append(("stored", stored))

    for source, session in candidates:
        if not looks_valid(session):
            logger.warning("PAWSES", f"The {source} pawchive session is malformed — ignoring it.")
            continue
        logger.info("PAWSES", f"Validating the {source} pawchive session...")
        valid = _is_valid(session)
        if valid is True:
            logger.info("PAWSES", "pawchive session is valid.")
            return session
        if valid is None:
            # Not evidence the cookie is bad — don't nag for a replacement.
            logger.warning("PAWSES",
                "Could not validate the pawchive session — using it as it is.")
            return session
        logger.warning("PAWSES", f"The {source} pawchive session is invalid or expired.")

    new_session = _prompt()
    if not new_session:
        logger.warning("PAWSES",
            "No pawchive session provided — oversized temporary files "
            "(t1.pawchive.pw) will not be downloaded.")
        return None

    if not looks_valid(new_session):
        logger.error("PAWSES",
            "That does not look like a pawchive session cookie. Continuing without login.")
        return None

    logger.info("PAWSES", "Validating new pawchive session...")
    valid = _is_valid(new_session)
    if valid is False:
        logger.error("PAWSES",
            "The provided pawchive session is not valid. "
            "Make sure you are logged in and copied the value of the 'session' cookie.")
        return None
    if valid is None:
        logger.warning("PAWSES",
            "Could not confirm the new pawchive session is valid (network issue) — "
            "saving it anyway; the downloads will simply fail if it turns out to be wrong.")

    _save(new_session)
    logger.success("PAWSES", "pawchive session saved.")
    return new_session