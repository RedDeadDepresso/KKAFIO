"""The gallery's keyboard shortcuts (kkafio/dialogs/gallery/shortcuts.py): reading a key press,
refusing keys that are taken, and the file they are saved in."""

import json

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.core.session_store import SessionStore
from kkafio.dialogs.gallery.shortcuts import (
    ACTION_DELETE, ACTION_FOLDER, ACTION_RENAME, ACTION_RESTORE, DEFAULTS, ShortcutSettings, combo_from_event)

CTRL, SHIFT, ALT_WIN = 0x4, 0x1, 0x20000


def test_key_presses_become_shortcut_text():
    assert combo_from_event("F2", 0) == "F2"
    assert combo_from_event("Return", 0) == "Enter" and combo_from_event("KP_Enter", 0) == "Enter"
    assert combo_from_event("r", CTRL) == "Ctrl+R"
    assert combo_from_event("R", CTRL | SHIFT) == "Ctrl+Shift+R"
    assert combo_from_event("F5", SHIFT | ALT_WIN, "win32") == "Alt+Shift+F5"
    assert combo_from_event("exclam", SHIFT) == "exclam"
    assert combo_from_event("1", SHIFT) == "1"                      # a character carries its own shift
    assert combo_from_event("Shift_L", SHIFT) is None and combo_from_event("Control_R", CTRL) is None


def test_defaults_and_refused_keys(tmp_path):
    s = ShortcutSettings(SessionStore(tmp_path / "s.json", "T"))
    assert s.keys == DEFAULTS
    assert s.set(ACTION_RENAME, "Left") == ("reserved", None)          # moves the cursor
    assert s.set(ACTION_RENAME, "Ctrl+A") == ("reserved", None)        # select all
    assert s.set(ACTION_RENAME, "Delete") == ("taken", ACTION_DELETE)
    assert s.set(ACTION_RENAME, "Ctrl+Shift+R") is None                # modifiers are fine
    assert s.set(ACTION_RENAME, "") == ("invalid", None)
    assert s.set(ACTION_RENAME, "F12") is None                         # its own key again is fine
    assert s.set(ACTION_RESTORE, "Delete") == ("taken", ACTION_DELETE)


def test_changes_are_saved_and_read_back(tmp_path):
    path = tmp_path / "config" / "s.json"
    s = ShortcutSettings(SessionStore(path, "T"))
    assert s.set(ACTION_RENAME, "Ctrl+R") is None
    s.clear(ACTION_DELETE)
    assert json.loads(path.read_text("utf-8"))["shortcuts"] == {
        ACTION_RENAME: "Ctrl+R", ACTION_FOLDER: "Enter", ACTION_DELETE: "", ACTION_RESTORE: "Insert"}
    again = ShortcutSettings(SessionStore(path, "T"))
    assert again.keys == s.keys and again.action_for("Ctrl+R") == ACTION_RENAME
    again.reset()
    assert ShortcutSettings(SessionStore(path, "T")).keys == DEFAULTS


def test_a_damaged_file_falls_back_to_the_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    assert ShortcutSettings(SessionStore(path, "T")).keys == DEFAULTS
    path.write_text(json.dumps({"shortcuts": {ACTION_RENAME: "Up", ACTION_FOLDER: "F3", ACTION_DELETE: "F3"}}),
                    encoding="utf-8")
    s = ShortcutSettings(SessionStore(path, "T"))
    assert s.keys == {ACTION_RENAME: "F12", ACTION_FOLDER: "F3", ACTION_DELETE: "Delete",
                      ACTION_RESTORE: "Insert"}   # bad / duplicate ones reset
