"""
gallery — The Run Gallery window and what each of its tabs does.

    model.py        what the window shows and is told, no GUI: CharaItem, GalleryModel, DialogText,
                    GalleryTab and the file helpers behind renaming a card
    window.py       the window itself: draws the tabs, knows nothing about characters,
                    coordinates or scenes
    characters.py   the Character tab: scan, suggest, move into series folders
    coordinates.py  the Outfit tab: scan, suggest, move into character folders
    scenes.py       the Scene tab: scan, suggest, move into author folders
    common.py       what the three tabs share

Imports run one way: tasks/run_gallery.py -> the tabs -> model.py, and the tabs -> tasks/group_*.
window.py only imports model.py; the Group tasks never import this package. The names below are
re-exported so callers can write `from kkafio.dialogs.gallery import gallery_dialog`; code that
patches a name (tests) has to patch it in the module that uses it.
"""

from kkafio.dialogs.gallery.model import (
    COVER_MAX, COVER_RATIO, SORT_CREATED, SORT_FILENAME, SORT_NAME, SORT_SIZE, SORT_UPDATED,
    VIEW_ALL, VIEW_SERIES, VIEW_TRASH, VIEW_UNTAGGED, AssignFn, CharaItem, DialogText, GalleryModel, GalleryTab,
    chara_dialog_text, clean_file_stem, rename_card,
)
from kkafio.dialogs.gallery.window import (
    ellipsize, gallery_dialog, load_cover, manual_dialog, round_corners, rounded_rect_points,
)

__all__ = [
    "COVER_MAX", "COVER_RATIO", "SORT_CREATED", "SORT_FILENAME", "SORT_NAME", "SORT_SIZE",
    "SORT_UPDATED", "VIEW_ALL", "VIEW_SERIES", "VIEW_TRASH", "VIEW_UNTAGGED", "AssignFn", "CharaItem", "DialogText",
    "GalleryModel", "GalleryTab", "chara_dialog_text", "clean_file_stem", "rename_card",
    "ellipsize", "gallery_dialog", "load_cover", "manual_dialog", "round_corners", "rounded_rect_points",
]
