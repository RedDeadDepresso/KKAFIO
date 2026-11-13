"""
qr_login.py — Sign in to Telegram by scanning a QR code with the phone app.

Telegram's login tokens expire after ~30 s, so the code is renewed (`QRLogin.recreate`)
and redrawn until the user scans it, cancels, asks for the phone-number flow, or the
overall time limit runs out.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from kkafio.core.logger import logger

QRResult = Literal["ok", "password", "phone", "cancelled"]

_TOKEN_SECONDS = 20      # Telegram tokens live ~30 s; renew well before that (scan + DC switch need slack)
_MAX_SECONDS = 300       # give up on QR (and fall back to the phone number) after this
_TICK_SECONDS = 0.1


async def sign_in_with_qr(client) -> QRResult:
    """
    Run the QR login on a connected, not-yet-authorised Telethon `client`.

    "ok"        signed in (the session is saved by Telethon)
    "password"  the QR was scanned but the account has 2FA: call `client.sign_in(password=...)`
    "phone"     QR can't be used (no qrcode package, error, timed out) or the user asked for the
                phone-number flow: carry on with `send_code_request`
    "cancelled" the user cancelled
    """
    from telethon.errors import SessionPasswordNeededError

    try:
        from kkafio.dialogs import qr_dialog
        qr_dialog._matrix("probe")                    # fails early if `qrcode` is missing
    except Exception as exc:
        logger.warning("QRLOGIN", f"QR code sign-in unavailable ({exc}) — using the phone number instead.")
        return "phone"

    window = qr_dialog.open_qr_window()
    deadline = time.monotonic() + _MAX_SECONDS
    try:
        qr = await client.qr_login()
        while time.monotonic() < deadline:
            if window is not None:
                window.set_url(qr.url)
            else:
                qr_dialog.print_terminal_qr(qr.url)

            wait = asyncio.ensure_future(qr.wait(timeout=_TOKEN_SECONDS))
            try:
                while not wait.done():
                    if window is not None:
                        window.refresh()
                        if window.choice is not None:
                            return "cancelled" if window.choice == "cancel" else "phone"
                    await asyncio.wait({wait}, timeout=_TICK_SECONDS)
                wait.result()
                return "ok"
            except SessionPasswordNeededError:
                return "password"
            except asyncio.TimeoutError:
                await qr.recreate()                   # token expired unscanned: new code
            except Exception as exc:
                # Telegram's AUTH_TOKEN_EXPIRED: the code was scanned (or the DC switch
                # finished) after the token's ~30 s life. Not a failure: show a new code.
                if "TokenExpired" not in type(exc).__name__:
                    raise
                logger.info("QRLOGIN", "QR code expired — showing a new one.")
                await qr.recreate()
            finally:
                if not wait.done():
                    wait.cancel()
        logger.warning("QRLOGIN", "QR code was not scanned in time — using the phone number instead.")
        return "phone"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("QRLOGIN", f"QR code sign-in failed ({type(exc).__name__}: {exc}) — using the phone number instead.")
        return "phone"
    finally:
        if window is not None:
            window.destroy()
