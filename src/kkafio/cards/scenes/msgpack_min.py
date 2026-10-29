"""A minimal MessagePack codec that re-encodes an unchanged value to the exact bytes it was read
from (each integer remembers its wire code, maps keep order and duplicate keys). Used to rewrite
the Timeline XML inside a Studio scene without disturbing anything around it.
"""

import struct


# ---- minimal MessagePack codec (only used for the Timeline XML rewrite) ----

class _MpInt:
    """int that remembers its wire code, so it re-encodes verbatim."""
    __slots__ = ("value", "code")

    def __init__(self, value: int, code: int):
        self.value, self.code = value, code


class _MpBin:
    __slots__ = ("data",)

    def __init__(self, data: bytes):
        self.data = data


class _MpExt:
    __slots__ = ("code", "data")

    def __init__(self, code: int, data: bytes):
        self.code, self.data = code, data


class _MpF32:
    __slots__ = ("raw",)

    def __init__(self, raw: bytes):
        self.raw = raw  # big-endian, as on the wire


class _MpMap:
    """Ordered map; keeps insertion order and duplicate keys so an unchanged
    map re-encodes to the same bytes."""
    __slots__ = ("items",)

    def __init__(self):
        self.items: list[list] = []  # [[key, value], ...]

    def get(self, key: str):
        found = None
        for k, v in self.items:
            if isinstance(k, str) and k == key:
                found = v  # last wins, like a dict
        return found


def _mp_take(buf: bytes, pos: int, n: int) -> tuple[bytes, int]:
    if n < 0 or pos + n > len(buf):
        raise EOFError(f"msgpack: read past end at {pos}")
    return buf[pos:pos + n], pos + n


def _mp_uint(buf: bytes, pos: int, w: int) -> tuple[int, int]:
    raw, pos = _mp_take(buf, pos, w)
    return int.from_bytes(raw, "big"), pos


def _mp_decode(buf: bytes, pos: int):
    if pos >= len(buf):
        raise EOFError("msgpack: read past end")
    b = buf[pos]
    pos += 1
    if b <= 0x7F:
        return _MpInt(b, b), pos
    if b >= 0xE0:
        return _MpInt(b - 0x100, b), pos
    if b <= 0x8F:
        return _mp_decode_map(buf, pos, b & 0x0F)
    if b <= 0x9F:
        return _mp_decode_arr(buf, pos, b & 0x0F)
    if b <= 0xBF:
        raw, pos = _mp_take(buf, pos, b & 0x1F)
        return raw.decode("utf-8"), pos
    if b == 0xC0:
        return None, pos
    if b == 0xC2:
        return False, pos
    if b == 0xC3:
        return True, pos
    if b in (0xC4, 0xC5, 0xC6):
        n, pos = _mp_uint(buf, pos, 1 << (b - 0xC4))
        raw, pos = _mp_take(buf, pos, n)
        return _MpBin(raw), pos
    if b in (0xC7, 0xC8, 0xC9):
        n, pos = _mp_uint(buf, pos, 1 << (b - 0xC7))
        return _mp_decode_ext(buf, pos, n)
    if b == 0xCA:
        raw, pos = _mp_take(buf, pos, 4)
        return _MpF32(raw), pos
    if b == 0xCB:
        raw, pos = _mp_take(buf, pos, 8)
        return struct.unpack(">d", raw)[0], pos
    if 0xCC <= b <= 0xCF:
        v, pos = _mp_uint(buf, pos, 1 << (b - 0xCC))
        return _MpInt(v, b), pos
    if 0xD0 <= b <= 0xD3:
        w = 1 << (b - 0xD0)
        raw, pos = _mp_take(buf, pos, w)
        return _MpInt(int.from_bytes(raw, "big", signed=True), b), pos
    if 0xD4 <= b <= 0xD8:
        return _mp_decode_ext(buf, pos, 1 << (b - 0xD4))
    if b in (0xD9, 0xDA, 0xDB):
        n, pos = _mp_uint(buf, pos, 1 << (b - 0xD9))
        raw, pos = _mp_take(buf, pos, n)
        return raw.decode("utf-8"), pos
    if b in (0xDC, 0xDD):
        n, pos = _mp_uint(buf, pos, 2 if b == 0xDC else 4)
        return _mp_decode_arr(buf, pos, n)
    if b in (0xDE, 0xDF):
        n, pos = _mp_uint(buf, pos, 2 if b == 0xDE else 4)
        return _mp_decode_map(buf, pos, n)
    raise ValueError(f"msgpack: unsupported byte 0x{b:02X} at {pos - 1}")


def _mp_decode_ext(buf: bytes, pos: int, n: int):
    code_raw, pos = _mp_take(buf, pos, 1)
    data, pos = _mp_take(buf, pos, n)
    return _MpExt(struct.unpack("b", code_raw)[0], data), pos


def _mp_decode_arr(buf: bytes, pos: int, n: int):
    out = []
    for _ in range(n):
        v, pos = _mp_decode(buf, pos)
        out.append(v)
    return out, pos


def _mp_decode_map(buf: bytes, pos: int, n: int):
    m = _MpMap()
    for _ in range(n):
        k, pos = _mp_decode(buf, pos)
        v, pos = _mp_decode(buf, pos)
        m.items.append([k, v])
    return m, pos


def _mp_encode(v, out: bytearray) -> None:
    if v is None:
        out.append(0xC0)
    elif v is True:
        out.append(0xC3)
    elif v is False:
        out.append(0xC2)
    elif isinstance(v, _MpInt):
        c = v.code
        if c <= 0x7F or c >= 0xE0:
            out.append(c)
        elif 0xCC <= c <= 0xCF:
            out.append(c)
            out += (v.value & ((1 << (8 * (1 << (c - 0xCC)))) - 1)).to_bytes(1 << (c - 0xCC), "big")
        else:
            w = 1 << (c - 0xD0)
            out.append(c)
            out += (v.value & ((1 << (8 * w)) - 1)).to_bytes(w, "big")
    elif isinstance(v, _MpF32):
        out.append(0xCA)
        out += v.raw
    elif isinstance(v, float):
        out.append(0xCB)
        out += struct.pack(">d", v)
    elif isinstance(v, _MpBin):
        n = len(v.data)
        if n < 0x100:
            out.append(0xC4); out += n.to_bytes(1, "big")
        elif n < 0x10000:
            out.append(0xC5); out += n.to_bytes(2, "big")
        else:
            out.append(0xC6); out += n.to_bytes(4, "big")
        out += v.data
    elif isinstance(v, str):
        b = v.encode("utf-8")
        n = len(b)
        if n < 32:
            out.append(0xA0 | n)
        elif n < 0x100:
            out.append(0xD9); out += n.to_bytes(1, "big")
        elif n < 0x10000:
            out.append(0xDA); out += n.to_bytes(2, "big")
        else:
            out.append(0xDB); out += n.to_bytes(4, "big")
        out += b
    elif isinstance(v, _MpExt):
        n = len(v.data)
        fixed = {1: 0xD4, 2: 0xD5, 4: 0xD6, 8: 0xD7, 16: 0xD8}
        if n in fixed:
            out.append(fixed[n])
        elif n < 0x100:
            out.append(0xC7); out += n.to_bytes(1, "big")
        elif n < 0x10000:
            out.append(0xC8); out += n.to_bytes(2, "big")
        else:
            out.append(0xC9); out += n.to_bytes(4, "big")
        out += struct.pack("b", v.code)
        out += v.data
    elif isinstance(v, list):
        n = len(v)
        if n < 16:
            out.append(0x90 | n)
        elif n < 0x10000:
            out.append(0xDC); out += n.to_bytes(2, "big")
        else:
            out.append(0xDD); out += n.to_bytes(4, "big")
        for x in v:
            _mp_encode(x, out)
    elif isinstance(v, _MpMap):
        n = len(v.items)
        if n < 16:
            out.append(0x80 | n)
        elif n < 0x10000:
            out.append(0xDE); out += n.to_bytes(2, "big")
        else:
            out.append(0xDF); out += n.to_bytes(4, "big")
        for k, val in v.items:
            _mp_encode(k, out)
            _mp_encode(val, out)
    else:
        raise ValueError(f"msgpack: cannot encode {type(v).__name__}")
