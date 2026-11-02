"""
settings_dialog.py — The library window's Settings dialog (the Settings button of every tab).

Two tabs: Shortcuts (shortcuts_dialog.py) and Folder suggestions, where each kind of card has a
drop-down: No / Existing folders / Existing folders + Series (characters), Characters
(coordinates) or Authors (scenes); and a drop-down for the language of the series suggestions. The tabs open the same dialog and change the same settings
objects, so a change applies to all of them; every change is saved at once.
"""

from __future__ import annotations

from kkafio.cards.chara.hint_language import available_languages
from kkafio.core.i18n import t
from kkafio.dialogs.library.shortcuts import ShortcutSettings
from kkafio.dialogs.library.shortcuts_dialog import build_shortcuts_panel
from kkafio.dialogs.library.suggestions import (
    CATEGORIES, CATEGORY_CHARA, CATEGORY_OUTFIT, CATEGORY_SCENE, SUGGEST_ALL, SUGGEST_EXISTING, SUGGEST_NONE,
    SuggestionSettings)


def category_labels() -> dict[str, str]:
    """The library's tab names: Character, Outfit, Scene."""
    return {CATEGORY_CHARA: t("dialog.library.tab_chara"),
            CATEGORY_OUTFIT: t("dialog.library.tab_outfit"),
            CATEGORY_SCENE: t("dialog.library.tab_scene")}


def mode_labels(category: str) -> dict[str, str]:
    """{mode: text} of the drop-down of `category`, in the order they are listed."""
    everything = {CATEGORY_CHARA: t("dialog.settings.mode_all_chara"),
                  CATEGORY_OUTFIT: t("dialog.settings.mode_all_outfit"),
                  CATEGORY_SCENE: t("dialog.settings.mode_all_scene")}[category]
    return {SUGGEST_NONE: t("dialog.settings.mode_none"),
            SUGGEST_EXISTING: t("dialog.settings.mode_existing"),
            SUGGEST_ALL: everything}


# Each language in its own writing, so it can be found whatever the interface language is.
LANGUAGE_NAMES = {"en_us": "English", "ja_jp": "日本語", "ko_kr": "한국어", "ru_ru": "Русский",
                  "zh_cn": "简体中文", "zh_tw": "繁體中文"}


def language_labels() -> dict[str, str]:
    """{language code: name} of the languages the series titles can be shown in."""
    return {code: LANGUAGE_NAMES.get(code, code) for code in available_languages()}


def build_suggestions_panel(panel, settings: SuggestionSettings) -> None:
    """Fill `panel` with one drop-down per kind of card."""
    import customtkinter as ctk

    panel.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(panel, text=t("dialog.settings.suggestions_intro"), anchor="w", justify="left", wraplength=440).grid(
        row=0, column=0, columnspan=2, padx=8, pady=(8, 10), sticky="ew")
    labels = category_labels()
    for row, category in enumerate(CATEGORIES, start=1):
        modes = mode_labels(category)
        by_text = {text: mode for mode, text in modes.items()}
        ctk.CTkLabel(panel, text=labels[category], anchor="w").grid(row=row, column=0, padx=(8, 10), pady=6, sticky="w")
        menu = ctk.CTkOptionMenu(
            panel, values=list(modes.values()), width=250,
            command=lambda text, c=category, by_text=by_text: settings.set(c, by_text[text]))
        menu.set(modes[settings.mode(category)])
        menu.grid(row=row, column=1, padx=(0, 8), pady=6, sticky="e")

    languages = language_labels()
    by_name = {name: code for code, name in languages.items()}
    row = len(CATEGORIES) + 1
    ctk.CTkLabel(panel, text=t("dialog.settings.series_language"), anchor="w").grid(
        row=row, column=0, padx=(8, 10), pady=(18, 6), sticky="w")
    menu = ctk.CTkOptionMenu(panel, values=list(languages.values()), width=250,
                             command=lambda name: settings.set_language(by_name[name]))
    menu.set(languages.get(settings.language, settings.language))
    menu.grid(row=row, column=1, padx=(0, 8), pady=(18, 6), sticky="e")


def open_settings_dialog(root, shortcuts: ShortcutSettings, suggestions: SuggestionSettings) -> None:
    """Show the settings dialog over `root` (one at a time: a second call brings it forward)."""
    import tkinter as tk
    import customtkinter as ctk

    existing = getattr(root, "_kkafio_settings_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass

    dlg = ctk.CTkToplevel(root)
    root._kkafio_settings_dialog = dlg
    dlg.title(t("dialog.settings.title"))
    dlg.transient(root)
    dlg.resizable(False, False)
    dlg.grid_columnconfigure(0, weight=1)

    shortcuts_name, suggestions_name = t("dialog.shortcuts.title"), t("dialog.settings.tab_suggestions")
    tabs = ctk.CTkTabview(dlg, width=500, height=350)
    tabs.grid(row=0, column=0, padx=16, pady=(8, 0), sticky="nsew")
    on_shortcut_key = build_shortcuts_panel(dlg, tabs.add(shortcuts_name), shortcuts)
    build_suggestions_panel(tabs.add(suggestions_name), suggestions)
    tabs.set(shortcuts_name)

    ctk.CTkButton(dlg, text=t("dialog.close"), width=110, command=dlg.destroy).grid(
        row=1, column=0, padx=16, pady=(12, 16), sticky="e")

    def on_key(event):
        if on_shortcut_key(event) == "break":          # a shortcut was being captured: Esc only cancels that
            return "break"
        if event.keysym == "Escape":
            dlg.destroy()
            return "break"
        return None

    dlg.bind("<KeyPress>", on_key)

    dlg.update_idletasks()                              # centered over the library window
    w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    x = root.winfo_rootx() + max(0, (root.winfo_width() - w) // 2)
    y = root.winfo_rooty() + max(0, (root.winfo_height() - h) // 3)
    dlg.wm_geometry(f"{w}x{h}+{x}+{y}")
    dlg.after(150, lambda: dlg.winfo_exists() and dlg.focus_force())
