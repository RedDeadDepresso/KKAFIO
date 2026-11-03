"""Sideloader Modpack index: {mod GUID: [download URL, ...]}.

Pre-built by tools/build_modpack_index.py from the BetterRepack server's listings and
shipped in assets/data/ (one file per game type), so it never needs downloading. It lets
Download Missing Mods, Archive Cards, Delete Unused Mods and the mods scan tell which GUIDs
the Sideloader Modpack already provides without opening every zipmod in the modpack folders.

Each GUID has one or more URLs, best first: the later ones are other copies or versions of
the same mod, tried when an earlier one fails. The path of a mod inside the modpack folder is
not stored; it is the URL's path below the server's base (see url_to_rel_path).
"""

import json
from functools import cache
from urllib.parse import unquote, urlparse

from kkafio.core.config import GameType
from kkafio.core.logger import logger
from kkafio.core.paths import ASSETS_DATA_DIR

MODPACK_INDEX_FILE_KK  = "kk_modpack_index.json"

MODPACK_INDEX_FILE_KKS = "kks_modpack_index.json"

MODPACK_HOST        = "sideload.betterrepack.com"
MODPACK_PATH_PREFIX = "/download/KKEC/"


def url_to_rel_path(url: str) -> str | None:
    """The path of a modpack URL below the server's base, "/"-separated and percent-decoded
    ("Sideloader Modpack/Author/[a] mod.zipmod"), or None if the URL isn't a usable modpack
    URL: another host, outside the base, or a path that could leave the mods folder."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != MODPACK_HOST:
        return None
    path = unquote(parsed.path)
    if not path.startswith(MODPACK_PATH_PREFIX):
        return None
    rel = path[len(MODPACK_PATH_PREFIX):]
    parts = rel.split("/")
    if len(parts) < 2 or any(p in ("", ".", "..") or "\\" in p for p in parts):
        return None
    return rel


@cache
def load_modpack_index(game_type: str = GameType.KOIKATSU.value) -> dict[str, list[str]] | None:
    """Load the pre-built Sideloader Modpack index for the given game type.

    Uses kks_modpack_index.json for KoikatsuSunshine,
    and kk_modpack_index.json for all other variants.

    Looked up at assets/<index_file> next to the running exe / script (this
    is always where it's shipped with the release — see docs/01-project-
    structure.md).

    Returns {guid: [url, ...]} (best URL first) or None if the file is missing or
    isn't in the current format (rebuild it with tools/build_modpack_index.py).

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
            guids = data["guids"]
            if not all(isinstance(urls, list) for urls in guids.values()):
                raise ValueError("not a {guid: [url, ...]} index — rebuild it with tools/build_modpack_index.py")
            logger.info("CACHE",
                f"Modpack index loaded: {len(guids)} GUIDs from {index_path.name}")
            return guids
        except Exception as e:
            logger.warning("CACHE", f"Could not load modpack index: {e}")
    return None
