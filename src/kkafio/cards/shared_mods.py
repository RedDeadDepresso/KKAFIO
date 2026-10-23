"""The "is this zipmod still used by something that stays?" check, shared by Delete Cards and
Uninstall Contents.

Both tasks are about to delete zipmods and must keep any that a character card, Studio scene or
coordinate card still referencing it. The GUIDs those cards use come from the per-folder GUID caches
in png_guids (reused when `use_cache` is on).
"""

import os
from pathlib import Path
from typing import Mapping, Sequence

from kkafio.cards.png_guids import (
    collect_chara_guids_by_file, collect_coord_guids_by_file, collect_scene_guids_by_file,
)
from kkafio.core.logger import logger


def path_key(p: Path | str) -> str:
    """Canonical string for comparing paths regardless of case (Windows),
    symlinks/junctions, or relative segments. Both the scanned cards and the
    exclusion set must go through this, otherwise a card can fail to exclude
    itself just because its folder was configured with different casing."""
    return os.path.normcase(os.path.realpath(str(p)))


def game_card_dirs(game_path: Mapping, chara_dirs: Sequence[Path | str] | None = None,
                   scene_dir: Path | str | None = None, coord_dir: Path | str | None = None,
                   ) -> tuple[list[Path], list[Path], list[Path]]:
    """The (chara, scene, coordinate) folders to scan: an explicit override where one is given,
    otherwise the game's own folders (both chara folders; scene / coordinate if installed)."""
    if chara_dirs:
        chara = [Path(d) for d in chara_dirs]
    else:
        chara = [Path(d) for d in (game_path.get("charaFemale"), game_path.get("charaMale")) if d]
    scene = [Path(scene_dir)] if scene_dir else ([Path(game_path["scene"])] if "scene" in game_path else [])
    coord = [Path(coord_dir)] if coord_dir else ([Path(game_path["coordinate"])] if "coordinate" in game_path else [])
    return chara, scene, coord


def collect_guids_in_use(chara_dirs: list[Path], scene_dirs: list[Path], coord_dirs: list[Path],
                         exclude: set[Path], use_cache: bool = False) -> set[str]:
    """Return the union of every mod GUID referenced by every chara card in
    chara_dirs, every scene in scene_dirs, and every coordinate card in
    coord_dirs, EXCLUDING every file in `exclude` (cards being deleted, and any coordinate
    cards bundled for deletion with them).

    The full per-file GUID cache for each folder is always built/reused —
    including the file(s) in `exclude` — and those files' own GUIDs are
    dropped from the union afterward, so the same on-disk cache can be reused
    no matter where the cards being deleted live.
    """
    guids: set[str] = set()
    exclude_keys = {path_key(p) for p in exclude}

    for dirs, collect_by_file in (
        (chara_dirs, collect_chara_guids_by_file),
        (scene_dirs, collect_scene_guids_by_file),
        (coord_dirs, collect_coord_guids_by_file),
    ):
        by_file = collect_by_file(dirs, use_cache=use_cache)
        for path_str, file_guids in by_file.items():
            if path_key(path_str) in exclude_keys:
                continue
            guids.update(file_guids)

    return guids


def find_guids_in_use(tag: str, game_path: Mapping, use_cache: bool,
                      exclude: set[Path] | None = None,
                      chara_dirs: Sequence[Path | str] | None = None,
                      scene_dir: Path | str | None = None,
                      coord_dir: Path | str | None = None) -> set[str]:
    """Scan the chara / scene / coordinate folders (see game_card_dirs) and return the GUIDs still
    referenced by cards other than `exclude`, logging progress under `tag`."""
    chara, scene, coord = game_card_dirs(game_path, chara_dirs, scene_dir, coord_dir)
    logger.line()
    logger.info(tag, "Checking for shared mods across chara/scene/coordinate folders before deleting...")
    in_use = collect_guids_in_use(chara, scene, coord, exclude or set(), use_cache=use_cache)
    logger.info(tag, f"Found {len(in_use)} GUID(s) still referenced elsewhere")
    return in_use


def keep_unshared(guid_by_path: Mapping[Path, str], in_use: set[str], tag: str) -> list[Path]:
    """The zipmods in `guid_by_path` ({path: guid}) that nothing else uses and can be deleted.
    Each one that is still in use is logged as kept."""
    free: list[Path] = []
    for path, guid in guid_by_path.items():
        if guid in in_use:
            logger.info(tag, f"    Keeping {path.name} — still used by another "
                             "installed character/scene/coordinate")
        else:
            free.append(path)
    return free
