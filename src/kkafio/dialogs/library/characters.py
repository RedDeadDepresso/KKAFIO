"""
characters.py — The Character tab of Manage Library: character cards sorted into series folders by hand.

The user types a series for a character and the card is moved into <chara folder>/<series>/ at
once. The scan reads the cards with the same key cache the LLM workflow uses (group_chara.py), and
the series suggestions come from the existing folders and the anime / games / vtubers index.
"""

from pathlib import Path
from typing import Sequence

from kkafio.cards.chara.chara_key_cache import CharaKeyCache
from kkafio.core.folder_names import safe_folder_name
from kkafio.core.logger import logger
from kkafio.dialogs.library.model import COVER_RATIO, LibraryTab
from kkafio.dialogs.library.common import (
    Suggester, dialog_text, existing_folders, is_plain_dir, make_item, rank_suggestions, rename_known, seed_known,
)
from kkafio.tasks.group_chara import move_card, read_card
from kkafio.cards.mods.parsing import parse_chara_guids
from kkafio.cards.mods.png_guids import collect_chara_guids_by_file


def scan_manual_items(folders: Sequence[Path], include_subfolders: bool,
                      key_cache: CharaKeyCache) -> list:
    """The character cards for the library dialog, as CharaItems. A card directly in a chara
    folder has no series; one inside a folder has that (first) folder as its series."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    from kkafio.indices.agv import split_key

    found: list[tuple[Path, Path]] = []
    for root in folders:
        for png in (root.rglob("*.png") if include_subfolders else root.glob("*.png")):
            if all(is_plain_dir(part) for part in png.relative_to(root).parts[:-1]):
                found.append((root, png))
    logger.info("GROUP", f"Scanning {len(found)} PNG file(s) for the library")

    unique = dict.fromkeys(png for _root, png in found)
    root_of = {png: root for root, png in found}
    items: list = []
    with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 2)) as ex:
        for png, key, _birth in ex.map(lambda p: read_card(p, key_cache), unique):
            if key is None:
                continue                                  # not a chara card
            if key.startswith("__error__"):
                logger.warning("GROUP", f"Could not process {png.name}: {key[9:]}")
                continue
            last, first, nick = split_key(key)
            parts = png.relative_to(root_of[png]).parts
            item = make_item(png, root_of[png], parts,         # the file name is shown under the cover
                             alias=f"{last} {first}".strip() or nick)    # the character's name stays searchable
            if item is not None:
                items.append(item)
    return items


def resolve_series_folder(text: str, known: dict[str, str], lang: str | None) -> str:
    """The folder name for a series the user typed: snapped to the index's title for it (when
    `lang` is set), made safe to use as a folder name, and spelled like the existing folder
    when one with the same name (in any case) exists. "" if nothing usable is left."""
    name = text.strip()
    if lang and name:
        from kkafio.indices import agv
        name = agv.canonical_series(name, lang)
    name = safe_folder_name(name)
    return known.get(name.casefold(), name) if name else ""


def manual_callbacks(known: dict[str, str], key_cache: CharaKeyCache, lang: str | None,
                     max_suggestions: int = 8):
    """(suggest, assign) for the library dialog. `known` ({casefolded: name}) holds the series
    folders that exist and grows as new ones are created; `lang` is the language of the index's
    series titles (None: no index, only the existing folders are suggested); `suggest.set_language`
    changes it later (not for a tab that has no index)."""
    state = {"lang": lang}

    def set_language(language: str) -> None:
        if state["lang"]:
            state["lang"] = language

    def existing(text: str) -> list[str]:
        return rank_suggestions(known.values(), text, max_suggestions)

    def suggest(text: str) -> list[str]:
        out = existing(text)
        if state["lang"] and text.strip():
            from kkafio.indices import agv
            seen = {n.casefold() for n in out}
            for title in agv.suggest_series(text, state["lang"], limit=max_suggestions):
                folder = safe_folder_name(title)          # shown as the folder it would become
                if folder and folder.casefold() not in seen:
                    seen.add(folder.casefold())
                    out.append(folder)
        return out[:max_suggestions]

    def assign(item, text: str):
        if not text.strip():                              # blank: up out of the series folder
            if item.path.parent == item.root:
                return None                               # already at the top: nothing to do
            dest = move_card(item.path, item.root, item.root.name, key_cache)
            return None if dest is None else (dest, item.root.name)
        folder = resolve_series_folder(text, known, state["lang"])
        if not folder:
            logger.warning("GROUP", f"'{text}' can't be used as a series folder name")
            return None
        dest_dir = item.root / folder
        if item.path.parent == dest_dir:
            return item.path, folder                      # already there
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("GROUP", f"Could not create {dest_dir}: {e}")
            return None
        dest = move_card(item.path, dest_dir, folder, key_cache)
        if dest is None:
            return None
        known.setdefault(folder.casefold(), folder)
        return dest, folder

    return Suggester(suggest, existing, set_language), assign


def chara_library_tab(folders: Sequence[Path], hint_lang: str | None, use_cache: bool, label: str = ""):
    """The Manual library of the character cards under `folders`: the user types a series per
    character and each card is moved at once.

    Returns a LibraryTab (its `.items` is empty when there is nothing to show) whose `finish()`
    must be called once the library is closed, also when it failed: it saves the key cache.
    `hint_lang` is the language of the character index's series titles (None: no index, only the
    existing series folders are suggested)."""
    if hint_lang:
        from kkafio.indices import agv
        agv.refresh("GROUP")
        if not agv.available():
            logger.info("GROUP", "No anime, games, vtubers index — only existing series folders are suggested.")
    else:
        logger.info("GROUP", "Hints Language is 'no' — only existing series folders are suggested.")

    key_cache = CharaKeyCache(folders, use_cache, "GROUP")

    def finish() -> None:
        if use_cache:
            logger.info("GROUP", f"Key cache: {key_cache.summary()}")
            key_cache.save()

    def folder_renamed(old: str, new: str, paths) -> None:
        rename_known(known, old, new)                     # the suggestions follow the folder
        for src, dest in paths.items():
            key_cache.moved(src, dest)                    # so do the cached keys

    known: dict[str, str] = {}
    try:
        # Always with subfolders: the library window shows the whole library, with each card's series
        # (its folder) shown; "Include subfolders" only decides what the LLM is sent.
        items = scan_manual_items(folders, True, key_cache)
        known.update(existing_folders(*folders))
        seed_known(known, items)
        suggest, assign = manual_callbacks(known, key_cache, hint_lang)
    except BaseException:
        finish()
        raise
    return LibraryTab(label, items, suggest, assign, dialog_text("dialog.manual"), COVER_RATIO, finish, "chara", parse_chara_guids,
                      lambda: collect_chara_guids_by_file(list(folders), use_cache), folder_renamed)
