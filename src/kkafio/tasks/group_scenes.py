"""
group_scenes.py — Sort Studio scenes into per-author folders using the
pepper-scene-index.

The index maps a scene's XXH3-128 content hash to its author. Each scene PNG is
hashed the same way Filter Duplicate Contents does (and shares its PNG hash
cache, so scenes already hashed there aren't hashed again); scenes found in the
index are moved to <scene folder>/<author>/.
"""

import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from kkafio.cards.cache_io import file_fp
from kkafio.core.errors import InputError, TaskFailedError
from kkafio.core.logger import logger
from kkafio.services.scene_index import load_scene_index
from kkafio.tasks.base_task import BaseTask
from kkafio.tasks.filter_duplicate_contents import (
    PNG_CACHE_FILE, _classify, _get_png_payload, _load_duplic_cache,
    _save_duplic_cache, _xxh,
)

TAG = "GRPSCN"

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def author_folder_name(author: str) -> str:
    """A safe single-folder name for `author` ('' if nothing usable is left)."""
    name = _INVALID_CHARS.sub("_", author).strip().rstrip(". ")
    if name.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
    }:
        name = f"_{name}"
    return name


class GroupScenes(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.group_scenes
        self.scene_dir_str      : str  = cfg.get("SceneDir", "")
        self.use_cache          : bool = cfg.get("UseCache", True)
        self.include_subfolders : bool = cfg.get("IncludeSubfolders", False)

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
        logger.line()

        index = load_scene_index()
        if not index:
            raise TaskFailedError("GroupScenes: the scene index is unavailable")

        png_files = list(folder.rglob("*.png") if self.include_subfolders else folder.glob("*.png"))
        logger.info(TAG, f"Scanning {len(png_files)} PNG file(s) in {folder}")

        # Same cache file (and entry layout) as Filter Duplicate Contents, so
        # hashes computed by either task are reused by the other.
        cache = _load_duplic_cache(folder, PNG_CACHE_FILE) if self.use_cache else {}

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
        logger.info(TAG, f"Scenes found: {len(scenes)}  (in the index: "
                         f"{sum(1 for _, d in scenes if d.lower() in index)})")
        logger.line()

        moved = skipped = unknown = 0
        moved_from: set[str] = set()
        for path, digest in sorted(scenes):
            author = index.get(digest.lower())
            if author is None:
                unknown += 1
                continue
            name = author_folder_name(author)
            if not name:
                logger.warning(TAG, f"{path.name}: unusable author name {author!r}")
                skipped += 1
                continue

            dest_dir = folder / name
            if path.parent == dest_dir:
                skipped += 1                      # already in the right author folder
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
            logger.success(TAG, f"Moved {path.name} -> {name}/")
            moved += 1
            # The move keeps mtime/size, so the cached hash stays valid under the new path.
            moved_from.add(str(path))
            entry = new_entries.pop(str(path), None)
            if entry is not None:
                new_entries[str(dest)] = entry

        if self.use_cache:
            # Merge into the existing cache: it also holds entries for files
            # this run didn't scan (e.g. subfolders when they're excluded).
            merged = {k: v for k, v in cache.items()
                      if k not in new_entries and k not in moved_from}
            merged.update(new_entries)
            _save_duplic_cache(folder, PNG_CACHE_FILE, merged)

        logger.line()
        logger.success(TAG, f"Done — moved: {moved}, already in place/skipped: {skipped}, "
                            f"not in index: {unknown}")
