"""
hair_color.py — Name a hair colour the way a person (or an LLM) would.

The character key sent to the LLM ends with "<colour> hair". The colour used to be the nearest
of ~930 XKCD survey names, which gave names nobody calls hair ("puce", "sea", "marine",
"mango", "pale grey"). This maps an RGB colour to a short, fixed hair vocabulary instead:

    black, blue-black, dark grey, grey, silver, white,
    dark brown, brown, light brown, dark blonde, blonde, platinum,
    ginger, orange, yellow, red, dark red, pink, magenta, purple, dark purple, lavender,
    blue, light blue, navy, teal, green, dark green, light green

The colour is classified by hue, saturation and value (HSV), so the thresholds below can be
tuned in one place. Pure Python: no numpy / scipy.
"""

from __future__ import annotations

import colorsys

UNKNOWN = "unknown"


def hair_color_name(rgb: tuple[int, int, int] | None) -> str:
    """The hair colour name of a 0-255 RGB colour, or ``unknown`` for None."""
    if rgb is None:
        return UNKNOWN
    r, g, b = (max(0, min(255, int(c))) / 255.0 for c in rgb[:3])
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hue = h * 360.0

    # --- very dark: black, with a tint when there is a clear one -------------------------
    bluish = s >= 0.3 and 200 <= hue < 270
    if v < 0.25 and bluish:
        return "blue-black"
    if v < 0.15 or (v < 0.25 and s < 0.12):
        return "black"

    # --- (almost) no colour: black / grey / silver / white -------------------------------
    if s < 0.12:
        if v >= 0.93:
            return "white"
        if v >= 0.72:
            return "silver"
        if v >= 0.35:
            return "grey"
        return "dark grey"

    # --- warm hair: red, ginger, orange, brown, blonde, yellow, pink ---------------------
    if hue >= 345 or hue < 14:                                   # red side
        if s >= 0.5:
            return "dark red" if v < 0.45 else "red"
        return "pink" if v >= 0.7 else "brown"
    if hue >= 320:                                               # pinks / wine
        return "burgundy" if v < 0.45 else "pink"
    if hue < 70:                                                 # orange .. yellow
        return _warm(hue, s, v)

    # --- the rest of the wheel ----------------------------------------------------------
    light = v >= 0.8 and s < 0.55
    if hue < 165:
        return "dark green" if v < 0.4 else ("light green" if light else "green")
    if hue < 195:
        return "teal"
    if hue < 255:
        return "navy" if v < 0.4 else ("light blue" if light else "blue")
    if hue < 290:
        return "dark purple" if v < 0.4 else ("lavender" if light else "purple")
    # 290-320: magenta / purple
    if v < 0.6:
        return "dark purple"
    return "magenta" if s >= 0.5 else "pink"


def _warm(hue: float, s: float, v: float) -> str:
    """Hue 14-70: the natural hair colours (brown, blonde, ginger ...) and orange / yellow."""
    if s < 0.25:                                                 # washed out
        if v >= 0.85:
            return "platinum"
        return "light brown" if v >= 0.55 else "brown"
    if v < 0.3:
        return "dark brown"
    if hue >= 50:                                                # yellow
        if v >= 0.75:
            return "yellow" if s >= 0.6 else "blonde"
        return "dark blonde" if v >= 0.5 else "brown"
    if v >= 0.7 and s >= 0.65:                                   # vivid
        return "ginger" if hue < 28 else "orange"
    if v < 0.38:
        return "dark brown"
    if v < 0.55:
        return "brown"
    if hue >= 36:                                                # yellow-ish brown
        return "blonde" if v >= 0.8 else "dark blonde"
    return "light brown"
