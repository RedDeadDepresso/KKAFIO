"""
delete_chara_scenes.py — Send character cards (or Studio scenes) and their
                          associated files to the bin.
"""



from pathlib import Path

from send2trash import send2trash

from tasks.base_task import BaseTask
from utils.chara_ops import (
    find_matching_coords, parse_chara_guids,
    parse_coord_guids, parse_scene_guids, resolve_paths, scan_mods,
)
from utils.classifier import CardType, get_card_type
from utils.logger import logger


class DeleteCharaScenes(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.delete_chara_scenes
        self.content_paths : list[str] = cfg.get("ContentPaths", [])
        self.auto_resolve  : bool      = cfg.get("AutoResolve", True)
        self.use_cache     : bool      = cfg.get("UseCache", True)
        self.mods_dir_str  : str       = cfg.get("ModsDir", "")
        self.coord_dir_str : str       = cfg.get("CoordDir", "")

    def _collect_files(self, content_path: Path, game_base: Path,
                       mods_ov: Path | None, coord_ov: Path | None) -> list[Path]:
        logger.info("DELETE", f"Processing: {content_path.name}")

        is_scene = get_card_type(content_path) == CardType.SCENE

        mods_dir, coord_dir = resolve_paths(
            content_path, game_base, self.auto_resolve, mods_ov, coord_ov)

        files: list[Path] = [content_path]
        coord_guids: set[str] = set()

        if is_scene:
            own_guids = parse_scene_guids(content_path)
            logger.info("DELETE", "  Scene — skipping coordinate matching")
        else:
            own_guids = parse_chara_guids(content_path)

            if coord_dir and coord_dir.exists():
                from kkloader import KoikatuCharaData
                try:
                    kc = KoikatuCharaData.load(str(content_path))
                    coord_paths = find_matching_coords(kc["Coordinate"].data, coord_dir,
                                                        use_cache=self.use_cache)
                    logger.info("DELETE", f"  Matching coordinates: {len(coord_paths)}")
                    for cp in coord_paths:
                        logger.info("DELETE", f"    {cp.name}")
                    files.extend(coord_paths)
                except Exception as e:
                    logger.error("DELETE", f"  Could not match coords: {e}")
            else:
                logger.info("DELETE", "  Coordinate directory not available — skipping")

            for f in files[1:]:
                coord_guids.update(parse_coord_guids(f))

        all_guids = set(own_guids) | coord_guids

        if mods_dir and mods_dir.exists() and all_guids:
            logger.info("DELETE",
                f"  Scanning mods ({len(all_guids)} GUIDs needed): {mods_dir}")
            # include_modpack is intentionally non-configurable here — never touch modpack mods
            guid_map = scan_mods(mods_dir, all_guids, include_modpack=False,
                                use_cache=self.use_cache)
            logger.info("DELETE",
                f"  Zipmods found: {len(guid_map)}  "
                f"missing: {len(all_guids - set(guid_map.keys()))}")
            files.extend(guid_map.values())
        elif not mods_dir:
            logger.info("DELETE", "  Mods directory not available — skipping mod lookup")

        return files

    def run(self) -> None:
        content_paths = [Path(p) for p in self.content_paths if p]
        if not content_paths:
            logger.error("DELETE", "No character cards or scenes specified")
            return

        game_base = Path(self.config.config_data["Core"]["GamePath"])
        mods_ov   = Path(self.mods_dir_str)  if self.mods_dir_str  else None
        coord_ov  = Path(self.coord_dir_str) if self.coord_dir_str else None

        for content_path in content_paths:
            if not content_path.is_file():
                logger.error("DELETE", f"Not found: {content_path}")
                continue

            self.log_start("DELETE")
            files   = self._collect_files(content_path, game_base, mods_ov, coord_ov)
            deleted = 0

            logger.info("DELETE", f"  Sending {len(files)} file(s) to bin:")
            for f in files:
                try:
                    send2trash(str(f))
                    logger.removed("DELETE", f.name)
                    deleted += 1
                except Exception as e:
                    logger.error("DELETE", f"  Could not delete {f.name}: {e}")

            self.log_done("DELETE", moved=deleted,
                          skipped=len(files) - deleted)
