"""Sideloader Modpack index: {mod GUID: path inside the modpack}.

Pre-built by tools/build_modpack_index.py and shipped in assets/data/ (one file per game
type), so it never needs downloading. It lets Download Missing Mods, Archive Cards, Delete
Unused Mods and the mods scan tell which GUIDs the Sideloader Modpack already provides
without opening every zipmod in the modpack folders.
"""

import json
from functools import cache

from kkafio.core.config import GameType
from kkafio.core.logger import logger
from kkafio.core.paths import ASSETS_DATA_DIR

MODPACK_INDEX_FILE_KK  = "kk_modpack_index.json"

MODPACK_INDEX_FILE_KKS = "kks_modpack_index.json"


@cache
def load_modpack_index(game_type: str = GameType.KOIKATSU.value) -> dict[str, str] | None:
    """Load the pre-built Sideloader Modpack GUID index for the given game type.

    Uses kks_modpack_index.json for KoikatsuSunshine,
    and kk_modpack_index.json for all other variants.

    Looked up at assets/<index_file> next to the running exe / script (this
    is always where it's shipped with the release — see docs/01-project-
    structure.md).

    Returns {guid: relative_path_str} or None if not found.

    Cached (per game_type) for the lifetime of the process — the index file
    doesn't change mid-run, and this is otherwise re-read and re-parsed
    from disk on every call (e.g. once per card in DeleteCards, which calls
    scan_mods() → load_modpack_index() per file).
    """
    index_file = (
        MODPACK_INDEX_FILE_KKS
        if game_type == GameType.KOIKATSU_SUNSHINE.value
        else MODPACK_INDEX_FILE_KK
    )

    index_path = ASSETS_DATA_DIR / index_file

    if index_path.exists():
        try:
            with index_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            guids = data.get("guids", {})
            logger.info("CACHE",
                f"Modpack index loaded: {len(guids)} GUIDs from {index_path.name}")
            return guids
        except Exception as e:
            logger.warning("CACHE", f"Could not load modpack index: {e}")
    return None
