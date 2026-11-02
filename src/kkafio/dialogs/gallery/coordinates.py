"""
coordinates.py — The Outfit tab of Run Gallery: coordinate cards sorted into character folders by hand.

The user names a character for a coordinate and the card is moved into <coordinate folder>/<name>/
at once. The suggestions are the folders that exist plus the filenames of the character cards.
The coordinate caches that group_coordinates.py keeps are brought along with every moved card.
"""

from pathlib import Path

from kkafio.cards.chara.outfits import build_coord_cache
from kkafio.core.folder_names import safe_folder_name
from kkafio.core.logger import logger
from kkafio.dialogs.gallery.model import COVER_RATIO, GalleryTab
from kkafio.dialogs.gallery.common import Suggester, dialog_text, existing_folders, is_plain_dir, make_item, rank_suggestions, seed_known
from kkafio.tasks.group_coordinates import TAG, move_unique, sorted_charas, update_coord_cache
from kkafio.cards.mods.parsing import parse_coord_guids
from kkafio.cards.mods.png_guids import collect_coord_guids_by_file


def chara_folder_names(chara_dirs: list[Path]) -> dict[str, str]:
    """{casefolded folder name: folder name} for every chara card under chara_dirs, from its
    filename without ".png" — what a character's coordinates are grouped under."""
    known: dict[str, str] = {}
    for chara, name in sorted_charas({png for folder in chara_dirs for png in folder.rglob("*.png")}):
        known.setdefault(name.casefold(), name)
    return known


def scan_manual_items(coord_dir: Path, use_cache: bool) -> list:
    """The coordinate cards under coord_dir (subfolders included) for the gallery, as CharaItems:
    named after the file, in the folder directly under coord_dir that holds it (None: directly
    in coord_dir)."""
    items = []
    for sp, key in sorted(build_coord_cache(coord_dir, use_cache=use_cache).items()):
        png = Path(sp)
        parts = png.relative_to(coord_dir).parts
        if not key or not all(is_plain_dir(part) for part in parts[:-1]):
            continue                                      # not a coordinate card / in a _skipped_ folder
        item = make_item(png, coord_dir, parts)
        if item is not None:
            items.append(item)
    return items


def manual_callbacks(known: dict[str, str], moves: dict[str, str], max_suggestions: int = 8,
                     extra: dict[str, str] | None = None):
    """(suggest, assign) for the gallery dialog. `known` ({casefolded: name}) holds the folders that
    exist and grows as folders are created; `extra` ({casefolded: name}) the other names to
    suggest — the characters' filenames — which `suggest.existing` leaves out. Every move `assign`
    makes is recorded in `moves` ({old path: new path}) so the caller can bring the caches along."""
    extra = {} if extra is None else extra

    def existing(text: str) -> list[str]:
        return rank_suggestions(known.values(), text, max_suggestions)

    def suggest(text: str) -> list[str]:
        return rank_suggestions({**extra, **known}.values(), text, max_suggestions)

    def assign(item, text: str):
        if not text.strip():                              # blank: up out of the character folder
            if item.path.parent == item.root:
                return None                               # already at the top: nothing to do
            try:
                dest = move_unique(item.path, item.root)
            except Exception as e:
                logger.warning(TAG, f"Could not move {item.path.name}: {e}")
                return None
            logger.success(TAG, f"Moved {item.path.name} -> {item.root.name}/")
            moves[str(item.path)] = str(dest)
            return dest, item.root.name
        name = safe_folder_name(text.strip())
        if not name:
            logger.warning(TAG, f"'{text}' can't be used as a folder name")
            return None
        key = name.casefold()
        name = known.get(key) or extra.get(key) or name   # spelled like the existing folder / character
        dest_dir = item.root / name
        if item.path.parent == dest_dir:
            return item.path, name                        # already there
        try:
            dest = move_unique(item.path, dest_dir)
        except Exception as e:
            logger.warning(TAG, f"Could not move {item.path.name}: {e}")
            return None
        logger.success(TAG, f"Moved {item.path.name} -> {name}/")
        moves[str(item.path)] = str(dest)
        known.setdefault(name.casefold(), name)
        return dest, name

    return Suggester(suggest, existing), assign


def coordinate_gallery_tab(chara_dirs: list[Path], coord_dir: Path, use_cache: bool, label: str = ""):
    """The Manual gallery of the coordinate cards under `coord_dir`: the user names a character per
    coordinate and each card is moved at once; the character filenames under `chara_dirs` (without
    .png) are the suggestions.

    Returns a GalleryTab (its `.items` is empty when there is nothing to show) whose `finish()`
    must be called once the gallery is closed, also when it failed: it brings the coordinate
    caches along with the cards that were moved."""
    logger.info(TAG, "Manual: reading the coordinate cards")
    items = scan_manual_items(coord_dir, use_cache)
    known = existing_folders(coord_dir)
    seed_known(known, items)
    extra = chara_folder_names(chara_dirs)
    moves: dict[str, str] = {}
    suggest, assign = manual_callbacks(known, moves, extra=extra)
    logger.info(TAG, f"{len(items)} coordinate(s) and {len({**extra, **known})} suggested name(s) for the gallery")

    def finish() -> None:
        if use_cache and moves:
            update_coord_cache(coord_dir, moves)       # also when the dialog fails part-way

    return GalleryTab(label, items, suggest, assign, dialog_text("dialog.manual_coord"), COVER_RATIO, finish, "outfit", parse_coord_guids,
                      lambda: collect_coord_guids_by_file([coord_dir], use_cache))
