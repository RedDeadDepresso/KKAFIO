"""
archive_cards.py — Bundle KK character cards, coordinate cards (or Studio
                    scenes) with their used zipmods and, for character cards,
                    matching coordinate cards into a 7z or zip archive.
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from tasks.base_task import BaseTask
from utils.chara_ops import (
    build_coord_cache, build_mods_cache, find_matching_coords, in_modpack_folder,
    load_modpack_index, parse_chara_guids, parse_coord_guids, parse_scene_guids,
    resolve_paths,
)
from utils.classifier import CardType, get_card_type, is_coordinate
from utils.config import GameType
from utils.logger import logger

ArchiveFormat = Literal["7z", "zip"]


# ---------------------------------------------------------------------------
# Main module class
# ---------------------------------------------------------------------------

class ArchiveCards(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.archive_cards
        self.content_paths      : list[str] = cfg.get("ContentPaths", [])
        self.format             : str       = cfg.get("Format", "7z")
        self.auto_resolve       : bool      = cfg.get("AutoResolve", True)
        self.use_cache          : bool      = cfg.get("UseCache", True)
        self.include_modpack    : bool      = cfg.get("IncludeModpack", False)
        self.combined_archive   : bool      = cfg.get("CombinedArchive", True)
        self.include_coordinates: bool      = cfg.get("IncludeCoordinates", True)
        self.mods_dir_str       : str       = cfg.get("ModsDir", "")
        self.coord_dir_str      : str       = cfg.get("CoordDir", "")
        self.output_dir_str     : str       = cfg.get("OutputPath", "")
        # Built once per distinct mods_dir/coord_dir and reused across every
        # card in the run, instead of re-scanning the whole folder from
        # scratch for every single card.
        self._local_mods_maps : dict[Path, dict[str, Path]]  = {}
        self._local_coord_maps: dict[Path, dict[str, str]]  = {}
        self._modpack_skip_counts: dict[Path, int] = {}

    def _get_local_mods_map(self, mods_dir: Path) -> dict[str, Path]:
        mods_dir = mods_dir.resolve()
        cached = self._local_mods_maps.get(mods_dir)
        if cached is not None:
            return cached
        guid_str_map = build_mods_cache(mods_dir, include_modpack=self.include_modpack,
                                        use_cache=self.use_cache)
        guid_map = {guid: Path(p) for guid, p in guid_str_map.items()}
        self._local_mods_maps[mods_dir] = guid_map
        return guid_map

    def _get_modpack_skip_count(self, mods_dir: Path) -> int:
        """How many zipmods under mods_dir live in a Sideloader Modpack
        folder — purely informational, logged once per card when
        IncludeModpack is off. Previously this did `mods_dir.rglob(...)`
        on every single card being archived; for a mods folder with
        thousands of zipmods (the Sideloader Modpack alone easily has
        several thousand) that's a full directory walk repeated once per
        card in the batch. Cached per mods_dir instead, computed once.
        """
        mods_dir = mods_dir.resolve()
        cached = self._modpack_skip_counts.get(mods_dir)
        if cached is not None:
            return cached
        count = sum(1 for zp in mods_dir.rglob("*.zipmod")
                    if in_modpack_folder(zp, mods_dir))
        self._modpack_skip_counts[mods_dir] = count
        return count

    def _get_coord_map(self, coord_dir: Path) -> dict[str, str]:
        coord_dir = coord_dir.resolve()
        cached = self._local_coord_maps.get(coord_dir)
        if cached is not None:
            return cached
        coord_map = build_coord_cache(coord_dir, use_cache=self.use_cache)
        self._local_coord_maps[coord_dir] = coord_map
        return coord_map

    def _process_one(self, content_path: Path, game_base: Path,
                     mods_ov: Path | None,
                     coord_ov: Path | None) -> tuple[list[Path], list[Path], set[str], set[str], set[str]]:
        """Return (coord_paths, zipmod_paths, missing, modpack_missing,
        all_guids) for a single chara card, coordinate card, or Studio
        scene. `missing` is GUIDs that couldn't be found anywhere;
        `modpack_missing` is GUIDs that exist in the Sideloader Modpack but
        were deliberately excluded (only ever non-empty when
        include_modpack is off)."""
        logger.info("ARCHV", f"Processing: {content_path.name}")

        raw       = content_path.read_bytes()
        card_type = get_card_type(raw)
        is_scene  = card_type == CardType.SCENE
        is_chara  = card_type in (CardType.KK, CardType.KKSP, CardType.KKS) and not is_scene
        is_coord  = card_type == CardType.UNKNOWN and is_coordinate(raw)

        mods_dir, coord_dir = resolve_paths(
            content_path, game_base, self.auto_resolve, mods_ov, coord_ov)

        coord_paths: list[Path] = []
        coord_guids_all: set[str] = set()

        if is_scene:
            own_guids = parse_scene_guids(content_path)
            logger.info("ARCHV", f"  Zipmod GUIDs in scene : {len(own_guids)}")
            logger.info("ARCHV", "  Scene — skipping coordinate matching")
        elif is_coord:
            own_guids = parse_coord_guids(content_path)
            logger.info("ARCHV", f"  Zipmod GUIDs in coord : {len(own_guids)}")
        elif is_chara:
            own_guids = parse_chara_guids(content_path)
            logger.info("ARCHV", f"  Zipmod GUIDs in chara : {len(own_guids)}")

            if not self.include_coordinates:
                logger.info("ARCHV", "  IncludeCoordinates disabled — skipping coordinate matching")
            elif coord_dir and coord_dir.exists():
                from kkloader import KoikatuCharaData
                try:
                    kc = KoikatuCharaData.load(str(content_path))
                    coord_map = self._get_coord_map(coord_dir)
                    coord_paths = find_matching_coords(kc["Coordinate"].data, coord_map)
                    logger.info("ARCHV", f"  Matching coordinates  : {len(coord_paths)}")
                    for cp in coord_paths:
                        logger.info("ARCHV", f"    {cp.name}")
                        coord_guids_all.update(parse_coord_guids(cp))
                except Exception as e:
                    logger.error("ARCHV", f"  Could not match coords: {e}")
            else:
                logger.info("ARCHV", "  Coordinate directory not available — skipping")
        else:
            own_guids = []
            logger.warning("ARCHV", "  Unrecognized card type — archiving file as-is, no mods scanned")

        all_guids = set(own_guids) | coord_guids_all
        zipmod_paths: list[Path] = []
        missing: set[str] = set()
        modpack_missing: set[str] = set()
        game_type = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)

        if mods_dir and mods_dir.exists() and all_guids:
            logger.info("ARCHV",
                f"  Scanning mods ({len(all_guids)} GUIDs needed): {mods_dir}")

            modpack_index = load_modpack_index(game_type=game_type) or {}
            local_map = self._get_local_mods_map(mods_dir)

            guid_map: dict[str, Path] = {}
            for guid in all_guids:
                if guid in modpack_index:
                    if self.include_modpack:
                        p = mods_dir / modpack_index[guid]
                        if p.exists():
                            guid_map[guid] = p
                    continue  # resolved via index either way — never fall through to local scan
                p = local_map.get(guid)
                if p is not None and p.exists():
                    guid_map[guid] = p

            if not self.include_modpack:
                skipped = self._get_modpack_skip_count(mods_dir)
                if skipped:
                    logger.info("ARCHV",
                        f"  Skipped {skipped} zipmod(s) in Sideloader Modpack folder(s)")
            zipmod_paths = list(guid_map.values())
            all_missing = all_guids - set(guid_map.keys())

            # When include_modpack is off, some "missing" GUIDs aren't
            # actually unavailable — they're just excluded because they're
            # covered by the Sideloader Modpack. Report those separately so
            # it's clear they're not a real problem.
            modpack_missing = all_missing & set(modpack_index.keys()) if not self.include_modpack else set()
            missing = all_missing - modpack_missing

            logger.info("ARCHV",
                f"  Zipmods found: {len(zipmod_paths)}  missing: {len(missing)}"
                + (f"  in Sideloader Modpack (excluded): {len(modpack_missing)}"
                   if modpack_missing else ""))
            for m in sorted(missing):
                logger.warning("ARCHV", f"    (missing) {m}")
            for m in sorted(modpack_missing):
                logger.info("ARCHV", f"    (Sideloader Modpack, not included) {m}")
        elif not mods_dir:
            logger.info("ARCHV", "  Mods directory not available — skipping mod lookup")

        return coord_paths, zipmod_paths, missing, modpack_missing, all_guids

    @staticmethod
    def _display_name(content_path: Path) -> str:
        """Return the character's in-game name (or the filename stem for
        scenes, coordinates, / on failure)."""
        card_type = get_card_type(content_path)
        if card_type not in (CardType.KK, CardType.KKSP, CardType.KKS):
            return content_path.stem
        try:
            from kkloader import KoikatuCharaData
            kc = KoikatuCharaData.load(str(content_path))
            p  = kc["Parameter"]
            name = f"{p.data.get('lastname', '')} {p.data.get('firstname', '')}".strip()
            return name or p.data.get("nickname", "") or content_path.stem
        except Exception:
            return content_path.stem

    @staticmethod
    def _build_readme(
        cards: list[dict],
        archive_name: str,
        include_modpack: bool,
        generated: str,
    ) -> str:
        """Build a human-readable README.txt for the archive.

        cards: list of {
            path, display_name, coords: [Path], mods: [Path],
            missing: set[str], modpack_missing: set[str], all_guids: set[str]
        }
        """
        lines: list[str] = []
        lines += [
            f"KKAFIO Archive — {archive_name}",
            f"Generated : {generated}",
            "",
            f"Cards     : {len(cards)}",
            f"Modpack mods included: {'Yes' if include_modpack else 'No — recipient needs Sideloader Modpack'}",
            "",
            "=" * 60,
            "",
        ]

        total_mods    = set()
        total_missing = set()
        total_modpack_missing = set()

        for card in cards:
            total_mods.update(p.name for p in card["mods"])
            total_missing.update(card["missing"])
            total_modpack_missing.update(card.get("modpack_missing", set()))

            lines.append(f"Card      : {card['display_name']}")
            lines.append(f"File      : {card['path'].name}")
            lines.append(f"Mods      : {len(card['all_guids'])} required, "
                         f"{len(card['mods'])} included, "
                         f"{len(card['missing'])} missing")

            if card["coords"]:
                lines.append(f"Coords    : {len(card['coords'])} included")
                for cp in card["coords"]:
                    lines.append(f"  - {cp.name}")

            if card["mods"]:
                lines.append("Mods included:")
                for mp in sorted(card["mods"], key=lambda p: p.name):
                    lines.append(f"  + {mp.name}")

            if card["missing"]:
                lines.append("Mods missing (not found locally):")
                for m in sorted(card["missing"]):
                    lines.append(f"  ! {m}")

            if card.get("modpack_missing"):
                lines.append("Mods excluded (in Sideloader Modpack, not bundled):")
                for m in sorted(card["modpack_missing"]):
                    lines.append(f"  ! {m}")

            lines.append("")

        if len(cards) > 1:
            lines += [
                "=" * 60,
                f"Total mods bundled : {len(total_mods)}",
                f"Total missing      : {len(total_missing)}",
            ]
            if total_missing:
                lines.append("Missing GUIDs:")
                for m in sorted(total_missing):
                    lines.append(f"  ! {m}")

        if total_modpack_missing:
            lines += [
                "",
                "=" * 60,
                "",
                "Mods excluded — Sideloader Modpack",
                "-----------------------------------",
                "These GUIDs are available via the Sideloader Modpack but were NOT",
                "bundled in this archive, since Include Modpack is off. The recipient",
                "needs the Sideloader Modpack installed to have these mods.",
                "",
            ]
            for m in sorted(total_modpack_missing):
                lines.append(f"  ! {m}")

        if not include_modpack:
            lines += [
                "",
                "NOTE: Mods from the Sideloader Modpack are NOT included in this",
                "archive. The recipient must have the Sideloader Modpack installed.",
                "Download it from: https://dl.betterrepack.com/",
            ]

        return "\n".join(lines)

    def run(self) -> None:
        content_paths = [Path(p) for p in self.content_paths if p]
        if not content_paths:
            logger.error("ARCHV", "No character cards, coordinates, or scenes specified")
            raise Exception("ArchiveCards: no content paths specified")

        game_base  = Path(self.config.config_data["Core"]["GamePath"])
        mods_ov    = Path(self.mods_dir_str)   if self.mods_dir_str   else None
        coord_ov   = Path(self.coord_dir_str)  if self.coord_dir_str  else None
        output_dir = Path(self.output_dir_str) if self.output_dir_str else None
        ext        = ".7z" if self.format == "7z" else ".zip"
        generated  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.log_start("ARCHV")

        if self.combined_archive:
            all_files:  list[Path] = []
            seen:       set[Path]  = set()
            card_infos: list[dict] = []

            for content_path in content_paths:
                if not content_path.is_file():
                    logger.error("ARCHV", f"Not found: {content_path}")
                    continue
                coord_paths, zipmod_paths, missing, modpack_missing, all_guids = self._process_one(
                    content_path, game_base, mods_ov, coord_ov)
                for f in [content_path] + coord_paths + zipmod_paths:
                    if f not in seen:
                        seen.add(f)
                        all_files.append(f)
                card_infos.append({
                    "path":            content_path,
                    "display_name":    self._display_name(content_path),
                    "coords":          coord_paths,
                    "mods":            zipmod_paths,
                    "missing":         missing,
                    "modpack_missing": modpack_missing,
                    "all_guids":       all_guids,
                })

            if not all_files:
                logger.error("ARCHV", "No files to archive")
                raise Exception("ArchiveCards: no files to archive")

            out_dir = output_dir or content_paths[0].parent
            out_dir.mkdir(parents=True, exist_ok=True)
            archive_name = (f"{content_paths[0].stem}_bundle{ext}"
                            if len(content_paths) == 1
                            else f"bundle__{datetime.now().strftime('%Y%m%d%H%M%S%f')}{ext}")
            archive_path = out_dir / archive_name

            # Write README to a temp file and include it in the archive
            readme_text = self._build_readme(
                card_infos, archive_name, self.include_modpack, generated)
            # A README.txt entry named for what it actually is once inside the
            # archive, written to a fresh temp *directory* per call rather
            # than a fixed shared filename in the system temp dir — the
            # previous shared path meant two ArchiveCards runs happening at
            # the same time (two MXU instances, or a scheduled run
            # overlapping a manual one) could read/write/delete each
            # other's README mid-run.
            import tempfile as _tf
            readme_dir = Path(_tf.mkdtemp(prefix="kkafio_archive_"))
            readme_tmp = readme_dir / "README.txt"
            readme_tmp.write_text(readme_text, encoding="utf-8")

            logger.line()
            logger.info("ARCHV",
                f"Creating combined {self.format} archive: {archive_name} "
                f"({len(all_files)} file(s) + {readme_tmp.name})")
            archive_failed = False
            try:
                self.file_manager.create_archive(
                    all_files + [readme_tmp], archive_path, self.format)
                logger.success("ARCHV", f"Done: {archive_path}")
            except Exception as e:
                logger.error("ARCHV", f"Archive creation failed: {e}")
                archive_failed = True
            finally:
                readme_tmp.unlink(missing_ok=True)

            # Re-raise so a pipeline running DeleteCards right after this
            # step doesn't proceed to delete cards whose archive was never
            # actually created (Ctrl+C aside, this makes cmd_run's
            # try/except in kkafio_cli.py abort the remaining tasks).
            if archive_failed:
                raise Exception("ArchiveCards: archive creation failed")

        else:
            for content_path in content_paths:
                if not content_path.is_file():
                    logger.error("ARCHV", f"Not found: {content_path}")
                    continue
                logger.line()
                coord_paths, zipmod_paths, missing, modpack_missing, all_guids = self._process_one(
                    content_path, game_base, mods_ov, coord_ov)
                all_files    = [content_path] + coord_paths + zipmod_paths
                out_dir      = output_dir or content_path.parent
                out_dir.mkdir(parents=True, exist_ok=True)
                archive_name = f"{content_path.stem}_bundle{ext}"
                archive_path = out_dir / archive_name

                card_info = {
                    "path":            content_path,
                    "display_name":    self._display_name(content_path),
                    "coords":          coord_paths,
                    "mods":            zipmod_paths,
                    "missing":         missing,
                    "modpack_missing": modpack_missing,
                    "all_guids":       all_guids,
                }
                readme_text = self._build_readme(
                    [card_info], archive_name, self.include_modpack, generated)
                import tempfile as _tf
                readme_dir = Path(_tf.mkdtemp(prefix="kkafio_archive_"))
                readme_tmp = readme_dir / "README.txt"
                readme_tmp.write_text(readme_text, encoding="utf-8")

                logger.info("ARCHV",
                    f"  Creating {self.format} archive: {archive_name} "
                    f"({len(all_files)} file(s) + {readme_tmp.name})")
                archive_failed = False
                try:
                    self.file_manager.create_archive(
                        all_files + [readme_tmp], archive_path, self.format)
                    logger.success("ARCHV", f"  Done: {archive_path}")
                except Exception as e:
                    logger.error("ARCHV", f"  Archive creation failed: {e}")
                    archive_failed = True
                finally:
                    shutil.rmtree(readme_dir, ignore_errors=True)

                if archive_failed:
                    # Stop instead of continuing to the next card / to a
                    # later DeleteCards step as if every archive had
                    # succeeded.
                    raise Exception(
                        f"ArchiveCards: archive creation failed for {content_path.name}")