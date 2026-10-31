"""
group_scenes.py — Sort Studio scenes into per-author folders using the
pepper-scene-index.

The index maps a scene's XXH3-128 content hash to {"author": ..., "year": ...}
(year may be null). Each scene PNG is hashed the same way Filter Duplicate
Contents does (and shares its PNG hash cache, so scenes already hashed there
aren't hashed again); scenes found in the index are moved to
<scene folder>/<author>/<year>/ (or <author>/Unsorted Years/ when the year is
null), or just <scene folder>/<author>/ when "subgroup by year" is off.

Grouping by hand (typing an author for each scene in a gallery dialog) is not part of this task;
it lives in the Run Gallery task (see run_gallery.py and dialogs/manual_group.py).
"""

import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from kkafio.cards.cache_io import file_fp
from kkafio.core.errors import InputError, TaskFailedError
from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.indices.author import canonical_name, load_authors, suggest_authors
from kkafio.indices.scene import load_scene_index
from kkafio.tasks.base_task import BaseTask
from kkafio.tasks.filter_duplicate_contents import (
    PNG_CACHE_FILE, _classify, _get_png_payload, _load_duplic_cache,
    _save_duplic_cache, _xxh,
)

TAG = "GRPSCN"

UNSORTED_YEARS = "Unsorted Years"

SCENE_COVER_RATIO = 180 / 320       # a scene card's picture is landscape (320x180)

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def author_folder_name(author: str) -> str:
    """A safe single-folder name for `author` ('' if nothing usable is left)."""
    name = _INVALID_CHARS.sub("_", author).strip().rstrip(". ")
    if name.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
    }:
        name = f"_{name}"
    return name


def scene_dialog_text():
    """The words of the gallery when it is sorting scenes into author folders."""
    from kkafio.dialogs.manual_group import DialogText
    return DialogText(
        search_placeholder=t("dialog.manual_scene.search_placeholder"),
        all=t("dialog.manual_scene.all"),
        untagged=t("dialog.manual_scene.untagged"),
        series_header=t("dialog.manual_scene.series_header"),
        filter_series=t("dialog.manual_scene.filter_series"),
        unknown_series=t("dialog.manual_scene.unknown_series"),
        no_cards=t("dialog.manual_scene.no_cards"),
        hint=t("dialog.manual_scene.hint"),
        status=t("dialog.manual_scene.status"),
        status_filtered=t("dialog.manual_scene.status_filtered"),
        moved=t("dialog.manual_scene.moved"),
        move_failed=t("dialog.manual_scene.move_failed"),
        multi_title=t("dialog.manual_scene.multi_title"),
        multi_prompt=t("dialog.manual_scene.multi_prompt"),
        multi_moved=t("dialog.manual_scene.multi_moved"),
        multi_moved_partial=t("dialog.manual_scene.multi_moved_partial"),
    )


def _is_author_dir(name: str) -> bool:
    """Folders starting with "_" or "." (e.g. _duplicates_) are not author folders."""
    return not name.startswith(("_", "."))


def existing_author_folders(folder: Path) -> dict[str, str]:
    """{casefolded name: name} of the author folders directly inside the scene folder."""
    known: dict[str, str] = {}
    try:
        for entry in sorted(folder.iterdir(), key=lambda e: e.name.casefold()):
            if entry.is_dir() and _is_author_dir(entry.name):
                known.setdefault(entry.name.casefold(), entry.name)
    except OSError:
        pass
    return known


def manual_callbacks(known: dict[str, str], authors: list, moves: dict[str, str],
                     max_suggestions: int = 8):
    """(suggest, assign) for the gallery dialog. `known` ({casefolded: name}) holds the author
    folders that exist and grows as folders are created; `authors` are the known authors (see
    indices/author). Every move `assign` makes is recorded in `moves` ({old path: new path}) so the
    caller can bring the hash cache along."""

    def suggest(text: str) -> list[str]:
        q = text.strip().casefold()
        out = suggest_authors(text, authors, max_suggestions)
        seen = {n.casefold() for n in out}
        if q:                                              # the existing folders that match too
            local = sorted((n for n in known.values() if q in n.casefold() and n.casefold() not in seen),
                           key=lambda n: (not n.casefold().startswith(q), n.casefold()))
            out += local
        else:
            out = sorted(known.values(), key=str.casefold)
        return out[:max_suggestions]

    def assign(item, text: str):
        name = author_folder_name(canonical_name(text, authors))
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

    return suggest, assign


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


def scan_scenes(folder: Path, include_subfolders: bool, use_cache: bool):
    """Hash the PNGs (reusing the shared cache) and pick out the scenes.
    Returns (scenes [(path, digest)], cache as loaded, this scan's cache entries)."""
    png_files = list(folder.rglob("*.png") if include_subfolders else folder.glob("*.png"))
    logger.info(TAG, f"Scanning {len(png_files)} PNG file(s) in {folder}")

    # Same cache file (and entry layout) as Filter Duplicate Contents, so
    # hashes computed by either task are reused by the other.
    cache = _load_duplic_cache(folder, PNG_CACHE_FILE) if use_cache else {}

    def _hash(path: Path):
        sp, fp = str(path), file_fp(path)
        cached = cache.get(sp)
        if cached and cached.get("fp") == fp and "xxh" in cached:
            return path, cached["xxh"], cached.get("category"), fp, True
        data = path.read_bytes()
        payload = _get_png_payload(data)
        return path, (_xxh(payload) if payload else _xxh(data)), _classify(data), fp, False

    scenes: list[tuple[Path, str]] = []
    new_entries: dict[str, dict] = {}
    reused = 0
    workers = min(32, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_hash, p): p for p in png_files}
        for future in as_completed(futures):
            try:
                path, digest, category, fp, was_cached = future.result()
            except Exception as e:
                logger.error(TAG, f"Could not read {futures[future].name}: {e}")
                continue
            new_entries[str(path)] = {"fp": fp, "xxh": digest, "category": category}
            reused += was_cached
            if category == "scene":
                scenes.append((path, digest))
    if reused:
        logger.info(TAG, f"Hash cache: {reused} unchanged, {len(png_files) - reused} new/changed")
    return scenes, cache, new_entries


def save_hash_cache(folder: Path, cache: dict, new_entries: dict, moved_from: set[str]) -> None:
    """Write a scan's entries to the cache file. It's merged into what's already
    there, since the file also holds entries for files that scan didn't cover
    (e.g. subfolders when they're excluded); the old path of a moved scene is dropped."""
    merged = {k: v for k, v in cache.items()
              if k not in new_entries and k not in moved_from}
    merged.update(new_entries)
    _save_duplic_cache(folder, PNG_CACHE_FILE, merged)


def scene_gallery_tab(folder: Path, use_cache: bool, label: str = ""):
    """The Manual gallery of the scenes under `folder` (subfolders included): the user names an
    author per scene and each card is moved at once.

    Returns a GalleryTab (its `.items` is empty when there is nothing to show) whose `finish()`
    must be called once the gallery is closed, also when it failed: it brings the hash cache
    along with the scenes that were moved."""
    from kkafio.dialogs.manual_group import CharaItem, GalleryTab

    logger.info(TAG, "Manual: reading the scenes")
    # Always with subfolders: the gallery is the whole library, with each scene's author (its
    # folder) shown; "Include subfolders" only decides what the automatic grouping looks at.
    scenes, cache, new_entries = scan_scenes(folder, True, use_cache)
    items = []
    for path, _digest in sorted(scenes):
        parts = path.relative_to(folder).parts
        if not all(_is_author_dir(part) for part in parts[:-1]):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        items.append(CharaItem(path=path, root=folder, name=path.stem,
                               series=parts[0] if len(parts) > 1 else None,
                               size=st.st_size, mtime=st.st_mtime,
                               ctime=getattr(st, "st_birthtime", st.st_ctime)))
    if use_cache:
        save_hash_cache(folder, cache, new_entries, set())     # hashing is the expensive part

    authors = load_authors()
    known = existing_author_folders(folder)
    for item in items:
        if item.series:
            known.setdefault(item.series.casefold(), item.series)
    moves: dict[str, str] = {}
    suggest, assign = manual_callbacks(known, authors, moves)
    logger.info(TAG, f"{len(items)} scene(s) and {len(authors)} known author(s) for the gallery")

    def finish() -> None:
        if use_cache and moves:
            for old, new in moves.items():                      # a move keeps mtime/size: the hash stays valid
                entry = new_entries.pop(old, None)
                if entry is not None:
                    new_entries[new] = entry
            save_hash_cache(folder, cache, new_entries, set(moves))

    return GalleryTab(label, items, suggest, assign, scene_dialog_text(), SCENE_COVER_RATIO, finish)


class GroupScenes(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.group_scenes
        self.scene_dir_str      : str  = cfg.get("SceneDir", "")
        self.use_cache          : bool = cfg.get("UseCache", True)
        self.include_subfolders : bool = cfg.get("IncludeSubfolders", False)
        self.subgroup_by_year   : bool = cfg.get("SubgroupByYear", True)

    def _scene_dir(self) -> Path:
        if self.scene_dir_str.strip():
            folder = Path(self.scene_dir_str)
        else:
            folder = self.config.game_path.get("scene")
            if folder is None:
                raise InputError("Scene folder not set and not resolvable from game path "
                                 "(is Studio installed?).", tag=TAG)
            folder = Path(folder)
        if not folder.exists():
            raise InputError(f"Scene directory does not exist: {folder}", tag=TAG)
        return folder

    def run(self) -> None:
        folder = self._scene_dir()

        self.log_start(TAG, str(folder))
        logger.info(TAG, f"Use cache         : {self.use_cache}")
        logger.info(TAG, f"Include subfolders: {self.include_subfolders}")
        logger.info(TAG, f"Subgroup by year  : {self.subgroup_by_year}")
        logger.line()

        self._run_automatic(folder)

    def _scan(self, folder: Path, include_subfolders: bool):
        return scan_scenes(folder, include_subfolders, self.use_cache)

    def _save_cache(self, folder: Path, cache: dict, new_entries: dict, moved_from: set[str]) -> None:
        save_hash_cache(folder, cache, new_entries, moved_from)

    def _run_automatic(self, folder: Path) -> None:
        """Group the scenes the scene index knows into their author folders."""
        index = load_scene_index()
        if not index:
            raise TaskFailedError("GroupScenes: the scene index is unavailable")

        scenes, cache, new_entries = self._scan(folder, self.include_subfolders)
        logger.info(TAG, f"Scenes found: {len(scenes)}  (in the index: "
                         f"{sum(1 for _, d in scenes if d.lower() in index)})")
        logger.line()

        moved_from: set[str] = set()      # old paths of scenes moved during this run

        def save_cache() -> None:
            self._save_cache(folder, cache, new_entries, moved_from)

        if self.use_cache:
            # Saved before anything is moved: the hashes are the expensive part, and a run
            # that is stopped or fails during the moves must not throw them away.
            save_cache()

        moved = skipped = unknown = 0
        try:
            for path, digest in sorted(scenes):
                entry = index.get(digest.lower())
                if entry is None:
                    unknown += 1
                    continue
                author = entry["author"]
                name = author_folder_name(author)
                if not name:
                    logger.warning(TAG, f"{path.name}: unusable author name {author!r}")
                    skipped += 1
                    continue

                dest_dir = folder / name
                rel = f"{name}/"
                if self.subgroup_by_year:
                    year = entry.get("year")
                    sub = str(year) if year is not None else UNSORTED_YEARS
                    dest_dir = dest_dir / sub
                    rel = f"{name}/{sub}/"
                if path.parent == dest_dir:
                    skipped += 1                      # already in the right folder
                    continue
                dest = dest_dir / path.name
                if dest.exists():
                    stem, suffix, counter = path.stem, path.suffix, 1
                    while dest.exists():
                        dest = dest_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(dest))
                except Exception as e:
                    logger.error(TAG, f"Could not move {path.name}: {e}")
                    skipped += 1
                    continue
                logger.success(TAG, f"Moved {path.name} -> {rel}")
                moved += 1
                # The move keeps mtime/size, so the cached hash stays valid under the new path.
                moved_from.add(str(path))
                cache_entry = new_entries.pop(str(path), None)
                if cache_entry is not None:
                    new_entries[str(dest)] = cache_entry
        finally:
            # Saved again once scenes have moved (even if the run was cut short), so the cache
            # has each moved scene under its new path and the next run doesn't re-hash it.
            if self.use_cache and moved_from:
                save_cache()

        logger.line()
        logger.success(TAG, f"Done — moved: {moved}, already in place/skipped: {skipped}, "
                            f"not in index: {unknown}")
