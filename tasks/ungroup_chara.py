"""
ungroup_chara.py — Move character cards from subfolders back to the top level.

Scans all subdirectories of the input folder for .png and .zipmod files and
moves them up to the top-level folder.  Optionally deletes empty folders after
moving.
"""


import shutil
from pathlib import Path

from utils.logger import logger


class UngroupChara:
    def __init__(self, config, file_manager):
        self.config       = config
        self.file_manager = file_manager
        cfg = self.config.ungroup_chara
        self.delete_empty: bool = cfg.get("DeleteEmptyFolders", True)

    def run(self, folder_path: Path | None = None) -> None:
        if folder_path is None:
            folder_path = Path(self.config.ungroup_chara["InputPath"])
        folder_path = Path(folder_path)

        logger.line()
        logger.info("UNGRP", f"Input folder    : {folder_path}")
        logger.info("UNGRP", f"Delete empty    : {self.delete_empty}")

        moved   = 0
        skipped = 0
        extensions = {".png", ".zipmod"}

        # Collect files from all subdirectories (not the top level itself)
        files_to_move: list[Path] = []
        for p in folder_path.rglob("*"):
            if p.is_file() and p.suffix.lower() in extensions and p.parent != folder_path:
                files_to_move.append(p)

        if not files_to_move:
            logger.success("UNGRP", "No files found in subfolders")
            return

        logger.info("UNGRP", f"Found {len(files_to_move)} file(s) in subfolders")

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
