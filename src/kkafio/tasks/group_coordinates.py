"""
group_coordinates.py — Sort coordinate cards into per-character subfolders.

Every chara card in the chara folder(s) is scanned (recursively, so cards that
are already grouped into series folders are included) and its outfits are
reduced to digests — the same matching Archive Cards / Delete Cards use for
"Include Coordinates". Every coordinate card in the coordinate folder is
digested the same way. A coordinate that is a copy of one of a character's
outfits is moved to <coordinate folder>/<chara filename>/.

A coordinate that matches several characters goes to the first one (sorted by
filename), unless it already sits in the folder of one of them, in which case
it stays put.

The chara folder(s) are always scanned recursively. With Include Subfolders off
(the default) only coordinates directly inside the coordinate folder are
considered; coordinate subfolders are left alone.
"""

import json
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from kkafio.cards.cache_io import atomic_write_json, file_fp
from kkafio.cards.classifier import CHARA_CARD_TYPES, get_card_type
from kkafio.cards.outfits import COORD_CACHE_FILE, COORD_CACHE_VERSION, build_coord_cache, chara_outfit_digests
from kkafio.core.errors import InputError
from kkafio.core.logger import logger
from kkafio.tasks.base_task import BaseTask, resolve_chara_dirs

TAG = "GRPCOORD"

CHARA_OUTFIT_CACHE_FILE = "kkafio_chara_outfit_cache.json"
CHARA_OUTFIT_CACHE_VERSION = 1

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def folder_name_for(chara_path: Path) -> str:
    """A safe single-folder name from a chara card's filename ('' if nothing usable is left)."""
    name = _INVALID_CHARS.sub("_", chara_path.stem).strip().rstrip(". ")
    if not name or set(name) <= {"."}:
        return ""
    if name.upper().split(".")[0] in _RESERVED:
        name = f"_{name}"
    return name[:100].rstrip(". ")


def _load_outfit_cache(coord_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads((coord_dir / CHARA_OUTFIT_CACHE_FILE).read_text(encoding="utf-8"))
        if data.get("version") == CHARA_OUTFIT_CACHE_VERSION:
            files = data.get("files", {})
            return files if isinstance(files, dict) else {}
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug("CACHE", f"Ignoring unreadable {coord_dir / CHARA_OUTFIT_CACHE_FILE}: {e}")
    return {}


def collect_chara_digests(chara_dirs: list[Path], coord_dir: Path,
                          use_cache: bool) -> dict[Path, set[str]]:
    """{chara PNG: set of its outfit digests} for every chara card under chara_dirs."""
    pngs: list[Path] = []
    for folder in chara_dirs:
        pngs.extend(folder.rglob("*.png"))
    pngs = sorted(set(pngs))
    logger.info(TAG, f"Scanning {len(pngs)} PNG file(s) for character cards")

    cache = _load_outfit_cache(coord_dir) if use_cache else {}
    new_cache: dict[str, dict] = {}
    result: dict[Path, set[str]] = {}
    to_read: list[Path] = []

    for png in pngs:
        sp, fp = str(png), file_fp(png)
        entry = cache.get(sp)
        if entry is not None and entry.get("fp") == fp and "digests" in entry:
            new_cache[sp] = entry
            if entry["digests"]:
                result[png] = set(entry["digests"])
        else:
            to_read.append(png)

    def _read(png: Path) -> tuple[Path, list[int], list[str]]:
        fp = file_fp(png)
        if get_card_type(png.read_bytes()) not in CHARA_CARD_TYPES:
            return png, fp, []
        from kkloader import KoikatuCharaData
        return png, fp, sorted(chara_outfit_digests(KoikatuCharaData.load(str(png))))

    if to_read:
        workers = min(32, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_read, p): p for p in to_read}
            for future in as_completed(futures):
                try:
                    png, fp, digests = future.result()
                except Exception as e:
                    logger.warning(TAG, f"Could not read {futures[future].name}: {e}")
                    continue
                new_cache[str(png)] = {"fp": fp, "digests": digests}
                if digests:
                    result[png] = set(digests)

    if use_cache:
        logger.info(TAG, f"Chara cache: {len(pngs) - len(to_read)} reused, {len(to_read)} read")
        try:
            atomic_write_json(coord_dir / CHARA_OUTFIT_CACHE_FILE,
                              {"version": CHARA_OUTFIT_CACHE_VERSION, "files": new_cache})
        except Exception as e:
            logger.warning(TAG, f"Could not save {coord_dir / CHARA_OUTFIT_CACHE_FILE}: {e}")
    return result


def _update_coord_cache(coord_dir: Path, moves: dict[str, str]) -> None:
    """Make the coordinate cache follow moved files (a move keeps mtime and size),
    so the next run doesn't re-parse them."""
    cache_path = coord_dir / COORD_CACHE_FILE
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("version") != COORD_CACHE_VERSION or data.get("coord_dir") != str(coord_dir):
            return
        for section in ("files", "coords"):
            entries = data.get(section, {})
            for old, new in moves.items():
                if old in entries:
                    entries[new] = entries.pop(old)
        atomic_write_json(cache_path, data)
    except Exception as e:
        logger.debug("CACHE", f"Could not update {cache_path}: {e}")


class GroupCoordinates(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.group_coordinates
        self.use_cache      : bool = cfg.get("UseCache", True)
        self.chara_dir_str  : str  = cfg.get("CharaDir", "")
        self.coord_dir_str  : str  = cfg.get("CoordDir", "")
        self.include_subfolders: bool = cfg.get("IncludeSubfolders", False)

    def _coord_dir(self) -> Path:
        if self.coord_dir_str.strip():
            folder = Path(self.coord_dir_str)
        else:
            folder = self.config.game_path.get("coordinate")
            if folder is None:
                raise InputError("Coordinate folder not set and not resolvable from game path.", tag=TAG)
            folder = Path(folder)
        if not folder.exists():
            raise InputError(f"Coordinate directory does not exist: {folder}", tag=TAG)
        return folder

    def run(self) -> None:
        chara_dirs = resolve_chara_dirs(self.config.game_path, self.chara_dir_str, TAG)
        coord_dir = self._coord_dir()

        self.log_start(TAG, f"{coord_dir}  (characters: {', '.join(str(d) for d in chara_dirs)})")
        logger.info(TAG, f"Use cache: {self.use_cache}")
        logger.info(TAG, f"Include coordinate subfolders: {self.include_subfolders}")
        logger.line()

        chara_digests = collect_chara_digests(chara_dirs, coord_dir, self.use_cache)
        if not chara_digests:
            logger.warning(TAG, "No character cards found — nothing to do")
            return

        # digest -> chara folder names that own it (sorted by chara filename for a stable pick)
        owners: dict[str, list[str]] = {}
        for chara in sorted(chara_digests, key=lambda p: (p.name.casefold(), str(p))):
            name = folder_name_for(chara)
            if not name:
                logger.warning(TAG, f"{chara.name}: unusable filename for a folder, skipped")
                continue
            for digest in chara_digests[chara]:
                lst = owners.setdefault(digest, [])
                if name not in lst:
                    lst.append(name)

        coord_map = build_coord_cache(coord_dir, use_cache=self.use_cache)
        coords = sorted(Path(sp) for sp, d in coord_map.items()
                        if d and (self.include_subfolders or Path(sp).parent == coord_dir))
        logger.info(TAG, f"Coordinate cards found: {len(coords)}")
        logger.line()

        moved = in_place = unmatched = failed = 0
        moves: dict[str, str] = {}
        try:
            for coord in coords:
                names: list[str] = []
                for name in owners.get(coord_map[str(coord)], []):
                    if name not in names:
                        names.append(name)
                if not names:
                    unmatched += 1
                    continue
                if coord.parent.parent == coord_dir and coord.parent.name in names:
                    in_place += 1                     # already in one of its characters' folders
                    continue
                if coord.parent == coord_dir / names[0]:
                    in_place += 1
                    continue
                if len(names) > 1:
                    logger.info(TAG, f"{coord.name} matches {len(names)} characters "
                                     f"({', '.join(names)}) — using {names[0]}")

                dest_dir = coord_dir / names[0]
                dest = dest_dir / coord.name
                if dest.exists():
                    counter = 1
                    while dest.exists():
                        dest = dest_dir / f"{coord.stem}_{counter}{coord.suffix}"
                        counter += 1
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(coord), str(dest))
                except Exception as e:
                    logger.error(TAG, f"Could not move {coord.name}: {e}")
                    failed += 1
                    continue
                logger.success(TAG, f"Moved {coord.name} -> {names[0]}/")
                moves[str(coord)] = str(dest)
                moved += 1
        finally:
            if self.use_cache and moves:
                _update_coord_cache(coord_dir, moves)

        logger.line()
        logger.success(TAG, f"Done — moved: {moved}, already in place: {in_place}, "
                            f"no matching character: {unmatched}"
                            + (f", failed: {failed}" if failed else ""))
