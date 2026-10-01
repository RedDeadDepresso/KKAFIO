"""Zipmods: reading their GUIDs, scanning a mods folder (with an incremental cache), the bundled
Sideloader Modpack index, and resolving a card's mods/coordinate folders.
"""

import json as _json
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import as_completed, ThreadPoolExecutor
from functools import cache
from pathlib import Path

from kkafio.cards.cache_io import _atomic_write_json, _file_fp
from kkafio.cards.classifier import is_mod_archive
from kkafio.core.config import GameType
from kkafio.core.logger import logger
from kkafio.core.paths import ASSETS_DATA_DIR


_SIDELOADER_MODPACK = "Sideloader Modpack"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

MODS_CACHE_FILE  = "kkafio_mods_cache.json"

MODPACK_INDEX_FILE_KK  = "kkafio_modpack_index_kk.json"

MODPACK_INDEX_FILE_KKS = "kkafio_modpack_index_kks.json"


@cache
def load_modpack_index(game_type: str = GameType.KOIKATSU.value) -> dict[str, str] | None:
    """Load the pre-built Sideloader Modpack GUID index for the given game type.

    Uses kkafio_modpack_index_kks.json for KoikatsuSunshine,
    and kkafio_modpack_index_kk.json for all other variants.

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
                data = _json.load(f)
            guids = data.get("guids", {})
            logger.info("CACHE",
                f"Modpack index loaded: {len(guids)} GUIDs from {index_path.name}")
            return guids
        except Exception as e:
            logger.warning("CACHE", f"Could not load modpack index: {e}")
    return None


# ---------------------------------------------------------------------------
# Mods cache  (incremental)
# ---------------------------------------------------------------------------

def save_mods_cache(mods_dir: Path, guid_map: dict[str, str],
                    files: dict | None = None) -> None:
    """Persist {guid: str(path)} (and optional file fingerprints) to cache."""
    cache_path = mods_dir / MODS_CACHE_FILE
    data: dict = {
        "mods_dir":   str(mods_dir),
        "file_count": len(files) if files is not None else len(guid_map),
        "guids":      guid_map,
    }
    if files is not None:
        data["files"] = files
    try:
        _atomic_write_json(cache_path, data)
    except Exception as e:
        logger.debug("CACHE", f"Could not save {cache_path}: {e}")


def build_mods_cache(mods_dir: Path, include_modpack: bool = False,
                     use_cache: bool = True) -> dict[str, str]:
    """Incrementally scan zipmods and return {guid: str(abs_path)}.

    Unchanged files (same mtime + size) are reused from the previous cache.
    Only new or changed zipmods are opened. Deleted files (among the ones
    actually in scope for this call's include_modpack setting) are pruned
    automatically — callers never need a separate staleness check before
    calling this.

    When `use_cache` is False, does a full scan every time and does not
    read or write the cache file.

    The cache file is shared between include_modpack=True and
    include_modpack=False callers on the same mods_dir. Saved entries for
    files outside the *current* call's filter (e.g. Sideloader Modpack
    zipmods during an include_modpack=False call) are carried forward
    untouched rather than dropped, so alternating between the two modes
    across different tasks never forces a full rescan of "the other side"
    — each mode's own cached entries persist until that mode is the one
    actually looking at them again.
    """
    cache_path = mods_dir / MODS_CACHE_FILE
    old_files: dict = {}
    if use_cache:
        try:
            prev = _json.loads(cache_path.read_text(encoding="utf-8"))
            if prev.get("mods_dir") == str(mods_dir):
                old_files = {sp: fp for sp, fp in prev.get("files", {}).items()
                             if isinstance(fp, list) and len(fp) == 3}
        except FileNotFoundError:
            pass                     # first run — nothing cached yet
        except Exception as e:
            logger.debug("CACHE", f"Ignoring unreadable {cache_path}: {e}")

    # Only iterate files actually present on disk — deleted files are implicitly pruned
    all_zips = [
        zp for zp in iter_mod_files(mods_dir)
        if include_modpack or not in_modpack_folder(zp, mods_dir)
    ]
    all_zip_strs = {str(zp) for zp in all_zips}

    guid_map:  dict[str, str] = {}
    # Carry forward everything already cached — including entries this
    # call's filter doesn't cover — so they aren't lost when we save below.
    new_files: dict       = dict(old_files)
    to_read:   list[Path] = []

    for zp in all_zips:
        sp = str(zp)
        fp = _file_fp(zp)
        old = old_files.get(sp)
        if old is not None and (old[0], old[1]) == fp:
            guid = old[2]
            if guid:
                guid_map[guid] = sp
        else:
            to_read.append(zp)

    # Prune stale entries for files that no longer exist — but only among
    # ones that would have been in scope for this call's filter. An entry
    # outside that scope (e.g. a modpack zipmod during an
    # include_modpack=False call) is left alone entirely; if it was
    # actually deleted, the next call that has it in scope will notice.
    for sp in list(new_files.keys()):
        if sp in all_zip_strs:
            continue
        p = Path(sp)
        try:
            in_scope = include_modpack or not in_modpack_folder(p, mods_dir)
        except Exception:
            in_scope = True
        if in_scope and not p.exists():
            del new_files[sp]

    reused = len(all_zips) - len(to_read)
    if reused:
        logger.info("CACHE", f"Mods cache: {reused} unchanged, {len(to_read)} new/changed")
    elif to_read:
        logger.info("CACHE", f"Scanning {len(to_read)} mod file(s) for GUIDs...")

    import os
    workers = min(32, (os.cpu_count() or 4) * 2)

    def _proc(zp: Path):
        return zp, guid_from_zipmod(zp)

    if to_read:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for future in as_completed({ex.submit(_proc, zp): zp for zp in to_read}):
                zp, guid = future.result()
                sp = str(zp)
                fp = _file_fp(zp)
                new_files[sp] = [fp[0], fp[1], guid]
                if guid:
                    guid_map[guid] = sp

    if use_cache:
        save_mods_cache(mods_dir, guid_map, new_files)
    return guid_map


# ---------------------------------------------------------------------------
# Zipmod scanner
# ---------------------------------------------------------------------------

def guid_from_zipmod(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            manifest = next(
                (n for n in zf.namelist() if n.lower().endswith("manifest.xml")),
                None,
            )
            if not manifest:
                return None
            root = ET.fromstring(zf.read(manifest))
            el = root.find("guid")
            return el.text.strip() if el is not None and el.text else None
    except Exception:
        return None


def iter_mod_files(mods_dir: Path):
    """Yield every mod archive under mods_dir: every ".zipmod" file, plus
    every plain ".zip" file that actually contains a manifest.xml (some
    mods are distributed without ever being renamed to ".zipmod"). Callers
    that used to do `mods_dir.rglob("*.zipmod")` should use this instead so
    plain-.zip mods aren't silently skipped."""
    yield from mods_dir.rglob("*.zipmod")
    for zp in mods_dir.rglob("*.zip"):
        if is_mod_archive(zp):
            yield zp


def in_modpack_folder(zp: Path, mods_dir: Path) -> bool:
    """Return True if zp lives inside a first-level Sideloader Modpack subfolder."""
    try:
        rel = zp.relative_to(mods_dir)
    except ValueError:
        return False
    return len(rel.parts) >= 2 and _SIDELOADER_MODPACK in rel.parts[0]


def scan_mods(mods_dir: Path, required: set[str],
              include_modpack: bool = False,
              use_cache: bool = False,
              game_type: str = GameType.KOIKATSU.value) -> dict[str, Path]:
    """Scan mods_dir for zipmods providing the required GUIDs.

    Fast path: if kkafio_modpack_index.json exists, GUIDs found there are
    resolved instantly (no file I/O per zipmod). GUIDs not in the index are
    resolved by scanning local non-modpack folders.

    When use_cache=True, also loads/builds a mods cache for the local scan.
    """
    found: dict[str, Path] = {}
    if not mods_dir.exists():
        return found

    remaining = set(required)

    # ── Step 1: check modpack index ────────────────────────────────────────
    modpack_index = load_modpack_index(game_type=game_type)

    if modpack_index is not None:
        for guid in list(remaining):
            if guid not in modpack_index:
                continue
            if include_modpack:
                rel = modpack_index[guid]
                abs_path = mods_dir / rel
                if abs_path.exists():
                    found[guid] = abs_path
            # Whether included or skipped, this GUID is resolved via index
            remaining.discard(guid)
    # If no index, fall through to full local scan (original behaviour)

    if not remaining:
        return found

    # ── Step 2: local scan for GUIDs not found in the index ───────────────
    if use_cache:
        guid_str_map = build_mods_cache(mods_dir, include_modpack=include_modpack, use_cache=True)
        for guid in remaining:
            if guid in guid_str_map:
                p = Path(guid_str_map[guid])
                if p.exists():
                    # Ensure we don't accidentally return a modpack path
                    # for a GUID that wasn't in the index (shouldn't happen
                    # if cache was built with same include_modpack, but guard
                    # against stale caches)
                    if not include_modpack and in_modpack_folder(p, mods_dir):
                        continue
                    found[guid] = p
        return found

    # No cache — scan local folders only (skip modpack folders when index
    # is present since those GUIDs were already handled above)
    skip_modpack = modpack_index is not None and not include_modpack
    for zp in iter_mod_files(mods_dir):
        if not remaining:
            break
        if in_modpack_folder(zp, mods_dir):
            if skip_modpack:
                continue
            if not include_modpack:
                continue
        guid = guid_from_zipmod(zp)
        if guid and guid in remaining:
            found[guid] = zp
            remaining.discard(guid)
    return found


# ---------------------------------------------------------------------------
# Path auto-resolution
# ---------------------------------------------------------------------------

def resolve_paths(
    chara_path: Path,
    game_base: Path,
    auto_resolve: bool,
    mods_override: Path | None,
    coord_override: Path | None,
) -> tuple[Path | None, Path | None]:
    """Return (mods_dir, coord_dir).

    auto_resolve=True  — always infer from card location; overrides ignored.
    auto_resolve=False — use explicit overrides as-is.
    """
    if not auto_resolve:
        return mods_override, coord_override

    try:
        chara_path.relative_to(game_base)
        in_game = True
    except ValueError:
        in_game = False

    if in_game:
        mods_dir  = game_base / "mods"
        coord_dir = game_base / "UserData" / "coordinate"
    else:
        mods_dir  = chara_path.parent
        coord_dir = chara_path.parent

    return (mods_dir  if mods_dir.exists()  else None,
            coord_dir if coord_dir.exists() else None)
