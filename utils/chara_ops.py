"""
utils/chara_ops.py — Shared helpers for archive_chara and delete_chara.

Extracted here to avoid cross-module imports and keep each task module focused
on its own logic.  Nothing in this file depends on config or file_manager.
"""



import io
import struct
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import msgpack

from utils.logger import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PNG_IEND = b"IEND"

UAR_EXT_IDS = {
    "com.bepis.sideloader.universalautoresolver",
    "EC.Core.Sideloader.UniversalAutoResolver",
}

KNOWN_CHARA_MARKERS = {
    "【KoiKatuChara】", "【KoiKatuCharaS】", "【KoiKatuCharaSP】",
    "【EroMakeChara】", "【AIS_Chara】", "【KoiKatuCharaSun】",
    "【RG_Chara】", "【HCChara】", "【HCPChara】", "【SVChara】",
    "【ACChara】",
}

KNOWN_COORD_MARKERS = {
    "【KoiKatuClothes】", "【AIS_Clothes】", "【SVClothes】", "【ACClothes】",
}

_SIDELOADER_MODPACK = "Sideloader Modpack"

# ---------------------------------------------------------------------------
# Binary reading helpers (.NET BinaryReader style)
# ---------------------------------------------------------------------------

def _find_iend_end(data: bytes) -> int:
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        pos += 12 + length
        if chunk_type == PNG_IEND:
            return pos
    return -1


def _read_7bit_int(s: io.BytesIO) -> int:
    result, shift = 0, 0
    while True:
        b = s.read(1)
        if not b:
            raise EOFError("Unexpected EOF")
        byte = b[0]
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result


def _read_str(s: io.BytesIO) -> str:
    return s.read(_read_7bit_int(s)).decode("utf-8", errors="replace")


def _ri(s: io.BytesIO) -> int:
    return struct.unpack("<i", s.read(4))[0]


def _rq(s: io.BytesIO) -> int:
    return struct.unpack("<q", s.read(8))[0]


def _rb(s: io.BytesIO) -> bytes:
    n = struct.unpack("b", s.read(1))[0]
    return s.read(n)


# ---------------------------------------------------------------------------
# GUID extraction from KKEx block
# ---------------------------------------------------------------------------

def _extract_guids_from_kkex(kkex_bytes: bytes) -> list[str]:
    guids: list[str] = []
    try:
        outer = msgpack.unpackb(kkex_bytes, raw=False, strict_map_key=False)
    except Exception:
        return guids

    if not isinstance(outer, dict):
        return guids

    for plugin_key, plugin_data_raw in outer.items():
        key_str = plugin_key if isinstance(plugin_key, str) \
                  else plugin_key.decode("utf-8", errors="replace")
        if key_str not in UAR_EXT_IDS:
            continue

        data_dict = None
        if isinstance(plugin_data_raw, dict):
            data_dict = plugin_data_raw.get(1)
        elif isinstance(plugin_data_raw, (list, tuple)) and len(plugin_data_raw) >= 2:
            data_dict = plugin_data_raw[1]

        if not isinstance(data_dict, dict):
            continue

        info_val = data_dict.get("info")
        if not isinstance(info_val, (list, tuple)):
            continue

        for item in info_val:
            if not isinstance(item, (bytes, bytearray, memoryview)):
                continue
            try:
                resolve_info = msgpack.unpackb(bytes(item), raw=False)
                if isinstance(resolve_info, dict):
                    guid = resolve_info.get("ModID")
                    if guid:
                        guids.append(str(guid))
            except Exception:
                pass

    return guids


# ---------------------------------------------------------------------------
# Chara and coordinate GUID parsers
# ---------------------------------------------------------------------------

def parse_chara_guids(path: Path) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a chara card."""
    data = path.read_bytes()
    png_end = _find_iend_end(data)
    if png_end < 0 or png_end >= len(data):
        return []

    s = io.BytesIO(data[png_end:])
    _ri(s)                              # product_no
    marker = _read_str(s)
    if marker not in KNOWN_CHARA_MARKERS:
        return []

    _read_str(s)                        # version
    face_len = _ri(s)
    if face_len > 0:
        s.seek(face_len, io.SEEK_CUR)

    bh_len = _ri(s)
    block_header = msgpack.unpackb(s.read(bh_len), raw=False)
    lst_info = block_header.get("lstInfo", []) if isinstance(block_header, dict) else []

    blocks: dict[str, tuple[int, int]] = {}
    for info in lst_info:
        if isinstance(info, dict):
            name = info.get("name")
            if name:
                blocks[str(name)] = (int(info.get("pos", 0)), int(info.get("size", 0)))

    _rq(s)                              # total data size
    base_pos = s.tell()

    if "KKEx" not in blocks:
        return []

    pos, size = blocks["KKEx"]
    s.seek(base_pos + pos)
    return sorted(set(_extract_guids_from_kkex(s.read(size))))


def parse_coord_guids(path: Path) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a coordinate card."""
    try:
        data = path.read_bytes()
        png_end = _find_iend_end(data)
        if png_end < 0 or png_end >= len(data):
            return []

        s = io.BytesIO(data[png_end:])
        if struct.unpack("<i", s.read(4))[0] != 100:
            return []

        marker = _read_str(s)
        if marker not in KNOWN_COORD_MARKERS:
            return []

        _read_str(s)
        if "AIS" in marker:
            s.read(4)
        _read_str(s)

        blob_len = _ri(s)
        s.seek(blob_len, io.SEEK_CUR)

        try:
            kkex_marker = _read_str(s)
        except Exception:
            return []

        if kkex_marker != "KKEx":
            return []

        s.read(4)
        ext_len = _ri(s)
        if ext_len <= 0:
            return []

        return sorted(set(_extract_guids_from_kkex(s.read(ext_len))))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Coordinate matching (colour fingerprint)
# ---------------------------------------------------------------------------

def _parse_coord_outfit(path: Path) -> dict | None:
    """Parse a coordinate PNG and return its outfit data, or None."""
    try:
        data = path.read_bytes()
        png_end = _find_iend_end(data)
        if png_end < 0 or png_end >= len(data):
            return None

        s = io.BytesIO(data[png_end:])
        if struct.unpack("<i", s.read(4))[0] != 100:
            return None

        header = _rb(s)
        if b"KoiKatuClothes" not in header:
            return None

        _rb(s)                          # version
        name = _rb(s).decode("utf-8", errors="replace")
        hiroin_no = struct.unpack("<i", s.read(4))[0]

        clothes_len = struct.unpack("<i", s.read(4))[0]
        clothes     = msgpack.unpackb(s.read(clothes_len), raw=False)
        acc_len     = struct.unpack("<i", s.read(4))[0]
        acc         = msgpack.unpackb(s.read(acc_len), raw=False)

        return {"path": path, "name": name, "hiroin_no": hiroin_no,
                "clothes": clothes, "accessory": acc}
    except Exception:
        return None


def _clothes_fp(clothes: dict) -> list[tuple]:
    fp = []
    for part in clothes.get("parts", []):
        colors = []
        for ci in part.get("colorInfo", []):
            if not isinstance(ci, dict):
                continue
            colors.append((
                tuple(ci["baseColor"])    if ci.get("baseColor")    else None,
                tuple(ci["patternColor"]) if ci.get("patternColor") else None,
                ci.get("pattern"),
                tuple(ci["tiling"])       if ci.get("tiling")       else None,
            ))
        fp.append(tuple(colors))
    return fp


def _acc_fp(acc: dict) -> tuple[frozenset, dict]:
    occupied, colors = [], {}
    for i, part in enumerate(acc.get("parts", [])):
        if not isinstance(part, dict) or part.get("id", 0) == 0:
            continue
        occupied.append(i)
        colors[i] = tuple(
            (tuple(ci["baseColor"])    if ci.get("baseColor")    else None,
             tuple(ci["patternColor"]) if ci.get("patternColor") else None)
            for ci in part.get("colorInfo", [])
            if isinstance(ci, dict)
        )
    return frozenset(occupied), colors


def _coord_matches_slot(slot: dict, coord: dict, threshold: float = 0.70) -> bool:
    c_fp  = _clothes_fp(slot["clothes"])
    co_fp = _clothes_fp(coord["clothes"])
    n     = max(len(c_fp), len(co_fp), 1)
    hits  = sum(1 for a, b in zip(c_fp, co_fp) if a == b)
    if hits / n < threshold:
        return False
    c_occ, c_col   = _acc_fp(slot["accessory"])
    co_occ, co_col = _acc_fp(coord["accessory"])
    if not co_occ:
        return True
    if c_occ != co_occ:
        return False
    shared = c_occ & co_occ
    return all(c_col.get(i) == co_col.get(i) for i in shared)


def find_matching_coords(chara_coords: list[dict], coord_dir: Path,
                         use_cache: bool = False) -> list[Path]:
    """Return coord PNGs that match any slot in the chara.

    When use_cache=True, loads (or rebuilds) a JSON cache at
    coord_dir.parent/kkafio_coord_cache.json keyed by the directory mtime.
    Parsing is parallelised (I/O bound) in both cached and non-cached paths.
    """
    if not coord_dir.exists():
        return []

    if use_cache:
        coord_map = load_coord_cache(coord_dir)
        if coord_map is None:
            logger.info("CACHE", f"Building coordinate cache for {coord_dir.name}...")
            coord_map = build_coord_cache(coord_dir)
            logger.info("CACHE", f"Coordinate cache built: {len(coord_map)} files")
        else:
            logger.info("CACHE", f"Coordinate cache hit: {len(coord_map)} files")

        matched: list[Path] = []
        for path_str, fp in coord_map.items():
            p = Path(path_str)
            if not p.exists():
                continue
            cached = _outfit_from_cache(fp, p)
            for slot in chara_coords:
                if _coord_matches_slot_cached(slot, cached):
                    matched.append(p)
                    break
        return sorted(matched)

    # No cache — full parallel parse
    coord_files = sorted(coord_dir.rglob("*.png"))
    if not coord_files:
        return []

    import os
    workers = min(32, (os.cpu_count() or 4) * 2)
    matched = []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_parse_coord_outfit, png): png for png in coord_files}
        for future in as_completed(futures):
            outfit = future.result()
            if outfit is None:
                continue
            for slot in chara_coords:
                if _coord_matches_slot(slot, outfit):
                    matched.append(outfit["path"])
                    break

    return sorted(matched)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

import json as _json

MODS_CACHE_FILE  = "kkafio_mods_cache.json"
COORD_CACHE_FILE = "kkafio_coord_cache.json"
MODPACK_INDEX_FILE = "kkafio_modpack_index.json"


def load_modpack_index(mods_dir: Path | None = None) -> dict[str, str] | None:
    """Load the pre-built Sideloader Modpack GUID index.

    Searches for kkafio_modpack_index.json in:
      1. The directory of the running exe / script (shipped with the release)
      2. mods_dir itself  (user-generated, placed next to mods folder)
      3. mods_dir.parent  (game root)

    Returns {guid: relative_path_str} or None if not found.
    """
    import sys
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).resolve().parent.parent  # repo root

    candidates = [exe_dir / MODPACK_INDEX_FILE]
    if mods_dir is not None:
        candidates.append(mods_dir / MODPACK_INDEX_FILE)
        candidates.append(mods_dir.parent / MODPACK_INDEX_FILE)

    for index_path in candidates:
        if index_path.exists():
            try:
                with index_path.open("r", encoding="utf-8") as f:
                    data = _json.load(f)
                guids = data.get("guids", {})
                logger.info("CACHE", f"Modpack index loaded: {len(guids)} GUIDs from {index_path.name}")
                return guids
            except Exception as e:
                logger.warning("CACHE", f"Could not load modpack index: {e}")
    return None


def _dir_mtime(directory: Path) -> float:
    """Return the directory's own mtime (not recursive — fast, O(1))."""
    try:
        return directory.stat().st_mtime
    except Exception:
        return 0.0


def load_mods_cache(mods_dir: Path) -> dict[str, str] | None:
    """Return {guid: absolute_path} from cache if still valid, else None."""
    cache_path = mods_dir.parent / MODS_CACHE_FILE
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            data = _json.load(f)
        if data.get("mods_dir") != str(mods_dir):
            return None
        if abs(data.get("mtime", 0) - _dir_mtime(mods_dir)) > 1:
            return None
        return data["guids"]           # {guid: str(path)}
    except Exception:
        return None


def save_mods_cache(mods_dir: Path, guid_map: dict[str, str]) -> None:
    """Write {guid: str(path)} to the cache file next to mods_dir."""
    cache_path = mods_dir.parent / MODS_CACHE_FILE
    data = {
        "mods_dir": str(mods_dir),
        "mtime":    _dir_mtime(mods_dir),
        "guids":    guid_map,
    }
    try:
        with cache_path.open("w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass   # cache write failure is non-fatal


def build_mods_cache(mods_dir: Path,
                     include_modpack: bool = False) -> dict[str, str]:
    """Scan every zipmod and return {guid: str(abs_path)}, saving to cache."""
    guid_map: dict[str, str] = {}
    for zp in mods_dir.rglob("*.zipmod"):
        if not include_modpack and in_modpack_folder(zp, mods_dir):
            continue
        guid = guid_from_zipmod(zp)
        if guid:
            guid_map[guid] = str(zp)
    save_mods_cache(mods_dir, guid_map)
    return guid_map


def load_coord_cache(coord_dir: Path) -> dict[str, dict] | None:
    """Return {str(path): fingerprint_dict} from cache if still valid, else None."""
    cache_path = coord_dir.parent / COORD_CACHE_FILE
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            data = _json.load(f)
        if data.get("coord_dir") != str(coord_dir):
            return None
        if abs(data.get("mtime", 0) - _dir_mtime(coord_dir)) > 1:
            return None
        return data["coords"]          # {str(path): {clothes_fp, acc_occupied, acc_colors}}
    except Exception:
        return None


def save_coord_cache(coord_dir: Path, coord_map: dict[str, dict]) -> None:
    """Write the coordinate fingerprint map to the cache file next to coord_dir."""
    cache_path = coord_dir.parent / COORD_CACHE_FILE
    data = {
        "coord_dir": str(coord_dir),
        "mtime":     _dir_mtime(coord_dir),
        "coords":    coord_map,
    }
    try:
        with cache_path.open("w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _outfit_to_cache(outfit: dict) -> dict:
    """Convert an outfit dict to a JSON-serialisable fingerprint."""
    def _conv(v):
        if isinstance(v, (list, tuple)):
            return [_conv(i) for i in v]
        if v is None:
            return None
        return v

    return {
        "clothes_fp":   _conv(_clothes_fp(outfit["clothes"])),
        "acc_occupied": sorted(list(_acc_fp(outfit["accessory"])[0])),
        "acc_colors":   {str(k): _conv(v)
                         for k, v in _acc_fp(outfit["accessory"])[1].items()},
    }


def _outfit_from_cache(fp: dict, path: Path) -> dict:
    """Reconstruct a fake outfit dict from a cached fingerprint for matching."""
    # We rebuild minimal clothes/accessory structures that _coord_matches_slot
    # can compare.  The fingerprint is the only thing that matters.
    return {
        "_cached_fp":        fp["clothes_fp"],
        "_cached_acc_occ":   frozenset(fp["acc_occupied"]),
        "_cached_acc_col":   {int(k): tuple(tuple(c) if c else None
                                            for c in v)
                              for k, v in fp["acc_colors"].items()},
        "path": path,
    }


def _coord_matches_slot_cached(slot: dict, cached: dict,
                               threshold: float = 0.70) -> bool:
    """Match a chara slot against a cached coord fingerprint."""
    c_fp   = _clothes_fp(slot["clothes"])
    co_fp  = cached["_cached_fp"]
    n      = max(len(c_fp), len(co_fp), 1)
    hits   = sum(1 for a, b in zip(c_fp, co_fp) if list(a) == b)
    if hits / n < threshold:
        return False

    c_occ, c_col   = _acc_fp(slot["accessory"])
    co_occ = cached["_cached_acc_occ"]
    co_col = cached["_cached_acc_col"]
    if not co_occ:
        return True
    if c_occ != co_occ:
        return False
    shared = c_occ & co_occ
    return all(
        (tuple(tuple(x) if x else None for x in c_col.get(i, ())) ==
         co_col.get(i))
        for i in shared
    )


def build_coord_cache(coord_dir: Path) -> dict[str, dict]:
    """Parse every coord PNG and return {str(path): fingerprint}, saving to cache."""
    import os
    workers  = min(32, (os.cpu_count() or 4) * 2)
    coord_map: dict[str, dict] = {}

    coord_files = sorted(coord_dir.rglob("*.png"))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_parse_coord_outfit, png): png
                   for png in coord_files}
        for future in as_completed(futures):
            outfit = future.result()
            if outfit is None:
                continue
            coord_map[str(outfit["path"])] = _outfit_to_cache(outfit)

    save_coord_cache(coord_dir, coord_map)
    return coord_map


# ---------------------------------------------------------------------------
# Zipmod scanner
# ---------------------------------------------------------------------------

def guid_from_zipmod(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            manifest = next(
                (n for n in zf.namelist() if n.lower().endswith("manifest.xml")),
                None,
            )
            if not manifest:
                return None
            root = ET.fromstring(zf.read(manifest))
            el = root.find("guid")
            return el.text.strip() if el is not None and el.text else None
    except Exception:
        return None


def in_modpack_folder(zp: Path, mods_dir: Path) -> bool:
    """Return True if zp lives inside a first-level Sideloader Modpack subfolder."""
    try:
        rel = zp.relative_to(mods_dir)
    except ValueError:
        return False
    return len(rel.parts) >= 2 and _SIDELOADER_MODPACK in rel.parts[0]


def scan_mods(mods_dir: Path, required: set[str],
              include_modpack: bool = False,
              use_cache: bool = False) -> dict[str, Path]:
    """Scan mods_dir for zipmods providing the required GUIDs.

    Fast path: if kkafio_modpack_index.json exists, GUIDs found there are
    resolved instantly (no file I/O per zipmod). GUIDs not in the index are
    resolved by scanning local non-modpack folders.

    When use_cache=True, also loads/builds a mods cache for the local scan.
    """
    found: dict[str, Path] = {}
    if not mods_dir.exists():
        return found

    remaining = set(required)

    # ── Step 1: check modpack index ────────────────────────────────────────
    modpack_index = load_modpack_index(mods_dir)

    if modpack_index is not None:
        for guid in list(remaining):
            if guid not in modpack_index:
                continue
            if include_modpack:
                rel = modpack_index[guid]
                abs_path = mods_dir / rel
                if abs_path.exists():
                    found[guid] = abs_path
            # Whether included or skipped, this GUID is resolved via index
            remaining.discard(guid)
    # If no index, fall through to full local scan (original behaviour)

    if not remaining:
        return found

    # ── Step 2: local scan for GUIDs not found in the index ───────────────
    if use_cache:
        guid_str_map = load_mods_cache(mods_dir)
        if guid_str_map is None:
            logger.info("CACHE", f"Building mods cache for {mods_dir.name}...")
            guid_str_map = build_mods_cache(mods_dir, include_modpack=include_modpack)
            logger.info("CACHE", f"Mods cache built: {len(guid_str_map)} GUIDs")
        else:
            logger.info("CACHE", f"Mods cache hit: {len(guid_str_map)} GUIDs")
        for guid in remaining:
            if guid in guid_str_map:
                p = Path(guid_str_map[guid])
                if p.exists():
                    # Ensure we don't accidentally return a modpack path
                    # for a GUID that wasn't in the index (shouldn't happen
                    # if cache was built with same include_modpack, but guard
                    # against stale caches)
                    if not include_modpack and in_modpack_folder(p, mods_dir):
                        continue
                    found[guid] = p
        return found

    # No cache — scan local folders only (skip modpack folders when index
    # is present since those GUIDs were already handled above)
    skip_modpack = modpack_index is not None and not include_modpack
    for zp in mods_dir.rglob("*.zipmod"):
        if not remaining:
            break
        if in_modpack_folder(zp, mods_dir):
            if skip_modpack:
                continue
            if not include_modpack:
                continue
        guid = guid_from_zipmod(zp)
        if guid and guid in remaining:
            found[guid] = zp
            remaining.discard(guid)
    return found


# ---------------------------------------------------------------------------
# Path auto-resolution
# ---------------------------------------------------------------------------

def resolve_paths(
    chara_path: Path,
    game_base: Path,
    auto_resolve: bool,
    mods_override: Path | None,
    coord_override: Path | None,
) -> tuple[Path | None, Path | None]:
    """Return (mods_dir, coord_dir).

    auto_resolve=True  — always infer from card location; overrides ignored.
    auto_resolve=False — use explicit overrides as-is.
    """
    if not auto_resolve:
        return mods_override, coord_override

    try:
        chara_path.relative_to(game_base)
        in_game = True
    except ValueError:
        in_game = False

    if in_game:
        mods_dir  = game_base / "mods"
        coord_dir = game_base / "UserData" / "coordinate"
    else:
        mods_dir  = chara_path.parent
        coord_dir = chara_path.parent

    return (mods_dir  if mods_dir.exists()  else None,
            coord_dir if coord_dir.exists() else None)