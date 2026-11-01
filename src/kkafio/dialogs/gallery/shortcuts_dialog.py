"""
shortcuts_dialog.py — The window where the gallery's keyboard shortcuts are set.

Opened by the Shortcuts button of the gallery window (every tab has one; they open the same
dialog and change the same `ShortcutSettings`, so a change applies to all tabs). Click the shortcut
of an action, press the keys you want (Esc cancels); a change is applied and saved at once.
"""

from __future__ import annotations

from kkafio.core.i18n import t
from kkafio.dialogs.gallery.shortcuts import (
    ACTION_DELETE, ACTION_FOLDER, ACTION_RENAME, ACTION_RESTORE, ACTIONS, ShortcutSettings, combo_from_event)


def action_labels() -> dict[str, str]:
    return {ACTION_RENAME: t("dialog.shortcuts.action_rename"),
            ACTION_FOLDER: t("dialog.shortcuts.action_folder"),
            ACTION_DELETE: t("dialog.shortcuts.action_delete"),
            ACTION_RESTORE: t("dialog.shortcuts.action_restore")}


def open_shortcuts_dialog(root, settings: ShortcutSettings) -> None:
    """Show the shortcuts dialog over `root` (one at a time: a second call brings it forward)."""
    import tkinter as tk
    import customtkinter as ctk

    existing = getattr(root, "_kkafio_shortcuts_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass

    labels = action_labels()
    dlg = ctk.CTkToplevel(root)
    root._kkafio_shortcuts_dialog = dlg
    dlg.title(t("dialog.shortcuts.title"))
    dlg.transient(root)
    dlg.resizable(False, False)
    dlg.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(dlg, text=t("dialog.shortcuts.intro"), anchor="w", justify="left", wraplength=440).grid(
        row=0, column=0, columnspan=3, padx=16, pady=(16, 10), sticky="ew")

    capturing: dict[str, str | None] = {"action": None}
    buttons: dict[str, ctk.CTkButton] = {}
    message = ctk.CTkLabel(dlg, text="", anchor="w", justify="left", wraplength=440, text_color=("#b91c1c", "#f87171"))

    def refresh() -> None:
        for action, button in buttons.items():
            if capturing["action"] == action:
                button.configure(text=t("dialog.shortcuts.press"))
            else:
                button.configure(text=settings.combo(action) or t("dialog.shortcuts.none"))

    def start_capture(action: str) -> None:
        capturing["action"] = action
        message.configure(text="")
        refresh()
        dlg.focus_force()

    def stop_capture() -> None:
        capturing["action"] = None
        refresh()

    def on_key(event):
        action = capturing["action"]
        if action is None:
            return None
        if event.keysym == "Escape":
            stop_capture()
            return "break"
        combo = combo_from_event(event.keysym, event.state)
        if combo is None:                              # only a modifier so far: wait for the key
            return "break"
        issue = settings.set(action, combo)
        if issue is None:
            message.configure(text="")
        elif issue[0] == "reserved":
            message.configure(text=t("dialog.shortcuts.reserved", combo=combo))
        elif issue[0] == "taken":
            message.configure(text=t("dialog.shortcuts.taken", combo=combo, action=labels[issue[1]]))
        stop_capture()
        return "break"

    def clear(action: str) -> None:
        settings.clear(action)
        message.configure(text="")
        stop_capture()

    def reset() -> None:
        settings.reset()
        message.configure(text="")
        stop_capture()

    for row, action in enumerate(ACTIONS, start=1):
        ctk.CTkLabel(dlg, text=labels[action], anchor="w").grid(row=row, column=0, padx=(16, 10), pady=4, sticky="w")
        button = ctk.CTkButton(dlg, text="", width=170, command=lambda a=action: start_capture(a))
        button.grid(row=row, column=1, padx=(0, 8), pady=4, sticky="e")
        buttons[action] = button
        ctk.CTkButton(dlg, text=t("dialog.shortcuts.clear"), width=70, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=lambda a=action: clear(a)).grid(
            row=row, column=2, padx=(0, 16), pady=4)

    message.grid(row=len(ACTIONS) + 1, column=0, columnspan=3, padx=16, pady=(6, 0), sticky="ew")
    foot = ctk.CTkFrame(dlg, fg_color="transparent")
    foot.grid(row=len(ACTIONS) + 2, column=0, columnspan=3, padx=16, pady=(12, 16), sticky="ew")
    foot.grid_columnconfigure(0, weight=1)
    ctk.CTkButton(foot, text=t("dialog.shortcuts.reset"), width=150, fg_color="transparent", border_width=1,
                  text_color=("gray10", "gray90"), command=reset).grid(row=0, column=0, sticky="w")
    ctk.CTkButton(foot, text=t("dialog.close"), width=110, command=dlg.destroy).grid(row=0, column=1, sticky="e")

    dlg.bind("<KeyPress>", on_key)
    dlg.bind("<Escape>", lambda _e: dlg.destroy())     # (while capturing, on_key has taken it already)
    refresh()

    dlg.update_idletasks()                              # centered over the gallery window
    w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    x = root.winfo_rootx() + max(0, (root.winfo_width() - w) // 2)
    y = root.winfo_rooty() + max(0, (root.winfo_height() - h) // 3)
    dlg.wm_geometry(f"{w}x{h}+{x}+{y}")
    dlg.after(150, lambda: dlg.winfo_exists() and dlg.focus_force())
