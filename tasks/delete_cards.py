"""
delete_cards.py — Send character cards, coordinate cards (or Studio scenes)
                   and their associated files to the bin.
"""



from pathlib import Path

from send2trash import send2trash

from tasks.base_task import BaseTask
from utils.chara_ops import (
    build_mods_cache, collect_chara_guids_by_file, collect_coord_guids_by_file,
    collect_scene_guids_by_file, find_matching_coords, load_mods_cache,
    load_modpack_index, parse_chara_guids, parse_coord_guids, parse_scene_guids,
    resolve_paths,
)
from utils.classifier import CardType, get_card_type, is_coordinate
from utils.config import GameType
from utils.logger import logger


def _collect_guids_in_use(chara_dirs: list[Path], scene_dirs: list[Path],
                          coord_dirs: list[Path],
                          exclude: set[Path], use_cache: bool = False) -> set[str]:
    """Return the union of every mod GUID referenced by every chara card in
    chara_dirs, every scene in scene_dirs, and every coordinate card in
    coord_dirs, EXCLUDING the card(s) actually being deleted. Used by
    CheckSharedMods to make sure a zipmod isn't deleted out from under a
    character/scene/coordinate that isn't being touched.

    This always scans (and, when `use_cache` is True, builds/reuses) the
    full per-file GUID cache for each folder — including the file(s) in
    `exclude` — then excludes those specific files' own GUIDs from the
    union afterward. That means the same on-disk cache
    (kkafio_chara_guid_cache.json etc.) can always be built/reused as-is,
    regardless of whether the card(s) being deleted happen to live inside
    one of these folders, instead of needing a separate uncached scan any
    time that's the case.
    """
    guids: set[str] = set()
    exclude_strs = {str(p.resolve()) for p in exclude}

    for dirs, collect_by_file in (
        (chara_dirs, collect_chara_guids_by_file),
        (scene_dirs, collect_scene_guids_by_file),
        (coord_dirs, collect_coord_guids_by_file),
    ):
        by_file = collect_by_file(dirs, use_cache=use_cache)
        for path_str, file_guids in by_file.items():
            if path_str in exclude_strs:
                continue
            guids.update(file_guids)

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
        self.chara_dir_str      : str       = cfg.get("CharaDir", "")
        self.scene_dir_str      : str       = cfg.get("SceneDir", "")
        self.coord_dir_str      : str       = cfg.get("CoordDir", "")
        # {mods_dir: {guid: path}} — built once per distinct mods_dir and
        # reused for every card, instead of re-scanning/re-validating the
        # whole mods folder from scratch on every single card. Entries are
        # removed in-memory as their zipmods get deleted during the run, so
        # later cards in the same run still see an accurate picture without
        # ever touching disk again.
        self._local_mods_maps: dict[Path, dict[str, Path]] = {}

    def _get_local_mods_map(self, mods_dir: Path) -> dict[str, Path]:
        mods_dir = mods_dir.resolve()
        cached = self._local_mods_maps.get(mods_dir)
        if cached is not None:
            return cached

        if self.use_cache:
            guid_str_map = load_mods_cache(mods_dir)
            if guid_str_map is None:
                logger.info("DELETE", f"Building mods cache for {mods_dir.name}...")
                guid_str_map = build_mods_cache(mods_dir, include_modpack=False)
                logger.info("DELETE", f"Mods cache built: {len(guid_str_map)} GUIDs")
            else:
                logger.info("DELETE", f"Mods cache hit: {len(guid_str_map)} GUIDs")
        else:
            guid_str_map = build_mods_cache(mods_dir, include_modpack=False)

        guid_map = {guid: Path(p) for guid, p in guid_str_map.items()}
        self._local_mods_maps[mods_dir] = guid_map
        return guid_map

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

            local_map = self._get_local_mods_map(mods_dir)
            game_type = self.config.config_data.get("Core", {}).get(
                "GameType", GameType.KOIKATSU.value)
            # include_modpack is intentionally non-configurable here — never touch modpack mods
            modpack_index = load_modpack_index(game_type=game_type) or {}

            guid_map: dict[str, Path] = {}
            for guid in all_guids:
                if guid in modpack_index:
                    continue  # modpack-provided — never touched by DeleteCards
                p = local_map.get(guid)
                if p is not None and p.exists():
                    guid_map[guid] = p

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
                f"missing: {len(all_guids - set(guid_map.keys()) - (guids_in_use_elsewhere or set()) - set(modpack_index))}"
                + (f"  kept (shared): {kept_shared}" if kept_shared else ""))
            files.extend(guid_map.values())

            # Reflect the deletions in the in-memory map so later cards in
            # this same run don't need to re-scan the mods folder to see
            # that these are now gone.
            for guid in guid_map:
                local_map.pop(guid, None)
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