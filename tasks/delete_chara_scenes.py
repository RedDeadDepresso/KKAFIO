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


def _collect_guids_in_use(chara_dirs: list[Path], scene_dirs: list[Path],
                          exclude: set[Path]) -> set[str]:
    """Scan every chara card in chara_dirs and every scene in scene_dirs
    (skipping anything in `exclude`) and return the union of every mod GUID
    they reference. Used by CheckSharedMods to make sure a zipmod isn't
    deleted out from under a character/scene that isn't being touched.
    """
    guids: set[str] = set()

    for d in chara_dirs:
        if not d.exists():
            continue
        for png in d.rglob("*.png"):
            if png.resolve() in exclude:
                continue
            try:
                if get_card_type(png.read_bytes()) in (CardType.KK, CardType.KKSP, CardType.KKS):
                    guids.update(parse_chara_guids(png))
            except Exception:
                pass

    for d in scene_dirs:
        if not d.exists():
            continue
        for png in d.rglob("*.png"):
            if png.resolve() in exclude:
                continue
            try:
                if get_card_type(png.read_bytes()) == CardType.SCENE:
                    guids.update(parse_scene_guids(png))
            except Exception:
                pass

    return guids


class DeleteCharaScenes(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.delete_chara_scenes
        self.content_paths    : list[str] = cfg.get("ContentPaths", [])
        self.check_shared_mods : bool     = cfg.get("CheckSharedMods", True)
        self.auto_resolve     : bool      = cfg.get("AutoResolve", True)
        self.use_cache        : bool      = cfg.get("UseCache", True)
        self.mods_dir_str     : str       = cfg.get("ModsDir", "")
        self.coord_dir_str    : str       = cfg.get("CoordDir", "")

    def _collect_files(self, content_path: Path, game_base: Path,
                       mods_ov: Path | None, coord_ov: Path | None,
                       guids_in_use_elsewhere: set[str] | None) -> list[Path]:
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

            kept_shared = 0
            if guids_in_use_elsewhere is not None:
                for guid in list(guid_map.keys()):
                    if guid in guids_in_use_elsewhere:
                        logger.info("DELETE",
                            f"    Keeping {guid_map[guid].name} — still used by another "
                            "installed character/scene")
                        del guid_map[guid]
                        kept_shared += 1

            logger.info("DELETE",
                f"  Zipmods found: {len(guid_map)}  "
                f"missing: {len(all_guids - set(guid_map.keys()) - (guids_in_use_elsewhere or set()))}"
                + (f"  kept (shared): {kept_shared}" if kept_shared else ""))
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

        guids_in_use_elsewhere: set[str] | None = None
        if self.check_shared_mods:
            game_path  = self.config.game_path
            chara_dirs = [d for d in (game_path.get("charaFemale"), game_path.get("charaMale")) if d]
            scene_dirs = [game_path["scene"]] if "scene" in game_path else []
            exclude    = {p.resolve() for p in content_paths}

            logger.info("DELETE",
                "Checking for shared mods across chara/scene folders before deleting...")
            guids_in_use_elsewhere = _collect_guids_in_use(chara_dirs, scene_dirs, exclude)
            logger.info("DELETE",
                f"Found {len(guids_in_use_elsewhere)} GUID(s) still referenced elsewhere")

        for content_path in content_paths:
            if not content_path.is_file():
                logger.error("DELETE", f"Not found: {content_path}")
                continue

            self.log_start("DELETE")
            files   = self._collect_files(content_path, game_base, mods_ov, coord_ov,
                                          guids_in_use_elsewhere)
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
