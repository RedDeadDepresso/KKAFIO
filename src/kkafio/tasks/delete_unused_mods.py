"""
delete_unused_mods.py — Send zipmods that no installed character, scene or
                         coordinate card uses to the bin.

unused = every GUID found in the mods folder
         - every GUID referenced by the chara / scene / coordinate folders

Sideloader Modpack mods are never touched, and an exception list lets the
user protect folders, filenames or GUIDs from deletion.
"""

import os
from pathlib import Path

from send2trash import send2trash

from kkafio.tasks.base_task import BaseTask
from kkafio.cards.mods import build_mods_cache, in_modpack_folder, load_modpack_index
from kkafio.cards.png_guids import (
    collect_chara_guids, collect_coord_guids, collect_scene_guids,
)
from kkafio.core.config import GameType
from kkafio.core.errors import InputError
from kkafio.core.logger import logger

TAG = "UNUSED"

DEFAULT_EXCEPTION_LIST = "BetterPenetration\\\nClo\\"

_MOD_EXTENSIONS = (".zip", ".zipmod")


def parse_exception_list(raw: str) -> tuple[set[str], set[str], set[str]]:
    """Parse the exception-list textbox into (folders, filenames, guids).

    One entry per line; blank lines and lines starting with '#' are skipped.
      - ends with '\\' (or '/')        -> folder, relative to the mods folder
      - ends with .zip / .zipmod       -> filename
      - anything else                  -> GUID

    Folders are stored without the trailing slash and with '/' separators;
    filenames are lower-cased (matching is case-insensitive).
    """
    folders: set[str] = set()
    filenames: set[str] = set()
    guids: set[str] = set()

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(("\\", "/")):
            folder = line.replace("\\", "/").strip("/")
            if folder:
                folders.add(folder)
        elif line.lower().endswith(_MOD_EXTENSIONS):
            filenames.add(line.lower())
        else:
            guids.add(line)

    return folders, filenames, guids


def _norm(p: Path | str) -> str:
    return os.path.normcase(os.path.realpath(str(p)))


def _in_folder(path: Path, folder: Path) -> bool:
    """True if `path` is inside `folder` (case-insensitive on Windows)."""
    try:
        Path(_norm(path)).relative_to(_norm(folder))
        return True
    except ValueError:
        return False


class DeleteUnusedMods(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.delete_unused_mods
        self.exception_list_raw: str  = cfg.get("ExceptionList", DEFAULT_EXCEPTION_LIST)
        self.use_cache         : bool = cfg.get("UseCache", True)
        self.mods_dir_str      : str  = cfg.get("ModsDir", "")
        self.chara_dir_str     : str  = cfg.get("CharaDir", "")
        self.scene_dir_str     : str  = cfg.get("SceneDir", "")
        self.coord_dir_str     : str  = cfg.get("CoordDir", "")

    def _card_dirs(self) -> tuple[list[Path], list[Path], list[Path]]:
        game_path = self.config.game_path

        if self.chara_dir_str:
            chara_dirs = [Path(self.chara_dir_str)]
        else:
            chara_dirs = [d for d in (game_path.get("charaFemale"), game_path.get("charaMale")) if d]
        if self.scene_dir_str:
            scene_dirs = [Path(self.scene_dir_str)]
        else:
            scene_dirs = [game_path["scene"]] if "scene" in game_path else []
        if self.coord_dir_str:
            coord_dirs = [Path(self.coord_dir_str)]
        else:
            coord_dirs = [game_path["coordinate"]] if "coordinate" in game_path else []

        return chara_dirs, scene_dirs, coord_dirs

    def run(self) -> None:
        game_path = self.config.game_path
        if self.mods_dir_str:
            mods_dir = Path(self.mods_dir_str)
        elif "mods" in game_path:
            mods_dir = game_path["mods"]
        else:
            raise InputError("Mods directory not set and not resolvable from game path.", tag=TAG)
        if not mods_dir.exists():
            raise InputError(f"Mods directory does not exist: {mods_dir}", tag=TAG)

        chara_dirs, scene_dirs, coord_dirs = self._card_dirs()
        chara_dirs = [d for d in chara_dirs if d.exists()]
        scene_dirs = [d for d in scene_dirs if d.exists()]
        coord_dirs = [d for d in coord_dirs if d.exists()]
        # Safety: with no card folder to read, every mod would look unused.
        if not (chara_dirs or scene_dirs or coord_dirs):
            raise InputError(
                "No chara, scene or coordinate folder found — refusing to run, "
                "since every mod would be considered unused.", tag=TAG)

        ex_folders, ex_filenames, ex_guids = parse_exception_list(self.exception_list_raw)

        self.log_start(TAG)
        logger.info(TAG, f"Mods directory : {mods_dir}")
        logger.info(TAG, f"Use cache      : {self.use_cache}")
        logger.info(TAG, f"Exceptions     : {len(ex_folders)} folder(s), "
                         f"{len(ex_filenames)} filename(s), {len(ex_guids)} GUID(s)")
        logger.line()

        # Sideloader Modpack folders are excluded from the map entirely.
        guid_str_map = build_mods_cache(mods_dir, include_modpack=False, use_cache=self.use_cache)
        guid_map = {guid: Path(p) for guid, p in guid_str_map.items()}
        logger.info(TAG, f"Mods found (excluding Sideloader Modpack): {len(guid_map)}")

        all_guids: set[str] = set()
        all_guids |= collect_chara_guids(chara_dirs, self.use_cache) if chara_dirs else set()
        all_guids |= collect_scene_guids(scene_dirs, self.use_cache) if scene_dirs else set()
        all_guids |= collect_coord_guids(coord_dirs, self.use_cache) if coord_dirs else set()
        logger.info(TAG, f"GUIDs used by chara/scene/coordinate: {len(all_guids)}")

        unused = set(guid_map.keys()) - all_guids
        logger.info(TAG, f"Unused mods: {len(unused)}")
        logger.line()

        game_type = self.config.config_data.get("Core", {}).get(
            "GameType", GameType.KOIKATSU.value)
        modpack_index = load_modpack_index(game_type=game_type) or {}
        folder_paths = [mods_dir / f for f in ex_folders]

        moved = 0
        skipped = 0
        for guid in sorted(unused):
            path = guid_map[guid]

            if guid in modpack_index or in_modpack_folder(path, mods_dir):
                skipped += 1
                logger.skipped(TAG, f"{path.name} — Sideloader Modpack")
                continue
            if guid in ex_guids:
                skipped += 1
                logger.skipped(TAG, f"{path.name} — excepted GUID {guid}")
                continue
            if path.name.lower() in ex_filenames:
                skipped += 1
                logger.skipped(TAG, f"{path.name} — excepted filename")
                continue
            hit = next((f for f in folder_paths if _in_folder(path, f)), None)
            if hit is not None:
                skipped += 1
                logger.skipped(TAG, f"{path.name} — excepted folder {hit.name}")
                continue
            if not path.exists():
                skipped += 1
                continue

            try:
                send2trash(str(path))
                moved += 1
                logger.removed(TAG, f"{path.name}  ({guid})")
            except Exception as e:
                skipped += 1
                logger.error(TAG, f"Could not delete {path.name}: {e}")

        self.log_done(TAG, moved=moved, skipped=skipped)
