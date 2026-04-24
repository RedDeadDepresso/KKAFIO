"""
delete_chara.py — Send character cards and their associated files to the bin.
"""



from pathlib import Path

from send2trash import send2trash

from tasks.base_task import BaseTask
from util.chara_ops import (
    find_matching_coords, parse_chara_guids,
    parse_coord_guids, resolve_paths, scan_mods,
)
from util.logger import logger


class DeleteChara(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.delete_chara
        self.chara_paths   : list[str] = cfg.get("CharaPaths", [])
        self.auto_resolve  : bool      = cfg.get("AutoResolve", True)
        self.use_cache     : bool      = cfg.get("UseCache", True)
        self.mods_dir_str  : str       = cfg.get("ModsDir", "")
        self.coord_dir_str : str       = cfg.get("CoordDir", "")

    def _collect_files(self, chara_path: Path, game_base: Path,
                       mods_ov: Path | None, coord_ov: Path | None) -> list[Path]:
        logger.info("DELET", f"Processing: {chara_path.name}")

        mods_dir, coord_dir = resolve_paths(
            chara_path, game_base, self.auto_resolve, mods_ov, coord_ov)

        files: list[Path] = [chara_path]

        if coord_dir and coord_dir.exists():
            from kkloader import KoikatuCharaData
            try:
                kc = KoikatuCharaData.load(str(chara_path))
                coord_paths = find_matching_coords(kc["Coordinate"].data, coord_dir,
                                                    use_cache=self.use_cache)
                logger.info("DELET", f"  Matching coordinates: {len(coord_paths)}")
                for cp in coord_paths:
                    logger.info("DELET", f"    {cp.name}")
                files.extend(coord_paths)
            except Exception as e:
                logger.error("DELET", f"  Could not match coords: {e}")
        else:
            logger.info("DELET", "  Coordinate directory not available — skipping")

        coord_guids: set[str] = set()
        for f in files[1:]:
            coord_guids.update(parse_coord_guids(f))

        all_guids = set(parse_chara_guids(chara_path)) | coord_guids

        if mods_dir and mods_dir.exists() and all_guids:
            logger.info("DELET",
                f"  Scanning mods ({len(all_guids)} GUIDs needed): {mods_dir}")
            # include_modpack is intentionally non-configurable here — never touch modpack mods
            guid_map = scan_mods(mods_dir, all_guids, include_modpack=False,
                                use_cache=self.use_cache)
            logger.info("DELET",
                f"  Zipmods found: {len(guid_map)}  "
                f"missing: {len(all_guids - set(guid_map.keys()))}")
            files.extend(guid_map.values())
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

            self.log_start("DELET")
            files   = self._collect_files(chara_path, game_base, mods_ov, coord_ov)
            deleted = 0

            logger.info("DELET", f"  Sending {len(files)} file(s) to bin:")
            for f in files:
                try:
                    send2trash(str(f))
                    logger.removed("DELET", f.name)
                    deleted += 1
                except Exception as e:
                    logger.error("DELET", f"  Could not delete {f.name}: {e}")

            self.log_done("DELET", moved=deleted,
                          skipped=len(files) - deleted)
