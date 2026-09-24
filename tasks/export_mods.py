"""
export_mods.py — Find zipmods by GUID and copy them into an output folder.

Typical use: paste the "Unresolvable mods" (or any other) section straight
out of a Download Missing Mods report — lines like "  ! GaryuX.Chloe" — into
the GUIDs field (the bullet is removed), and this task will locate each one (searching both the
regular mods folder and any Sideloader Modpack subfolder) and copy it out,
optionally renamed to [guid].zipmod so it's immediately obvious which file
is which.
"""

import filecmp
import shutil
from pathlib import Path

from tasks.base_task import BaseTask
from utils.chara_ops import build_mods_cache
from utils.logger import logger

# Leading markers Download Missing Mods' report puts in front of each GUID.
_BULLETS = {"!", "✗", "+", "~"}


def parse_guids(raw: str) -> list[str]:
    """Parse the GUIDs textbox into an ordered, de-duplicated list.

    One GUID per line. Anything from a '#' onward is a comment and is
    dropped. Each line is then stripped, a leading report bullet ('!', '✗',
    '+' or '~') is removed along with the whitespace after it, and the rest
    of the line is used as the GUID verbatim. Blank lines are skipped.
    """
    seen: set[str] = set()
    guids: list[str] = []

    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and line[0] in _BULLETS:
            line = line[1:].lstrip()
        if line and line not in seen:
            seen.add(line)
            guids.append(line)

    return guids


class ExportMods(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.export_mods
        self.output_path_str : str  = cfg.get("OutputPath", "")
        self.guids_raw        : str = cfg.get("Guids", "")
        self.rename_to_guid   : bool = cfg.get("RenameToGuid", True)
        self.use_cache        : bool = cfg.get("UseCache", True)
        self.mods_dir_str     : str  = cfg.get("ModsDir", "")

    def run(self) -> None:
        guids = parse_guids(self.guids_raw)
        if not guids:
            logger.error("EXPORT", "No GUIDs to export — paste one or more GUIDs first.")
            return

        game_path = self.config.game_path
        if self.mods_dir_str:
            mods_dir = Path(self.mods_dir_str)
        elif "mods" in game_path:
            mods_dir = game_path["mods"]
        else:
            logger.error("EXPORT", "Mods directory not set and not resolvable from game path.")
            return

        if not mods_dir.exists():
            logger.error("EXPORT", f"Mods directory does not exist: {mods_dir}")
            return

        if not self.output_path_str:
            logger.error("EXPORT", "No output directory specified.")
            return
        output_dir = Path(self.output_path_str)

        logger.line()
        logger.info("EXPORT", f"Mods directory : {mods_dir}")
        logger.info("EXPORT", f"Output         : {output_dir}")
        logger.info("EXPORT", f"GUIDs requested: {len(guids)}")
        logger.info("EXPORT", f"Rename to GUID : {self.rename_to_guid}")
        logger.info("EXPORT", f"Use cache      : {self.use_cache}")
        logger.line()

        # include_modpack=True — unlike Archive/Delete Cards, exporting a
        # copy of a mod doesn't touch or remove anything from the
        # Sideloader Modpack, so mods living there are fair game too.
        guid_str_map = build_mods_cache(mods_dir, include_modpack=True, use_cache=self.use_cache)
        guid_map = {guid: Path(p) for guid, p in guid_str_map.items()}

        output_dir.mkdir(parents=True, exist_ok=True)

        exported = 0
        missing: list[str] = []
        written_paths: set[Path] = set()

        for guid in guids:
            src = guid_map.get(guid)
            if src is None or not src.exists():
                missing.append(guid)
                logger.warning("EXPORT", f"  Not found: {guid}")
                continue

            dest_name = f"{guid}.zipmod" if self.rename_to_guid else src.name
            dest = output_dir / dest_name

            if dest in written_paths:
                # Same destination already written this run (e.g. two
                # requested GUIDs happened to come from the same zipmod,
                # and renaming is off so they'd collide on the same name).
                logger.info("EXPORT", f"  {guid} -> {dest_name} (already exported)")
                exported += 1
                continue

            if dest.exists() and dest.resolve() != src.resolve():
                # With RenameToGuid on, dest is always the same
                # "<guid>.zipmod" path on every run, so re-running this
                # task on GUIDs it already exported would otherwise always
                # hit this branch and rename around its own prior output —
                # guid.zipmod, guid_1.zipmod, guid_2.zipmod, ... forever.
                # A dest that already holds the exact same file src would
                # copy isn't a real clash to rename around; only rename
                # when dest exists and is genuinely different content.
                if filecmp.cmp(dest, src, shallow=False):
                    logger.info("EXPORT", f"  {guid} -> {dest.name} (already exported, unchanged)")
                    written_paths.add(dest)
                    exported += 1
                    continue
                stem, suffix = dest.stem, dest.suffix
                counter = 1
                while dest.exists():
                    dest = output_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                shutil.copy2(src, dest)
                written_paths.add(dest)
                logger.success("EXPORT", f"  {guid} -> {dest.name}")
                exported += 1
            except Exception as e:
                missing.append(guid)
                logger.error("EXPORT", f"  Could not copy {src.name} for {guid}: {e}")

        logger.line()
        logger.info("EXPORT", f"Exported: {exported}  Missing: {len(missing)}")
        if missing:
            logger.info("EXPORT", "Missing GUIDs:")
            for guid in missing:
                logger.info("EXPORT", f"  ! {guid}")
        logger.line()
