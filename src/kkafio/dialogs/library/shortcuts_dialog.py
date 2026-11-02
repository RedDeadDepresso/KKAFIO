"""
shortcuts_dialog.py — The Shortcuts tab of the library's Settings window: where the keyboard
shortcuts are set.

Click the shortcut of an action, press the keys you want (Esc cancels); a change is applied and
saved at once, to the `ShortcutSettings` every tab of the library window shares.
"""

from __future__ import annotations

from kkafio.core.i18n import t
from kkafio.dialogs.library.shortcuts import (
    ACTION_DELETE, ACTION_FOLDER, ACTION_RENAME, ACTION_RESTORE, ACTIONS, ShortcutSettings, combo_from_event)


def action_labels() -> dict[str, str]:
    return {ACTION_RENAME: t("dialog.shortcuts.action_rename"),
            ACTION_FOLDER: t("dialog.shortcuts.action_folder"),
            ACTION_DELETE: t("dialog.shortcuts.action_delete"),
            ACTION_RESTORE: t("dialog.shortcuts.action_restore")}


def build_shortcuts_panel(dlg, panel, settings: ShortcutSettings):
    """Fill `panel` (a frame inside the settings window `dlg`) with the shortcut editor. Returns
    `on_key(event)`, which the window calls for every key press: while a shortcut is being
    captured it takes the key (returning "break"), otherwise it returns None."""
    import customtkinter as ctk

    labels = action_labels()
    panel.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(panel, text=t("dialog.shortcuts.intro"), anchor="w", justify="left", wraplength=440).grid(
        row=0, column=0, columnspan=3, padx=8, pady=(8, 10), sticky="ew")

    capturing: dict[str, str | None] = {"action": None}
    buttons: dict[str, ctk.CTkButton] = {}
    message = ctk.CTkLabel(panel, text="", anchor="w", justify="left", wraplength=440, text_color=("#b91c1c", "#f87171"))

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
        ctk.CTkLabel(panel, text=labels[action], anchor="w").grid(row=row, column=0, padx=(8, 10), pady=4, sticky="w")
        button = ctk.CTkButton(panel, text="", width=170, command=lambda a=action: start_capture(a))
        button.grid(row=row, column=1, padx=(0, 8), pady=4, sticky="e")
        buttons[action] = button
        ctk.CTkButton(panel, text=t("dialog.shortcuts.clear"), width=70, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=lambda a=action: clear(a)).grid(
            row=row, column=2, padx=(0, 8), pady=4)

    message.grid(row=len(ACTIONS) + 1, column=0, columnspan=3, padx=8, pady=(6, 0), sticky="ew")
    ctk.CTkButton(panel, text=t("dialog.shortcuts.reset"), width=150, fg_color="transparent", border_width=1,
                  text_color=("gray10", "gray90"), command=reset).grid(
        row=len(ACTIONS) + 2, column=0, columnspan=3, padx=8, pady=(12, 8), sticky="w")
    refresh()
    return on_key
