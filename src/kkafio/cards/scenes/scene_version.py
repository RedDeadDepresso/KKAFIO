"""
scene_version.py — Read-only helpers for telling Koikatsu (KK) and Koikatsu
Sunshine (KKS) Studio scenes apart.

A Studio scene is a PNG picture followed by the scene data, which starts with
a version string. KK writes 1.0.x.x; KKS writes 1.1.0.0 or newer and uses a
different data layout that KK cannot load. get_card_type() can tell that a
file *is* a scene (it carries the "KStudio" tail mark) but not which game
saved it, so this module reads the version string.

Shared by FilterConvertKKS (which converts KKS scenes to KK) and by
InstallContents / UninstallContents (which must not put a KKS scene into a
Koikatsu / Koikatsu Party install).
"""

import struct

_PNG_SIG = b"\x89PNG\r\n\x1a\n"

# First scene version written by Koikatsu Sunshine.
KKS_SCENE_MIN_VERSION = "1.1.0.0"


class SceneNotKKSError(Exception):
    """Base class for 'this file is not a KKS scene we can convert'."""


class AlreadyKKSceneError(SceneNotKKSError):
    """The scene is already in the KK layout (version < 1.1.0.0)."""


class NotASceneError(SceneNotKKSError):
    """The file is not a Studio scene (plain picture, chara card, ...)."""


def _ver(s: str) -> tuple[int, int, int, int]:
    """System.Version-like: missing parts compare as 0."""
    parts = [int(x) for x in s.split(".")]
    parts = (parts + [0, 0, 0, 0])[:4]
    return tuple(parts)  # type: ignore[return-value]


class _Reader:
    """BinaryReader-compatible reader over a bytes object."""

    def __init__(self, data: bytes, pos: int = 0):
        self.b = data
        self.p = pos

    @property
    def remaining(self) -> int:
        return len(self.b) - self.p

    def raw(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.b):
            raise EOFError(f"read past end at {self.p} (+{n})")
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def i32(self) -> int:
        return struct.unpack("<i", self.raw(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.raw(8))[0]

    def boolean(self) -> bool:
        return self.raw(1)[0] != 0

    def string(self) -> str:
        """BinaryReader.ReadString: 7-bit encoded byte length + UTF-8."""
        n = shift = 0
        while True:
            c = self.raw(1)[0]
            n |= (c & 0x7F) << shift
            if not c & 0x80:
                break
            shift += 7
            if shift > 35:
                raise ValueError(f"bad 7-bit string length at {self.p}")
        return self.raw(n).decode("utf-8")


def _is_version_string(s: str) -> bool:
    """"1.1.2.1"-style: 2 to 4 dot-separated numbers."""
    parts = s.split(".")
    if not 2 <= len(parts) <= 4:
        return False
    return all(p.isdigit() and p.isascii() and len(p) <= 9 for p in parts)


def _describe_non_scene(data: bytes, start: int) -> str | None:
    """Character / coordinate cards store int32 productNo + a mark string.
    Returns what the data at `start` looks like, or None if unrecognised."""
    try:
        r = _Reader(data, start)
        r.i32()
        mark = r.string()
        if mark.startswith("【KoiKatuChara"):
            return f"this is a character card ({mark})"
        if mark.startswith("【KoiKatuClothes"):
            return f"this is a coordinate card ({mark})"
    except (EOFError, ValueError, UnicodeDecodeError):
        pass
    return None


def _png_length(data: bytes) -> int:
    """Length of the leading PNG image (up to and including IEND)."""
    if len(data) < 8:
        raise NotASceneError(f"file too small ({len(data)} bytes)")
    if data[:8] != _PNG_SIG:
        raise NotASceneError(_describe_non_scene(data, 0) or "not a PNG file")
    p = 8
    while True:
        if p + 8 > len(data):
            raise EOFError(f"truncated PNG chunk at {p}")
        length = struct.unpack(">i", data[p:p + 4])[0]
        iend = data[p + 4:p + 8] == b"IEND"
        p += 12 + length
        if iend:
            return p


def read_scene_version(data: bytes) -> str:
    """The scene version string that follows the leading PNG. Raises
    NotASceneError if the file is not a Studio scene."""
    r = _Reader(data, _png_length(data))
    if r.remaining == 0:
        raise NotASceneError("nothing follows the PNG image (a plain picture)")
    start = r.p
    v = None
    try:
        v = r.string()
    except (EOFError, ValueError, UnicodeDecodeError):
        pass
    if v is not None and _is_version_string(v):
        return v
    raise NotASceneError(_describe_non_scene(data, start)
                         or "the data after the PNG image is not a scene header")


def is_kks_scene(data: bytes) -> bool:
    """True if `data` is a Studio scene in the KKS layout (version >= 1.1.0.0)."""
    try:
        return _ver(read_scene_version(data)) >= _ver(KKS_SCENE_MIN_VERSION)
    except (SceneNotKKSError, EOFError, ValueError):
        return False
