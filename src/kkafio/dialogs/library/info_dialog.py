"""
info_dialog.py — The library window's Info dialog (the Info button at the bottom of every tab).

A short guide to using the library: selecting cards, giving them a folder, renaming, finding and
sorting, and the To Delete / To Archive lists. The keys that can be changed in Settings are shown as
they are set now.
"""

from __future__ import annotations

from kkafio.core.i18n import t
from kkafio.dialogs.library.shortcuts import (
    ACTION_ARCHIVE, ACTION_DELETE, ACTION_EXPLORER, ACTION_FOLDER, ACTION_RENAME, ACTION_RESTORE, ShortcutSettings)

# The sections, in order: each has the i18n keys "dialog.info.<name>.title" and ".body".
SECTIONS = ("select", "folder", "rename", "find", "marks", "shortcuts")


def info_sections(shortcuts: ShortcutSettings) -> list[tuple[str, str]]:
    """[(title, body)] of the guide, with the shortcuts as they are set now."""
    none = t("dialog.shortcuts.none")
    keys = {name: shortcuts.combo(action) or none for name, action in (
        ("rename", ACTION_RENAME), ("folder", ACTION_FOLDER), ("delete", ACTION_DELETE),
        ("archive", ACTION_ARCHIVE), ("restore", ACTION_RESTORE), ("explorer", ACTION_EXPLORER))}
    return [(t(f"dialog.info.{name}.title"), t(f"dialog.info.{name}.body", **keys)) for name in SECTIONS]


def open_info_dialog(root, shortcuts: ShortcutSettings) -> None:
    """Show the guide over `root` (one at a time: a second call brings it forward)."""
    import tkinter as tk
    import customtkinter as ctk

    existing = getattr(root, "_kkafio_info_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass

    dlg = ctk.CTkToplevel(root)
    root._kkafio_info_dialog = dlg
    dlg.title(t("dialog.info.title"))
    dlg.transient(root)
    dlg.grid_columnconfigure(0, weight=1)
    dlg.grid_rowconfigure(0, weight=1)

    body = ctk.CTkScrollableFrame(dlg, width=520, height=420, fg_color="transparent")
    body.grid(row=0, column=0, padx=12, pady=(12, 0), sticky="nsew")
    body.grid_columnconfigure(0, weight=1)
    for row, (title, text) in enumerate(info_sections(shortcuts)):
        ctk.CTkLabel(body, text=title, anchor="w", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=2 * row, column=0, padx=6, pady=(10 if row else 0, 2), sticky="ew")
        ctk.CTkLabel(body, text=text, anchor="w", justify="left", wraplength=500).grid(
            row=2 * row + 1, column=0, padx=6, sticky="ew")

    ctk.CTkButton(dlg, text=t("dialog.close"), width=110, command=dlg.destroy).grid(
        row=1, column=0, padx=16, pady=(10, 14), sticky="e")
    dlg.bind("<Escape>", lambda _e: (dlg.destroy(), "break")[1])

    dlg.update_idletasks()                              # centered over the library window
    w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    x = root.winfo_rootx() + max(0, (root.winfo_width() - w) // 2)
    y = root.winfo_rooty() + max(0, (root.winfo_height() - h) // 3)
    dlg.wm_geometry(f"{w}x{h}+{x}+{y}")
    dlg.after(150, lambda: dlg.winfo_exists() and dlg.focus_force())
