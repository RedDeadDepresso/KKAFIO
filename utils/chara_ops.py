"""
utils/chara_ops.py — Shared helpers for archive_cards and delete_cards.

Extracted here to avoid cross-module imports and keep each task module focused
on its own logic.  Nothing in this file depends on config or file_manager.
"""



import io
import struct
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import cache
from pathlib import Path

import msgpack

from utils.config import GameType
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


def _extract_guids_from_scene_blob(data: bytes) -> list[str]:
    """Scan a Studio scene payload for Sideloader UAR GUID references.

    Unlike chara/coordinate cards, a Studio scene does not store a single
    well-defined KKEx block — extended-save plugin data is embedded once per
    actor/object scattered throughout the file, and the exact scene binary
    layout is not something we parse elsewhere in this codebase. Instead we
    scan the raw bytes for the UAR extension-id strings (which appear as
    literal UTF-8 msgpack map keys) and msgpack-decode the value that
    immediately follows each occurrence. Since msgpack values are
    self-delimiting, this works regardless of where the enclosing dictionary
    actually starts or ends.
    """
    guids: list[str] = []
    for ext_id in UAR_EXT_IDS:
        needle = ext_id.encode("utf-8")
        search_from = 0
        while True:
            idx = data.find(needle, search_from)
            if idx < 0:
                break
            search_from = idx + 1
            value_start = idx + len(needle)
            try:
                unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
                unpacker.feed(data[value_start:value_start + 20_000_000])
                plugin_data_raw = unpacker.unpack()
            except Exception:
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


def parse_scene_guids(path: Path) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a Studio scene file."""
    try:
        data = path.read_bytes()
        png_end = _find_iend_end(data)
        if png_end < 0 or png_end >= len(data):
            return []
        return sorted(set(_extract_guids_from_scene_blob(data[png_end:])))
    except Exception:
        return []


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
                         use_cache: bool = False,
                         coord_map: dict[str, dict] | None = None) -> list[Path]:
    """Return coord PNGs that match any slot in the chara.

    When use_cache=True (and coord_map isn't already supplied), loads or
    incrementally rebuilds a JSON cache at coord_dir/kkafio_coord_cache.json.
    Parsing is parallelised (I/O bound) in both cached and non-cached paths.

    Pass a pre-built `coord_map` (from build_coord_cache()) to skip loading/
    building it here entirely — useful for callers processing many chara
    cards against the same coord_dir in one run, so the folder only needs
    to be scanned once instead of once per card.
    """
    if not coord_dir.exists():
        return []

    if coord_map is not None or use_cache:
        if coord_map is None:
            coord_map = build_coord_cache(coord_dir, use_cache=True)

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
MODPACK_INDEX_FILE_KK  = "kkafio_modpack_index_kk.json"
MODPACK_INDEX_FILE_KKS = "kkafio_modpack_index_kks.json"


@cache
def load_modpack_index(game_type: str = GameType.KOIKATSU.value) -> dict[str, str] | None:
    """Load the pre-built Sideloader Modpack GUID index for the given game type.

    Uses kkafio_modpack_index_kks.json for KoikatsuSunshine,
    and kkafio_modpack_index_kk.json for all other variants.

    Looked up at assets/<index_file> next to the running exe / script (this
    is always where it's shipped with the release — see docs/01-project-
    structure.md).

    Returns {guid: relative_path_str} or None if not found.

    Cached (per game_type) for the lifetime of the process — the index file
    doesn't change mid-run, and this is otherwise re-read and re-parsed
    from disk on every call (e.g. once per card in DeleteCards, which calls
    scan_mods() → load_modpack_index() per file).
    """
    import sys

    index_file = (
        MODPACK_INDEX_FILE_KKS
        if game_type == GameType.KOIKATSU_SUNSHINE.value
        else MODPACK_INDEX_FILE_KK
    )

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).resolve().parent.parent  # repo root

    index_path = exe_dir / "assets" / "data" / index_file

    if index_path.exists():
        try:
            with index_path.open("r", encoding="utf-8") as f:
                data = _json.load(f)
            guids = data.get("guids", {})
            logger.info("CACHE",
                f"Modpack index loaded: {len(guids)} GUIDs from {index_path.name}")
            return guids
        except Exception as e:
            logger.warning("CACHE", f"Could not load modpack index: {e}")
    return None


# ---------------------------------------------------------------------------
# File fingerprint helpers
# ---------------------------------------------------------------------------

def _file_fp(p: Path) -> tuple[int, int]:
    """Return (mtime_int, size) for a file — used as a change fingerprint."""
    try:
        st = p.stat()
        return (int(st.st_mtime), st.st_size)
    except OSError:
        return (0, 0)


# ---------------------------------------------------------------------------
# Generic incremental PNG GUID collector (chara / scene / coordinate)
# ---------------------------------------------------------------------------
#
# Shared by any task that needs "every mod GUID referenced by every card of a
# given type in a folder" — e.g. DownloadMissingMods (to find what's missing)
# and DeleteCards (to find what's still in use elsewhere before deleting a
# zipmod). One cache file per content type, keyed by the folder set being
# scanned, so the three tasks can all reuse the same on-disk cache.

CHARA_GUID_CACHE_FILE = "kkafio_chara_guid_cache.json"
SCENE_GUID_CACHE_FILE = "kkafio_scene_guid_cache.json"
COORD_GUID_CACHE_FILE = "kkafio_coord_guid_cache.json"


def collect_png_guids(
    dirs: list[Path],
    use_cache: bool,
    cache_file: str,
    label: str,
    is_valid,
    parse_guids,
) -> tuple[set[str], dict[str, list[str]]]:
    """Incrementally collect GUIDs from a set of PNG-based card files.

    Unchanged PNG files (same mtime + size) reuse their cached GUIDs.
    Only new or changed PNGs are fully read. Shared implementation used for
    chara cards, Studio scenes, and coordinate cards — `is_valid(raw_bytes)`
    decides whether a given PNG belongs to this collector's content type,
    and `parse_guids` extracts the GUIDs from files that pass.

    The cache file lives inside `dirs[0]`. When `use_cache` is False, this
    does a full scan every time and does not read or write the cache — the
    complete per-file scan still happens either way, so the returned
    `guids_by_file` is always accurate for every currently-existing file
    in `dirs`, regardless of the `use_cache` setting.

    Returns (guids, guids_by_file):
      guids         — the union of every GUID across every file
      guids_by_file — {absolute_path_str: [guid, ...]} for every file, so
                      callers can exclude specific files (e.g. cards about
                      to be deleted) from the union after the fact, instead
                      of needing a separate uncached scan that skips them.
    """
    if not dirs:
        return set(), {}

    key        = "|".join(str(d) for d in dirs)
    cache_path = dirs[0] / cache_file

    old_files:  dict = {}
    old_guids_by_file: dict[str, list[str]] = {}

    if use_cache:
        try:
            prev = _json.loads(cache_path.read_text(encoding="utf-8"))
            if prev.get("dirs") == key:
                prev_files         = {sp: fp for sp, fp in prev.get("files", {}).items()
                                      if isinstance(fp, list) and len(fp) == 2}
                prev_guids_by_file = prev.get("guids_by_file", {})

                if prev_files:
                    old_files         = prev_files
                    old_guids_by_file = prev_guids_by_file
                    logger.info("CACHE", f"{label} cache loaded: {len(old_files)} file fingerprints")
                else:
                    logger.info("CACHE", f"{label} cache empty — building for the first time")
        except Exception:
            pass

    all_pngs: list[Path] = []
    for d in dirs:
        if d.exists():
            all_pngs.extend(d.rglob("*.png"))

    guids:     set[str]        = set()
    new_files: dict            = {}
    new_guids_by_file: dict[str, list[str]] = {}
    to_read:   list[Path]      = []

    for png in all_pngs:
        sp = str(png)
        fp = _file_fp(png)
        old = old_files.get(sp)
        if old is not None and (old[0], old[1]) == fp and sp in old_guids_by_file:
            file_guids = old_guids_by_file[sp]
            guids.update(file_guids)
            new_files[sp] = old
            new_guids_by_file[sp] = file_guids
        else:
            to_read.append(png)

    reused = len(all_pngs) - len(to_read)
    if reused:
        logger.info("CACHE", f"{label} cache: {reused} unchanged, {len(to_read)} new/changed")
    else:
        logger.info("CACHE", f"Scanning {len(to_read)} {label.lower()}(s) for mod GUIDs...")

    import os
    workers = min(32, (os.cpu_count() or 4) * 2)

    def _proc(png: Path):
        try:
            raw = png.read_bytes()
            if is_valid(raw):
                return png, [g for g in parse_guids(png) if g]
        except Exception:
            pass
        return png, None

    if to_read:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for future in as_completed({ex.submit(_proc, png): png for png in to_read}):
                png, file_guids = future.result()
                if file_guids is None:
                    continue
                sp = str(png)
                fp = _file_fp(png)
                guids.update(file_guids)
                new_files[sp] = [fp[0], fp[1]]
                new_guids_by_file[sp] = file_guids

    if use_cache:
        # Store guids_by_file for per-file incremental reuse next run
        data = {
            "dirs":           key,
            "file_count":     len(new_files),
            "guids":          sorted(guids),
            "files":          new_files,
            "guids_by_file":  new_guids_by_file,
        }
        try:
            cache_path.write_text(
                _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info("CACHE", f"{label} cache saved: {len(guids)} GUIDs from {len(new_files)} files")
        except Exception:
            pass
    else:
        logger.info("CACHE", f"{label} scan complete: {len(guids)} GUIDs")

    return guids, new_guids_by_file


def collect_chara_guids(chara_dirs: list[Path], use_cache: bool) -> set[str]:
    """Incrementally collect GUIDs referenced by chara cards in chara_dirs."""
    from utils.classifier import CardType, get_card_type
    guids, _ = collect_png_guids(
        chara_dirs, use_cache, CHARA_GUID_CACHE_FILE, "Chara",
        lambda raw: get_card_type(raw) in (CardType.KK, CardType.KKSP, CardType.KKS),
        parse_chara_guids,
    )
    return guids


def collect_chara_guids_by_file(chara_dirs: list[Path], use_cache: bool) -> dict[str, list[str]]:
    """Same as collect_chara_guids, but returns the per-file GUID mapping
    ({absolute_path_str: [guid, ...]}) instead of the aggregated set."""
    from utils.classifier import CardType, get_card_type
    _, by_file = collect_png_guids(
        chara_dirs, use_cache, CHARA_GUID_CACHE_FILE, "Chara",
        lambda raw: get_card_type(raw) in (CardType.KK, CardType.KKSP, CardType.KKS),
        parse_chara_guids,
    )
    return by_file


def collect_scene_guids(scene_dirs: list[Path], use_cache: bool) -> set[str]:
    """Incrementally collect GUIDs referenced by Studio scenes in scene_dirs."""
    from utils.classifier import CardType, get_card_type
    guids, _ = collect_png_guids(
        scene_dirs, use_cache, SCENE_GUID_CACHE_FILE, "Scene",
        lambda raw: get_card_type(raw) == CardType.SCENE,
        parse_scene_guids,
    )
    return guids


def collect_scene_guids_by_file(scene_dirs: list[Path], use_cache: bool) -> dict[str, list[str]]:
    """Same as collect_scene_guids, but returns the per-file GUID mapping
    ({absolute_path_str: [guid, ...]}) instead of the aggregated set."""
    from utils.classifier import CardType, get_card_type
    _, by_file = collect_png_guids(
        scene_dirs, use_cache, SCENE_GUID_CACHE_FILE, "Scene",
        lambda raw: get_card_type(raw) == CardType.SCENE,
        parse_scene_guids,
    )
    return by_file


def collect_coord_guids(coord_dirs: list[Path], use_cache: bool) -> set[str]:
    """Incrementally collect GUIDs referenced by coordinate cards in coord_dirs."""
    from utils.classifier import CardType, get_card_type, is_coordinate
    guids, _ = collect_png_guids(
        coord_dirs, use_cache, COORD_GUID_CACHE_FILE, "Coord",
        lambda raw: get_card_type(raw) == CardType.UNKNOWN and is_coordinate(raw),
        parse_coord_guids,
    )
    return guids


def collect_coord_guids_by_file(coord_dirs: list[Path], use_cache: bool) -> dict[str, list[str]]:
    """Same as collect_coord_guids, but returns the per-file GUID mapping
    ({absolute_path_str: [guid, ...]}) instead of the aggregated set."""
    from utils.classifier import CardType, get_card_type, is_coordinate
    _, by_file = collect_png_guids(
        coord_dirs, use_cache, COORD_GUID_CACHE_FILE, "Coord",
        lambda raw: get_card_type(raw) == CardType.UNKNOWN and is_coordinate(raw),
        parse_coord_guids,
    )
    return by_file


# ---------------------------------------------------------------------------
# Mods cache  (incremental)
# ---------------------------------------------------------------------------

def save_mods_cache(mods_dir: Path, guid_map: dict[str, str],
                    files: dict | None = None) -> None:
    """Persist {guid: str(path)} (and optional file fingerprints) to cache."""
    cache_path = mods_dir / MODS_CACHE_FILE
    data: dict = {
        "mods_dir":   str(mods_dir),
        "file_count": len(files) if files is not None else len(guid_map),
        "guids":      guid_map,
    }
    if files is not None:
        data["files"] = files
    try:
        cache_path.write_text(
            _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def build_mods_cache(mods_dir: Path, include_modpack: bool = False,
                     use_cache: bool = True) -> dict[str, str]:
    """Incrementally scan zipmods and return {guid: str(abs_path)}.

    Unchanged files (same mtime + size) are reused from the previous cache.
    Only new or changed zipmods are opened. Deleted files (among the ones
    actually in scope for this call's include_modpack setting) are pruned
    automatically — callers never need a separate staleness check before
    calling this.

    When `use_cache` is False, does a full scan every time and does not
    read or write the cache file.

    The cache file is shared between include_modpack=True and
    include_modpack=False callers on the same mods_dir. Saved entries for
    files outside the *current* call's filter (e.g. Sideloader Modpack
    zipmods during an include_modpack=False call) are carried forward
    untouched rather than dropped, so alternating between the two modes
    across different tasks never forces a full rescan of "the other side"
    — each mode's own cached entries persist until that mode is the one
    actually looking at them again.
    """
    cache_path = mods_dir / MODS_CACHE_FILE
    old_files: dict = {}
    if use_cache:
        try:
            prev = _json.loads(cache_path.read_text(encoding="utf-8"))
            if prev.get("mods_dir") == str(mods_dir):
                old_files = {sp: fp for sp, fp in prev.get("files", {}).items()
                             if isinstance(fp, list) and len(fp) == 3}
        except Exception:
            pass

    # Only iterate files actually present on disk — deleted files are implicitly pruned
    all_zips = [
        zp for zp in mods_dir.rglob("*.zipmod")
        if include_modpack or not in_modpack_folder(zp, mods_dir)
    ]
    all_zip_strs = {str(zp) for zp in all_zips}

    guid_map:  dict[str, str] = {}
    # Carry forward everything already cached — including entries this
    # call's filter doesn't cover — so they aren't lost when we save below.
    new_files: dict       = dict(old_files)
    to_read:   list[Path] = []

    for zp in all_zips:
        sp = str(zp)
        fp = _file_fp(zp)
        old = old_files.get(sp)
        if old is not None and (old[0], old[1]) == fp:
            guid = old[2]
            if guid:
                guid_map[guid] = sp
        else:
            to_read.append(zp)

    # Prune stale entries for files that no longer exist — but only among
    # ones that would have been in scope for this call's filter. An entry
    # outside that scope (e.g. a modpack zipmod during an
    # include_modpack=False call) is left alone entirely; if it was
    # actually deleted, the next call that has it in scope will notice.
    for sp in list(new_files.keys()):
        if sp in all_zip_strs:
            continue
        p = Path(sp)
        try:
            in_scope = include_modpack or not in_modpack_folder(p, mods_dir)
        except Exception:
            in_scope = True
        if in_scope and not p.exists():
            del new_files[sp]

    reused = len(all_zips) - len(to_read)
    if reused:
        logger.info("CACHE", f"Mods cache: {reused} unchanged, {len(to_read)} new/changed")
    elif to_read:
        logger.info("CACHE", f"Scanning {len(to_read)} zipmod(s) for GUIDs...")

    import os
    workers = min(32, (os.cpu_count() or 4) * 2)

    def _proc(zp: Path):
        return zp, guid_from_zipmod(zp)

    if to_read:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for future in as_completed({ex.submit(_proc, zp): zp for zp in to_read}):
                zp, guid = future.result()
                sp = str(zp)
                fp = _file_fp(zp)
                new_files[sp] = [fp[0], fp[1], guid]
                if guid:
                    guid_map[guid] = sp

    if use_cache:
        save_mods_cache(mods_dir, guid_map, new_files)
    return guid_map


# ---------------------------------------------------------------------------
# Coord cache  (incremental)
# ---------------------------------------------------------------------------

def save_coord_cache(coord_dir: Path, coord_map: dict[str, dict],
                     files: dict | None = None) -> None:
    """Persist coordinate fingerprints (and file fingerprints) to cache."""
    cache_path = coord_dir / COORD_CACHE_FILE
    data: dict = {
        "coord_dir":  str(coord_dir),
        "file_count": len(files) if files is not None else len(coord_map),
        "coords":     coord_map,
    }
    if files is not None:
        data["files"] = files
    try:
        cache_path.write_text(
            _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
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


def _norm(v):
    """Recursively convert lists/tuples to tuples so a fingerprint compares
    equal whether it came straight from msgpack (tuples/lists mixed) or
    round-tripped through JSON (lists only)."""
    if isinstance(v, (list, tuple)):
        return tuple(_norm(i) for i in v)
    return v


def _outfit_from_cache(fp: dict, path: Path) -> dict:
    """Reconstruct a fake outfit dict from a cached fingerprint for matching.

    Everything is normalised with _norm() so it can be compared against a
    live slot fingerprint built by _clothes_fp()/_acc_fp() (which use tuples).
    """
    return {
        "_cached_fp":        _norm(fp["clothes_fp"]),
        "_cached_acc_occ":   frozenset(fp["acc_occupied"]),
        "_cached_acc_col":   {int(k): _norm(v) for k, v in fp["acc_colors"].items()},
        "path": path,
    }


def _coord_matches_slot_cached(slot: dict, cached: dict,
                               threshold: float = 0.70) -> bool:
    """Match a chara slot against a cached coord fingerprint."""
    c_fp   = _norm(_clothes_fp(slot["clothes"]))
    co_fp  = cached["_cached_fp"]
    n      = max(len(c_fp), len(co_fp), 1)
    hits   = sum(1 for a, b in zip(c_fp, co_fp) if a == b)
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
    return all(_norm(c_col.get(i, ())) == co_col.get(i, ()) for i in shared)


def build_coord_cache(coord_dir: Path, use_cache: bool = True) -> dict[str, dict]:
    """Incrementally parse coord PNGs and return {str(path): fingerprint}.

    Unchanged files (same mtime + size) are reused from the previous cache.
    Only new or changed PNGs are fully parsed. Deleted files are pruned
    automatically, since only files actually present on disk are scanned —
    callers never need a separate staleness check before calling this.

    When `use_cache` is False, does a full scan every time and does not
    read or write the cache file.
    """
    import os

    cache_path = coord_dir / COORD_CACHE_FILE
    old_files:  dict = {}
    old_coords: dict = {}
    if use_cache:
        try:
            prev = _json.loads(cache_path.read_text(encoding="utf-8"))
            if prev.get("coord_dir") == str(coord_dir):
                old_files  = {sp: fp for sp, fp in prev.get("files",  {}).items()
                              if isinstance(fp, list) and len(fp) == 2}
                old_coords = prev.get("coords", {})
        except Exception:
            pass

    all_pngs  = sorted(coord_dir.rglob("*.png"))
    coord_map: dict[str, dict] = {}
    new_files: dict            = {}
    to_parse:  list[Path]      = []

    for png in all_pngs:
        sp = str(png)
        fp = _file_fp(png)
        old = old_files.get(sp)
        if old is not None and (old[0], old[1]) == fp and sp in old_coords:
            coord_map[sp] = old_coords[sp]
            new_files[sp] = old
        else:
            to_parse.append(png)

    reused = len(all_pngs) - len(to_parse)
    if reused:
        logger.info("CACHE", f"Coord cache: {reused} unchanged, {len(to_parse)} new/changed")
    elif to_parse:
        logger.info("CACHE", f"Parsing {len(to_parse)} coordinate PNG(s)...")

    workers = min(32, (os.cpu_count() or 4) * 2)
    if to_parse:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_parse_coord_outfit, png): png for png in to_parse}
            for future in as_completed(futures):
                outfit = future.result()
                if outfit is None:
                    continue
                png = outfit["path"]
                sp  = str(png)
                fp  = _file_fp(png)
                new_files[sp] = [fp[0], fp[1]]
                coord_map[sp] = _outfit_to_cache(outfit)

    if use_cache:
        save_coord_cache(coord_dir, coord_map, new_files)
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
              use_cache: bool = False,
              game_type: str = GameType.KOIKATSU.value) -> dict[str, Path]:
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
    modpack_index = load_modpack_index(game_type=game_type)

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
        guid_str_map = build_mods_cache(mods_dir, include_modpack=include_modpack, use_cache=True)
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