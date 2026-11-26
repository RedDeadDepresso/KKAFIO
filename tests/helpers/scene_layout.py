"""Writes Studio scenes in the real binary layout (PNG, version, object tree, scene settings,
"【KStudio】" + KKEx tail) for KK 1.0.4.2 and KKS 1.1.2.1 style versions, so the scene walker can
be tested on something with the structure of a real scene rather than on a needle in a blob.

The field order is the one of `kkafio.cards.scenes.kks_scene._SceneTranscoder` (the readers are
checked against real scenes by `tools/verify_scene_walk.py`; this writer only has to agree with
them on shape).
"""

import struct

import card_factory as cf
import msgpack

from kkafio.cards.scenes.scene_version import _ver


def i32(v: int) -> bytes:
    return struct.pack("<i", v)


def f32() -> bytes:
    return struct.pack("<f", 1.0)


def s(text: str) -> bytes:
    data = text.encode("utf-8")
    n, out = len(data), bytearray()
    while True:
        c = n & 0x7F
        n >>= 7
        out.append(c | 0x80 if n else c)
        if not n:
            return bytes(out) + data


def sb(data: bytes) -> bytes:
    """A length-prefixed string from raw bytes (the walker skips strings without decoding them)."""
    return s("x" * len(data))[: len(s("x" * len(data))) - len(data)] + data


def lenbytes(n: int = 0) -> bytes:
    return i32(n) + b"\x07" * n


def base(other: bool = True) -> bytes:
    return i32(1) + b"\x00" * 36 + (i32(0) + b"\x01" if other else b"")


def children(kids: list[bytes]) -> bytes:
    """`ObjectInfoAssist.LoadChild` form: count, then (kind, body) each; `kids` are `obj()`s."""
    return i32(len(kids)) + b"".join(kids)


def obj(kind: int, body: bytes) -> bytes:
    return i32(kind) + body


def pattern() -> bytes:
    return i32(0) + s("") + b"\x00" + s("") + f32()


def item(v, kids=(), bones: int = 0, color_blob: bytes = b"") -> bytes:
    v = _ver(v)
    ge = lambda x: v >= _ver(x)
    b = base() + i32(1) * 3 + (i32(0) if ge("1.1.1.0") else b"") + f32()
    b += sb(b'{"c":1}' + color_blob) + s('{"c":1}') * ((8 if ge("0.0.3") else 7) - 1) + pattern() * 3 + f32()
    if ge("0.0.4"):
        b += s("#fff") + f32()
    if ge("0.0.7"):
        b += s("#000") + f32() + f32()
    if ge("0.0.6"):
        b += pattern()
    b += b"\x00" + i32(bones) + (s("bone") + base(False)) * bones
    if ge("1.0.1"):
        b += b"\x00"
    return obj(1, b + f32() + children(list(kids)))


def folder(kids=()) -> bytes:
    return obj(3, base() + s("folder") + children(list(kids)))


def light() -> bytes:
    return obj(2, base() + i32(1) + f32() * 7 + b"\x00" * 3)


def camera() -> bytes:
    return obj(5, base() + s("cam") + b"\x00")


def text() -> bytes:
    return obj(7, base() + i32(0) + s("a") + s("b") + f32() + lenbytes())


def route(v, points: int = 2, kids=()) -> bytes:
    v = _ver(v)
    ge = lambda x: v >= _ver(x)
    b = base() + s("route") + children(list(kids)) + i32(points)
    for _ in range(points):
        b += base(False) + f32() + i32(0)
        if v == _ver("1.0.3"):
            b += b"\x00"
        if ge("1.0.4.1"):
            b += i32(0) + base(False) + b"\x00"
        if ge("1.0.4.2"):
            b += b"\x00"
    if ge("1.0.3"):
        b += b"\x00" * 3
    if ge("1.0.4"):
        b += i32(0)
    if ge("1.0.4.1"):
        b += s("")
    return obj(4, b)


def character(v, guids=None, marker: str = "【KoiKatuChara】", other_plugin_bytes: int = 0,
              accessory_children=(), bones: int = 1) -> bytes:
    """An OICharInfo. `guids` is None for a card without a KKEx block."""
    v = _ver(v)
    ge = lambda x: v >= _ver(x)
    if guids is None:                                            # a card with no KKEx block at all
        header = msgpack.packb({"lstInfo": [{"name": "Custom", "pos": 0, "size": 4}]})
        pay = (i32(100) + s(marker) + s("0.0.0") + lenbytes(5) + i32(len(header)) + header
               + struct.pack("<q", 4) + b"\x09" * 4)
    elif other_plugin_bytes:                                     # a big non-UAR plugin before the UAR entry
        pay = _card_payload(_kkex_with_other(guids, other_plugin_bytes), marker)
    else:
        pay = cf.chara_card(guids, marker=marker)[len(cf.png_bytes()):]
    b = base() + i32(0) + pay
    b += i32(bones) + (i32(1) + base(False)) * bones             # bones
    b += i32(0)                                                  # ikTarget
    b += i32(len(accessory_children)) + b"".join(i32(i) + children([c]) for i, c in enumerate(accessory_children))
    b += i32(0) + i32(0) * 3 + i32(0) * 2 + f32() + b"\x00" * 5 + f32() + b"\x00"
    b += base(False) + b"\x00" + b"\x00" * 5 + b"\x00" + b"\x00" * 7
    b += b"\x00" * (8 if ge("0.0.9") else 4) + f32() * 2 + b"\x00" * 2
    b += i32(1) + i32(0) * 3 + i32(0) + b"\x00" + f32() + b"\x00" + s("#fff") + f32() * 2
    b += lenbytes() + lenbytes(3) + f32() + i32(1) + i32(0) * 2 + i32(1) + i32(0) * 2
    return obj(0, b)


def _kkex_with_other(guids, n: int) -> bytes:
    return msgpack.packb({"other.plugin": {1: {"blob": b"\x01" * n}},
                          cf.UAR: {1: {"info": [msgpack.packb({"ModID": g}) for g in guids]}}})


def _card_payload(kkex: bytes, marker: str) -> bytes:
    header = msgpack.packb({"lstInfo": [{"name": "Custom", "pos": 0, "size": 4},
                                        {"name": "KKEx", "pos": 4, "size": len(kkex)}]})
    return (i32(100) + s(marker) + s("0.0.0") + lenbytes(5) + i32(len(header)) + header
            + struct.pack("<q", 4 + len(kkex)) + b"\x09" * 4 + kkex)


def settings(v) -> bytes:
    v = _ver(v)
    ge = lambda x: v >= _ver(x)
    b = i32(0) + b"\x00" * 36 + i32(0) + b"\x00" + i32(0)
    if ge("0.0.2"):
        b += f32()
    if v <= _ver("0.0.1"):
        b += b"\x00" + f32() + s("")
    if ge("0.0.2"):
        b += b"\x00" + s("") + f32()
    b += b"\x00" + f32() * 2
    if ge("0.0.2"):
        b += f32()
    if v <= _ver("0.0.1"):
        b += b"\x00"
    b += b"\x00" + f32() * 2 + b"\x00"
    if v <= _ver("0.0.1"):
        b += f32()
    b += b"\x00"
    if ge("0.0.2"):
        b += s("") + f32() * 2
    b += b"\x00"
    if ge("0.0.2"):
        b += s("") * 2
    if ge("0.0.4"):
        b += i32(0)
    if ge("0.0.2"):
        b += b"\x00"
    if ge("0.0.4"):
        b += b"\x00" * 2 + f32() + s("")
    if ge("0.0.5"):
        b += f32() + i32(0) + f32()
    if ge("1.1.0.0"):
        b += i32(0)
    if ge("1.1.2.0"):
        b += lenbytes(9)
    for _ in range(11):                                          # cameraSaveData + 10 saved cameras
        b += i32(2) + f32() * 6 + b"\x00" * 12 + f32()
    b += s("") + f32() * 3 + b"\x00"                             # charaLight
    b += s("") + f32() * 3 + b"\x00" + i32(0)                    # mapLight
    b += i32(0) * 2 + b"\x00" + i32(0) * 2 + b"\x00" + i32(0) + s("") + b"\x00"
    return b + s("bg/background.png") + s("")


def scene(v="1.0.4.2", objects=(), tail_guids=(), tail_plugin_bytes: int = 0,
          tail_marker: bool = True, tail_extra: bytes = b"") -> bytes:
    """A whole scene file: PNG + version + `objects` (each from `item()`, `character()`, ...) +
    settings + tail. `tail_guids` go in the tail KKEx's UAR entry, after a non-UAR plugin value
    of `tail_plugin_bytes` bytes."""
    body = s(v) + i32(len(objects)) + b"".join(i32(i) + o for i, o in enumerate(objects))
    body += settings(v)
    kk = (_kkex_with_other(tail_guids, tail_plugin_bytes) if tail_plugin_bytes
          else cf.kkex(tail_guids))
    if tail_marker:
        body += s(cf.SCENE_MARKER) + s("KKEx") + i32(1) + i32(len(kk)) + kk + tail_extra
    return cf.png_bytes() + body
