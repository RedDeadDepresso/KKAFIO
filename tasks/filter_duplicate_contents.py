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
        cache_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
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
# Fuzzy grouping — union-find, same pattern as reference script
# ---------------------------------------------------------------------------

def _fuzzy_group(paths: list[Path], phashes: list[str | None]) -> list[list[Path]]:
    """Group paths by perceptual similarity, given precomputed phashes
    (same order/length as paths — a None entry means that file's phash
    couldn't be computed, so it's never grouped with anything)."""
    if not paths:
        return []

    n = len(paths)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(n):
        if phashes[i] is None:
            continue
        for j in range(i + 1, n):
            if phashes[j] is None:
                continue
            if _phash_distance(phashes[i], phashes[j]) <= _FUZZY_THRESHOLD:
                union(i, j)

    groups: dict[int, list[Path]] = defaultdict(list)
    for i, path in enumerate(paths):
        groups[find(i)].append(path)

    return list(groups.values())


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

        def _hash_png(path: Path):
            """Fingerprint one PNG (XXH3 of the character-data payload, or
            the whole file for non-chara PNGs) + classify it. Reuses the
            cached result if the file's mtime/size haven't changed since
            the last run. Runs in a thread pool worker."""
            sp = str(path)
            fp = _file_fp(path)
            cached = png_cache.get(sp)
            if cached and cached.get("fp") == fp and "xxh" in cached:
                return path, cached["xxh"], cached.get("category"), fp, True
            data    = path.read_bytes()
            payload = _get_png_payload(data)
            digest  = _xxh(payload) if payload else _xxh(data)
            cat     = _classify(data)
            return path, digest, cat, fp, False

        # Use min(32, cpu_count * 2) workers — I/O bound so more threads help
        workers = min(32, (os.cpu_count() or 4) * 2)
        logger.info("DUPLIC", f"Hashing {len(png_files)} PNG files (workers: {workers})...")

        completed = 0
        reused_png = 0
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_hash_png, p): p for p in png_files}
            for future in as_completed(futures):
                completed += 1
                if completed % 100 == 0:
                    logger.info("DUPLIC", f"Processed {completed}/{len(png_files)}...")
                try:
                    path, digest, cat, fp, was_cached = future.result()
                    hash_dict[digest].append(path)
                    if digest not in category_map:
                        category_map[digest] = cat
                    new_png_cache[str(path)] = {"fp": fp, "xxh": digest, "category": cat}
                    if was_cached:
                        reused_png += 1
                except Exception as e:
                    path = futures[future]
                    logger.error("DUPLIC", f"Could not read {path.name}: {e}")

        if self.use_cache:
            _save_duplic_cache(folder_path, PNG_CACHE_FILE, new_png_cache)
        if reused_png:
            logger.info("DUPLIC",
                f"PNG cache: {reused_png} unchanged, {len(png_files) - reused_png} new/changed")

        # ------------------------------------------------------------------
        # 3. Exact duplicate groups — any hash with 2+ files
        # ------------------------------------------------------------------
        duplicate_groups: list[tuple[list[Path], Category | None]] = []
        exact_chara_paths: set[Path] = set()

        for fp, files in hash_dict.items():
            if len(files) < 2:
                continue
            category = category_map.get(fp)
            duplicate_groups.append((files, category))
            if category == "chara":
                exact_chara_paths.update(files)
            logger.info("DUPLIC",
                f"Exact {category} set ({len(files)}): "
                + ", ".join(p.name for p in files))

        # ------------------------------------------------------------------
        # 4. Fuzzy chara grouping — only cards not already caught exactly
        # ------------------------------------------------------------------
        if self.fuzzy_chara:
            def _is_chara(path: Path):
                return path, _classify(path.read_bytes()) == "chara"

            non_exact = [p for p in png_files if p not in exact_chara_paths]
            fuzzy_candidates = []
            if non_exact:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    fuzz_futures = {ex.submit(_is_chara, p): p for p in non_exact}
                    for future in as_completed(fuzz_futures):
                        try:
                            path, is_chara = future.result()
                            if is_chara:
                                fuzzy_candidates.append(path)
                        except Exception as e:
                            path = fuzz_futures[future]
                            logger.error("DUPLIC", f"Could not read {path.name}: {e}")

            if fuzzy_candidates:
                logger.info("DUPLIC",
                    f"Fuzzy matching {len(fuzzy_candidates)} chara cards...")

                fuzzy_cache = _load_duplic_cache(folder_path, FUZZY_CACHE_FILE) if self.use_cache else {}
                new_fuzzy_cache: dict[str, dict] = {}
                phashes: list[str | None] = []
                fuzzy_unavailable = False
                reused_fuzzy = 0

                for path in fuzzy_candidates:
                    sp = str(path)
                    fp = _file_fp(path)
                    cached = fuzzy_cache.get(sp)
                    if cached and cached.get("fp") == fp:
                        ph = cached.get("phash")
                        reused_fuzzy += 1
                    else:
                        try:
                            data = path.read_bytes()
                            image_bytes = _get_png_image_bytes(data)
                            ph = _phash(image_bytes)
                            if ph is None and not fuzzy_unavailable:
                                logger.error("DUPLIC",
                                    "pillow/imagehash not installed. Install with: pip install pillow imagehash")
                                fuzzy_unavailable = True
                        except Exception as e:
                            logger.error("DUPLIC", f"Could not hash {path.name}: {e}")
                            ph = None
                    phashes.append(ph)
                    new_fuzzy_cache[sp] = {"fp": fp, "phash": ph}

                if self.use_cache:
                    _save_duplic_cache(folder_path, FUZZY_CACHE_FILE, new_fuzzy_cache)
                if reused_fuzzy:
                    logger.info("DUPLIC",
                        f"Fuzzy cache: {reused_fuzzy} unchanged, "
                        f"{len(fuzzy_candidates) - reused_fuzzy} new/changed")

                for group in _fuzzy_group(fuzzy_candidates, phashes):
                    if len(group) > 1:
                        duplicate_groups.append((group, "chara"))
                        logger.info("DUPLIC",
                            f"Fuzzy chara set ({len(group)}): "
                            + ", ".join(p.name for p in group))

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