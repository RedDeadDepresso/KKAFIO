"""
similarity.py — Perceptual-hash helpers for comparing card cover images.

Used by Review Similar Characters. (They used to live in
filter_duplicate_contents.py, back when that task had a fuzzy-matching option.)

A Koikatsu card is a PNG preview image with the character data appended after
the IEND chunk, so a card can be tens of MB while its preview is a few KB.
Everything here reads only the preview part of the file.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_IEND = b"IEND"

# Hamming distance (out of 64 bits) at or below which two pHashes count as
# "the same cover".
PHASH_THRESHOLD = 8


# ---------------------------------------------------------------------------
# Reading just the preview image of a card
# ---------------------------------------------------------------------------

def read_png_preview(path: Path) -> bytes | None:
    """Return the PNG image portion of `path` (signature through IEND), or
    None if it isn't a well-formed PNG.

    Reads chunk by chunk and stops at IEND, so the (possibly huge) character
    data after it is never loaded.
    """
    try:
        with path.open("rb") as f:
            sig = f.read(8)
            if sig != _PNG_SIG:
                return None
            out = [sig]
            while True:
                header = f.read(8)
                if len(header) < 8:
                    return None
                length = struct.unpack(">I", header[:4])[0]
                body = f.read(length + 4)          # chunk data + CRC
                if len(body) < length + 4:
                    return None
                out.append(header)
                out.append(body)
                if header[4:8] == _IEND:
                    return b"".join(out)
    except OSError:
        return None


def phash_of_png_bytes(image_bytes: bytes) -> str | None:
    """64-bit perceptual hash (16 hex chars) of PNG bytes, or None on failure."""
    try:
        import imagehash
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def phash_of_file(path: Path) -> str | None:
    """Perceptual hash of a card's preview image, or None if it can't be read."""
    preview = read_png_preview(path)
    return phash_of_png_bytes(preview) if preview else None


# ---------------------------------------------------------------------------
# Grouping — leader clustering over 64-bit pHashes (numpy-vectorised)
# ---------------------------------------------------------------------------

_POPCOUNT8 = None  # lazily-built byte popcount table (numpy < 2.0 fallback)


def hash_to_int(ph: str | None) -> int | None:
    """Parse a 64-bit imagehash hex string into an int (None if unusable)."""
    if not ph or len(ph) != 16:
        return None
    try:
        return int(ph, 16)
    except ValueError:
        return None


def _hamming_to_many(hashes, value):
    """Hamming distance between `value` and every element of a uint64 array."""
    import numpy as np
    x = np.bitwise_xor(hashes, np.uint64(value))
    if hasattr(np, "bitwise_count"):           # numpy >= 2.0
        return np.bitwise_count(x)
    global _POPCOUNT8
    if _POPCOUNT8 is None:
        _POPCOUNT8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    return _POPCOUNT8[x.view(np.uint8)].reshape(-1, 8).sum(axis=1)


def fuzzy_group(paths: list[Path], phashes: list[str | None],
                threshold: int = PHASH_THRESHOLD) -> list[list[Path]]:
    """Group paths by perceptual similarity (paths/phashes are parallel lists;
    a None phash means "couldn't be computed" and is never grouped).

    Leader clustering: each group is defined by its FIRST member (the
    "leader"). A path joins the group of the nearest leader within
    `threshold` bits, otherwise it starts a new group of its own. Every
    member of a group is therefore within `threshold` of the group's first
    member, and a chain of gradually different covers (A~B, B~C, A≁C) can't
    merge into one big group the way a transitive grouping would.

    Results depend on input order (the first card of a cluster is its
    leader), so callers should pass a stable, sorted order.

    Singletons are returned too (callers filter on len > 1).
    """
    import numpy as np

    groups: list[list[Path]] = []
    leaders = np.zeros(len(paths), dtype=np.uint64)
    leader_group: list[int] = []   # leader index -> index into `groups`

    for path, ph in zip(paths, phashes, strict=True):
        value = hash_to_int(ph)
        if value is None:
            groups.append([path])
            continue
        if leader_group:
            dist = _hamming_to_many(leaders[:len(leader_group)], value)
            best = int(dist.argmin())            # ties -> earliest leader
            if int(dist[best]) <= threshold:
                groups[leader_group[best]].append(path)
                continue
        leaders[len(leader_group)] = value
        leader_group.append(len(groups))
        groups.append([path])

    return groups
