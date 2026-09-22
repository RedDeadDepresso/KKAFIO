"""
export_mods.py — Find zipmods by GUID and copy them into an output folder.

Typical use: paste the "Unresolvable mods" (or any other) section straight
out of a Download Missing Mods report — lines like "  ! GaryuX.Chloe" — into
the GUIDs field, and this task will locate each one (searching both the
regular mods folder and any Sideloader Modpack subfolder) and copy it out,
optionally renamed to [guid].zipmod so it's immediately obvious which file
is which.
"""

import filecmp
import re
import shutil
from pathlib import Path

from tasks.base_task import BaseTask
from utils.chara_ops import build_mods_cache
from utils.logger import logger

# Matches a bulleted report line's leading marker, e.g. "  ! ", "  ✗ ", "  + ",
# "  ~ " — the exact prefixes Download Missing Mods' report uses, plus a few
# generic list bullets in case someone hand-writes a list.
_BULLET_RE = re.compile(r"^\s*[!✗✓+~\-\*>•]\s*")

# A single simple GUID-shaped token: letters/digits/underscore/dot/hyphen,
# no spaces. Used only for BARE (no-bullet) lines, as a heuristic to tell a
# hand-typed comma/semicolon list of simple GUIDs apart from pasted prose
# (a report's description lines, which always contain ordinary spaces).
_GUID_SHAPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def parse_guids(raw: str) -> list[str]:
    """Parse the GUIDs textbox into an ordered, de-duplicated list.

    Accepts one GUID per line — optionally prefixed with a report bullet
    ('!', '✗', '+', '~', '-', '*') and/or followed by a "(...)" note like
    Download Missing Mods' report uses — comma/semicolon-separated GUIDs on
    a bare (non-bulleted) line, blank lines, and '#'-comment lines, so a
    report section can be pasted in directly.

    A *bulleted* line is trusted completely: everything after the bullet
    (and the trailing parenthetical note, if any) is taken as a single
    GUID verbatim, whatever characters it contains. This matters because
    real GUIDs are very often not simple identifiers — around 13% of the
    Sideloader Modpack's own GUIDs contain spaces ("3DPubicHairs by
    CM12"), start with a character other than a letter/digit
    (".com top_matoi"), or even contain a literal comma
    ("MiIlefiore Hime Clothes1-top-b, SIHUKU1TOP" is one single GUID, not
    two). A report only ever emits exactly one GUID per bulleted line, so
    there's no ambiguity to resolve for that case — treating the bullet as
    permission to take the rest of the line as-is is both safe and
    necessary; running such a line through the strict shape check below
    silently dropped every one of these GUIDs even though the user had
    unambiguously marked them as single items.

    A *bare* line with no recognized bullet is a different, inherently
    ambiguous case (is a comma separating two GUIDs, or part of one GUID's
    name? is this whole line a GUID, or a plain-English description from a
    report header?), so it keeps the older, more conservative behavior: it
    is only accepted if it consists *entirely* of simple GUID-shaped
    token(s) with no spaces — this is what keeps a report's plain
    description lines ("These mods could not be downloaded...") from being
    misread as GUIDs. A bare line for a GUID containing a space or a
    leading symbol needs a bullet (e.g. "- 3DPubicHairs by CM12") to be
    recognized; this is exactly what pasting a report section already
    gives you for free.
    """
    seen: set[str] = set()
    guids: list[str] = []

    for raw_line in raw.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        has_bullet = bool(_BULLET_RE.match(line))
        line = _BULLET_RE.sub("", line, count=1).strip()
        if not line:
            continue

        # Drop a trailing parenthetical note, e.g. "guid (Sideloader Modpack/x.zipmod)"
        # — the space before "(" is required and deliberate: it's exactly
        # what separates a report-appended note (the report always writes
        # "{guid} ({path})", with a space) from a GUID that has its own
        # parenthetical baked into the name with no preceding space, e.g.
        # "MiIlefiore Hime Dress(Ep.5)" or "Kurisus Jacket(Glove)" — both
        # real Sideloader Modpack GUIDs. Splitting on bare "\(" regardless
        # of a preceding space, as this used to, truncated those down to
        # "MiIlefiore Hime Dress" and "Kurisus Jacket", silently mangling
        # them into the wrong (and likely nonexistent) GUID.
        line = re.split(r"\s+\(", line, maxsplit=1)[0].strip()
        if not line:
            continue

        if has_bullet:
            # One bullet, one GUID — take the whole remainder verbatim,
            # commas/spaces/leading symbols and all.
            if line not in seen:
                seen.add(line)
                guids.append(line)
            continue

        parts = [p.strip() for p in re.split(r"[,;]", line) if p.strip()]
        if not parts:
            continue

        if any(not _GUID_SHAPE_RE.match(p) for p in parts):
            continue  # looks like prose, not a GUID (or list of GUIDs) — skip

        for guid in parts:
            if guid not in seen:
                seen.add(guid)
                guids.append(guid)

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
        # Sideloader Modpack, so mods living there are fair game too (the
        # "~ guid (Sideloader Modpack/...)" report lines point there).
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
