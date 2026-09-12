"""
delete_cards.py — Send character cards, coordinate cards (or Studio scenes)
                   and their associated files to the bin.
"""



from pathlib import Path

from send2trash import send2trash

from tasks.base_task import BaseTask
from utils.chara_ops import (
    collect_chara_guids, collect_coord_guids, collect_scene_guids,
    find_matching_coords, parse_chara_guids,
    parse_coord_guids, parse_scene_guids, resolve_paths, scan_mods,
)
from utils.classifier import CardType, get_card_type, is_coordinate
from utils.logger import logger


def _collect_guids_in_use(chara_dirs: list[Path], scene_dirs: list[Path],
                          coord_dirs: list[Path],
                          exclude: set[Path], use_cache: bool = False) -> set[str]:
    """Return the union of every mod GUID referenced by every chara card in
    chara_dirs, every scene in scene_dirs, and every coordinate card in
    coord_dirs. Used by CheckSharedMods to make sure a zipmod isn't deleted
    out from under a character/scene/coordinate that isn't being touched.

    When `use_cache` is True, this reuses the same incremental GUID caches
    as DownloadMissingMods (`kkafio_chara_guid_cache.json` etc.), so repeat
    runs against an unchanged folder skip re-parsing every card. Anything in
    `exclude` (the file(s) actually being deleted) is scanned separately
    since it must never be cached as "in use elsewhere".
    """
    guids: set[str] = set()

    def _has_excluded(dirs: list[Path]) -> bool:
        """Cache collectors scan whole folders and can't skip individual
        excluded files, so if any excluded file lives inside one of these
        dirs we fall back to an uncached, exclude-aware scan instead."""
        resolved_dirs = [d.resolve() for d in dirs]
        return any(d in p.parents for d in resolved_dirs for p in exclude)

    def _scan_uncached(dirs: list[Path], is_valid, parse_guids) -> set[str]:
        found: set[str] = set()
        for d in dirs:
            if not d.exists():
                continue
            for png in d.rglob("*.png"):
                if png.resolve() in exclude:
                    continue
                try:
                    raw = png.read_bytes()
                    if is_valid(raw):
                        found.update(parse_guids(png))
                except Exception:
                    pass
        return found

    chara_is_valid = lambda raw: get_card_type(raw) in (CardType.KK, CardType.KKSP, CardType.KKS)
    scene_is_valid = lambda raw: get_card_type(raw) == CardType.SCENE
    coord_is_valid = lambda raw: get_card_type(raw) == CardType.UNKNOWN and is_coordinate(raw)

    for dirs, collector, is_valid, parse_guids in (
        (chara_dirs, collect_chara_guids, chara_is_valid, parse_chara_guids),
        (scene_dirs, collect_scene_guids, scene_is_valid, parse_scene_guids),
        (coord_dirs, collect_coord_guids, coord_is_valid, parse_coord_guids),
    ):
        if use_cache and not _has_excluded(dirs):
            guids.update(collector(dirs, use_cache=True))
        else:
            guids.update(_scan_uncached(dirs, is_valid, parse_guids))

    return guids


class DeleteCards(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.delete_cards
        self.content_paths      : list[str] = cfg.get("ContentPaths", [])
        self.check_shared_mods  : bool      = cfg.get("CheckSharedMods", True)
        self.auto_resolve       : bool      = cfg.get("AutoResolve", True)
        self.use_cache          : bool      = cfg.get("UseCache", True)
        self.include_coordinates: bool      = cfg.get("IncludeCoordinates", True)
        self.mods_dir_str       : str       = cfg.get("ModsDir", "")
        self.coord_dir_str      : str       = cfg.get("CoordDir", "")

    def _collect_files(self, content_path: Path, game_base: Path,
                       mods_ov: Path | None, coord_ov: Path | None,
                       guids_in_use_elsewhere: set[str] | None) -> list[Path]:
        logger.info("DELETE", f"Processing: {content_path.name}")

        raw       = content_path.read_bytes()
        card_type = get_card_type(raw)
        is_scene  = card_type == CardType.SCENE
        is_chara  = card_type in (CardType.KK, CardType.KKSP, CardType.KKS) and not is_scene
        is_coord  = card_type == CardType.UNKNOWN and is_coordinate(raw)

        mods_dir, coord_dir = resolve_paths(
            content_path, game_base, self.auto_resolve, mods_ov, coord_ov)

        files: list[Path] = [content_path]
        coord_guids: set[str] = set()

        if is_scene:
            own_guids = parse_scene_guids(content_path)
            logger.info("DELETE", "  Scene — skipping coordinate matching")
        elif is_coord:
            own_guids = parse_coord_guids(content_path)
            logger.info("DELETE", "  Coordinate card — bundling its own mods only")
        elif is_chara:
            own_guids = parse_chara_guids(content_path)

            if not self.include_coordinates:
                logger.info("DELETE", "  IncludeCoordinates disabled — skipping coordinate matching")
            elif coord_dir and coord_dir.exists():
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
        else:
            own_guids = []
            logger.warning("DELETE", "  Unrecognized card type — deleting file as-is, no mods scanned")

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
            logger.error("DELETE", "No character cards, coordinates, or scenes specified")
            return

        game_base = Path(self.config.config_data["Core"]["GamePath"])
        mods_ov   = Path(self.mods_dir_str)  if self.mods_dir_str  else None
        coord_ov  = Path(self.coord_dir_str) if self.coord_dir_str else None

        guids_in_use_elsewhere: set[str] | None = None
        if self.check_shared_mods:
            game_path  = self.config.game_path
            chara_dirs = [d for d in (game_path.get("charaFemale"), game_path.get("charaMale")) if d]
            scene_dirs = [game_path["scene"]] if "scene" in game_path else []
            coord_dirs = [game_path["coordinate"]] if "coordinate" in game_path else []
            exclude    = {p.resolve() for p in content_paths}

            logger.info("DELETE",
                "Checking for shared mods across chara/scene/coordinate folders before deleting...")
            guids_in_use_elsewhere = _collect_guids_in_use(chara_dirs, scene_dirs, coord_dirs,
                                                            exclude, use_cache=self.use_cache)
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
