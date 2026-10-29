"""
session_flow.py — The "get me a logged-in cookie" flow shared by the
content sources that need one (koikatsucards.com, pixiv.net, pawchive.pw).

Each service describes itself with a `SessionSpec` and calls `obtain_session`:

  1. candidates, in order: the service's environment variable (if it has one),
     then the stored session (``{"session": "<cookie>"}`` in its SessionStore)
  2. a candidate that is malformed (`looks_valid`) is ignored
  3. a candidate is then checked against the site (`is_valid`, optional):
       True  -> use it
       None  -> the check proved nothing (network error, 5xx, bot block, ...):
                that is not evidence the cookie is bad, so use it as it is
       False -> expired / rejected: try the next candidate
     Without an `is_valid` the shape check alone decides (pixiv: there is no
     cheap "am I logged in?" endpoint).
  4. nothing usable: open the login page and ask the user to paste the cookie;
     the pasted value gets the same shape and site checks and is saved if it
     passes (or if the site check could not be performed)

Returns the cookie value, or None to continue without a login.
"""

from __future__ import annotations

import os
import webbrowser
from dataclasses import dataclass
from typing import Callable

from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.core.session_store import SessionStore
from kkafio.dialogs.password_dialog import password_dialog


@dataclass(frozen=True)
class SessionSpec:
    tag:        str                 # logger category, e.g. "PXVSES"
    label:      str                 # what the value is called in messages, e.g. "pixiv PHPSESSID"
    store:      SessionStore
    login_url:  str                 # opened in the browser when asking for a new value
    login_hint: str                 # log line shown with it (which cookie to copy)
    dialog_key: str                 # i18n: dialog.password.<key>_title / <key>_body
    cancel_message:    str          # logged when the dialog is cancelled
    malformed_message: str          # logged when the pasted value is clearly not this cookie
    rejected_message:  str          # logged when the site rejects the pasted value
    env_var:    str | None = None
    clean:      Callable[[str], str]        = str.strip     # tidy up what the user pasted
    looks_valid: Callable[[str], bool] | None = None        # shape check, no network
    is_valid:   Callable[[str], bool | None] | None = None  # site check: True / False / None (unknown)


def _prompt(spec: SessionSpec) -> str | None:
    """Open the login page and ask the user to paste the cookie."""
    logger.info(spec.tag, spec.login_hint)
    webbrowser.open(spec.login_url)
    value = spec.clean(password_dialog(
        t(f"dialog.password.{spec.dialog_key}_title"),
        t(f"dialog.password.{spec.dialog_key}_body"),
    ))
    return value or None


def obtain_session(spec: SessionSpec) -> str | None:
    tag, label = spec.tag, spec.label

    candidates: list[tuple[str, str]] = []
    if spec.env_var:
        env_value = spec.clean(os.environ.get(spec.env_var, ""))
        if env_value:
            candidates.append((f"the {spec.env_var} environment variable", env_value))
    stored = spec.clean(spec.store.load().get("session", ""))
    if stored:
        candidates.append(("the stored session", stored))

    for source, session in candidates:
        if spec.looks_valid and not spec.looks_valid(session):
            logger.warning(tag, f"The {label} from {source} is malformed — ignoring it.")
            continue
        if spec.is_valid is None:
            logger.info(tag, f"Using the {label} from {source}.")
            return session
        logger.info(tag, f"Validating the {label} from {source}...")
        valid = spec.is_valid(session)
        if valid is True:
            logger.info(tag, f"The {label} is valid.")
            return session
        if valid is None:
            logger.warning(tag, f"Could not validate the {label} — using it as it is.")
            return session
        logger.warning(tag, f"The {label} from {source} is invalid or expired.")

    new_session = _prompt(spec)
    if not new_session:
        logger.warning(tag, spec.cancel_message)
        return None
    if spec.looks_valid and not spec.looks_valid(new_session):
        logger.error(tag, spec.malformed_message)
        return None

    if spec.is_valid is not None:
        logger.info(tag, f"Validating the new {label}...")
        valid = spec.is_valid(new_session)
        if valid is False:
            logger.error(tag, spec.rejected_message)
            return None
        if valid is None:
            logger.warning(tag,
                f"Could not confirm the new {label} is valid (network issue) — saving it "
                "anyway; downloads will simply fail if it turns out to be wrong.")

    spec.store.save({"session": new_session})
    logger.success(tag, f"The {label} was saved.")
    return new_session
