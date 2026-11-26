"""Reading mod GUIDs out of card files: the PNG/.NET binary helpers, the KKEx block, and the
chara / Studio-scene / coordinate GUID parsers.
"""

import io
import struct
from contextlib import contextmanager
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


def _read_str(s: io.BytesIO, max_len: int | None = None) -> str:
    n = _read_7bit_int(s)
    if max_len is not None and n > max_len:
        raise ValueError(f"string length {n} exceeds {max_len}")
    return s.read(n).decode("utf-8", errors="replace")


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


# ---------------------------------------------------------------------------
# Reading only the UAR entries of a KKEx block
# ---------------------------------------------------------------------------
#
# A KKEx block is one msgpack map {plugin id: plugin data}. Only the Sideloader
# UniversalAutoResolver entry matters for mod GUIDs, but the block also carries every
# other extended-save plugin's data (KSOX / Material Editor textures, for instance, can
# be megabytes of `bin`). msgpack values are length-prefixed, so a value can be stepped
# over by reading its small header and seek()ing past the payload.

_MP_FIXED = {0xca: 4, 0xcb: 8, 0xcc: 1, 0xcd: 2, 0xce: 4, 0xcf: 8,
             0xd0: 1, 0xd1: 2, 0xd2: 4, 0xd3: 8,
             0xd4: 2, 0xd5: 3, 0xd6: 5, 0xd7: 9, 0xd8: 17}     # fixext: 1 type byte + data
_MP_LEN_PREFIXED = {0xc4: (1, 0), 0xc5: (2, 0), 0xc6: (4, 0),   # bin 8/16/32
                    0xc7: (1, 1), 0xc8: (2, 1), 0xc9: (4, 1),   # ext 8/16/32 (+1 type byte)
                    0xd9: (1, 0), 0xda: (2, 0), 0xdb: (4, 0)}   # str 8/16/32


def _rd(s, n: int) -> bytes:
    data = s.read(n)
    if len(data) < n:
        raise EOFError("truncated msgpack data")
    return data


def _mp_skip(s) -> None:
    """Advance `s` past one msgpack value without reading its string/bin/ext payload."""
    pending = 1                                  # values still to step over (maps/arrays add more)
    while pending:
        pending -= 1
        b = _rd(s, 1)[0]
        if b <= 0x7f or b >= 0xe0 or b in (0xc0, 0xc2, 0xc3):
            continue
        if b <= 0x8f:
            pending += 2 * (b & 0x0f)            # fixmap
        elif b <= 0x9f:
            pending += b & 0x0f                  # fixarray
        elif b <= 0xbf:
            s.seek(b & 0x1f, io.SEEK_CUR)        # fixstr
        elif b in _MP_FIXED:
            s.seek(_MP_FIXED[b], io.SEEK_CUR)
        elif b in _MP_LEN_PREFIXED:
            width, extra = _MP_LEN_PREFIXED[b]
            n = int.from_bytes(_rd(s, width), "big")
            s.seek(n + extra, io.SEEK_CUR)
        elif b in (0xdc, 0xdd):
            pending += int.from_bytes(_rd(s, 2 if b == 0xdc else 4), "big")
        elif b in (0xde, 0xdf):
            pending += 2 * int.from_bytes(_rd(s, 2 if b == 0xde else 4), "big")
        else:
            raise ValueError(f"invalid msgpack type byte 0x{b:02x}")


def _mp_key(s) -> str | None:
    """Read a map key that is a str or bin and return it as text; step over any other key
    type and return None."""
    b = _rd(s, 1)[0]
    if 0xa0 <= b <= 0xbf:
        n = b & 0x1f
    elif b in (0xd9, 0xda, 0xdb, 0xc4, 0xc5, 0xc6):
        n = int.from_bytes(_rd(s, _MP_LEN_PREFIXED[b][0]), "big")
    else:
        s.seek(-1, io.SEEK_CUR)
        _mp_skip(s)
        return None
    return _rd(s, n).decode("utf-8", errors="replace")


def _mp_map_header(count: int) -> bytes:
    if count < 16:
        return bytes([0x80 | count])
    if count < 0x10000:
        return b"\xde" + struct.pack(">H", count)
    return b"\xdf" + struct.pack(">I", count)


def _read_kkex_block(s, size: int, only_uar: bool = True) -> bytes:
    """The KKEx block that starts at the current position of `s` and is `size` bytes long.

    With `only_uar`, the result is a smaller KKEx map holding just the Sideloader
    UniversalAutoResolver entries (same format, so every consumer works unchanged); the
    other plugins' values are skipped with seek(). Anything unexpected — not a map, a
    truncated or invalid value — falls back to reading the whole block."""
    start = s.tell()
    if not only_uar:
        return s.read(size)
    end = start + size
    try:
        head = _rd(s, 1)[0]
        if 0x80 <= head <= 0x8f:
            n = head & 0x0f
        elif head in (0xde, 0xdf):
            n = int.from_bytes(_rd(s, 2 if head == 0xde else 4), "big")
        else:
            raise ValueError("KKEx is not a msgpack map")
        kept: list[bytes] = []
        for _ in range(n):
            key = _mp_key(s)
            value_start = s.tell()
            _mp_skip(s)
            if s.tell() > end:
                raise EOFError("value runs past the end of the KKEx block")
            if key in UAR_EXT_IDS:
                value_end = s.tell()
                s.seek(value_start)
                kept.append(msgpack.packb(key) + _rd(s, value_end - value_start))
        return _mp_map_header(len(kept)) + b"".join(kept)
    except Exception as e:
        logger.debug("CARD", f"KKEx: reading it whole ({type(e).__name__}: {e})")
        s.seek(start)
        return s.read(size)


def _extract_guids_from_kkex(kkex_bytes: bytes) -> list[str]:
    return [str(i["ModID"]) for i in uar_resolve_infos(_unpack_kkex(kkex_bytes))
            if i.get("ModID")]


# ---------------------------------------------------------------------------
# Chara and coordinate GUID parsers
# ---------------------------------------------------------------------------

_HEAD_BUFFER = 512      # small read buffer: the readers below hop around the file with seek()


def _payload_span(f) -> tuple[int, int] | None:
    """(offset, length) of the bytes after the PNG's IEND chunk in the open file `f`, or None
    if there are none. Only the 8-byte chunk headers are read; the image data is skipped."""
    size = f.seek(0, io.SEEK_END)
    pos = 8                                 # same as _find_iend_end: signature isn't checked
    while pos + 12 <= size:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return None
        length = struct.unpack(">I", head[:4])[0]
        pos += 12 + length
        if head[4:8] == PNG_IEND:
            return (pos, size - pos) if pos < size else None
    return None


class _PayloadStream:
    """Read-only, seekable view of the payload of an open card file (offsets are relative to
    the first byte after the PNG). Same read/seek/tell behaviour as `io.BytesIO(payload)` for
    what the parsers use, but only the bytes actually requested are read from disk."""

    def __init__(self, f, start: int, size: int):
        self._f, self._start, self._size, self._pos = f, start, size, 0

    def read(self, n: int = -1) -> bytes:
        left = self._size - self._pos
        if left <= 0:
            return b""
        n = left if n is None or n < 0 else min(n, left)
        self._f.seek(self._start + self._pos)
        data = self._f.read(n)
        self._pos += len(data)
        return data

    def readinto(self, buf) -> int:
        """Fill `buf` from the current position (up to its length, or the end of the payload)."""
        left = self._size - self._pos
        if left <= 0:
            return 0
        view = memoryview(buf)
        if len(view) > left:
            view = view[:left]
        self._f.seek(self._start + self._pos)
        n = self._f.readinto(view) or 0
        self._pos += n
        return n

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        if base + offset < 0:
            raise ValueError("negative seek position")
        self._pos = base + offset           # may point past the end; read() then returns b""
        return self._pos

    def tell(self) -> int:
        return self._pos


@contextmanager
def _open_payload(path: Path):
    """Yield a `_PayloadStream` over the bytes after the PNG in `path`, or None if it has none."""
    with open(path, "rb", buffering=_HEAD_BUFFER) as f:
        span = _payload_span(f)
        yield _PayloadStream(f, *span) if span else None


def _payload_after_png(path: Path) -> bytes | None:
    """Return the raw bytes appended after the PNG's own IEND chunk — the game's
    custom card/scene/coordinate payload that every parse_*_guids() function
    starts from — or None if the file has no such payload.

    The PNG image itself (usually the bulk of the file) is never read: the chunk
    headers are walked with seek(), then only the payload is read. Same result as
    reading the whole file and slicing after `_find_iend_end`, for far less I/O.

    Chara and coordinate cards need not even this: see `scan_chara_guids` /
    `scan_coord_guids`, which read only the header and the KKEx block.
    """
    with _open_payload(path) as s:
        return s.read() if s is not None else None


def payload_of(raw: bytes) -> bytes | None:
    """The bytes after the PNG's IEND chunk in an already-read card file (what
    `_payload_after_png` reads from disk), or None if there are none."""
    end = _find_iend_end(raw)
    return raw[end:] if 0 <= end < len(raw) else None


_MARKER_MAX_LEN = 256   # a real marker is ~20 bytes; anything longer means "not a card"


def _chara_kkex_from_stream(s, only_uar: bool = True) -> tuple[str, bytes | None] | None:
    """(marker, KKEx block or None) of a chara card, given a stream over its payload.
    None if the payload doesn't start with a known chara marker.

    Only the header, the block index and the KKEx block are read: the face image and every
    other block (Custom, Coordinate, Parameter, Status, ...) are skipped with seek(), and
    inside KKEx so is every plugin but UniversalAutoResolver (see `_read_kkex_block`)."""
    _ri(s)                              # product_no
    marker = _read_str(s, _MARKER_MAX_LEN)
    if marker not in KNOWN_CHARA_MARKERS:
        return None

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
        return marker, None

    pos, size = blocks["KKEx"]
    s.seek(base_pos + pos)
    return marker, _read_kkex_block(s, size, only_uar)


def parse_chara_guids(path: Path, payload: bytes | None = None) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a chara card.

    `payload` is the bytes after the PNG (see `_payload_after_png`) when the caller
    has already read them, so the file isn't read a second time. Without it, only the
    header and the KKEx block are read from disk."""
    if payload is not None:
        found = _chara_kkex_from_stream(io.BytesIO(payload))
    else:
        with _open_payload(path) as s:
            found = _chara_kkex_from_stream(s) if s is not None else None
    kkex = found[1] if found else None
    return sorted(set(_extract_guids_from_kkex(kkex))) if kkex else []


def scan_chara_guids(path: Path) -> list[str] | None:
    """GUIDs of a Koikatu-family chara card (KK / KKSP / KKS), or None if `path` isn't one.

    One open, reading only the first bytes of the payload (to tell a chara card from a scene,
    a coordinate card or a stray PNG), the block index and the KKEx block."""
    with _open_payload(path) as s:
        found = _chara_kkex_from_stream(s) if s is not None else None
    if found is None or "KoiKatuChara" not in found[0]:
        return None
    kkex = found[1]
    return sorted(set(_extract_guids_from_kkex(kkex))) if kkex else []


_SCENE_UNPACK_START = 64 * 1024        # first attempt: enough for almost every UAR value

_SCENE_UNPACK_MAX   = 20_000_000       # never feed more than this per occurrence

_SCENE_UNPACK_GROW  = 8

_NEED_MORE = object()                  # `_unpack_value_at`: the value runs past the end of a partial buffer


def _unpack_value_at(mv: memoryview, start: int, complete: bool = True):
    """Decode the single msgpack value that begins at `mv[start:]`.

    `complete` says whether `mv` ends where the data really ends. When it is a window
    into a larger stream (`complete=False`) and the value runs past its end, `_NEED_MORE`
    is returned instead of None so the caller can decode it from the stream itself.

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
                if not complete and end >= total and end - start < _SCENE_UNPACK_MAX:
                    return _NEED_MORE       # cut off by the end of the buffer, not of the data
                return None                 # value truncated by EOF / too large
            size *= _SCENE_UNPACK_GROW      # retry with a bigger window
        except Exception:
            return None                     # not msgpack at this offset


def _guids_from_uar_value(plugin_data_raw) -> list[str]:
    """ModIDs in one decoded UAR plugin value ({1: {"info": [packed ResolveInfo, ...]}} or the
    [version, {"info": ...}] form); [] if it has another shape."""
    data_dict = None
    if isinstance(plugin_data_raw, dict):
        data_dict = plugin_data_raw.get(1)
    elif isinstance(plugin_data_raw, (list, tuple)) and len(plugin_data_raw) >= 2:
        data_dict = plugin_data_raw[1]

    if not isinstance(data_dict, dict):
        return []

    info_val = data_dict.get("info")
    if not isinstance(info_val, (list, tuple)):
        return []

    guids: list[str] = []
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

            guids.extend(_guids_from_uar_value(plugin_data_raw))

    return guids


_SCENE_CHUNK = 1 << 20                 # bytes read from disk per step of a scene scan

_SCENE_TAIL_MARK = b"KStudio"          # every scene carries the "【KStudio】" tail mark


class _StreamSlicer:
    """Sliceable view of a `_PayloadStream` (`len()` and `[a:b]`), so `_unpack_value_at` can pull
    a window of any size straight from the file."""

    def __init__(self, s):
        self._s = s

    def __len__(self) -> int:
        return self._s._size

    def __getitem__(self, sl: slice) -> bytes:
        start, stop, _ = sl.indices(len(self))
        self._s.seek(start)
        return self._s.read(max(stop - start, 0))


def _scan_scene_stream(s) -> tuple[bool, list[str]]:
    """(is a Studio scene, UAR GUIDs) for a scene payload, read from `s` in fixed-size chunks.

    One pass does what used to take three over a payload held in memory (the "KStudio" check
    and a search for each UAR extension id), with memory bounded by one chunk instead of the
    whole scene. Every occurrence of an extension id is still found and decoded, wherever it
    sits: embedded characters carry their own KKEx, so there is no fixed place to look.

    The buffer keeps the last `lookahead` bytes of each step as the start of the next one. A hit
    is only handled once at least `lookahead` bytes follow it (or the data has ended), which
    guarantees a needle never straddles two steps and that the first window handed to the msgpack
    decoder (`_SCENE_UNPACK_START`) is always in the buffer. A value longer than that is decoded
    from the file itself."""
    size = s._size
    needles = [i.encode("utf-8") for i in sorted(UAR_EXT_IDS)]
    lookahead = max(_SCENE_UNPACK_START + 64, *(len(n) for n in needles), len(_SCENE_TAIL_MARK))
    buf = bytearray(lookahead + _SCENE_CHUNK)
    view = memoryview(buf)
    slicer = _StreamSlicer(s)

    # The "KStudio" mark opens the tail, which runs to the end of the file, so it is nearly always
    # within the last chunk: look there first, and only search the rest of the file for it when it
    # is not (a tail bigger than a chunk, or no scene at all). Same answer as searching everything
    # -- "found anywhere" -- for one of the three passes over the data instead of all of them.
    probe = min(size, _SCENE_CHUNK)
    s.seek(size - probe)
    is_scene = buf.find(_SCENE_TAIL_MARK, 0, s.readinto(view[:probe])) >= 0
    guids: list[str] = []
    base = 0            # payload offset of buf[0]
    filled = 0          # valid bytes in buf
    read_pos = 0        # payload offset of the next byte to read
    while True:
        s.seek(read_pos)
        n = s.readinto(view[filled:])
        read_pos += n
        filled += n
        at_end = n == 0 or read_pos >= size
        if not at_end and filled <= lookahead:
            continue                                      # short read: top the buffer up first
        hi = filled if at_end else filled - lookahead     # hits starting before `hi` are settled now

        if not is_scene and buf.find(_SCENE_TAIL_MARK, 0, filled) >= 0:
            is_scene = True

        for needle in needles:
            end = min(filled, hi + len(needle) - 1)       # a hit must start before `hi` ...
            idx = buf.find(needle, 0, end)                # ... and lie wholly inside the buffer
            while idx >= 0:
                value_at = idx + len(needle)
                value = _unpack_value_at(view[:filled], value_at, complete=at_end)
                if value is _NEED_MORE:
                    value = _unpack_value_at(slicer, base + value_at)
                guids.extend(_guids_from_uar_value(value))
                idx = buf.find(needle, value_at, end)

        if at_end:
            return is_scene, guids
        view[:lookahead] = bytes(view[filled - lookahead:filled])    # keep the unsettled tail
        base += filled - lookahead
        filled = lookahead


def scan_scene_guids(path: Path) -> list[str] | None:
    """GUIDs of a Studio scene, or None if `path` isn't one (no "KStudio" tail mark). Streams
    the payload in chunks; the PNG image is never read."""
    with _open_payload(path) as s:
        if s is None:
            return None
        is_scene, guids = _scan_scene_stream(s)
    return sorted(set(guids)) if is_scene else None


def parse_scene_guids(path: Path, payload: bytes | None = None) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a Studio scene file."""
    try:
        if payload is not None:
            return sorted(set(_extract_guids_from_scene_blob(payload)))
        with _open_payload(path) as s:
            return sorted(set(_scan_scene_stream(s)[1])) if s is not None else []
    except Exception:
        return []


def _coord_card_from_stream(s, only_uar: bool = True) -> tuple[str, bytes | None] | None:
    """(marker, KKEx block or None) of a coordinate card, given a stream over its payload.
    None if the payload isn't a coordinate card. The outfit blob is skipped with seek(), and
    so are the non-UAR plugins inside KKEx (see `_read_kkex_block`)."""
    if struct.unpack("<i", s.read(4))[0] != 100:
        return None

    marker = _read_str(s, _MARKER_MAX_LEN)
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
        return marker, None
    if kkex_marker != "KKEx":
        return marker, None

    s.read(4)
    ext_len = _ri(s)
    if ext_len <= 0:
        return marker, None
    return marker, _read_kkex_block(s, ext_len, only_uar)


def _coord_kkex_bytes(payload: bytes) -> bytes | None:
    """Raw KKEx block of a coordinate card, given the bytes after its PNG.
    None if the payload isn't a coordinate card or carries no KKEx."""
    found = _coord_card_from_stream(io.BytesIO(payload), only_uar=False) if payload else None
    return found[1] if found else None


def parse_coord_guids(path: Path, payload: bytes | None = None) -> list[str]:
    """Return sorted unique zipmod GUIDs referenced by a coordinate card."""
    try:
        if payload is not None:
            found = _coord_card_from_stream(io.BytesIO(payload))
        else:
            with _open_payload(path) as s:
                found = _coord_card_from_stream(s) if s is not None else None
        kkex = found[1] if found else None
        return sorted(set(_extract_guids_from_kkex(kkex))) if kkex else []
    except Exception:
        return []


def scan_coord_guids(path: Path) -> list[str] | None:
    """GUIDs of a Koikatu coordinate card, or None if `path` isn't one (a chara card, a scene,
    another game's coordinate, a stray PNG). Reads only the header and the KKEx block."""
    with _open_payload(path) as s:
        try:
            found = _coord_card_from_stream(s) if s is not None else None
        except (struct.error, EOFError, ValueError):
            return None                 # truncated or garbled header: not a usable card
    if found is None or "KoiKatuClothes" not in found[0]:
        return None
    kkex = found[1]
    try:
        return sorted(set(_extract_guids_from_kkex(kkex))) if kkex else []
    except Exception:
        return []
