"""Converting a Koikatsu Sunshine Studio scene into one the original Koikatsu can load."""

import struct
from typing import Callable

from kkafio.cards.msgpack_min import _mp_decode, _mp_encode, _MpMap
from kkafio.cards.scene_version import (
    _png_length,
    _Reader,
    _ver,
    AlreadyKKSceneError,
    read_scene_version,
)


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
