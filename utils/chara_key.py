"""
chara_key.py — Build the "name | personality | hair_color" identification
key used by both GroupChara and RenameChara to describe a chara card to an
LLM and to match the LLM's JSON response back to the right file.

This was previously copy-pasted (with tiny formatting drift) into both
tasks/group_chara.py and tasks/rename_chara.py; kept in one place now so a
future change to how a character is identified doesn't need to be made
twice, and can't quietly drift into producing different keys for the same
card depending on which task built it.
"""

from __future__ import annotations

from kkloader import KoikatuCharaData

from utils.classifier import PERSONALITIES, get_simple_color_description


def unity_to_rgb(r: float, g: float, b: float) -> tuple[int, int, int]:
    """Convert Unity's 0.0-1.0 float color channels to 0-255 ints."""
    return (int(r * 255), int(g * 255), int(b * 255))


def hair_color(kc: KoikatuCharaData) -> tuple[int, int, int]:
    """Best-effort base hair color for a loaded chara card, as 0-255 RGB."""
    parts = kc["Custom"]["hair"]["parts"]
    for i, part in enumerate(parts):
        if part.get("id", 0) == 0 and i != 1:
            continue
        if i == 3:
            continue
        base = part.get("baseColor")
        if not base:
            continue
        # baseColor is a list [r, g, b, a] of 0.0-1.0 floats
        if isinstance(base, (list, tuple)) and len(base) >= 3:
            return unity_to_rgb(base[0], base[1], base[2])
        # Fallback: dict with r/g/b keys
        if isinstance(base, dict):
            vals = list(base.values())
            if len(vals) >= 3:
                return unity_to_rgb(vals[0], vals[1], vals[2])
    return (0, 0, 0)


def make_key(kc: KoikatuCharaData) -> str:
    """Stable, deterministic "name | personality | hair_color" key.

    Used both when exporting cards to an LLM prompt and when matching the
    LLM's JSON response back to the file(s) it describes — the two sides
    must build byte-for-byte the same key for a given card, which is the
    whole reason this lives in one place instead of two.
    """
    name            = kc._repr_name()
    personality_idx = kc["Parameter"]["personality"]
    personality     = (PERSONALITIES[personality_idx]
                        if personality_idx < len(PERSONALITIES) else str(personality_idx))
    color           = get_simple_color_description(hair_color(kc))
    return f"{name} | {personality} | {color} hair"
