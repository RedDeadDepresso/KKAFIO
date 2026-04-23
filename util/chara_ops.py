"""
util/chara_ops.py — Shared helpers for archive_chara and delete_chara.

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


def find_matching_coords(chara_coords: list[dict], coord_dir: Path) -> list[Path]:
    """Return coord PNGs that match any slot in the chara, using a thread pool
    to parse coord files concurrently (I/O bound).

    Order of results is non-deterministic during parsing but the final list is
    sorted by path for reproducibility.
    """
    coord_files = sorted(coord_dir.rglob("*.png"))
    if not coord_files:
        return []

    import os
    workers = min(32, (os.cpu_count() or 4) * 2)

    matched: list[Path] = []

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
              include_modpack: bool = False) -> dict[str, Path]:
    """Scan mods_dir for zipmods providing the required GUIDs."""
    found: dict[str, Path] = {}
    if not mods_dir.exists():
        return found
    for zp in mods_dir.rglob("*.zipmod"):
        if len(found) == len(required):
            break
        if not include_modpack and in_modpack_folder(zp, mods_dir):
            continue
        guid = guid_from_zipmod(zp)
        if guid and guid in required:
            found[guid] = zp
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
