"""
lenient_load.py — load a chara card that KoiCardTexTool wrote with a damaged extended-data key.

When KoiCardTexTool compresses a card whose MaterialEditor texture dictionary is under 64 KB, it
replaces the dictionary's msgpack header assuming it was 5 bytes (bin32) when it was really 3
(bin16). The new 5-byte header lands 2 bytes too early and overwrites the last two characters of
the key, so the card holds "\\xb1TextureDictiona\\xc6..." instead of "\\xb1TextureDictionary\\xc6...".
kkloader then fails with "'utf-8' codec can't decode byte ..." and the card's extended data
(KKEx: mods, plugin data) can't be read. Nothing but those two characters is lost, so putting them
back repairs the card. This is done in memory; the file on disk is never changed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from kkloader import KoikatuCharaData

from kkafio.cards.chara.kkex_repair import repair_card

def load_chara(path: str | Path, cls=KoikatuCharaData) -> KoikatuCharaData:
    """KoikatuCharaData.load(path); when that fails to decode the card's extended data and the card
    has the KoiCardTexTool damage above, the repaired card loaded instead. `cls` is the loader
    class, so a caller (or a test) that substitutes KoikatuCharaData keeps doing so."""
    try:
        return cls.load(str(path))
    except UnicodeDecodeError as first:
        repaired = repair_card(Path(path).read_bytes())
        if repaired is None:
            raise first
        fd, tmp = tempfile.mkstemp(suffix=".png")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(repaired)
            return cls.load(tmp)
        except Exception:
            raise first
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
