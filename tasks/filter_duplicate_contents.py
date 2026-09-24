import io
import json as _json
import shutil
import struct
from collections import defaultdict
from pathlib import Path
from typing import Literal

import xxhash

from tasks.base_task import DEFAULT_DOWNLOADS_PATH, validate_input_path
from utils.classifier import CardType, get_card_type, is_coordinate
from utils.config import Config
from utils.file_manager import FileManager
from utils.logger import logger

# ---------------------------------------------------------------------------
# Keep strategy constants — must match OptionsConfigItem values exactly
# ---------------------------------------------------------------------------

KEEP_NONE      = "None"   # move all copies; matches interface.json's KeepStrategy case name
KEEP_NEWEST    = "Newest"
KEEP_OLDEST    = "Oldest"
KEEP_BIGGEST   = "Biggest file size"
KEEP_SMALLEST  = "Smallest file size"
KEEP_LAST_LEX  = "Last alphabetically"
KEEP_FIRST_LEX = "First alphabetically"

# ---------------------------------------------------------------------------
# Duplicate action constants — must match OptionsConfigItem case names exactly
# ---------------------------------------------------------------------------

ACTION_MOVE_RENAME = "Move & Rename"
ACTION_MOVE        = "Move"
ACTION_DELETE      = "Delete"

Category = Literal["chara", "coordinate", "mods", "overlays", "scene"]

# ---------------------------------------------------------------------------
# Cache — per-file fingerprint (mtime, size) reuse, same idea as the
# incremental caches elsewhere in KKAFIO. Split into three separate cache
# files so toggling Fuzzy Chara on/off (an expensive, chara-only operation)
# never invalidates or forces recomputation of the cheaper content hashes, and
# vice versa.
# ---------------------------------------------------------------------------

PNG_CACHE_FILE   = "kkafio_duplicate_png_cache.json"    # XXH3 + category, all PNGs
FUZZY_CACHE_FILE = "kkafio_duplicate_fuzzy_cache.json"  # phash, chara cards only
MODS_CACHE_FILE  = "kkafio_duplicate_mods_cache.json"   # XXH3, zipmods only


def _file_fp(p: Path) -> list[int]:
    st = p.stat()
    return [int(st.st_mtime), st.st_size]


def _load_duplic_cache(folder_path: Path, cache_file: str) -> dict[str, dict]:
    """Return {abs_path_str: {"fp": [mtime, size], ...}} from the given
    cache file, or {} if missing/unreadable/for a different folder."""
    cache_path = folder_path / cache_file
    try:
        data = _json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("dir") == str(folder_path):
            return data.get("files", {})
    except Exception:
        pass
    return {}


def _save_duplic_cache(folder_path: Path, cache_file: str, files: dict[str, dict]) -> None:
    cache_path = folder_path / cache_file
    data = {"dir": str(folder_path), "files": files}
    try:
        # Atomic (temp file + os.replace) and compact, same as the other
        # incremental caches in utils/chara_ops.py — see _atomic_write_json
        # there for why. This cache can hold one entry per PNG scanned, so
        # pretty-printing it is pure overhead, and a plain write_text() left
        # a half-written cache readable-but-corrupt if interrupted.
        from utils.chara_ops import _atomic_write_json
        _atomic_write_json(cache_path, data)
    except Exception as e:
        logger.warning("DUPLIC", f"Could not save {cache_file}: {e}")

# ---------------------------------------------------------------------------
# PNG payload extraction
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_IEND    = b"IEND"


def _get_png_payload(data: bytes) -> bytes | None:
    """Return the bytes after the IEND chunk (character data), or None."""
    if not data.startswith(_PNG_SIG):
        return None
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        pos += 12 + length
        if chunk_type == _IEND:
            payload = data[pos:]
            return payload if payload else None
    return None


def _get_png_image_bytes(data: bytes) -> bytes:
    """Return only the PNG image portion (up to and including IEND).
    Used for perceptual hashing so the card preview is what gets compared.
    """
    if not data.startswith(_PNG_SIG):
        return data
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if chunk_type == _IEND:
            return data[:end]
        pos = end
    return data


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _xxh(data: bytes) -> str:
    """Non-cryptographic 128-bit content hash (XXH3). Much faster than MD5 and
    plenty for duplicate detection — collisions are astronomically unlikely."""
    return xxhash.xxh3_128_hexdigest(data)


def _xxh_file(path: Path) -> str:
    h = xxhash.xxh3_128()
    with path.open("rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _phash(image_bytes: bytes) -> str | None:
    try:
        import imagehash
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        return str(imagehash.phash(img))
    except Exception:
        return None


def _phash_distance(a: str, b: str) -> int:
    """Kept for any external caller; _fuzzy_group parses each hash once
    instead of calling this per pair (see below)."""
    try:
        import imagehash
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except Exception:
        return 64


_FUZZY_THRESHOLD = 8

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(data: bytes) -> Category | None:
    card_type = get_card_type(data)
    match card_type:
        case CardType.KK | CardType.KKSP | CardType.KKS:
            return "chara"
        case CardType.SCENE:
            return "scene"
        case CardType.UNKNOWN:
            if is_coordinate(data):
                return "coordinate"
            else:
                return "overlays"
        case _:
            return None  # skip


# ---------------------------------------------------------------------------
# Keep strategy
# ---------------------------------------------------------------------------

def _tiebreak(paths: list[Path]) -> Path:
    return max(paths, key=lambda p: p.name)


def _select_keep(paths: list[Path], keep: str) -> Path | None:
    if keep == KEEP_NONE:
        return None
    if keep == KEEP_NEWEST:
        best = max(p.stat().st_mtime for p in paths)
        return _tiebreak([p for p in paths if p.stat().st_mtime == best])
    if keep == KEEP_OLDEST:
        best = min(p.stat().st_mtime for p in paths)
        return _tiebreak([p for p in paths if p.stat().st_mtime == best])
    if keep == KEEP_BIGGEST:
        best = max(p.stat().st_size for p in paths)
        return _tiebreak([p for p in paths if p.stat().st_size == best])
    if keep == KEEP_SMALLEST:
        best = min(p.stat().st_size for p in paths)
        return _tiebreak([p for p in paths if p.stat().st_size == best])
    if keep == KEEP_LAST_LEX:
        return max(paths, key=lambda p: p.name)
    if keep == KEEP_FIRST_LEX:
        return min(paths, key=lambda p: p.name)
    return _tiebreak(paths)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def _send_to_bin(path: Path) -> bool:
    try:
        from send2trash import send2trash
        send2trash(str(path))
        return True
    except Exception as e:
        logger.error("DUPLIC", f"Could not send to bin: {path.name} - {e}")
        return False


def _move_to_folder(path: Path, dest_folder: Path, dest_name: str | None = None) -> bool:
    dest_folder.mkdir(parents=True, exist_ok=True)
    name = dest_name if dest_name else path.name
    dest = dest_folder / name
    if dest.exists():
        stem, suffix = Path(name).stem, Path(name).suffix
        counter = 1
        while dest.exists():
            dest = dest_folder / f"{stem}_{counter}{suffix}"
            counter += 1
    try:
        shutil.move(str(path), str(dest))
        return True
    except Exception as e:
        logger.error("DUPLIC", f"Could not move {path.name} - {e}")
        return False


def _build_rename_map(to_handle: list[Path], keep_path: Path | None) -> dict[Path, str]:
    """Work out the target filename for each duplicate about to be moved.

    - keep_path is not None (a copy is being kept in the source): every
      moved duplicate is renamed to the kept file's name + a number,
      e.g. keep "foo.png" -> duplicates "foo_1.png", "foo_2.png", ...
    - keep_path is None (all copies are being moved, none kept): the first
      duplicate found keeps its own name unchanged, and the rest are
      renamed after it with a number, e.g. "bar.png", "bar_1.png",
      "bar_2.png", ...
    """
    rename_map: dict[Path, str] = {}
    if not to_handle:
        return rename_map

    if keep_path is not None:
        base_stem = keep_path.stem
        for i, p in enumerate(to_handle, start=1):
            rename_map[p] = f"{base_stem}_{i}{p.suffix}"
    else:
        base_stem = to_handle[0].stem
        rename_map[to_handle[0]] = f"{base_stem}{to_handle[0].suffix}"
        for i, p in enumerate(to_handle[1:], start=1):
            rename_map[p] = f"{base_stem}_{i}{p.suffix}"

    return rename_map


# ---------------------------------------------------------------------------
# Fuzzy grouping — leader clustering over 64-bit pHashes (numpy-vectorised)
# ---------------------------------------------------------------------------

_POPCOUNT8 = None  # lazily-built byte popcount table (numpy < 2.0 fallback)


def _hash_to_int(ph: str | None) -> int | None:
    """Parse a 64-bit imagehash hex string into an int (None if unusable)."""
    if not ph or len(ph) != 16:
        return None
    try:
        return int(ph, 16)
    except ValueError:
        return None


def _hamming_to_many(hashes, value):
    """Hamming distance between `value` and every element of a uint64 array."""
    import numpy as np
    x = np.bitwise_xor(hashes, np.uint64(value))
    if hasattr(np, "bitwise_count"):           # numpy >= 2.0
        return np.bitwise_count(x)
    global _POPCOUNT8
    if _POPCOUNT8 is None:
        _POPCOUNT8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    return _POPCOUNT8[x.view(np.uint8)].reshape(-1, 8).sum(axis=1)


def _fuzzy_group(paths: list[Path], phashes: list[str | None],
                 threshold: int = _FUZZY_THRESHOLD) -> list[list[Path]]:
    """Group paths by perceptual similarity (paths/phashes are parallel lists;
    a None phash means "couldn't be computed" and is never grouped).

    Leader clustering: each group is defined by its FIRST member (the
    "leader"). A path joins the group of the nearest leader within
    `threshold` bits, otherwise it starts a new group of its own. Every
    member of a group is therefore within `threshold` of the group's first
    member.

    This replaces the earlier union-find grouping, which was transitive:
    A~B and B~C put A and C in one group even when A and C were far apart,
    so a chain of gradually different cards could merge into one huge
    "duplicate" set. Here a card can never be pulled in by another member,
    only by the leader.

    Results depend on input order (the first card of a cluster is its
    leader), so callers should pass a stable, sorted order. Comparisons
    against all leaders are done in one numpy operation, so the cost is
    O(n * leaders) but with a tiny constant instead of pure-Python loops.

    Singletons are returned too (callers filter on len > 1).

    pHash distance alone still can't tell "same character, re-saved" from
    "different characters in the same pose", so the default action for a
    fuzzy group should stay reversible (Move, not Delete).
    """
    import numpy as np

    groups: list[list[Path]] = []
    leaders = np.zeros(len(paths), dtype=np.uint64)
    leader_group: list[int] = []   # leader index -> index into `groups`

    for path, ph in zip(paths, phashes):
        value = _hash_to_int(ph)
        if value is None:
            groups.append([path])
            continue
        if leader_group:
            dist = _hamming_to_many(leaders[:len(leader_group)], value)
            best = int(dist.argmin())            # ties -> earliest leader
            if int(dist[best]) <= threshold:
                groups[leader_group[best]].append(path)
                continue
        leaders[len(leader_group)] = value
        leader_group.append(len(groups))
        groups.append([path])

    return groups


# ---------------------------------------------------------------------------
# Main module class
# ---------------------------------------------------------------------------

class FilterDuplicateContents:
    DUPLICATES_DIR = "_duplicates_"

    def __init__(self, config: Config, file_manager: FileManager):
        self.config       = config
        self.file_manager = file_manager
        cfg = self.config.filter_duplicate_contents
        self.fuzzy_chara : bool = cfg.get("FuzzyChara", False)
        self.keep        : str  = cfg.get("Keep",       KEEP_BIGGEST)
        self.duplicate_action : str = cfg.get("DuplicateAction", ACTION_MOVE_RENAME)
        self.use_cache   : bool = cfg.get("UseCache",   True)

    def run(self, folder_path: Path | None = None) -> None:
        if folder_path is None:
            folder_path = Path(self.config.filter_duplicate_contents["InputPath"])
        folder_path = Path(folder_path)
        duplicates_root = folder_path / self.DUPLICATES_DIR

        validate_input_path("DUPLIC", folder_path, default_path=DEFAULT_DOWNLOADS_PATH)

        logger.line()
        logger.info("DUPLIC", f"Scanning        : {folder_path}")
        logger.info("DUPLIC", f"Keep strategy   : {self.keep}")
        logger.info("DUPLIC", f"Fuzzy chara     : {self.fuzzy_chara}")
        logger.info("DUPLIC", f"Duplicate action: {self.duplicate_action}")
        logger.info("DUPLIC", f"Use cache       : {self.use_cache}")

        # ------------------------------------------------------------------
        # 1. Collect all files
        # ------------------------------------------------------------------
        png_files: list[Path] = []
        mod_files: list[Path] = []

        for p in folder_path.rglob("*"):
            if not p.is_file():
                continue
            try:
                p.relative_to(duplicates_root)
                continue  # skip _duplicates_/ folder
            except ValueError:
                pass
            suffix = p.suffix.lower()
            if suffix == ".png":
                png_files.append(p)
            elif suffix == ".zipmod":
                mod_files.append(p)

        logger.info("DUPLIC",
            f"Found {len(png_files)} PNG(s) and {len(mod_files)} zipmod(s)")

        # ------------------------------------------------------------------
        # 2. Hash all PNGs and group by fingerprint.
        #    ThreadPoolExecutor parallelises read_bytes() calls — the GIL is
        #    released during I/O so threads genuinely run concurrently here.
        # ------------------------------------------------------------------
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import os

        hash_dict: dict[str, list[Path]]         = defaultdict(list)
        category_map: dict[str, Category | None] = {}

        png_cache = _load_duplic_cache(folder_path, PNG_CACHE_FILE) if self.use_cache else {}
        new_png_cache: dict[str, dict] = {}

        # Fuzzy matching needs pillow + imagehash; check once up front rather
        # than failing per file.
        do_fuzzy = self.fuzzy_chara
        if do_fuzzy:
            try:
                import imagehash  # noqa: F401
                from PIL import Image  # noqa: F401
            except Exception:
                logger.error("DUPLIC",
                    "pillow/imagehash not installed - fuzzy matching disabled. "
                    "Install with: pip install pillow imagehash")
                do_fuzzy = False

        fuzzy_cache = (_load_duplic_cache(folder_path, FUZZY_CACHE_FILE)
                       if (self.use_cache and do_fuzzy) else {})
        new_fuzzy_cache: dict[str, dict] = {}
        phash_map: dict[Path, str | None] = {}

        def _hash_png(path: Path):
            """Fingerprint one PNG (XXH3 of the character-data payload, or
            the whole file for non-chara PNGs), classify it, and - for chara
            cards when fuzzy matching is on - compute its perceptual hash.
            Reuses cached results if the file's mtime/size haven't changed.
            Runs in a thread pool worker.

            The perceptual hash is computed here, while the bytes are already
            in hand, so only a short hash string leaves the worker. (The
            previous version kept every new chara card's full bytes in RAM
            until the fuzzy step, i.e. the whole library on a first run.)
            """
            sp = str(path)
            fp = _file_fp(path)
            data: bytes | None = None
            cached = png_cache.get(sp)
            if cached and cached.get("fp") == fp and "xxh" in cached:
                digest, cat, was_cached = cached["xxh"], cached.get("category"), True
            else:
                data    = path.read_bytes()
                payload = _get_png_payload(data)
                digest  = _xxh(payload) if payload else _xxh(data)
                cat     = _classify(data)
                was_cached = False

            ph: str | None = None
            ph_cached = False
            if do_fuzzy and cat == "chara":
                fc = fuzzy_cache.get(sp)
                if fc and fc.get("fp") == fp and fc.get("phash"):
                    ph, ph_cached = fc["phash"], True
                else:
                    if data is None:
                        data = path.read_bytes()
                    ph = _phash(_get_png_image_bytes(data))
            return path, digest, cat, fp, was_cached, ph, ph_cached

        # Use min(32, cpu_count * 2) workers — I/O bound so more threads help
        workers = min(32, (os.cpu_count() or 4) * 2)
        logger.info("DUPLIC", f"Hashing {len(png_files)} PNG files (workers: {workers})...")

        completed = 0
        reused_png = 0
        reused_fuzzy = 0
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_hash_png, p): p for p in png_files}
            for future in as_completed(futures):
                completed += 1
                if completed % 100 == 0:
                    logger.info("DUPLIC", f"Processed {completed}/{len(png_files)}...")
                try:
                    path, digest, cat, fp, was_cached, ph, ph_cached = future.result()
                    hash_dict[digest].append(path)
                    if digest not in category_map:
                        category_map[digest] = cat
                    new_png_cache[str(path)] = {"fp": fp, "xxh": digest, "category": cat}
                    if was_cached:
                        reused_png += 1
                    if do_fuzzy and cat == "chara":
                        phash_map[path] = ph
                        if ph:
                            # Never cache a failed (None) hash: it would be
                            # reused forever, even after the cause is fixed.
                            new_fuzzy_cache[str(path)] = {"fp": fp, "phash": ph}
                            if ph_cached:
                                reused_fuzzy += 1
                except Exception as e:
                    path = futures[future]
                    logger.error("DUPLIC", f"Could not read {path.name}: {e}")

        if self.use_cache:
            _save_duplic_cache(folder_path, PNG_CACHE_FILE, new_png_cache)
            if do_fuzzy:
                _save_duplic_cache(folder_path, FUZZY_CACHE_FILE, new_fuzzy_cache)
        if reused_png:
            logger.info("DUPLIC",
                f"PNG cache: {reused_png} unchanged, {len(png_files) - reused_png} new/changed")
        if reused_fuzzy:
            logger.info("DUPLIC", f"Fuzzy cache: {reused_fuzzy} perceptual hash(es) reused")

        # ------------------------------------------------------------------
        # 3. Exact duplicate groups — any hash with 2+ files
        # ------------------------------------------------------------------
        duplicate_groups: list[tuple[list[Path], Category | None]] = []
        # With fuzzy matching on, exact chara groups are held back until step 4
        # decides whether a group is absorbed into a larger fuzzy set.
        exact_chara_groups: dict[str, list[Path]] = {}

        for digest, files in hash_dict.items():
            if len(files) < 2:
                continue
            category = category_map.get(digest)
            if category == "chara" and do_fuzzy:
                exact_chara_groups[digest] = files
            else:
                duplicate_groups.append((files, category))
            logger.info("DUPLIC",
                f"Exact {category} set ({len(files)}): "
                + ", ".join(p.name for p in files))

        # ------------------------------------------------------------------
        # 4. Fuzzy chara grouping.
        #    Every distinct chara card takes part, including one representative
        #    of each exact-duplicate set - previously exact sets were left out
        #    entirely, so a kept copy was never compared with similar cards.
        #    If a representative matches other cards, its whole exact set is
        #    merged into the fuzzy set (and not handled a second time).
        # ------------------------------------------------------------------
        if do_fuzzy:
            rep_digest: dict[Path, str] = {}
            for digest, files in hash_dict.items():
                if category_map.get(digest) == "chara":
                    rep_digest[min(files, key=str)] = digest

            candidates = sorted(rep_digest, key=str)   # stable order -> stable leaders
            no_hash = sum(1 for p in candidates if not phash_map.get(p))
            if no_hash:
                logger.warning("DUPLIC",
                    f"{no_hash} chara card(s) had no usable perceptual hash and were not compared")

            absorbed: set[str] = set()
            if len(candidates) > 1:
                logger.info("DUPLIC", f"Fuzzy matching {len(candidates)} chara cards...")
                phashes = [phash_map.get(p) for p in candidates]
                for group in _fuzzy_group(candidates, phashes):
                    if len(group) < 2:
                        continue
                    digests = [rep_digest[p] for p in group]
                    absorbed.update(digests)
                    members = sorted((f for d in digests for f in hash_dict[d]), key=str)
                    duplicate_groups.append((members, "chara"))
                    logger.info("DUPLIC",
                        f"Fuzzy chara set ({len(members)}): "
                        + ", ".join(f.name for f in members))

            for digest, files in exact_chara_groups.items():
                if digest not in absorbed:
                    duplicate_groups.append((files, "chara"))

        # ------------------------------------------------------------------
        # 5. Zipmod grouping
        # ------------------------------------------------------------------
        mods_cache = _load_duplic_cache(folder_path, MODS_CACHE_FILE) if self.use_cache else {}
        new_mods_cache: dict[str, dict] = {}

        def _hash_mod(path: Path):
            sp = str(path)
            fp = _file_fp(path)
            cached = mods_cache.get(sp)
            if cached and cached.get("fp") == fp and "xxh" in cached:
                return path, cached["xxh"], fp, True
            return path, _xxh_file(path), fp, False

        mod_hash_dict: dict[str, list[Path]] = defaultdict(list)
        reused_mods = 0
        if mod_files:
            logger.info("DUPLIC", f"Hashing {len(mod_files)} zipmod file(s)...")
            with ThreadPoolExecutor(max_workers=workers) as ex:
                mod_futures = {ex.submit(_hash_mod, p): p for p in mod_files}
                for future in as_completed(mod_futures):
                    try:
                        path, digest, fp, was_cached = future.result()
                        mod_hash_dict[digest].append(path)
                        new_mods_cache[str(path)] = {"fp": fp, "xxh": digest}
                        if was_cached:
                            reused_mods += 1
                    except Exception as e:
                        path = mod_futures[future]
                        logger.error("DUPLIC", f"Could not read {path.name}: {e}")

            if self.use_cache:
                _save_duplic_cache(folder_path, MODS_CACHE_FILE, new_mods_cache)
            if reused_mods:
                logger.info("DUPLIC",
                    f"Mods cache: {reused_mods} unchanged, {len(mod_files) - reused_mods} new/changed")

        for fp, files in mod_hash_dict.items():
            if len(files) > 1:
                duplicate_groups.append((files, "mods"))
                logger.info("DUPLIC",
                    f"Duplicate mod set ({len(files)}): "
                    + ", ".join(p.name for p in files))

        logger.line()

        if not duplicate_groups:
            logger.success("DUPLIC", "No duplicates found")
            return

        logger.info("DUPLIC",
            f"{len(duplicate_groups)} duplicate set(s) found - keep: {self.keep}")

        # ------------------------------------------------------------------
        # 6. Apply keep strategy and handle files
        # ------------------------------------------------------------------
        counts: dict[str, int] = {
            "chara": 0, "coordinate": 0, "mods": 0, "overlays": 0, "scene": 0, "skipped": 0
        }

        for group_paths, category in duplicate_groups:
            if category is None:
                for p in group_paths:
                    logger.skipped("DUPLIC", p.name)
                counts["skipped"] += len(group_paths)
                continue

            keep_path = _select_keep(group_paths, self.keep)
            to_handle = [p for p in group_paths if p != keep_path]

            if keep_path:
                logger.info("DUPLIC", f"Keeping : {keep_path.name}")

            rename_map: dict[Path, str] = {}
            if self.duplicate_action == ACTION_MOVE_RENAME:
                rename_map = _build_rename_map(to_handle, keep_path)

            for path in to_handle:
                if self.duplicate_action == ACTION_DELETE:
                    if _send_to_bin(path):
                        logger.removed("DUPLIC", path.name)
                        counts[category] += 1
                else:
                    dest = duplicates_root / category
                    dest_name = rename_map.get(path)
                    if _move_to_folder(path, dest, dest_name=dest_name):
                        if dest_name and dest_name != path.name:
                            logger.success("DUPLIC",
                                f"Moved to _duplicates_/{category}/: {path.name} -> {dest_name}")
                        else:
                            logger.success("DUPLIC",
                                f"Moved to _duplicates_/{category}/: {path.name}")
                        counts[category] += 1

        logger.line()
        action = "Deleted" if self.duplicate_action == ACTION_DELETE else "Moved"
        logger.success(
            "DUPLIC",
            f"{action} - chara: {counts['chara']}, "
            f"coordinate: {counts['coordinate']}, "
            f"scene: {counts['scene']}, "
            f"overlays: {counts['overlays']}, "
            f"mods: {counts['mods']}, "
            f"skipped: {counts['skipped']}",
        )