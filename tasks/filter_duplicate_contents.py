import hashlib
import io
import shutil
import struct
from collections import defaultdict
from pathlib import Path
from typing import Literal

from util.classifier import CardType, get_card_type, is_coordinate
from util.config import Config
from util.file_manager import FileManager
from util.logger import logger

# ---------------------------------------------------------------------------
# Keep strategy constants — must match OptionsConfigItem values exactly
# ---------------------------------------------------------------------------

KEEP_NONE      = "None — move all copies"
KEEP_NEWEST    = "Newest"
KEEP_OLDEST    = "Oldest"
KEEP_BIGGEST   = "Biggest file size"
KEEP_SMALLEST  = "Smallest file size"
KEEP_LAST_LEX  = "Last alphabetically"
KEEP_FIRST_LEX = "First alphabetically"

Category = Literal["chara", "coordinate", "mods", "overlays", "scene"]

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

def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _md5_file(path: Path) -> str:
    h = hashlib.md5()
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


def _move_to_folder(path: Path, dest_folder: Path) -> bool:
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest = dest_folder / path.name
    if dest.exists():
        stem, suffix = path.stem, path.suffix
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


# ---------------------------------------------------------------------------
# Fuzzy grouping — union-find, same pattern as reference script
# ---------------------------------------------------------------------------

def _fuzzy_group(paths: list[Path]) -> list[list[Path]]:
    """Group paths by perceptual similarity. Reads each file once."""
    if not paths:
        return []

    phashes: list[str | None] = []
    fuzzy_unavailable = False

    for path in paths:
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
        self.delete      : bool = cfg.get("Delete",     False)
        self.fuzzy_chara : bool = cfg.get("FuzzyChara", False)
        self.keep        : str  = cfg.get("Keep",       KEEP_BIGGEST)

    def run(self, folder_path: Path | None = None) -> None:
        if folder_path is None:
            folder_path = Path(self.config.filter_duplicate_contents["InputPath"])
        folder_path = Path(folder_path)
        duplicates_root = folder_path / self.DUPLICATES_DIR

        logger.line()
        logger.info("DUPLIC", f"Scanning      : {folder_path}")
        logger.info("DUPLIC", f"Keep strategy : {self.keep}")
        logger.info("DUPLIC", f"Fuzzy chara   : {self.fuzzy_chara}")
        logger.info("DUPLIC", f"Delete mode   : {self.delete}")

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

        def _hash_png(path: Path):
            """Read and fingerprint one PNG. Runs in a thread pool worker."""
            data    = path.read_bytes()
            payload = _get_png_payload(data)
            fp      = _md5(payload) if payload else _md5(data)
            cat     = _classify(data)
            return path, fp, cat

        # Use min(32, cpu_count * 2) workers — I/O bound so more threads help
        workers = min(32, (os.cpu_count() or 4) * 2)
        logger.info("DUPLIC", f"Hashing {len(png_files)} PNG files (workers: {workers})...")

        completed = 0
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_hash_png, p): p for p in png_files}
            for future in as_completed(futures):
                completed += 1
                if completed % 100 == 0:
                    logger.info("DUPLIC", f"Processed {completed}/{len(png_files)}...")
                try:
                    path, fp, cat = future.result()
                    hash_dict[fp].append(path)
                    if fp not in category_map:
                        category_map[fp] = cat
                except Exception as e:
                    path = futures[future]
                    logger.error("DUPLIC", f"Could not read {path.name}: {e}")

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
                for group in _fuzzy_group(fuzzy_candidates):
                    if len(group) > 1:
                        duplicate_groups.append((group, "chara"))
                        logger.info("DUPLIC",
                            f"Fuzzy chara set ({len(group)}): "
                            + ", ".join(p.name for p in group))

        # ------------------------------------------------------------------
        # 5. Zipmod grouping
        # ------------------------------------------------------------------
        mod_hash_dict: dict[str, list[Path]] = defaultdict(list)
        if mod_files:
            logger.info("DUPLIC", f"Hashing {len(mod_files)} zipmod file(s)...")
            with ThreadPoolExecutor(max_workers=workers) as ex:
                mod_futures = {ex.submit(_md5_file, p): p for p in mod_files}
                for future in as_completed(mod_futures):
                    try:
                        fp = future.result()
                        mod_hash_dict[fp].append(mod_futures[future])
                    except Exception as e:
                        path = mod_futures[future]
                        logger.error("DUPLIC", f"Could not read {path.name}: {e}")

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
            "chara": 0, "coordinate": 0, "mods": 0, "overlays": 0, "skipped": 0
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

            for path in to_handle:
                if self.delete:
                    if _send_to_bin(path):
                        logger.removed("DUPLIC", path.name)
                        counts[category] += 1
                else:
                    dest = duplicates_root / category
                    if _move_to_folder(path, dest):
                        logger.success("DUPLIC",
                            f"Moved to _duplicates_/{category}/: {path.name}")
                        counts[category] += 1

        logger.line()
        action = "Deleted" if self.delete else "Moved"
        logger.success(
            "DUPLIC",
            f"{action} - chara: {counts['chara']}, "
            f"coordinate: {counts['coordinate']}, "
            f"overlays: {counts['overlays']}, "
            f"mods: {counts['mods']}, "
            f"skipped: {counts['skipped']}",
        )
