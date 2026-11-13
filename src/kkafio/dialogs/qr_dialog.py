"""
qr_dialog.py — A CustomTkinter window that shows a login QR code (Telegram).

Like the progress window, it does not block: Tk is driven from the asyncio loop the
caller runs in (``refresh()`` is called every ~100 ms), so the window stays on the
main thread while the login is awaited::

    window = open_qr_window()              # None where no window can be made
    window.set_url(qr_login.url)           # draw / redraw the code
    while window.choice is None: window.refresh(); await asyncio.sleep(0.1)
    window.destroy()

``choice`` becomes ``"cancel"`` (Cancel / window closed) or ``"phone"`` (the user wants
to sign in with a phone number instead). Where no window is possible (no display,
tests) ``open_qr_window()`` returns None and callers use ``print_terminal_qr()``.
"""

from __future__ import annotations

import time

from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.dialogs.common import _center_on_screen, _get_host, new_window

_IMAGE_PX = 300
_WIDTH = 360
_QUIET_ZONE = 4               # modules of white border the QR standard asks for
_SHOW_DELAY_SECONDS = 0.4     # same reason as progress_dialog: let CustomTkinter finish its setup


def _matrix(url: str) -> list[list[bool]]:
    import qrcode

    qr = qrcode.QRCode(border=_QUIET_ZONE, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.get_matrix()


def render_qr_image(url: str, size: int = _IMAGE_PX):
    """The QR code of `url` as a black-on-white PIL image of about `size` pixels (whole pixels per module)."""
    from PIL import Image

    matrix = _matrix(url)
    n = len(matrix)
    img = Image.new("L", (n, n), 255)
    img.putdata([0 if cell else 255 for row in matrix for cell in row])
    box = max(1, size // n)
    return img.resize((n * box, n * box), Image.NEAREST).convert("RGB")


def print_terminal_qr(url: str) -> None:
    """Print the QR code as text, for terminals / machines without a display."""
    import qrcode

    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    print()
    qr.print_ascii(invert=True)
    print()
    print(t("dialog.qr.terminal_body"))


class QRWindow:
    def __init__(self) -> None:
        import customtkinter as ctk

        self.choice: str | None = None
        self.alive = True
        self._image = None                      # keeps the CTkImage alive
        self._created = time.monotonic()
        self._visible = False

        win = self.win = new_window(t("dialog.qr.title"))
        win.resizable(False, False)
        win.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(win, text=t("dialog.qr.body"), wraplength=_WIDTH - 24, justify="left",
                     anchor="w").grid(row=0, column=0, padx=12, pady=(14, 8), sticky="w")

        self._qr_label = ctk.CTkLabel(win, text="", width=_IMAGE_PX, height=_IMAGE_PX)
        self._qr_label.grid(row=1, column=0, padx=12, pady=4)

        buttons = ctk.CTkFrame(win, fg_color="transparent")
        buttons.grid(row=2, column=0, padx=12, pady=12, sticky="e")
        ctk.CTkButton(buttons, text=t("dialog.qr.use_phone"),
                      command=lambda: self._pick("phone")).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text=t("dialog.cancel"), width=90,
                      command=lambda: self._pick("cancel")).pack(side="left")
        win.bind("<Escape>", lambda _e: self._pick("cancel"))
        win.protocol("WM_DELETE_WINDOW", lambda: self._pick("cancel"))

    def _pick(self, choice: str) -> None:
        if self.choice is None:
            self.choice = choice

    def set_url(self, url: str) -> None:
        import customtkinter as ctk

        img = render_qr_image(url)
        self._image = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        self._qr_label.configure(image=self._image)

    def refresh(self) -> None:
        """Let Tk handle its pending events (and show the window once it is ready)."""
        import tkinter as tk

        if not self.alive:
            return
        try:
            self.win.update_idletasks()
            self.win.update()
            if not self._visible and time.monotonic() - self._created >= _SHOW_DELAY_SECONDS:
                self._visible = True
                _center_on_screen(self.win)
                self.win.deiconify()
                self.win.lift()
        except tk.TclError as exc:
            logger.warning("QRLOGIN", f"QR window was lost: {exc}")
            self.alive = False
            self._pick("cancel")

    def destroy(self) -> None:
        import tkinter as tk

        self.alive = False
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def open_qr_window() -> QRWindow | None:
    """Open the QR window, or None where none can be made (no display, no tkinter)."""
    try:
        _get_host()                 # the hidden root; fails first if there is no display
        return QRWindow()
    except Exception as exc:
        logger.warning("QRLOGIN", f"QR window unavailable — using the terminal instead: {exc}")
        return None
