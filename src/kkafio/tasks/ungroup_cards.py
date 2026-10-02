"""
ungroup_cards.py — Move cards from subfolders back to the top level.

Scans all subdirectories of the input folder for character cards, Studio scenes
and coordinate cards (whichever types are selected) and moves them up to the
top-level folder.  Optionally deletes empty folders after moving.
"""


import shutil
from pathlib import Path

from kkafio.cards.classifier import CardType, get_card_type, is_coordinate
from kkafio.core.errors import InputError
from kkafio.core.logger import logger
from kkafio.tasks.base_task import resolve_chara_dirs, validate_input_path


def _card_kind(path: Path) -> str | None:
    """"chara", "scene" or "coordinate" for a card PNG; None for anything else
    (overlays, other images, unreadable files)."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        logger.error("UNGRP", f"Could not read {path.name}: {e}")
        return None
    match get_card_type(raw):
        case CardType.KK | CardType.KKSP | CardType.KKS:
            return "chara"
        case CardType.SCENE:
            return "scene"
        case CardType.UNKNOWN:
            return "coordinate" if is_coordinate(raw) else None
        case _:
            return None


class UngroupCards:
    def __init__(self, config, file_manager):
        self.config       = config
        self.file_manager = file_manager
        cfg = self.config.ungroup_cards
        self.delete_empty: bool = cfg.get("DeleteEmptyFolders", True)
        self.do_chara    : bool = cfg.get("Chara",  True)
        self.do_scenes   : bool = cfg.get("Scenes", True)
        self.do_coords   : bool = cfg.get("Coords", True)

    def run(self, folder_path: Path | None = None) -> None:
        if folder_path is None:
            folder_path = Path(self.config.ungroup_cards["InputPath"])
        folder_path = Path(folder_path)

        validate_input_path("UNGRP", folder_path)

        wanted = {kind for kind, on in (("chara", self.do_chara),
                                        ("scene", self.do_scenes),
                                        ("coordinate", self.do_coords)) if on}
        if not wanted:
            raise InputError("No card types selected.", tag="UNGRP")

        # Guard: the game only reads chara cards inside UserData/chara/female
        # and /male. Ungrouping the game's chara folder itself would lift
        # cards out of those two folders into UserData/chara, where the game
        # can't see them — so work inside female/ and male/ instead.
        folders = resolve_chara_dirs(self.config.game_path, str(folder_path), "UNGRP")
        if folders != [folder_path]:
            logger.info("UNGRP",
                f"{folder_path} is the game's chara folder — ungrouping inside "
                "female/ and male/ so cards stay where the game reads them")

        for folder in folders:
            self._ungroup_folder(folder, wanted)

    def _ungroup_folder(self, folder_path: Path, wanted: set[str]) -> None:
        logger.line()
        logger.info("UNGRP", f"Input folder    : {folder_path}")
        logger.info("UNGRP", f"Delete empty    : {self.delete_empty}")
        logger.info("UNGRP", f"Card types      : {', '.join(sorted(wanted))}")

        moved   = 0
        skipped = 0

        # Collect cards of the selected types from all subdirectories
        # (not the top level itself)
        files_to_move: list[Path] = []
        for p in folder_path.rglob("*"):
            if p.is_file() and p.suffix.lower() == ".png" and p.parent != folder_path:
                if _card_kind(p) in wanted:
                    files_to_move.append(p)

        if not files_to_move:
            logger.success("UNGRP", "No cards found in subfolders")
            return

        logger.info("UNGRP", f"Found {len(files_to_move)} card(s) in subfolders")

        for src in files_to_move:
            dest = folder_path / src.name

            # Handle filename collision
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                counter = 1
                while dest.exists():
                    dest = folder_path / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                shutil.move(str(src), str(dest))
                logger.success("UNGRP", f"Moved {src.parent.name}/{src.name} -> {dest.name}")
                moved += 1
            except Exception as e:
                logger.error("UNGRP", f"Could not move {src.name}: {e}")
                skipped += 1

        # Delete empty subdirectories bottom-up
        if self.delete_empty:
            deleted_dirs = 0
            # Walk bottom-up so nested empty dirs are removed before parents
            for dirpath in sorted(folder_path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if dirpath == folder_path or not dirpath.is_dir():
                    continue
                try:
                    dirpath.rmdir()  # only succeeds if empty
                    logger.info("UNGRP", f"Removed empty folder: {dirpath.relative_to(folder_path)}")
                    deleted_dirs += 1
                except OSError:
                    pass  # not empty — leave it

            if deleted_dirs:
                logger.info("UNGRP", f"Removed {deleted_dirs} empty folder(s)")

        logger.line()
        logger.success("UNGRP", f"Done — moved: {moved}, skipped: {skipped}")
