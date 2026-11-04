"""Builds tiny synthetic cards/scenes/coordinates/zipmods for tests (no game files needed)."""

import struct
import zlib
import zipfile
from pathlib import Path

import msgpack

UAR = "com.bepis.sideloader.universalautoresolver"
CHARA_MARKER = "【KoiKatuChara】"
COORD_MARKER = "【KoiKatuClothes】"
SCENE_MARKER = "【KStudio】"


def png_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _s(text: str) -> bytes:
    raw = text.encode("utf-8")
    n, prefix = len(raw), b""
    while True:                                    # .NET 7-bit encoded length
        byte = n & 0x7F
        n >>= 7
        prefix += bytes([byte | (0x80 if n else 0)])
        if not n:
            break
    return prefix + raw


def kkex(guids, ext_id: str = UAR) -> bytes:
    infos = [msgpack.packb({"ModID": g, "Slot": 1, "LocalSlot": 1000 + i, "Property": "x"})
             for i, g in enumerate(guids)]
    return msgpack.packb({ext_id: {1: {"info": infos}}})


def chara_card(guids, marker: str = CHARA_MARKER, with_kkex: bool = True) -> bytes:
    kk = kkex(guids)
    lst = [{"name": "KKEx", "pos": 0, "size": len(kk)}] if with_kkex else [{"name": "Other", "pos": 0, "size": 0}]
    header = msgpack.packb({"lstInfo": lst})
    payload = (struct.pack("<i", 100) + _s(marker) + _s("0.0.0") + struct.pack("<i", 0)
               + struct.pack("<i", len(header)) + header + struct.pack("<q", len(kk)) + kk)
    return png_bytes() + payload


def coord_card(guids, marker: str = COORD_MARKER, product_no: int = 100) -> bytes:
    kk = kkex(guids)
    payload = struct.pack("<i", product_no) + _s(marker) + _s("1.0.0")
    if "AIS" in marker:
        payload += b"\x00\x00\x00\x00"
    payload += _s("coord name") + struct.pack("<i", 3) + b"abc"
    payload += _s("KKEx") + struct.pack("<i", 1) + struct.pack("<i", len(kk)) + kk
    return png_bytes() + payload


def scene_card(guids_per_value, ext_id: str = UAR) -> bytes:
    """A scene: arbitrary bytes, the KStudio tail mark, and one UAR value per list in `guids_per_value`."""
    blob = b"\x00junk" + SCENE_MARKER.encode("utf-8")
    for guids in guids_per_value:
        infos = [msgpack.packb({"ModID": g}) for g in guids]
        blob += b"\x01" + ext_id.encode("utf-8") + msgpack.packb({1: {"info": infos}})
    return png_bytes() + blob


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def zipmod(path: Path, guid: str | None, manifest_name: str = "manifest.xml") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        if guid is not None:
            zf.writestr(manifest_name, f"<manifest><guid>{guid}</guid><name>x</name></manifest>")
        else:
            zf.writestr("readme.txt", "not a mod")
    return path
