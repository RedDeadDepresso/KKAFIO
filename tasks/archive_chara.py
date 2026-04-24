"""
archive_chara.py — Bundle KK character cards with their used zipmods and
                   matching coordinate cards into a 7z or zip archive.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from tasks.base_task import BaseTask
from util.chara_ops import (
    find_matching_coords, in_modpack_folder, parse_chara_guids,
    parse_coord_guids, resolve_paths, scan_mods,
)
from util.logger import logger

ArchiveFormat = Literal["7z", "zip"]


# ---------------------------------------------------------------------------
# Main module class
# ---------------------------------------------------------------------------

class ArchiveChara(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.archive_chara
        self.chara_paths      : list[str] = cfg.get("CharaPaths", [])
        self.format           : str       = cfg.get("Format", "7z")
        self.auto_resolve     : bool      = cfg.get("AutoResolve", True)
        self.use_cache        : bool      = cfg.get("UseCache", True)
        self.include_modpack  : bool      = cfg.get("IncludeModpack", False)
        self.combined_archive : bool      = cfg.get("CombinedArchive", True)
        self.mods_dir_str     : str       = cfg.get("ModsDir", "")
        self.coord_dir_str    : str       = cfg.get("CoordDir", "")
        self.output_dir_str   : str       = cfg.get("OutputDir", "")

    def _process_one(self, chara_path: Path, game_base: Path,
                     mods_ov: Path | None,
                     coord_ov: Path | None) -> tuple[list[Path], list[Path]]:
        """Return (coord_paths, zipmod_paths) for a single chara card."""
        logger.info("ARCHV", f"Processing: {chara_path.name}")

        mods_dir, coord_dir = resolve_paths(
            chara_path, game_base, self.auto_resolve, mods_ov, coord_ov)

        chara_guids = parse_chara_guids(chara_path)
        logger.info("ARCHV", f"  Zipmod GUIDs in chara : {len(chara_guids)}")

        coord_paths: list[Path] = []
        coord_guids_all: set[str] = set()

        if coord_dir and coord_dir.exists():
            from kkloader import KoikatuCharaData
            try:
                kc = KoikatuCharaData.load(str(chara_path))
                coord_paths = find_matching_coords(kc["Coordinate"].data, coord_dir,
                                                    use_cache=self.use_cache)
                logger.info("ARCHV", f"  Matching coordinates  : {len(coord_paths)}")
                for cp in coord_paths:
                    logger.info("ARCHV", f"    {cp.name}")
                    coord_guids_all.update(parse_coord_guids(cp))
            except Exception as e:
                logger.error("ARCHV", f"  Could not match coords: {e}")
        else:
            logger.info("ARCHV", "  Coordinate directory not available — skipping")

        all_guids = set(chara_guids) | coord_guids_all
        zipmod_paths: list[Path] = []

        if mods_dir and mods_dir.exists() and all_guids:
            logger.info("ARCHV",
                f"  Scanning mods ({len(all_guids)} GUIDs needed): {mods_dir}")
            guid_map = scan_mods(mods_dir, all_guids,
                                 include_modpack=self.include_modpack,
                                 use_cache=self.use_cache)
            if not self.include_modpack:
                skipped = sum(1 for zp in mods_dir.rglob("*.zipmod")
                              if in_modpack_folder(zp, mods_dir))
                if skipped:
                    logger.info("ARCHV",
                        f"  Skipped {skipped} zipmod(s) in Sideloader Modpack folder(s)")
            zipmod_paths = list(guid_map.values())
            missing = all_guids - set(guid_map.keys())
            logger.info("ARCHV",
                f"  Zipmods found: {len(zipmod_paths)}  missing: {len(missing)}")
            for m in sorted(missing):
                logger.warning("ARCHV", f"    (missing) {m}")
        elif not mods_dir:
            logger.info("ARCHV", "  Mods directory not available — skipping mod lookup")

        return coord_paths, zipmod_paths

    def run(self) -> None:
        chara_paths = [Path(p) for p in self.chara_paths if p]
        if not chara_paths:
            logger.error("ARCHV", "No character cards specified")
            return

        game_base  = Path(self.config.config_data["Core"]["GamePath"])
        mods_ov    = Path(self.mods_dir_str)   if self.mods_dir_str   else None
        coord_ov   = Path(self.coord_dir_str)  if self.coord_dir_str  else None
        output_dir = Path(self.output_dir_str) if self.output_dir_str else None
        ext        = ".7z" if self.format == "7z" else ".zip"

        self.log_start("ARCHV")

        if self.combined_archive:
            # All cards, their coords, and their mods in one archive
            all_files: list[Path] = []
            seen: set[Path] = set()

            for chara_path in chara_paths:
                if not chara_path.is_file():
                    logger.error("ARCHV", f"Not found: {chara_path}")
                    continue
                coord_paths, zipmod_paths = self._process_one(
                    chara_path, game_base, mods_ov, coord_ov)
                for f in [chara_path] + coord_paths + zipmod_paths:
                    if f not in seen:
                        seen.add(f)
                        all_files.append(f)

            if not all_files:
                logger.error("ARCHV", "No files to archive")
                return

            out_dir = output_dir or chara_paths[0].parent
            out_dir.mkdir(parents=True, exist_ok=True)
            archive_name = (f"{chara_paths[0].stem}_bundle{ext}"
                            if len(chara_paths) == 1 else f"bundle__{datetime.now().strftime('%Y%m%d%H%M%S%f')}{ext}")
            archive_path = out_dir / archive_name

            logger.line()
            logger.info("ARCHV",
                f"Creating combined {self.format} archive: {archive_name} "
                f"({len(all_files)} file(s))")
            try:
                self.file_manager.create_archive(all_files, archive_path, self.format)
                logger.success("ARCHV", f"Done: {archive_path}")
            except Exception as e:
                logger.error("ARCHV", f"Archive creation failed: {e}")
        else:
            for chara_path in chara_paths:
                if not chara_path.is_file():
                    logger.error("ARCHV", f"Not found: {chara_path}")
                    continue
                logger.line()
                coord_paths, zipmod_paths = self._process_one(
                    chara_path, game_base, mods_ov, coord_ov)
                all_files = [chara_path] + coord_paths + zipmod_paths
                out_dir = output_dir or chara_path.parent
                out_dir.mkdir(parents=True, exist_ok=True)
                archive_path = out_dir / f"{chara_path.stem}_bundle{ext}"

                logger.info("ARCHV",
                    f"  Creating {self.format} archive: {archive_path.name} "
                    f"({len(all_files)} file(s))")
                try:
                    self.file_manager.create_archive(all_files, archive_path, self.format)
                    logger.success("ARCHV", f"  Done: {archive_path}")
                except Exception as e:
                    logger.error("ARCHV", f"  Archive creation failed: {e}")
