"""
password_dialog.py — Show a native-looking input prompt (masked by default).

On Windows uses a CustomTkinter window (see dialog_common.py for the shared
always-on-top / foreground / centering behaviour). It runs in-process and blocks
the calling thread until the user answers, so it disappears automatically if
this process is killed — no child process or Job Object is involved.
On macOS/Linux falls back to a terminal prompt.
"""

import sys

from kkafio.core.i18n import t
from kkafio.system.dialog_common import new_window, show_and_wait


def password_dialog(title: str, content: str, mask: bool = True) -> str:
    """Prompt for a string. Returns '' if cancelled.

    Pass ``mask=False`` for values that aren't secret (phone numbers,
    verification codes, API IDs) so the user can see what they typed.
    """
    if sys.platform == "win32":
        try:
            return _ctk_dialog(title, content, mask)
        except Exception:
            return _terminal_prompt(title, content, mask)
    return _terminal_prompt(title, content, mask)


def _ctk_dialog(title: str, content: str, mask: bool = True) -> str:
    """
    Show a fixed-size window with a wrapped label, an entry (masked unless
    ``mask`` is False) and OK / Cancel buttons. The window height follows the
    label's wrapped text, so multi-line instructions are never clipped.
    Enter = OK, Esc = Cancel. Returns the entered text, or '' if cancelled.
    """
    import customtkinter as ctk

    root = new_window(title)
    root.resizable(False, False)  # no maximize; minimize stays available
    root.grid_columnconfigure(0, weight=1)

    result = {"value": ""}

    def on_ok(_event=None) -> None:
        result["value"] = entry.get()
        root.quit()

    def on_cancel(_event=None) -> None:
        root.quit()

    label = ctk.CTkLabel(root, text=content, wraplength=384, justify="left", anchor="w")
    label.grid(row=0, column=0, padx=10, pady=(15, 0), sticky="w")

    entry = ctk.CTkEntry(root, width=384)
    if mask:
        entry.configure(show="*")
    entry.grid(row=1, column=0, padx=10, pady=(8, 0), sticky="ew")

    buttons = ctk.CTkFrame(root, fg_color="transparent")
    buttons.grid(row=2, column=0, padx=10, pady=12, sticky="e")
    ctk.CTkButton(buttons, text=t("dialog.ok"), width=90, command=on_ok).pack(side="left", padx=(0, 10))
    ctk.CTkButton(buttons, text=t("dialog.cancel"), width=90, command=on_cancel).pack(side="left")

    root.bind("<Return>", on_ok)
    root.bind("<Escape>", on_cancel)
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    show_and_wait(root, focus_widget=entry)
    return result["value"]


def _terminal_prompt(title: str, content: str, mask: bool = True) -> str:
    print(f"\n{title}\n{content}")
    if mask:
        import getpass
        return getpass.getpass(t("dialog.password.prompt") + " ")
    return input("> ")


if __name__ == "__main__":
    pwd = password_dialog("Enter Password", "Please enter the archive password:")
    print(f"Password: {pwd}")