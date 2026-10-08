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

A first pass matches coordinates to characters by hair: a coordinate whose hair (see
same_hair) is worn in an outfit of exactly one character moves to that character's folder.
Coordinates whose hair fits no character, or fits several, are left for the outfit matching
described below, which then handles everything the hair pass didn't.

By default a coordinate must be an exact copy of an outfit. With Ignore Accessories on, the
accessories are left out of the comparison, so a coordinate with the same clothes as one of a
character's outfits matches whatever accessories it has. With a Clothes Tolerance of M > 0 up to
M clothes parts (slots such as top, bottom, gloves, pantyhose...) may differ in item, colours or
patterns. When several characters match, the one with the fewest clothes differences wins, then
the first by filename.

With Group By Hair, coordinates that are still ungrouped afterwards (left directly
in the coordinate folder) are grouped by their hair (see same_hair), using every
coordinate card including those in subfolders: an ungrouped coordinate with the same hair as the
coordinates of one folder moves into that folder, and ungrouped coordinates that
share hair with each other but with no folder move together into a new
UNKNOWN_<n> folder. Hair that appears in several folders is ambiguous, so those
coordinates stay where they are.

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
    COORD_CACHE_FILE, COORD_CACHE_VERSION, HAIR_CACHE_FILE, HAIR_CACHE_VERSION, SIG_CACHE_FILE,
    SIG_CACHE_VERSION, OutfitSignature,
    accessory_difference, build_coord_cache, build_coord_hair_cache, build_coord_sig_cache,
    chara_outfit_digests, chara_outfit_hairs,
    chara_outfit_signatures, clothes_difference,
)
from kkafio.core.errors import InputError
from kkafio.core.logger import logger
from kkafio.tasks.base_task import BaseTask, resolve_chara_dirs

TAG = "GRPCOORD"

CHARA_OUTFIT_CACHE_FILE = "kkafio_chara_outfit_cache.json"
CHARA_OUTFIT_CACHE_VERSION = 4

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
                 need_sigs: bool, need_hairs: bool = False) -> dict[Path, dict]:
    """{chara PNG: {"digests": [...], "sigs": [[[clothes tokens], [accessories]], ...],
    "hairs": [[hair items], ...]}} for every chara card under chara_dirs. All three are always
    computed for cards that get read, so the cache can serve any mode; entries cached without
    "sigs" / "hairs" are only re-read when `need_sigs` / `need_hairs`."""
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
                and (not need_sigs or "sigs" in entry) and (not need_hairs or "hairs" in entry)):
            new_cache[sp] = entry
            if entry["digests"]:
                result[png] = entry
        else:
            to_read.append(png)

    def _read(png: Path) -> tuple[Path, list[int], dict]:
        fp = file_fp(png)
        if get_card_type(png.read_bytes()) not in CHARA_CARD_TYPES:
            return png, fp, {"digests": [], "sigs": [], "hairs": []}
        from kkloader import KoikatuCharaData
        kc = KoikatuCharaData.load(str(png))
        return png, fp, {"digests": sorted(chara_outfit_digests(kc)),
                         "sigs": [[list(clothes), list(accs)] for clothes, accs in chara_outfit_signatures(kc)],
                         "hairs": [list(hair) for hair in chara_outfit_hairs(kc)]}

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
    return {png: [(tuple(clothes), tuple(accs)) for clothes, accs in e["sigs"]] for png, e in scanned.items()}


def collect_chara_hairs(chara_dirs: list[Path], coord_dir: Path,
                        use_cache: bool) -> dict[Path, list[tuple[str, ...]]]:
    """{chara PNG: the hairs its outfits wear} for every chara card under chara_dirs."""
    scanned = _scan_charas(chara_dirs, coord_dir, use_cache, need_sigs=False, need_hairs=True)
    return {png: [tuple(hair) for hair in e.get("hairs", [])] for png, e in scanned.items()}


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
    """matches(digest) -> [(folder name, 0, 0)] for coordinates that are an exact copy of an outfit."""
    # digest -> chara folder names that own it (sorted by chara filename for a stable pick)
    owners: dict[str, list[str]] = {}
    for chara, name in _sorted_charas(chara_digests):
        for digest in chara_digests[chara]:
            lst = owners.setdefault(digest, [])
            if name not in lst:
                lst.append(name)
    return lambda digest: [(name, 0, 0) for name in owners.get(digest, [])]


def fuzzy_matcher(chara_sigs: dict[Path, list[OutfitSignature]], ignore_accessories: bool,
                  clothes_tolerance: int = 0):
    """matches(signature) -> [(folder name, clothes parts differing, accessories differing)],
    best first, for coordinates with at most `clothes_tolerance` clothes parts different from
    one of a character's outfits. Unless `ignore_accessories` is set the accessories must also
    be identical; when it is set they aren't compared (and are reported as 0 differing). Best
    is the fewest differences in total (then fewest clothes differences); ties go to the
    character that sorts first by filename."""
    outfits: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []      # (folder name, clothes, accessories)
    rank: dict[str, int] = {}
    for chara, name in _sorted_charas(chara_sigs):
        rank.setdefault(name, len(rank))
        for clothes, accessories in chara_sigs[chara]:
            outfits.append((name, clothes, accessories))

    # Candidate lookup, so a big library isn't compared outfit by outfit with every coordinate.
    by_clothes: dict[tuple[str, ...], list[int]] = defaultdict(list)       # exact clothes (tolerance 0)
    postings: dict[str, list[int]] = defaultdict(list)                     # clothes token -> outfits
    for n, (_, clothes, _) in enumerate(outfits):
        if clothes_tolerance:
            for token in clothes:
                postings[token].append(n)
        else:
            by_clothes[clothes].append(n)

    def candidates(clothes: tuple[str, ...]):
        if not clothes_tolerance:
            return by_clothes.get(clothes, ())
        # An outfit with at most k clothes parts different shares at least one of any k+1
        # of this coordinate's tokens, so the k+1 rarest tokens are enough to find them.
        rarest = sorted(set(clothes), key=lambda t: len(postings.get(t, ())))[:clothes_tolerance + 1]
        return {n for token in rarest for n in postings.get(token, ())}

    def matches(sig: OutfitSignature) -> list[tuple[str, int, int]]:
        best: dict[str, tuple[int, int]] = {}
        for n in candidates(sig[0]):
            name, clothes, accessories = outfits[n]
            clothes_diff = clothes_difference(sig[0], clothes)
            if clothes_diff > clothes_tolerance:
                continue
            accessory_diff = 0 if ignore_accessories else accessory_difference(sig[1], accessories)
            if accessory_diff:
                continue
            found = (clothes_diff + accessory_diff, clothes_diff)
            if name not in best or found < best[name]:
                best[name] = found
        ranked = sorted(best.items(), key=lambda item: (item[1], rank[item[0]]))
        return [(name, clothes_diff, total - clothes_diff) for name, (total, clothes_diff) in ranked]

    return matches


UNKNOWN_FOLDER_PREFIX = "UNKNOWN_"

_UNKNOWN_FOLDER_RE = re.compile(rf"^{UNKNOWN_FOLDER_PREFIX}(\d+)$", re.IGNORECASE)


def next_unknown_number(coord_dir: Path) -> int:
    """The number for the next UNKNOWN_<n> folder: one above the highest existing one."""
    highest = 0
    for entry in coord_dir.iterdir():
        match = _UNKNOWN_FOLDER_RE.match(entry.name)
        if match and entry.is_dir():
            highest = max(highest, int(match.group(1)))
    return highest + 1


def same_hair(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Whether two coordinates (hair items in slot order, see outfit_hair) have the same hair:
    each one's main hair (its first hair accessory) is also worn in the other. Extra
    ornaments therefore don't matter, but an ornament-only coordinate doesn't match one that
    has that ornament plus a real hairstyle, and neither do two different hairstyles that
    happen to share an ornament."""
    return bool(a) and bool(b) and a[0] in b and b[0] in a


def hair_matcher(chara_hairs: dict[Path, list[tuple[str, ...]]]):
    """matches(coordinate hair) -> [folder names] of the characters that wear that hair (see
    same_hair) in one of their outfits, in filename order. Empty for a coordinate without hair."""
    by_item: dict[str, list[tuple[str, tuple[str, ...]]]] = defaultdict(list)
    rank: dict[str, int] = {}
    for chara, name in _sorted_charas(chara_hairs):
        rank.setdefault(name, len(rank))
        for hair in chara_hairs[chara]:
            if hair:
                by_item[hair[0]].append((name, hair))

    def matches(hair: tuple[str, ...]) -> list[str]:
        if not hair:
            return []
        # same_hair needs the character's main hair to be worn in the coordinate too, so look
        # the character's outfits up by every item of the coordinate's hair.
        found = {name for item in hair for name, other in by_item.get(item, ()) if same_hair(hair, other)}
        return sorted(found, key=rank.__getitem__)

    return matches


def plan_hair_groups(coord_hair: dict[Path, tuple[str, ...] | None], coord_dir: Path,
                     first_unknown: int = 1) -> dict:
    """Decide where ungrouped coordinates (directly in coord_dir) go, by hair.

    `coord_hair` maps every coordinate card under coord_dir, subfolders included, to its
    hair items (None: not a coordinate card, empty: no hair). A folder named UNKNOWN_<n>
    only stands in for a real folder, so when a hair is used in both, the real folder(s)
    decide. Returns a dict with
      "moves":     {coordinate: destination folder name}
      "ambiguous": {coordinate: the folders whose coordinates share its hair}  (left alone)
      "no_hair":   [ungrouped coordinates without hair]
      "lone":      {ungrouped coordinate: its hair} whose hair matches nothing else
      "grouped":   how many coordinates are already in folders
    """
    # item -> (top-level folder, hair) of the grouped coordinates wearing it as hair
    grouped_by_item: dict[str, list[tuple[str, tuple[str, ...]]]] = defaultdict(list)
    ungrouped: list[tuple[Path, tuple[str, ...]]] = []
    no_hair: list[Path] = []
    grouped = 0
    for path in sorted(coord_hair):
        hair = coord_hair[path]
        if hair is None:
            continue
        parts = path.relative_to(coord_dir).parts
        if len(parts) > 1:                       # in a folder; the top-level one names the group
            grouped += 1
            for item in hair:
                grouped_by_item[item].append((parts[0], hair))
        elif hair:
            ungrouped.append((path, hair))
        else:
            no_hair.append(path)

    moves: dict[Path, str] = {}
    ambiguous: dict[Path, list[str]] = {}
    pending: list[tuple[Path, tuple[str, ...]]] = []
    for path, hair in ungrouped:
        folders = {folder for folder, other in grouped_by_item.get(hair[0], ()) if same_hair(hair, other)}
        deciding = {f for f in folders if not _UNKNOWN_FOLDER_RE.match(f)} or folders
        if not deciding:
            pending.append((path, hair))
        elif len(deciding) == 1:
            moves[path] = next(iter(deciding))
        else:
            ambiguous[path] = sorted(deciding, key=str.casefold)

    # The rest: ungrouped coordinates that share hair with each other form a new folder.
    parent = list(range(len(pending)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pending_by_item: dict[str, list[int]] = defaultdict(list)
    for i, (_, hair) in enumerate(pending):
        for item in hair:
            pending_by_item[item].append(i)
    for i, (_, hair) in enumerate(pending):
        for j in pending_by_item.get(hair[0], ()):
            if j != i and same_hair(hair, pending[j][1]):
                parent[find(i)] = find(j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(pending)):
        clusters[find(i)].append(i)

    lone: dict[Path, tuple[str, ...]] = {}
    number = first_unknown
    for members in sorted(clusters.values(), key=lambda m: pending[m[0]][0]):    # pending is in path order
        if len(members) < 2:
            path, hair = pending[members[0]]
            lone[path] = hair
            continue
        for i in members:
            moves[pending[i][0]] = f"{UNKNOWN_FOLDER_PREFIX}{number}"
        number += 1
    return {"moves": moves, "ambiguous": ambiguous, "no_hair": no_hair, "lone": lone, "grouped": grouped}


def _move_unique(src: Path, dest_dir: Path) -> Path:
    """Move `src` into `dest_dir` (created if needed), numbering the name if taken; returns the new path."""
    dest = dest_dir / src.name
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


def _difference_note(clothes_diff: int, accessory_diff: int) -> str:
    """" (1 clothes part, 2 accessories differ)" for the log; empty for an exact match."""
    parts = []
    if clothes_diff:
        parts.append(f"{clothes_diff} clothes part{'s' if clothes_diff != 1 else ''}")
    if accessory_diff:
        parts.append(f"{accessory_diff} accessor{'ies' if accessory_diff != 1 else 'y'}")
    if not parts:
        return ""
    return f" ({', '.join(parts)} differ{'s' if len(parts) == 1 and (clothes_diff or accessory_diff) == 1 else ''})"


def _update_coord_cache(coord_dir: Path, moves: dict[str, str]) -> None:
    """Make the coordinate caches follow moved files (a move keeps mtime and size),
    so the next run doesn't re-parse them."""
    for file_name, version, sections in (
            (COORD_CACHE_FILE, COORD_CACHE_VERSION, ("files", "coords")),
            (SIG_CACHE_FILE, SIG_CACHE_VERSION, ("files", "sigs")),
            (HAIR_CACHE_FILE, HAIR_CACHE_VERSION, ("files", "hairs"))):
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
        self.ignore_accessories: bool = cfg.get("IgnoreAccessories", False)
        self.clothes_tolerance_raw = cfg.get("ClothesTolerance", 0)
        self.group_by_hair: bool = cfg.get("GroupByHair", False)

    @staticmethod
    def _tolerance(raw, label: str) -> int:
        if raw is None:
            return 0
        try:
            value = int(str(raw).strip() or 0)
        except (TypeError, ValueError):
            raise InputError(f"{label} tolerance must be a whole number, got {raw!r}.", tag=TAG) from None
        if value < 0:
            raise InputError(f"{label} tolerance can't be negative, got {value}.", tag=TAG)
        return value

    def _clothes_tolerance(self) -> int:
        return self._tolerance(self.clothes_tolerance_raw, "Clothes")

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
        clothes_tolerance = self._clothes_tolerance()
        fuzzy = bool(self.ignore_accessories or clothes_tolerance)

        self.log_start(TAG, f"{coord_dir}  (characters: {', '.join(str(d) for d in chara_dirs)})")
        logger.info(TAG, f"Use cache: {self.use_cache}")
        logger.info(TAG, f"Include coordinate subfolders: {self.include_subfolders}")
        logger.info(TAG, f"Ignore accessories: {self.ignore_accessories}, clothes tolerance: {clothes_tolerance}"
                         + ("" if fuzzy else " (exact copies only)"))
        logger.info(TAG, f"Group remaining coordinates by hair: {self.group_by_hair}")
        logger.line()

        handled = self._group_by_chara_hair(chara_dirs, coord_dir)
        logger.line()
        logger.info(TAG, "Matching the remaining coordinates by outfit")
        self._group_by_outfits(chara_dirs, coord_dir, clothes_tolerance, handled)
        if self.group_by_hair:
            self._group_by_hair(coord_dir)

    def _group_by_chara_hair(self, chara_dirs: list[Path], coord_dir: Path) -> set[str]:
        """Move coordinates into the folder of the one character that wears their hair.
        Returns the paths (as they are now) of the coordinates this pass settled, so the outfit
        pass leaves them alone. A coordinate whose hair fits no character or several is not
        settled."""
        chara_hairs = collect_chara_hairs(chara_dirs, coord_dir, self.use_cache)
        if not any(hairs for hairs in chara_hairs.values()):
            logger.info(TAG, "No character wears a hair accessory — skipping the hair pass")
            return set()
        matches = hair_matcher(chara_hairs)
        coord_hair = build_coord_hair_cache(coord_dir, use_cache=self.use_cache)
        coords = sorted(Path(sp) for sp, hair in coord_hair.items()
                        if hair and (self.include_subfolders or Path(sp).parent == coord_dir))
        logger.info(TAG, f"Coordinate cards with hair: {len(coords)}")

        handled: set[str] = set()
        moved = in_place = ambiguous = failed = 0
        moves: dict[str, str] = {}
        try:
            for coord in coords:
                names = matches(tuple(coord_hair[str(coord)]))
                if not names:
                    continue
                if len(names) > 1:
                    logger.info(TAG, f"{coord.name}: its hair is worn by several characters "
                                     f"({', '.join(names)}) — left for outfit matching")
                    ambiguous += 1
                    continue
                if coord.parent == coord_dir / names[0]:
                    in_place += 1
                    handled.add(str(coord))
                    continue
                try:
                    dest = _move_unique(coord, coord_dir / names[0])
                except Exception as e:
                    logger.error(TAG, f"Could not move {coord.name}: {e}")
                    failed += 1
                    continue
                logger.success(TAG, f"Moved {coord.name} -> {names[0]}/ (character's hair)")
                moves[str(coord)] = str(dest)
                handled.add(str(dest))
                moved += 1
        finally:
            if self.use_cache and moves:
                _update_coord_cache(coord_dir, moves)

        logger.line()
        logger.success(TAG, f"Hair matching done — moved: {moved}, already in place: {in_place}, "
                            f"hair shared by several characters: {ambiguous}"
                            + (f", failed: {failed}" if failed else ""))
        return handled

    def _group_by_outfits(self, chara_dirs: list[Path], coord_dir: Path,
                          clothes_tolerance: int,
                          skip: set[str] = frozenset()) -> bool:
        """Move coordinates into the folder of the character whose outfit they match, except
        the coordinates in `skip` (paths already settled by the hair pass).
        Returns False if there are no character cards to match against."""
        # matches(key) -> [(chara folder name, clothes differences, accessory differences)], best first;
        # coord_keys maps each coordinate card's path to its key (falsy: not a coordinate card).
        if self.ignore_accessories or clothes_tolerance:
            chara_sigs = collect_chara_signatures(chara_dirs, coord_dir, self.use_cache)
            if not chara_sigs:
                logger.warning(TAG, "No character cards found — nothing to match outfits against")
                return False
            matches = fuzzy_matcher(chara_sigs, self.ignore_accessories, clothes_tolerance)
            coord_keys = build_coord_sig_cache(coord_dir, use_cache=self.use_cache)
        else:
            chara_digests = collect_chara_digests(chara_dirs, coord_dir, self.use_cache)
            if not chara_digests:
                logger.warning(TAG, "No character cards found — nothing to match outfits against")
                return False
            matches = exact_matcher(chara_digests)
            coord_keys = build_coord_cache(coord_dir, use_cache=self.use_cache)

        coords = sorted(Path(sp) for sp, key in coord_keys.items()
                        if key and sp not in skip
                        and (self.include_subfolders or Path(sp).parent == coord_dir))
        logger.info(TAG, f"Coordinate cards found: {len(coords)}")
        logger.line()

        moved = in_place = unmatched = failed = 0
        moves: dict[str, str] = {}
        try:
            for coord in coords:
                found = matches(coord_keys[str(coord)])
                names: list[str] = []
                for name, _, _ in found:
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
                try:
                    dest = _move_unique(coord, coord_dir / names[0])
                except Exception as e:
                    logger.error(TAG, f"Could not move {coord.name}: {e}")
                    failed += 1
                    continue
                note = _difference_note(found[0][1], found[0][2])
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
        return True

    def _group_by_hair(self, coord_dir: Path) -> None:
        """Group the coordinates still sitting directly in coord_dir by their hair."""
        logger.line()
        logger.info(TAG, "Grouping remaining coordinates by hair")
        coord_hair = {Path(sp): hair for sp, hair in
                      build_coord_hair_cache(coord_dir, use_cache=self.use_cache).items()}
        plan = plan_hair_groups(coord_hair, coord_dir, next_unknown_number(coord_dir))

        ungrouped = len(plan["moves"]) + len(plan["ambiguous"]) + len(plan["lone"]) + len(plan["no_hair"])
        logger.info(TAG, f"Coordinates already in folders: {plan['grouped']}, "
                         f"ungrouped (directly in the coordinate folder): {ungrouped}")
        for coord, folders in sorted(plan["ambiguous"].items()):
            logger.info(TAG, f"{coord.name}: its hair is used in several folders ({', '.join(folders)}) — left alone")
        for coord, hair in sorted(plan["lone"].items()):
            logger.info(TAG, f"{coord.name}: its hair ({', '.join(hair)}) isn't shared with any other coordinate — left alone")
        for coord in sorted(plan["no_hair"]):
            logger.info(TAG, f"{coord.name}: no hair accessory found — left alone")

        moved_known = moved_unknown = failed = 0
        moves: dict[str, str] = {}
        try:
            for coord, folder in sorted(plan["moves"].items()):
                try:
                    dest = _move_unique(coord, coord_dir / folder)
                except Exception as e:
                    logger.error(TAG, f"Could not move {coord.name}: {e}")
                    failed += 1
                    continue
                logger.success(TAG, f"Moved {coord.name} -> {folder}/ (same hair)")
                moves[str(coord)] = str(dest)
                if folder.upper().startswith(UNKNOWN_FOLDER_PREFIX):
                    moved_unknown += 1
                else:
                    moved_known += 1
        finally:
            if self.use_cache and moves:
                _update_coord_cache(coord_dir, moves)

        logger.line()
        logger.success(TAG, f"Hair grouping done — added to existing folders: {moved_known}, "
                            f"moved to new {UNKNOWN_FOLDER_PREFIX}* folders: {moved_unknown}, "
                            f"ambiguous hair: {len(plan['ambiguous'])}, hair matches nothing: {len(plan['lone'])}, "
                            f"no hair found: {len(plan['no_hair'])}"
                            + (f", failed: {failed}" if failed else ""))
