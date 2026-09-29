"""
FilterConvertKKS
================
Scans a folder for PNG character cards and Studio scenes, optionally
converts Koikatsu Sunshine (KKS) cards and scenes to Koikatsu (KK) format,
then optionally sorts each type into its own folder or sends it to the
Recycle Bin.

Convert    — produce a KK-compatible copy of each KKS card / scene. The copy
            is written alongside its source file; whether it then gets
            moved, deleted, or left in place is decided by KKAction below,
            exactly like any other KK file.
              • Cards  — binary header patch.
              • Scenes — full transcode of the scene data (see the
                "KKS scene transcoder" section below): version rewrite,
                KKS-only fields removed, Text objects dropped, embedded
                chara cards down-converted, background path and Timeline
                owner names fixed. Ported from KoikatsuSceneConverter
                (https://github.com/maguro-alternative/KoikatsuSceneConverter).

KKAction   — what to do with every KK/KKSP card / KK scene found (including
            any KKS card or scene just converted to KK by the option above):
              • Keep   — do nothing, leave it where it is (default)
              • Move   — move it into _KK_card_/
              • Delete — send it to the Recycle Bin

KKSAction  — the same three choices, applied to the original KKS cards and
            scenes (never to their converted copies, which KKAction covers).
"""

import struct
from pathlib import Path
from typing import Callable

from send2trash import send2trash

from kkafio.tasks.base_task import DEFAULT_DOWNLOADS_PATH, validate_input_path
from kkafio.core.config import Config
from kkafio.cards.classifier import CardType, get_card_type
from kkafio.core.file_manager import FileManager
from kkafio.cards.scene_version import (
    AlreadyKKSceneError,
    SceneNotKKSError,
    _Reader,
    _png_length,
    _ver,
    read_scene_version,
)
from kkafio.core.logger import logger

ACTION_KEEP   = "Keep"
ACTION_MOVE   = "Move"
ACTION_DELETE = "Delete"

# Output folders this task creates. Excluded from the next run's scan so
# re-running it (e.g. as a repeating pipeline step) doesn't re-process
# cards it already sorted on a previous run.
_OUTPUT_FOLDER_NAMES = {"_KKS_card_", "_KK_card_"}


# ======================================================================
# KKS scene transcoder
# ======================================================================
#
# Down-converts a Koikatsu Sunshine (KKS) Studio scene so that Koikatsu (KK)
# CharaStudio can load it. Python port of KoikatsuSceneConverter's
# Transcoder (Scene.cs / Msgpack.cs):
#   https://github.com/maguro-alternative/KoikatsuSceneConverter
#
# Why KK cannot read KKS scenes:
#   * KK's SceneInfo.Load has no version guard; it reads the stream with the
#     KK layout (1.0.4.2) while KKS writes 1.1.2.1 with a few extra fields.
#   * Embedded chara cards carry the mark "【KoiKatuCharaSun】"; KK rejects it.
#   * OIItemInfo gained `animePattern` (int) between `no` and `animeSpeed`.
#   * SceneInfo gained `shaderType` (int) and a MessagePack `SkyInfo` blob.
#   * Object kind 7 (OITextInfo) does not exist in KK.
#   * KKS stores `background` as a path, KK wants a bare file name.
#   * KK skips chara-card blocks whose BlockHeader version is newer than it
#     knows (KKS Parameter 0.0.6 > KK 0.0.5) -> name / personality vanish.
#   * KKSPE registers Timeline interpolables with owner="KKSPE"; KK's
#     Timeline only knows "KKPE" and silently drops them.
# Everything else is byte-identical between the two games and is copied
# verbatim.

_KK_SCENE_VERSION = "1.0.4.2"
_MARK_KKS = "【KoiKatuCharaSun】"
_MARK_KK = "【KoiKatuChara】"
_TAIL_MARK = "【KStudio】"

# Highest chara-card block versions KK accepts (ChaFileDefine in KK).
_KK_BLOCK_VERSIONS = (
    ("Custom", "0.0.0"),
    ("Coordinate", "0.0.0"),
    ("Parameter", "0.0.5"),
    ("Status", "0.0.0"),
)

# Timeline interpolable owners that are named differently in KKS plugins.
_TIMELINE_OWNER_RENAMES = (('owner="KKSPE"', 'owner="KKPE"'),)


class _Writer:
    """BinaryWriter-compatible writer with a buffer stack, so a whole object
    can be written and then discarded (used to drop Text objects)."""

    def __init__(self):
        self._stack: list[bytearray] = [bytearray()]

    def push(self) -> None:
        self._stack.append(bytearray())

    def pop(self) -> bytes:
        return bytes(self._stack.pop())

    def to_bytes(self) -> bytes:
        return bytes(self._stack[-1])

    def raw(self, b: bytes) -> None:
        self._stack[-1] += b

    def i32(self, v: int) -> None:
        self._stack[-1] += struct.pack("<i", v)

    def i64(self, v: int) -> None:
        self._stack[-1] += struct.pack("<q", v)

    def string(self, s: str) -> None:
        data = s.encode("utf-8")
        n = len(data)
        out = self._stack[-1]
        while True:
            c = n & 0x7F
            n >>= 7
            if n:
                out.append(c | 0x80)
            else:
                out.append(c)
                break
        out += data


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


# ---- scene patches --------------------------------------------------------

def _fixstr(s: str) -> bytes:
    b = s.encode("utf-8")
    if len(b) > 31:
        raise ValueError("fixstr too long")
    return bytes([0xA0 | len(b)]) + b


def _patch_block_versions(header: bytes) -> tuple[bytes, dict[str, str]]:
    """Rewrite `version` of known blocks inside a ChaFile BlockHeader
    (msgpack map {lstInfo:[{name,version,pos,size}...]}) so KK does not skip
    them. Works on the raw bytes: `name` and `version` are always short
    fixstr."""
    out = header
    changed: dict[str, str] = {}
    for name, max_ver in _KK_BLOCK_VERSIONS:
        needle = b"\xa4name" + _fixstr(name) + b"\xa7version"
        i = out.find(needle)
        if i < 0:
            continue
        j = i + len(needle)
        if j >= len(out):
            continue
        tag = out[j]
        if (tag & 0xE0) != 0xA0:  # not a fixstr; leave untouched
            continue
        n = tag & 0x1F
        if j + 1 + n > len(out):
            continue
        cur = out[j + 1:j + 1 + n].decode("utf-8")
        if _ver(cur) > _ver(max_ver):
            out = out[:j] + _fixstr(max_ver) + out[j + 1 + n:]
            changed[name] = f"{cur}->{max_ver}"
    return out, changed


def _patch_scene_tail(tail: bytes, log: Callable[[str], None],
                      changes: list[str]) -> bytes:
    """tail = everything after `frame`: "【KStudio】" + ExtensibleSaveFormat
    block ("KKEx", int32 version, int32 len, msgpack Dictionary<string,
    PluginData>). Rewrites Timeline XML owner names KK does not know;
    otherwise returns the input unchanged (byte-identical)."""
    r = _Reader(tail)
    try:
        if r.string() != _TAIL_MARK or r.string() != "KKEx":
            return tail
        ver = r.i32()
        blob = r.raw(r.i32())
    except Exception:
        return tail

    if not any(old.encode("utf-8") in blob for old, _ in _TIMELINE_OWNER_RENAMES):
        return tail

    try:
        d, end = _mp_decode(blob, 0)
        if end != len(blob):
            raise ValueError("trailing bytes in KKEx blob")
        if not isinstance(d, _MpMap):
            raise ValueError("KKEx root is not a map")

        # PluginData is serialized by MessagePack-CSharp as [version, data]
        # (index-keyed), not as a string-keyed map.
        tl = d.get("timeline")
        data = None
        if isinstance(tl, list) and len(tl) >= 2 and isinstance(tl[1], _MpMap):
            data = tl[1]
        elif isinstance(tl, _MpMap) and isinstance(tl.get("data"), _MpMap):
            data = tl.get("data")

        if data is not None:
            for item in data.items:
                val = item[1]
                if not isinstance(val, str):
                    continue
                cur = val
                for old, new in _TIMELINE_OWNER_RENAMES:
                    c = cur.count(old)
                    if c > 0:
                        cur = cur.replace(old, new)
                        changes.append(f"{old} x{c}")
                item[1] = cur

        if not changes:
            return tail

        new_blob = bytearray()
        _mp_encode(d, new_blob)
        w = _Writer()
        w.string(_TAIL_MARK)
        w.string("KKEx")
        w.i32(ver)
        w.i32(len(new_blob))
        w.raw(bytes(new_blob))
        w.raw(r.raw(r.remaining))  # anything after the KKEx block (normally nothing)
        return w.to_bytes()
    except Exception as e:
        log(f"warning: could not rewrite Timeline owners ({e}); tail copied verbatim")
        changes.clear()
        return tail


class _SceneTranscoder:
    """Reads the KKS scene layout, writes the KK scene layout."""

    def __init__(self, data: bytes, log: Callable[[str], None] | None = None):
        self.r = _Reader(data)
        self.w = _Writer()
        self.log = log or (lambda s: None)
        self.src_ver = (0, 0, 0, 0)
        self.texts_dropped = 0
        self.chars = 0
        self.items = 0
        self.block_versions: dict[str, str] = {}
        self.timeline_renames: list[str] = []

    # ---- primitive copy helpers -------------------------------------
    def _ci32(self) -> int:
        v = self.r.i32()
        self.w.i32(v)
        return v

    def _ci64(self) -> int:
        v = self.r.i64()
        self.w.i64(v)
        return v

    def _craw(self, n: int) -> bytes:
        v = self.r.raw(n)
        self.w.raw(v)
        return v

    def _cf32(self) -> None:
        self._craw(4)

    def _cbool(self) -> None:
        self._craw(1)

    def _cstr(self) -> str:
        v = self.r.string()
        self.w.string(v)
        return v

    def _clenbytes(self) -> None:
        self._craw(self._ci32())

    def _cvec3(self) -> None:
        self._craw(12)

    def _cchange_amount(self) -> None:
        self._craw(36)

    def _vge(self, s: str) -> bool:
        return self.src_ver >= _ver(s)

    # ---- ObjectInfo family ------------------------------------------
    def _object_base(self, other: bool = True) -> int:
        key = self._ci32()
        self._cchange_amount()
        if other:
            self._ci32()   # treeState
            self._cbool()  # visible
        return key

    def _pattern_info(self) -> None:
        self._ci32(); self._cstr(); self._cbool(); self._cstr(); self._cf32()

    def _item(self) -> None:
        self._object_base()
        g, c, n = self._ci32(), self._ci32(), self._ci32()
        if self._vge("1.1.1.0"):
            self.r.i32()  # animePattern - KK does not have it
        self._cf32()      # animeSpeed
        for _ in range(8 if self._vge("0.0.3") else 7):
            self._cstr()  # color json
        for _ in range(3):
            self._pattern_info()
        self._cf32()      # alpha
        if self._vge("0.0.4"):
            self._cstr(); self._cf32()                   # lineColor, lineWidth
        if self._vge("0.0.7"):
            self._cstr(); self._cf32(); self._cf32()     # emission, lightCancel
        if self._vge("0.0.6"):
            self._pattern_info()                         # panel
        self._cbool()     # enableFK
        for _ in range(self._ci32()):
            self._cstr()
            self._object_base(False)                     # OIBoneInfo
        if self._vge("1.0.1"):
            self._cbool()                                # enableDynamicBone
        self._cf32()      # animeNormalizedTime
        self._load_child()
        self.items += 1
        self.log(f"      item group={g} cat={c} no={n}")

    def _chara_card(self) -> str:
        self._ci32()  # product
        mark = self.r.string()
        if mark == _MARK_KKS:
            self.w.string(_MARK_KK)
        elif mark == _MARK_KK:
            self.w.string(mark)
        else:
            raise ValueError(f'unexpected chara mark "{mark}"')
        self._cstr()       # ChaFileVersion
        self._clenbytes()  # face png
        header = self.r.raw(self.r.i32())  # BlockHeader (msgpack)
        header, changed = _patch_block_versions(header)
        self.block_versions.update(changed)
        self.w.i32(len(header))
        self.w.raw(header)
        total = self._ci64()
        self._craw(total)  # all blocks incl. KKEx
        return mark

    def _character(self) -> None:
        self._object_base()
        sex = self._ci32()
        mark = self._chara_card()
        for _ in range(self._ci32()):                    # bones
            self._ci32(); self._object_base(False)
        for _ in range(self._ci32()):                    # ikTarget
            self._ci32(); self._object_base(False)
        for _ in range(self._ci32()):                    # child per accessory point
            self._ci32(); self._load_child()
        self._ci32()                                     # kinematicMode
        self._ci32(); self._ci32(); self._ci32()         # animeInfo
        self._ci32(); self._ci32()                       # handPtn
        self._cf32()                                     # nipple
        self._craw(5)                                    # siru
        self._cf32()                                     # mouthOpen
        self._cbool()                                    # lipSync
        self._object_base(False)                         # lookAtTarget
        self._cbool()                                    # enableIK
        for _ in range(5):
            self._cbool()
        self._cbool()                                    # enableFK
        for _ in range(7):
            self._cbool()
        for _ in range(8 if self._vge("0.0.9") else 4):
            self._cbool()                                # expression
        self._cf32(); self._cf32()                       # animeSpeed, animePattern(float)
        self._cbool(); self._cbool()                     # animeOptionVisible, isAnimeForceLoop
        for _ in range(self._ci32()):                    # voiceCtrl list
            self._ci32(); self._ci32(); self._ci32()
        self._ci32()                                     # voice repeat
        self._cbool(); self._cf32(); self._cbool()       # visibleSon, sonLength, visibleSimple
        self._cstr()                                     # simpleColor
        self._cf32(); self._cf32()                       # animeOptionParam
        self._clenbytes()                                # neckByteData
        self._clenbytes()                                # eyesByteData
        self._cf32()                                     # animeNormalizedTime
        for _ in range(self._ci32()):                    # dicAccessGroup
            self._ci32(); self._ci32()
        for _ in range(self._ci32()):                    # dicAccessNo
            self._ci32(); self._ci32()
        self.chars += 1
        self.log(f"      character sex={sex} mark={mark}")

    def _light(self) -> None:
        self._object_base()
        self._ci32()
        for _ in range(4):
            self._cf32()  # color rgba
        self._cf32(); self._cf32(); self._cf32()
        self._cbool(); self._cbool(); self._cbool()

    def _folder(self) -> None:
        self._object_base()
        name = self._cstr()
        self.log(f'      folder "{name}"')
        self._load_child()

    def _camera(self) -> None:
        self._object_base()
        self._cstr(); self._cbool()

    def _route_point(self) -> None:
        self._object_base(False)
        self._cf32(); self._ci32()  # speed, easeType
        if self.src_ver == _ver("1.0.3"):
            self._cbool()
        if self._vge("1.0.4.1"):
            self._ci32()              # connection
            self._object_base(False)  # aidInfo
            self._cbool()             # aidInfo.isInit
        if self._vge("1.0.4.2"):
            self._cbool()             # link

    def _route(self) -> None:
        self._object_base()
        self._cstr()
        self._load_child()
        for _ in range(self._ci32()):
            self._route_point()
        if self._vge("1.0.3"):
            self._cbool(); self._cbool(); self._cbool()
        if self._vge("1.0.4"):
            self._ci32()
        if self._vge("1.0.4.1"):
            self._cstr()

    def _text(self) -> None:
        # Parsed into the current (throw-away) buffer; caller discards it.
        self._object_base()
        self._ci32(); self._cstr(); self._cstr(); self._cf32()
        self._clenbytes()

    def _object_body(self, kind: int) -> None:
        handlers = {
            0: self._character, 1: self._item, 2: self._light, 3: self._folder,
            4: self._route, 5: self._camera, 7: self._text,
        }
        h = handlers.get(kind)
        if h is None:
            raise ValueError(f"unknown object kind {kind} at {self.r.p}")
        h()

    def _load_child(self) -> None:
        """ObjectInfoAssist.LoadChild: count, then (kind, body)*. Drops kind 7."""
        n = self.r.i32()
        kept: list[bytes] = []
        for _ in range(n):
            kind = self.r.i32()
            self.w.push()
            self.w.i32(kind)
            self._object_body(kind)
            buf = self.w.pop()
            if kind == 7:
                self.texts_dropped += 1
            else:
                kept.append(buf)
        self.w.i32(len(kept))
        for buf in kept:
            self.w.raw(buf)

    # ---- SceneInfo ---------------------------------------------------
    def _camera_data(self) -> None:
        ver = self._ci32()
        for _ in range(6):
            self._cf32()
        if ver == 1:
            self._cf32()
        else:
            self._cvec3()
        self._cf32()  # parse

    def _light_info(self, is_map: bool) -> None:
        self._cstr(); self._cf32(); self._cf32(); self._cf32(); self._cbool()
        if is_map:
            self._ci32()  # LightType

    def transcode(self) -> bytes:
        r, w = self.r, self.w

        # --- PNG ---
        png_len = _png_length(r.b)
        self._craw(png_len)

        # --- header ---
        src = read_scene_version(r.b)
        r.string()  # consume the (already validated) version string
        self.src_ver = _ver(src)
        if self.src_ver < _ver("1.1.0.0"):
            raise AlreadyKKSceneError(
                f"scene version {src} is already KK-compatible (< 1.1.0.0)")
        self.log(f"scene version {src} -> {_KK_SCENE_VERSION}")
        w.string(_KK_SCENE_VERSION)

        # --- root objects: (key, kind, body)* with kind-7 filtering ---
        n = r.i32()
        kept: list[bytes] = []
        for _ in range(n):
            key = r.i32()
            kind = r.i32()
            self.log(f"  root key={key} kind={kind}")
            w.push()
            w.i32(key)
            w.i32(kind)
            self._object_body(kind)
            buf = w.pop()
            if kind == 7:
                self.texts_dropped += 1
            else:
                kept.append(buf)
        w.i32(len(kept))
        for buf in kept:
            w.raw(buf)

        # --- scene-wide settings ---
        self._ci32()               # map
        self._cchange_amount()     # caMap
        self._ci32()               # sunLightType
        self._cbool()              # mapOption
        self._ci32()               # aceNo
        if self._vge("0.0.2"):
            self._cf32()           # aceBlend
        if self.src_ver <= _ver("0.0.1"):
            self._cbool(); self._cf32(); self._cstr()
        if self._vge("0.0.2"):
            self._cbool(); self._cstr(); self._cf32()   # AOE
        self._cbool(); self._cf32(); self._cf32()       # bloom
        if self._vge("0.0.2"):
            self._cf32()           # bloomThreshold
        if self.src_ver <= _ver("0.0.1"):
            self._cbool()
        self._cbool(); self._cf32(); self._cf32()       # depth
        self._cbool()              # vignette
        if self.src_ver <= _ver("0.0.1"):
            self._cf32()
        self._cbool()              # fog
        if self._vge("0.0.2"):
            self._cstr(); self._cf32(); self._cf32()
        self._cbool()              # sunShafts
        if self._vge("0.0.2"):
            self._cstr(); self._cstr()
        if self._vge("0.0.4"):
            self._ci32()           # sunCaster
        if self._vge("0.0.2"):
            self._cbool()          # enableShadow
        if self._vge("0.0.4"):
            self._cbool(); self._cbool(); self._cf32(); self._cstr()
        if self._vge("0.0.5"):
            self._cf32(); self._ci32(); self._cf32()    # lineWidthG, rampG, ambientShadowG
        if self._vge("1.1.0.0"):
            r.i32()                # shaderType (KKS only)
        if self._vge("1.1.2.0"):
            r.raw(r.i32())         # SkyInfo msgpack (KKS only)
        self._camera_data()        # cameraSaveData
        for _ in range(10):
            self._camera_data()
        self._light_info(False)    # charaLight
        self._light_info(True)     # mapLight
        self._ci32(); self._ci32(); self._cbool()       # bgmCtrl
        self._ci32(); self._ci32(); self._cbool()       # envCtrl
        self._ci32(); self._cstr(); self._cbool()       # outsideSoundCtrl

        bg = r.string()
        bg_kk = bg
        if bg:
            bg_kk = bg.replace("\\", "/").rsplit("/", 1)[-1]
        w.string(bg_kk)
        if bg != bg_kk:
            self.log(f'background "{bg}" -> "{bg_kk}"')
        self._cstr()               # frame

        # --- tail: "【KStudio】" + ExtensibleSaveFormat block, identical in KK ---
        tail = r.raw(r.remaining)
        try:
            mark = _Reader(tail).string()
        except (EOFError, ValueError, UnicodeDecodeError):
            mark = None
        if mark != _TAIL_MARK:
            self.log(f'warning: expected "{_TAIL_MARK}" after frame, got {mark!r}')
        else:
            changes: list[str] = []
            tail = _patch_scene_tail(tail, self.log, changes)
            if changes:
                self.timeline_renames.extend(changes)
                self.log("timeline owners renamed: " + ", ".join(changes))
        w.raw(tail)
        return w.to_bytes()


def convert_kks_scene(data: bytes, log: Callable[[str], None] | None = None
                      ) -> tuple[bytes, dict]:
    """Convert a KKS Studio scene (PNG bytes) to KK layout.

    Returns (converted_bytes, stats). Raises AlreadyKKSceneError when the
    scene is already KK-compatible and NotASceneError when the file is not
    a Studio scene. Any other exception means the scene data was malformed
    or uses a layout this transcoder does not understand."""
    t = _SceneTranscoder(data, log)
    out = t.transcode()
    return out, {
        "chars": t.chars,
        "items": t.items,
        "texts_dropped": t.texts_dropped,
        "block_versions": dict(t.block_versions),
        "timeline_renames": list(t.timeline_renames),
    }


class FilterConvertKKS:
    def __init__(self, config: Config, file_manager: FileManager):
        self.config          = config
        self.file_manager    = file_manager
        self.convert          = self.config.filter_convert_kks.get("Convert", False)
        self.kk_action        = self.config.filter_convert_kks.get("KKAction", ACTION_KEEP)
        self.kks_action       = self.config.filter_convert_kks.get("KKSAction", ACTION_KEEP)
        self.extract_archive  = self.config.filter_convert_kks.get("ExtractArchive", True)

    # ------------------------------------------------------------------
    # Card-type helpers
    # ------------------------------------------------------------------

    def get_list(self, folder_path: Path) -> list[Path]:
        out: list[Path] = []
        for f in folder_path.rglob("*.png"):
            if _OUTPUT_FOLDER_NAMES & {p.name for p in f.relative_to(folder_path).parents}:
                continue
            out.append(f)
        return out

    def check_png(self, card_path: Path) -> CardType:
        return get_card_type(card_path.read_bytes())

    @staticmethod
    def _scene_layout(data: bytes) -> str | None:
        """"KKS" / "KK" for a Studio scene, None if the header can't be read.
        get_card_type() can tell that a file is a scene but not which game
        saved it, so this reads the scene version string that follows the
        picture (KKS = 1.1.0.0 or newer)."""
        try:
            version = read_scene_version(data)
        except (SceneNotKKSError, EOFError, ValueError):
            return None
        return "KKS" if _ver(version) >= _ver("1.1.0.0") else "KK"

    # ------------------------------------------------------------------
    # Binary conversion helper
    # ------------------------------------------------------------------

    def _patch_kks_to_kk(self, card_path: Path) -> Path | None:
        """Patch a KKS card binary so it loads as a KK card, saving the
        result alongside the source card. Returns the new file's path, or
        None if it couldn't be written."""
        data = card_path.read_bytes()
        for old, new in [
            (b"\x15\xe3\x80\x90KoiKatuCharaSun", b"\x12\xe3\x80\x90KoiKatuChara"),
            (b"Parameter\xa7version\xa50.0.6",     b"Parameter\xa7version\xa50.0.5"),
            (b"version\xa50.0.6\xa3sex",            b"version\xa50.0.5\xa3sex"),
        ]:
            data = data.replace(old, new)
        out = card_path.parent / f"KKS2KK_{card_path.name}"
        try:
            out.write_bytes(data)
            return out
        except OSError as e:
            logger.error("SCRIPT", f"Could not write converted copy of {card_path.name}: {e}")
            return None

    def _convert_scene(self, scene_path: Path) -> Path | None:
        """Transcode a KKS scene to the KK layout, saving the result
        alongside the source scene. Returns the new file's path, or None
        if it couldn't be converted or written."""
        try:
            converted, stats = convert_kks_scene(scene_path.read_bytes())
        except AlreadyKKSceneError:
            return None
        except Exception as e:
            logger.error("SCRIPT", f"Could not convert scene {scene_path.name}: {e}")
            return None
        out = scene_path.parent / f"KKS2KK_{scene_path.name}"
        try:
            out.write_bytes(converted)
        except OSError as e:
            logger.error("SCRIPT", f"Could not write converted copy of {scene_path.name}: {e}")
            return None
        if stats["texts_dropped"]:
            logger.warning("SCRIPT",
                f"{scene_path.name}: {stats['texts_dropped']} Text object(s) removed (KK has none)")
        return out

    # ------------------------------------------------------------------
    # Sorting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_move(src: Path, dest_folder: Path) -> Path | None:
        """Move src into dest_folder, handling name clashes and I/O errors
        instead of letting shutil.move raise/clobber. Returns the final
        path, or None if the move failed."""
        import shutil
        dest = dest_folder / src.name
        if dest.exists() and not dest.samefile(src):
            stem, suffix = src.stem, src.suffix
            n = 1
            while dest.exists():
                dest = dest_folder / f"{stem}_{n}{suffix}"
                n += 1
        try:
            shutil.move(str(src), str(dest))
            return dest
        except OSError as e:
            logger.error("SCRIPT", f"Could not move {src.name}: {e}")
            return None

    def _apply_action(self, cards: list[Path], action: str, base_path: Path,
                       dest_folder_name: str, label: str) -> None:
        """Apply Keep/Move/Delete to a list of cards, logging one summary
        line for the whole batch. `base_path` is always the top-level
        folder this run scanned — not each card's own parent folder — so
        every KK/KKSP (or KKS) card ends up in exactly one shared
        `base_path/_KK_card_` (or `_KKS_card_`), including cards found
        inside an extracted archive's subfolder. Anchoring on a card's own
        parent instead would scatter cards into a different destination
        folder per source subfolder."""
        if not cards:
            logger.success("SCRIPT", f"No {label} cards found")
            return

        if action == ACTION_KEEP:
            logger.success("SCRIPT", f"[{len(cards)}] {label} card(s) found (kept in place)")
            return

        if action == ACTION_MOVE:
            dest_folder = base_path / dest_folder_name
            dest_folder.mkdir(exist_ok=True)
            moved_ok = sum(1 for c in cards if self._safe_move(c, dest_folder) is not None)
            logger.success("SCRIPT",
                f"[{moved_ok}] {label} card(s) -> [{dest_folder_name}]"
                + (f"  ({len(cards) - moved_ok} failed)" if moved_ok < len(cards) else ""))
            return

        if action == ACTION_DELETE:
            deleted_ok = 0
            for c in cards:
                try:
                    send2trash(str(c))
                    deleted_ok += 1
                except Exception as e:
                    logger.error("SCRIPT", f"Could not delete {c.name}: {e}")
            logger.success("SCRIPT",
                f"[{deleted_ok}] {label} card(s) sent to the Recycle Bin"
                + (f"  ({len(cards) - deleted_ok} failed)" if deleted_ok < len(cards) else ""))
            return

        logger.warning("SCRIPT", f"Unknown action '{action}' for {label} cards — leaving them in place")

    # ------------------------------------------------------------------
    # Archive extraction
    # ------------------------------------------------------------------

    def _extract_archives(self, path: Path) -> None:
        """Extract every archive found under `path` into a folder named after
        it, skipping archives whose folder already exists. The extracted
        folders are left in place."""
        _, archive_list = self.file_manager.find_all_files(path)
        if not archive_list:
            return
        logger.info("SCRIPT", f"Extracting {len(archive_list)} archive(s) before filtering")
        for archive in archive_list:
            self.file_manager.extract_archive(
                archive[0], task_config=self.config.filter_convert_kks)

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def run(self) -> None:
        path = Path(self.config.filter_convert_kks["InputPath"])

        validate_input_path("FILTER", path, default_path=DEFAULT_DOWNLOADS_PATH)

        # 1. Extract archives, if enabled.
        if self.extract_archive:
            self._extract_archives(path)

        png_list = self.get_list(path)
        if not png_list:
            logger.success("SCRIPT", "No PNG files found")
            return

        logger.info("SCRIPT", "KK: Koikatsu / KKSP: Koikatsu Special / KKS: Koikatsu Sunshine "
                              "(scenes are handled by version: KKS scene = 1.1.0.0+)")
        logger.line()
        logger.info("FOLDER", str(path))

        kks_cards: list[Path] = []   # KKS character cards
        kk_cards:  list[Path] = []   # KK / KKSP character cards
        kks_scenes: list[Path] = []  # KKS Studio scenes

        for png in png_list:
            try:
                data = png.read_bytes()
            except OSError as e:
                logger.error("SCRIPT", f"Could not read {png.name}: {e}")
                continue

            card_type = get_card_type(data)
            if card_type == CardType.SCENE:
                # Only KKS scenes need work; KK scenes are left where they are.
                if self._scene_layout(data) == "KKS":
                    logger.info("KKS SCENE", png.name)
                    kks_scenes.append(png)
            elif card_type == CardType.KKS:
                logger.info(card_type.value, png.name)
                kks_cards.append(png)
            elif card_type in (CardType.KK, CardType.KKSP):
                logger.info(card_type.value, png.name)
                kk_cards.append(png)

        logger.line()

        # 2. Convert KKS -> KK, if enabled. Each converted copy is written
        # next to its source and joins kk_cards, so KKAction below applies
        # to it exactly like any other KK card.
        if self.convert and kks_cards:
            converted = 0
            for card in kks_cards:
                new_card = self._patch_kks_to_kk(card)
                if new_card is not None:
                    if new_card not in kk_cards:  # a previous run's copy is already listed
                        kk_cards.append(new_card)
                    converted += 1
            logger.success("SCRIPT", f"[{converted}] KKS card(s) converted to KK")
            if converted < len(kks_cards):
                logger.warning("SCRIPT",
                    f"[{len(kks_cards) - converted}] KKS card(s) could not be converted")

        # 2b. Same for KKS scenes: the converted copy is a KK scene, so it
        # joins kk_scenes and is handled by KKAction; the original KKS scene
        # is handled by KKSAction.
        kk_scenes: list[Path] = []
        if self.convert and kks_scenes:
            converted = 0
            for scene in kks_scenes:
                new_scene = self._convert_scene(scene)
                if new_scene is not None:
                    kk_scenes.append(new_scene)
                    converted += 1
            logger.success("SCRIPT", f"[{converted}] KKS scene(s) converted to KK")
            if converted < len(kks_scenes):
                logger.warning("SCRIPT",
                    f"[{len(kks_scenes) - converted}] KKS scene(s) could not be converted")

        # 3. Run KKSAction / KKAction, unless left at Keep.
        self._apply_action(kks_cards, self.kks_action, path, "_KKS_card_", "KKS")
        self._apply_action(kk_cards,  self.kk_action,  path, "_KK_card_",  "KK/KKSP")
        if kks_scenes:
            self._apply_action(kks_scenes, self.kks_action, path, "_KKS_card_", "KKS scene")
        if kk_scenes:
            self._apply_action(kk_scenes,  self.kk_action,  path, "_KK_card_",  "converted KK scene")
