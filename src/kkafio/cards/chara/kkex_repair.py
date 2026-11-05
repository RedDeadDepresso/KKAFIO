"""
kkex_repair.py — put right the damage KoiCardTexTool does to a card's "TextureDictionary" key.

When KoiCardTexTool compresses a card whose MaterialEditor texture dictionary is under 64 KB, it
replaces the dictionary's msgpack header assuming it was 5 bytes (bin32) when it was really 3
(bin16). The new 5-byte header lands 2 bytes too early and overwrites the last two characters of
the key, so the card holds "\\xb1TextureDictiona\\xc6..." instead of "\\xb1TextureDictionary\\xc6...".
Readers then fail on the extended data (KKEx: mods, plugin data) with "'utf-8' codec can't
decode byte ...". Nothing but those two characters is lost, so putting them back repairs it.
Works on a whole card or on just its KKEx block; it never touches a file, only the bytes given.
"""

from __future__ import annotations

# str "TextureDictionary" is 17 bytes (fixstr 0xb1); the damaged key has 15 of them, then the bin32 marker
_DAMAGED = b"\xb1TextureDictiona\xc6"
_REPAIRED = b"\xb1TextureDictionary\xc6"


def repair_card(raw: bytes) -> bytes | None:
    """`raw` (a card, or its KKEx block) with the damaged "TextureDictionary" key restored, or
    None when it doesn't have that damage."""
    if raw.count(_DAMAGED) != 1:
        return None
    return raw.replace(_DAMAGED, _REPAIRED)
