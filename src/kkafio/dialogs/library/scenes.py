"""
scenes.py — The Scene tab of Manage Library: Studio scenes sorted into author folders by hand.

The user names an author for a scene and the card is moved into <scene folder>/<author>/ at once.
The suggestions are the known authors (indices/author.py) and the folders that exist. The scenes
are hashed with the PNG hash cache that group_scenes.py and Filter Duplicate Contents share, and
the cache is brought along with every moved scene.
"""

import shutil
from pathlib import Path

from kkafio.core.folder_names import safe_folder_name
from kkafio.core.logger import logger
from kkafio.dialogs.library.model import LibraryTab
from kkafio.dialogs.library.common import Suggester, dialog_text, existing_folders, is_plain_dir, make_item, rank_suggestions, seed_known
from kkafio.indices.author import canonical_name, load_authors, suggest_authors
from kkafio.tasks.group_scenes import TAG, save_hash_cache, scan_scenes
from kkafio.cards.mods.parsing import parse_scene_guids
from kkafio.cards.mods.png_guids import collect_scene_guids_by_file

SCENE_COVER_RATIO = 180 / 320       # a scene card's picture is landscape (320x180)


def manual_callbacks(known: dict[str, str], authors: list, moves: dict[str, str],
                     max_suggestions: int = 8):
    """(suggest, assign) for the library dialog. `known` ({casefolded: name}) holds the author
    folders that exist and grows as folders are created; `authors` are the known authors (see
    indices/author). Every move `assign` makes is recorded in `moves` ({old path: new path}) so the
    caller can bring the hash cache along."""

    def existing(text: str) -> list[str]:
        return rank_suggestions(known.values(), text, max_suggestions)

    def suggest(text: str) -> list[str]:
        out = suggest_authors(text, authors, max_suggestions)        # none when nothing is typed
        seen = {n.casefold() for n in out}
        # the existing folders that match too (all of them when nothing is typed)
        out += rank_suggestions((n for n in known.values() if n.casefold() not in seen), text, max_suggestions)
        return out[:max_suggestions]

    def assign(item, text: str):
        if not text.strip():                               # blank: up out of the author folder
            if item.path.parent == item.root:
                return None                                # already at the top: nothing to do
            dest = _move_unique(item.path, item.root)
            if dest is None:
                return None
            moves[str(item.path)] = str(dest)
            return dest, item.root.name
        name = safe_folder_name(canonical_name(text, authors))
        if not name:
            logger.warning(TAG, f"'{text}' can't be used as a folder name")
            return None
        name = known.get(name.casefold(), name)            # spelled like the existing folder
        dest_dir = item.root / name
        if item.path.parent == dest_dir:
            return item.path, name                         # already there
        dest = _move_unique(item.path, dest_dir)
        if dest is None:
            return None
        moves[str(item.path)] = str(dest)
        known.setdefault(name.casefold(), name)
        return dest, name

    return Suggester(suggest, existing), assign


def _move_unique(src: Path, dest_dir: Path) -> Path | None:
    """Move `src` into `dest_dir` (created if needed; a taken name gets _1, _2 ...). The new path,
    or None (after a warning) if it couldn't be moved."""
    dest = dest_dir / src.name
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    except Exception as e:
        logger.warning(TAG, f"Could not move {src.name}: {e}")
        return None
    return dest


def scene_library_tab(folder: Path, use_cache: bool, label: str = ""):
    """The Manual library of the scenes under `folder` (subfolders included): the user names an
    author per scene and each card is moved at once.

    Returns a LibraryTab (its `.items` is empty when there is nothing to show) whose `finish()`
    must be called once the library is closed, also when it failed: it brings the hash cache
    along with the scenes that were moved."""
    logger.info(TAG, "Manual: reading the scenes")
    # Always with subfolders: the library window shows the whole library, with each scene's author (its
    # folder) shown; "Include subfolders" only decides what the automatic grouping looks at.
    scenes, cache, new_entries = scan_scenes(folder, True, use_cache)
    items = []
    for path, _digest in sorted(scenes):
        parts = path.relative_to(folder).parts
        if not all(is_plain_dir(part) for part in parts[:-1]):
            continue
        item = make_item(path, folder, parts)
        if item is not None:
            items.append(item)
    if use_cache:
        save_hash_cache(folder, cache, new_entries, set())     # hashing is the expensive part

    authors = load_authors()
    known = existing_folders(folder)
    seed_known(known, items)
    moves: dict[str, str] = {}
    suggest, assign = manual_callbacks(known, authors, moves)
    logger.info(TAG, f"{len(items)} scene(s) and {len(authors)} known author(s) for the library")

    def finish() -> None:
        if use_cache and moves:
            for old, new in moves.items():                      # a move keeps mtime/size: the hash stays valid
                entry = new_entries.pop(old, None)
                if entry is not None:
                    new_entries[new] = entry
            save_hash_cache(folder, cache, new_entries, set(moves))

    return LibraryTab(label, items, suggest, assign, dialog_text("dialog.manual_scene"), SCENE_COVER_RATIO, finish, "scene", parse_scene_guids,
                      lambda: collect_scene_guids_by_file([folder], use_cache))
