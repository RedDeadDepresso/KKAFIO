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

By default a coordinate must be an exact copy of an outfit. With an Accessory
Tolerance of N > 0 it may instead be the same clothes with up to N accessories
added, removed or swapped (accessories are compared regardless of their slot).
The clothes must still match exactly. When several characters match, the one
with the fewest differing accessories wins, then the first by filename.

The chara folder(s) are always scanned recursively. With Include Subfolders off
(the default) only coordinates directly inside the coordinate folder are
considered; coordinate subfolders are left alone.
"""

import json
import os
import re
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from kkafio.cards.cache_io import atomic_write_json, file_fp
from kkafio.cards.classifier import CHARA_CARD_TYPES, get_card_type
from kkafio.cards.outfits import (
    COORD_CACHE_FILE, COORD_CACHE_VERSION, SIG_CACHE_FILE, SIG_CACHE_VERSION, OutfitSignature,
    accessory_difference, build_coord_cache, build_coord_sig_cache, chara_outfit_digests,
    chara_outfit_signatures,
)
from kkafio.core.errors import InputError
from kkafio.core.logger import logger
from kkafio.tasks.base_task import BaseTask, resolve_chara_dirs

TAG = "GRPCOORD"

CHARA_OUTFIT_CACHE_FILE = "kkafio_chara_outfit_cache.json"
CHARA_OUTFIT_CACHE_VERSION = 3

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


def _scan_charas(chara_dirs: list[Path], coord_dir: Path, use_cache: bool,
                 need_sigs: bool) -> dict[Path, dict]:
    """{chara PNG: {"digests": [...], "sigs": [[clothes, [accessories]], ...]}} for every
    chara card under chara_dirs. Both are always computed for cards that get read, so
    the cache can serve either mode; entries cached without "sigs" are only re-read
    when `need_sigs`."""
    pngs: list[Path] = []
    for folder in chara_dirs:
        pngs.extend(folder.rglob("*.png"))
    pngs = sorted(set(pngs))
    logger.info(TAG, f"Scanning {len(pngs)} PNG file(s) for character cards")

    cache = _load_outfit_cache(coord_dir) if use_cache else {}
    new_cache: dict[str, dict] = {}
    result: dict[Path, dict] = {}
    to_read: list[Path] = []

    for png in pngs:
        sp, fp = str(png), file_fp(png)
        entry = cache.get(sp)
        if (entry is not None and entry.get("fp") == fp and "digests" in entry
                and (not need_sigs or "sigs" in entry)):
            new_cache[sp] = entry
            if entry["digests"]:
                result[png] = entry
        else:
            to_read.append(png)

    def _read(png: Path) -> tuple[Path, list[int], dict]:
        fp = file_fp(png)
        if get_card_type(png.read_bytes()) not in CHARA_CARD_TYPES:
            return png, fp, {"digests": [], "sigs": []}
        from kkloader import KoikatuCharaData
        kc = KoikatuCharaData.load(str(png))
        return png, fp, {"digests": sorted(chara_outfit_digests(kc)),
                         "sigs": [[clothes, list(accs)] for clothes, accs in chara_outfit_signatures(kc)]}

    if to_read:
        workers = min(32, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_read, p): p for p in to_read}
            for future in as_completed(futures):
                try:
                    png, fp, data = future.result()
                except Exception as e:
                    logger.warning(TAG, f"Could not read {futures[future].name}: {e}")
                    continue
                new_cache[str(png)] = {"fp": fp, **data}
                if data["digests"]:
                    result[png] = data

    if use_cache:
        logger.info(TAG, f"Chara cache: {len(pngs) - len(to_read)} reused, {len(to_read)} read")
        try:
            atomic_write_json(coord_dir / CHARA_OUTFIT_CACHE_FILE,
                              {"version": CHARA_OUTFIT_CACHE_VERSION, "files": new_cache})
        except Exception as e:
            logger.warning(TAG, f"Could not save {coord_dir / CHARA_OUTFIT_CACHE_FILE}: {e}")
    return result


def collect_chara_digests(chara_dirs: list[Path], coord_dir: Path,
                          use_cache: bool) -> dict[Path, set[str]]:
    """{chara PNG: set of its outfit digests} for every chara card under chara_dirs."""
    scanned = _scan_charas(chara_dirs, coord_dir, use_cache, need_sigs=False)
    return {png: set(e["digests"]) for png, e in scanned.items()}


def collect_chara_signatures(chara_dirs: list[Path], coord_dir: Path,
                             use_cache: bool) -> dict[Path, list[OutfitSignature]]:
    """{chara PNG: its outfit signatures} for every chara card under chara_dirs."""
    scanned = _scan_charas(chara_dirs, coord_dir, use_cache, need_sigs=True)
    return {png: [(clothes, tuple(accs)) for clothes, accs in e["sigs"]] for png, e in scanned.items()}


def _sorted_charas(charas) -> list[tuple[Path, str]]:
    """[(chara PNG, folder name)] in a stable order (by filename); unusable names are skipped."""
    out = []
    for chara in sorted(charas, key=lambda p: (p.name.casefold(), str(p))):
        name = folder_name_for(chara)
        if not name:
            logger.warning(TAG, f"{chara.name}: unusable filename for a folder, skipped")
            continue
        out.append((chara, name))
    return out


def exact_matcher(chara_digests: dict[Path, set[str]]):
    """matches(digest) -> [(folder name, 0)] for coordinates that are an exact copy of an outfit."""
    # digest -> chara folder names that own it (sorted by chara filename for a stable pick)
    owners: dict[str, list[str]] = {}
    for chara, name in _sorted_charas(chara_digests):
        for digest in chara_digests[chara]:
            lst = owners.setdefault(digest, [])
            if name not in lst:
                lst.append(name)
    return lambda digest: [(name, 0) for name in owners.get(digest, [])]


def fuzzy_matcher(chara_sigs: dict[Path, list[OutfitSignature]], tolerance: int):
    """matches(signature) -> [(folder name, accessory difference)], best first, for
    coordinates with the same clothes as an outfit and at most `tolerance` accessories
    different. Ties go to the character that sorts first by filename."""
    # clothes digest -> [(folder name, accessory digests)]
    by_clothes: dict[str, list[tuple[str, tuple[str, ...]]]] = defaultdict(list)
    rank: dict[str, int] = {}
    for chara, name in _sorted_charas(chara_sigs):
        rank.setdefault(name, len(rank))
        for clothes, accessories in chara_sigs[chara]:
            by_clothes[clothes].append((name, accessories))

    def matches(sig: OutfitSignature) -> list[tuple[str, int]]:
        best: dict[str, int] = {}
        for name, accessories in by_clothes.get(sig[0], ()):
            diff = accessory_difference(sig[1], accessories)
            if diff <= tolerance and diff < best.get(name, tolerance + 1):
                best[name] = diff
        return sorted(best.items(), key=lambda item: (item[1], rank[item[0]]))

    return matches


def _update_coord_cache(coord_dir: Path, moves: dict[str, str]) -> None:
    """Make the coordinate caches follow moved files (a move keeps mtime and size),
    so the next run doesn't re-parse them."""
    for file_name, version, sections in (
            (COORD_CACHE_FILE, COORD_CACHE_VERSION, ("files", "coords")),
            (SIG_CACHE_FILE, SIG_CACHE_VERSION, ("files", "sigs"))):
        cache_path = coord_dir / file_name
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if data.get("version") != version or data.get("coord_dir") != str(coord_dir):
                continue
            for section in sections:
                entries = data.get(section, {})
                for old, new in moves.items():
                    if old in entries:
                        entries[new] = entries.pop(old)
            atomic_write_json(cache_path, data)
        except FileNotFoundError:
            continue
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
        self.accessory_tolerance_raw = cfg.get("AccessoryTolerance", 0)

    def _accessory_tolerance(self) -> int:
        raw = self.accessory_tolerance_raw
        if raw is None:
            return 0
        try:
            value = int(str(raw).strip() or 0)
        except (TypeError, ValueError):
            raise InputError(f"Accessory tolerance must be a whole number, got {raw!r}.", tag=TAG) from None
        if value < 0:
            raise InputError(f"Accessory tolerance can't be negative, got {value}.", tag=TAG)
        return value

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
        tolerance = self._accessory_tolerance()

        self.log_start(TAG, f"{coord_dir}  (characters: {', '.join(str(d) for d in chara_dirs)})")
        logger.info(TAG, f"Use cache: {self.use_cache}")
        logger.info(TAG, f"Include coordinate subfolders: {self.include_subfolders}")
        logger.info(TAG, "Accessory tolerance: "
                         + (f"{tolerance} (same clothes, up to {tolerance} accessory difference(s))"
                            if tolerance else "0 (exact copies only)"))
        logger.line()

        # matches(key) -> [(chara folder name, accessory difference)], best first;
        # coord_keys maps each coordinate card's path to its key (falsy: not a coordinate card).
        if tolerance:
            chara_sigs = collect_chara_signatures(chara_dirs, coord_dir, self.use_cache)
            if not chara_sigs:
                logger.warning(TAG, "No character cards found — nothing to do")
                return
            matches = fuzzy_matcher(chara_sigs, tolerance)
            coord_keys = build_coord_sig_cache(coord_dir, use_cache=self.use_cache)
        else:
            chara_digests = collect_chara_digests(chara_dirs, coord_dir, self.use_cache)
            if not chara_digests:
                logger.warning(TAG, "No character cards found — nothing to do")
                return
            matches = exact_matcher(chara_digests)
            coord_keys = build_coord_cache(coord_dir, use_cache=self.use_cache)

        coords = sorted(Path(sp) for sp, key in coord_keys.items()
                        if key and (self.include_subfolders or Path(sp).parent == coord_dir))
        logger.info(TAG, f"Coordinate cards found: {len(coords)}")
        logger.line()

        moved = in_place = unmatched = failed = 0
        moves: dict[str, str] = {}
        try:
            for coord in coords:
                found = matches(coord_keys[str(coord)])
                names: list[str] = []
                for name, _ in found:
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
                diff = found[0][1]
                note = f" ({diff} accessor{'y' if diff == 1 else 'ies'} differ{'s' if diff == 1 else ''})" if diff else ""
                logger.success(TAG, f"Moved {coord.name} -> {names[0]}/{note}")
                moves[str(coord)] = str(dest)
                moved += 1
        finally:
            if self.use_cache and moves:
                _update_coord_cache(coord_dir, moves)

        logger.line()
        logger.success(TAG, f"Done — moved: {moved}, already in place: {in_place}, "
                            f"no matching character: {unmatched}"
                            + (f", failed: {failed}" if failed else ""))
