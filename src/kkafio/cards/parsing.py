"""Reading mod GUIDs out of card files: the PNG/.NET binary helpers, the KKEx block, and the
chara / Studio-scene / coordinate GUID parsers.
"""

import io
import struct
from pathlib import Path

import msgpack

from kkafio.core.logger import logger


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


# ---------------------------------------------------------------------------
# GUID extraction from KKEx block
# ---------------------------------------------------------------------------

def _unpack_kkex(kkex_bytes: bytes) -> dict:
    """Decode a raw KKEx block into {plugin id: plugin data}, or {}."""
    try:
        outer = msgpack.unpackb(kkex_bytes, raw=False, strict_map_key=False)
    except Exception:
        return {}
    return outer if isinstance(outer, dict) else {}


def uar_resolve_infos(kkex: dict | None) -> list[dict]:
    """Decoded Sideloader UniversalAutoResolver ResolveInfo entries (ModID,
    Slot, LocalSlot, Property, ...) from a decoded KKEx dict — either
    kkloader's `kc["KKEx"].data` or `_unpack_kkex()` output."""
    infos: list[dict] = []
    for plugin_key, plugin_data_raw in (kkex or {}).items():
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
            except Exception as e:
                logger.debug("CARD", f"Skipping undecodable UAR ResolveInfo entry: {e}")
                continue
            if isinstance(resolve_info, dict):
                infos.append(resolve_info)
    return infos


def _extract_guids_from_kkex(kkex_bytes: bytes) -> list[str]:
    return [str(i["ModID"]) for i in uar_resolve_infos(_unpack_kkex(kkex_bytes))
            if i.get("ModID")]


# ---------------------------------------------------------------------------
# Chara and coordinate GUID parsers
# ---------------------------------------------------------------------------

def _payload_after_png(path: Path) -> bytes | None:
    """Read `path` and return the raw bytes appended after the PNG's own
    IEND chunk — the game's custom card/scene/coordinate payload that every
    parse_*_guids()/parse_coord_outfit() function starts from. This exact
    read-file + find-IEND-end + slice sequence used to be repeated at the
    top of each of those functions; factored out here so there's one place
    that knows how a KKAFIO-relevant PNG is structured, rather than several
    copies that could individually drift (e.g. one checking `>=` and
    another `>`) without anyone noticing.
    """
    data = path.read_bytes()
    png_end = _find_iend_end(data)
    if png_end < 0 or png_end >= len(data):
        return None
    return data[png_end:]


def parse_chara_guids(path: Path) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a chara card."""
    payload = _payload_after_png(path)
    if payload is None:
        return []

    s = io.BytesIO(payload)
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


_SCENE_UNPACK_START = 64 * 1024        # first attempt: enough for almost every UAR value

_SCENE_UNPACK_MAX   = 20_000_000       # never feed more than this per occurrence

_SCENE_UNPACK_GROW  = 8


def _unpack_value_at(mv: memoryview, start: int):
    """Decode the single msgpack value that begins at `mv[start:]`.

    msgpack values are self-delimiting but `Unpacker.feed()` COPIES what it is
    given, so feeding a fixed 20 MB window for every occurrence of the needle
    (a scene with many actors/objects has dozens) copied tens of MB per hit
    even though the value itself is usually a few KB. Instead, start with a
    small window and grow it only when the decoder reports the value runs past
    the end of what it was given (OutOfData).

    Returns the decoded value, or None if nothing decodable starts here.
    """
    size = _SCENE_UNPACK_START
    total = len(mv)
    while True:
        end = min(start + size, total, start + _SCENE_UNPACK_MAX)
        unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
        unpacker.feed(mv[start:end])
        try:
            return unpacker.unpack()
        except msgpack.OutOfData:
            if end >= total or end - start >= _SCENE_UNPACK_MAX:
                return None                 # value truncated by EOF / too large
            size *= _SCENE_UNPACK_GROW      # retry with a bigger window
        except Exception:
            return None                     # not msgpack at this offset


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

    `data.find()` scans the buffer in C. Each hit is decoded through
    `_unpack_value_at`, which only ever copies as much of the buffer as the
    value actually needs (see its docstring).
    """
    guids: list[str] = []
    mv = memoryview(data)
    for ext_id in UAR_EXT_IDS:
        needle = ext_id.encode("utf-8")
        search_from = 0
        while True:
            idx = data.find(needle, search_from)
            if idx < 0:
                break
            search_from = idx + len(needle)
            value_start = idx + len(needle)
            plugin_data_raw = _unpack_value_at(mv, value_start)

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
                except Exception as e:
                    logger.debug("CARD", f"Skipping undecodable scene ResolveInfo entry: {e}")

    return guids


def parse_scene_guids(path: Path) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a Studio scene file."""
    try:
        payload = _payload_after_png(path)
        if payload is None:
            return []
        return sorted(set(_extract_guids_from_scene_blob(payload)))
    except Exception:
        return []


def _coord_kkex_bytes(payload: bytes) -> bytes | None:
    """Raw KKEx block of a coordinate card, given the bytes after its PNG.
    None if the payload isn't a coordinate card or carries no KKEx."""
    s = io.BytesIO(payload)
    if struct.unpack("<i", s.read(4))[0] != 100:
        return None

    marker = _read_str(s)
    if marker not in KNOWN_COORD_MARKERS:
        return None

    _read_str(s)
    if "AIS" in marker:
        s.read(4)
    _read_str(s)

    blob_len = _ri(s)
    s.seek(blob_len, io.SEEK_CUR)

    try:
        kkex_marker = _read_str(s)
    except Exception:
        return None
    if kkex_marker != "KKEx":
        return None

    s.read(4)
    ext_len = _ri(s)
    if ext_len <= 0:
        return None
    return s.read(ext_len)


def parse_coord_guids(path: Path) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a coordinate card."""
    try:
        payload = _payload_after_png(path)
        kkex = _coord_kkex_bytes(payload) if payload is not None else None
        return sorted(set(_extract_guids_from_kkex(kkex))) if kkex else []
    except Exception:
        return []
