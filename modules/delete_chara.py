"""
delete_chara.py — Send character cards and their associated files to the bin.

Uses the same path resolution and file-discovery logic as archive_chara:
  - Resolves mods/coord directories from game path or chara location
  - Finds matching coordinate cards by colour fingerprint
  - Finds used zipmods by GUID (never touches Sideloader Modpack mods)

All files are sent to the system recycle bin via send2trash.
"""

from __future__ import annotations

from pathlib import Path

from send2trash import send2trash

from modules.archive_chara import (
    _find_matching_coords,
    _in_modpack_folder,
    _parse_chara_guids,
    _parse_coord_guids,
    _resolve_paths,
    _scan_mods,
)
from util.logger import logger


class DeleteChara:
    def __init__(self, config, file_manager):
        self.config       = config
        self.file_manager = file_manager
        cfg = self.config.delete_chara
        self.chara_paths    : list[str] = cfg.get("CharaPaths", [])
        self.auto_resolve   : bool      = cfg.get("AutoResolve", True)
        self.mods_dir_str   : str       = cfg.get("ModsDir", "")
        self.coord_dir_str  : str       = cfg.get("CoordDir", "")

    def _collect_files(self, chara_path: Path, game_base: Path,
                       mods_ov: Path | None, coord_ov: Path | None) -> list[Path]:
        """Return all files associated with chara_path (card + coords + mods)."""
        logger.info("DELET", f"Processing: {chara_path.name}")

        mods_dir, coord_dir = _resolve_paths(
            chara_path, game_base, self.auto_resolve, mods_ov, coord_ov)

        files: list[Path] = [chara_path]

        # Matching coordinate cards
        if coord_dir and coord_dir.exists():
            from kkloader import KoikatuCharaData
            try:
                kc = KoikatuCharaData.load(str(chara_path))
                chara_slots = kc["Coordinate"].data
                coord_paths = _find_matching_coords(chara_slots, coord_dir)
                logger.info("DELET", f"  Matching coordinates: {len(coord_paths)}")
                for cp in coord_paths:
                    logger.info("DELET", f"    {cp.name}")
                files.extend(coord_paths)
            except Exception as e:
                logger.error("DELET", f"  Could not match coords: {e}")
        else:
            logger.info("DELET", "  Coordinate directory not available — skipping")

        # Used zipmods — never from Sideloader Modpack folders
        coord_guids: set[str] = set()
        for f in files[1:]:  # coord paths already added
            coord_guids.update(_parse_coord_guids(f))

        chara_guids = _parse_chara_guids(chara_path)
        all_guids   = set(chara_guids) | coord_guids

        if mods_dir and mods_dir.exists() and all_guids:
            logger.info("DELET",
                f"  Scanning mods ({len(all_guids)} GUIDs needed): {mods_dir}")
            # include_modpack=False is intentional and non-configurable here
            guid_map = _scan_mods(mods_dir, all_guids, include_modpack=False)
            zipmod_paths = list(guid_map.values())
            missing = all_guids - set(guid_map.keys())
            logger.info("DELET",
                f"  Zipmods found: {len(zipmod_paths)}  missing: {len(missing)}")
            for m in sorted(missing):
                logger.error("DELET", f"    (missing) {m}")
            files.extend(zipmod_paths)
        elif not mods_dir:
            logger.info("DELET", "  Mods directory not available — skipping mod lookup")

        return files

    def run(self) -> None:
        chara_paths = [Path(p) for p in self.chara_paths if p]
        if not chara_paths:
            logger.error("DELET", "No character cards specified")
            return

        game_base = Path(self.config.config_data["Core"]["GamePath"])
        mods_ov   = Path(self.mods_dir_str)  if self.mods_dir_str  else None
        coord_ov  = Path(self.coord_dir_str) if self.coord_dir_str else None

        for chara_path in chara_paths:
            if not chara_path.is_file():
                logger.error("DELET", f"Not found: {chara_path}")
                continue

            logger.line()
            files = self._collect_files(chara_path, game_base, mods_ov, coord_ov)

            logger.info("DELET", f"  Sending {len(files)} file(s) to bin:")
            deleted = 0
            for f in files:
                try:
                    send2trash(str(f))
                    logger.removed("DELET", f.name)
                    deleted += 1
                except Exception as e:
                    logger.error("DELET", f"  Could not delete {f.name}: {e}")

            logger.success("DELET", f"  Done — {deleted}/{len(files)} file(s) sent to bin")

        logger.line()
