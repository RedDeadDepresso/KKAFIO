"""
library — The Manage Library window and what each of its tabs does.

    model.py        what the window shows and is told, no GUI: CharaItem, LibraryModel, DialogText,
                    LibraryTab and the file helpers behind renaming a card
    window.py       the window itself: draws the tabs, knows nothing about characters,
                    coordinates or scenes
    characters.py   the Character tab: scan, suggest, move into series folders
    coordinates.py  the Outfit tab: scan, suggest, move into character folders
    scenes.py       the Scene tab: scan, suggest, move into author folders
    common.py       what the three tabs share
    shortcuts.py    the keyboard shortcuts (rename, change folder, delete) and their config file
    suggestions.py  what the folder box offers per kind of card (none / existing / all), and its config file
    settings_dialog.py  the Settings window (Settings button): the Shortcuts and Folder suggestions tabs
    shortcuts_dialog.py  the Shortcuts tab of it
    viewstate.py    the last open tab and each tab's sort and cover size, kept between runs
    mods_panel.py   the mod dependencies sidebar's data: a card's mods and which are installed
    mods_sidebar.py the collapsible sidebar on the right that lists them (imported by window.py on use)

Imports run one way: tasks/manage_library.py -> the tabs -> model.py, and the tabs -> tasks/group_*.
window.py only imports model.py; the Group tasks never import this package. The names below are
re-exported so callers can write `from kkafio.dialogs.library import library_dialog`; code that
patches a name (tests) has to patch it in the module that uses it.
"""

from kkafio.dialogs.library.model import (
    COVER_MAX, COVER_RATIO, SORT_CREATED, SORT_FILENAME, SORT_NAME, SORT_SIZE, SORT_UPDATED,
    VIEW_ALL, VIEW_ARCHIVE, VIEW_SERIES, VIEW_TRASH, VIEW_UNTAGGED, AssignFn, CharaItem, DialogText, LibraryModel, LibraryTab,
    chara_dialog_text, clean_file_stem, rename_card,
)
from kkafio.dialogs.library.window import (
    ellipsize, library_dialog, load_cover, manual_dialog, round_corners, rounded_rect_points,
)

__all__ = [
    "COVER_MAX", "COVER_RATIO", "SORT_CREATED", "SORT_FILENAME", "SORT_NAME", "SORT_SIZE",
    "SORT_UPDATED", "VIEW_ALL", "VIEW_ARCHIVE", "VIEW_SERIES", "VIEW_TRASH", "VIEW_UNTAGGED", "AssignFn", "CharaItem", "DialogText",
    "LibraryModel", "LibraryTab", "chara_dialog_text", "clean_file_stem", "rename_card",
    "ellipsize", "library_dialog", "load_cover", "manual_dialog", "round_corners", "rounded_rect_points",
]
