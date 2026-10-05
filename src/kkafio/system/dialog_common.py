"""
dialog_common.py — Shared helpers for the CustomTkinter dialogs
(password_dialog.py, llm_dialog.py).

Everything here exists to reproduce the window behaviour the old PowerShell
WinForms dialogs had:

* always on top, centered on the screen;
* forced to the foreground even when the CLI was launched from a hidden /
  background process (Windows "foreground lock" can otherwise leave the
  window behind whatever app currently has focus, e.g. a browser);
* Ctrl+C / CTRL_BREAK (see cli.install_graceful_stop_handler) still works
  while the dialog is open.

The dialogs run in-process on the calling thread and block it until the window
closes, exactly like the old ``proc.wait()`` did. Tk must therefore only be used
from the thread that calls the dialog function (the main thread in KKAFIO).

Window model: one hidden, never-shown CTk root ("host") is created lazily and
kept for the life of the process; every dialog is a CTkToplevel on it.
Creating and destroying a fresh CTk root per dialog is tempting but leaves
CustomTkinter's internal DPI/appearance timers orphaned on the destroyed root,
which then fire in the next dialog's event loop and print "invalid command
name" errors to stderr.
"""

import sys

# How often Tk hands control back to Python. A Tk mainloop() that is sitting
# idle never runs Python-level signal handlers, so without this periodic no-op
# a KeyboardInterrupt (raised by the CTRL_BREAK / SIGTERM handler installed in
# cli.py) would not be noticed until the user touched the window.
_SIGNAL_TICK_MS = 100

# Delay before grabbing the foreground. CustomTkinter finishes some Windows
# window setup (title bar colour, icon) shortly after the mainloop starts, and
# grabbing focus before that can be undone by it.
_FOREGROUND_DELAY_MS = 100


_host = None


def _get_host():
    """Return the hidden CTk root all dialogs hang off, creating it once."""
    global _host
    import tkinter as tk
    import customtkinter as ctk

    if _host is not None:
        try:
            if _host.winfo_exists():
                return _host
        except tk.TclError:
            pass
    ctk.set_appearance_mode("system")
    _host = ctk.CTk()
    _host.withdraw()
    return _host


def new_window(title: str, topmost: bool = True):
    """Create a dialog window: titled, always on top (unless `topmost` is
    False, for long-lived windows the user should be able to put behind other
    apps), hidden until show_and_wait() has laid it out and centered it (so it
    never flashes in the wrong place)."""
    import customtkinter as ctk

    win = ctk.CTkToplevel(_get_host())
    win.withdraw()
    win.title(title)
    if topmost:
        win.attributes("-topmost", True)
    return win


def _center_on_screen(root) -> None:
    """Size the window to its content and center it on the primary screen."""
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    # Never bigger than the screen (with a margin for the taskbar / title bar),
    # however large the content asks to be.
    w = min(w, int(root.winfo_screenwidth() * 0.9))
    h = min(h, int(root.winfo_screenheight() * 0.85))
    x = max(0, (root.winfo_screenwidth() - w) // 2)
    y = max(0, (root.winfo_screenheight() - h) // 2)
    # wm_geometry (raw Tk, real pixels) rather than CTk's geometry(), which
    # would apply CustomTkinter's DPI scaling a second time to w and h.
    root.wm_geometry(f"{w}x{h}+{x}+{y}")


def _grab_foreground(root, focus_widget, on_shown) -> None:
    import tkinter as tk

    try:
        root.lift()
        root.focus_force()
        if sys.platform == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            # winfo_id() is Tk's inner child window; the real top-level
            # window that SetForegroundWindow needs is its parent.
            hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
            user32.SetForegroundWindow(hwnd)
        if focus_widget is not None:
            focus_widget.focus_set()
        if on_shown is not None:
            on_shown()
    except tk.TclError:
        pass  # window was closed before the delayed call ran


def show_and_wait(root, focus_widget=None, on_shown=None) -> None:
    """Center, show and run ``root`` until a handler calls ``root.quit()``,
    then destroy it. KeyboardInterrupt propagates after the window is gone.

    ``on_shown`` (optional) is called once, shortly after the window is up and
    has been brought to the foreground.
    """
    import tkinter as tk

    _center_on_screen(root)
    root.deiconify()
    timers = {
        "foreground": root.after(_FOREGROUND_DELAY_MS, lambda: _grab_foreground(root, focus_widget, on_shown)),
    }

    def _tick() -> None:
        timers["tick"] = root.after(_SIGNAL_TICK_MS, _tick)

    timers["tick"] = root.after(_SIGNAL_TICK_MS, _tick)

    try:
        root.mainloop()
    finally:
        # Cancel our timers before destroying: an `after` callback that is
        # still queued would otherwise fire later (during the next dialog's
        # event loop) and print "invalid command name" errors.
        for after_id in timers.values():
            try:
                root.after_cancel(after_id)
            except tk.TclError:
                pass
        try:
            root.destroy()
        except tk.TclError:
            pass