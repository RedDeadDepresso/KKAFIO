"""The image sizes that identify a KK card or scene.

Shared by the sources that have to decide, from the image header alone,
whether a PNG is worth downloading (pixiv, pawchive).
"""

from __future__ import annotations

# KK scene thumbnails are exactly 320x180; character/coordinate cards are
# 252x352 — but a re-saved card may be scaled, so any image with that aspect
# ratio (within a small tolerance for rounding) is accepted.
KK_SCENE_SIZE = (320, 180)
KK_CARD_SIZE  = (252, 352)
KK_RATIO_TOLERANCE = 0.01   # relative


def kk_size_matches(width, height) -> bool:
    """True for exactly 320x180, or any image with the 252x352 aspect ratio."""
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0:
        return False
    if (w, h) == KK_SCENE_SIZE:
        return True
    target = KK_CARD_SIZE[0] / KK_CARD_SIZE[1]
    return abs(w / h - target) <= target * KK_RATIO_TOLERANCE
