"""
shortcuts.py — The keyboard shortcuts of the library window, and where they are saved.

Six actions can be given a key by the user (Shortcuts tab of the window's Settings): rename the card,
change its folder, delete it (move it to the trash), archive it (move it to the archive queue),
restore it (out of the trash or the archive queue) and show it in the file explorer. One `ShortcutSettings` is shared by every
tab of the window, so a change applies to all of them at once. It is saved in
``<CONFIG_DIR>/config/library-shortcuts.json`` (on Windows: %APPDATA%/KKAFIO/config), next to the
GUI's own config, and read again the next time the library opens.

A shortcut is written as text — "F12", "Delete", "Enter", "Ctrl+R", "Ctrl+Shift+F5" — made of the
modifiers Ctrl, Alt, Shift (in that order) and one key. No GUI in here (see shortcuts_dialog.py for
the tab that edits them); `combo_from_event()` turns the parts of a Tk key event into that text.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.core.session_store import SessionStore

TAG = "LIBRARY"
FILE_NAME = "library-shortcuts.json"

ACTION_RENAME = "rename"
ACTION_FOLDER = "folder"
ACTION_DELETE = "delete"
ACTION_ARCHIVE = "archive"
ACTION_RESTORE = "restore"
ACTION_EXPLORER = "explorer"
ACTIONS = (ACTION_RENAME, ACTION_FOLDER, ACTION_DELETE, ACTION_ARCHIVE, ACTION_RESTORE, ACTION_EXPLORER)

DEFAULTS = {ACTION_RENAME: "F12", ACTION_FOLDER: "Enter", ACTION_DELETE: "Delete", ACTION_ARCHIVE: "F11",
            ACTION_RESTORE: "Insert", ACTION_EXPLORER: "F10"}

# Keys the library already uses to move around and select (with any modifier held); they can't
# be given to an action.
RESERVED_KEYS = frozenset({"Escape", "Left", "Right", "Up", "Down", "W", "A", "S", "D", "Space",
                           "PageUp", "PageDown", "Home", "End"})

_MODIFIERS = ("Ctrl", "Alt", "Shift")
_MODIFIER_KEYSYMS = frozenset({
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Meta_L", "Meta_R", "Super_L",
    "Super_R", "Caps_Lock", "Num_Lock", "Scroll_Lock", "ISO_Level3_Shift", "Mode_switch"})
_KEY_NAMES = {"Return": "Enter", "KP_Enter": "Enter", "BackSpace": "Backspace", "Prior": "PageUp",
              "Next": "PageDown", "space": "Space", "Escape": "Escape", "Delete": "Delete",
              "KP_Delete": "Delete"}


def combo_from_event(keysym: str, state: int, platform: str = sys.platform) -> str | None:
    """The shortcut text for a Tk key press (`event.keysym`, `event.state`), or None for a bare
    modifier key (Shift, Ctrl, ...), which is no shortcut on its own."""
    if not keysym or keysym in _MODIFIER_KEYSYMS:
        return None
    key = _KEY_NAMES.get(keysym, keysym)
    ctrl = bool(state & 0x4)
    alt = bool(state & (0x20000 if platform == "win32" else 0x8))     # Windows: Alt; elsewhere Mod1
    shift = bool(state & 0x1)
    if len(key) == 1:
        key = key.upper()
        if not key.isalpha():
            shift = False                          # the shifted character is the key itself ("!")
    elif key[0].islower():
        shift = False                              # a punctuation keysym name ("exclam"), same reason
    parts = [name for name, held in zip(_MODIFIERS, (ctrl, alt, shift)) if held]
    return "+".join(parts + [key])


def split_combo(combo: str) -> tuple[frozenset[str], str] | None:
    """(modifiers, key) of a shortcut text, or None if it isn't a well-formed one."""
    if not isinstance(combo, str) or not combo.strip():
        return None
    *mods, key = combo.split("+")
    if not key or any(m not in _MODIFIERS for m in mods) or len(set(mods)) != len(mods):
        return None
    return frozenset(mods), key


def is_reserved(combo: str) -> bool:
    parts = split_combo(combo)
    return parts is not None and parts[1].upper() in {k.upper() for k in RESERVED_KEYS}


class ShortcutSettings:
    """The shortcuts the library window listens to: {action: shortcut text}; "" = no shortcut.

    `store` is where they are kept (None: nothing is read or written, e.g. in tests)."""

    def __init__(self, store: SessionStore | None = None):
        self.store = store
        self.keys: dict[str, str] = dict(DEFAULTS)
        if store is not None:
            self._load()

    @classmethod
    def from_user_config(cls) -> "ShortcutSettings":
        """The settings saved in the per-user config folder (the defaults where there are none)."""
        from kkafio.core.paths import CONFIG_DIR
        return cls(SessionStore(CONFIG_DIR / "config" / FILE_NAME, TAG))

    # -- reading ---------------------------------------------------------------------------------

    def combo(self, action: str) -> str:
        return self.keys.get(action, "")

    def action_for(self, combo: str) -> str | None:
        """The action `combo` (as `combo_from_event` writes it) is the shortcut of, if any."""
        if not combo:
            return None
        return next((a for a in ACTIONS if self.keys.get(a) == combo), None)

    def problem(self, action: str, combo: str) -> tuple[str, str | None] | None:
        """Why `combo` can't be the shortcut of `action`: ("invalid", None), ("reserved", None) or
        ("taken", the action that has it); None if it can."""
        if split_combo(combo) is None:
            return "invalid", None
        if is_reserved(combo):
            return "reserved", None
        other = self.action_for(combo)
        if other is not None and other != action:
            return "taken", other
        return None

    # -- changing --------------------------------------------------------------------------------

    def set(self, action: str, combo: str) -> tuple[str, str | None] | None:
        """Give `action` the shortcut `combo` and save. Returns what `problem()` would (nothing is
        changed then), else None."""
        issue = self.problem(action, combo)
        if issue is None:
            self.keys[action] = combo
            self.save()
        return issue

    def clear(self, action: str) -> None:
        self.keys[action] = ""
        self.save()

    def reset(self) -> None:
        self.keys = dict(DEFAULTS)
        self.save()

    # -- the file --------------------------------------------------------------------------------

    def _load(self) -> None:
        data = self.store.load().get("shortcuts")
        if not isinstance(data, dict):
            return
        loaded: dict[str, str] = {}
        taken: set[str] = set()
        for action in ACTIONS:
            value = data.get(action, DEFAULTS[action])
            if not isinstance(value, str):
                value = DEFAULTS[action]
            if value and (split_combo(value) is None or is_reserved(value) or value in taken):
                logger.warning(TAG, f"Ignoring the saved shortcut '{value}' for {action}")
                value = DEFAULTS[action] if DEFAULTS[action] not in taken else ""
            loaded[action] = value
            if value:
                taken.add(value)
        self.keys = loaded

    def save(self) -> bool:
        if self.store is None:
            return True
        return self.store.save({"version": 1, "shortcuts": dict(self.keys)})
