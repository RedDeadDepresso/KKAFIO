"""Zipmods: reading their GUIDs, scanning a mods folder (with an incremental cache), and
resolving a card's mods/coordinate folders. The Sideloader Modpack index lives in
kkafio.indices.modpack.
"""

import json as _json
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import as_completed, ThreadPoolExecutor
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from kkafio.cards.cache_io import atomic_write_json, file_fp
from kkafio.cards.classifier import is_mod_archive
from kkafio.core.logger import logger
from kkafio.indices.modpack import load_modpack_index, url_to_rel_path


_SIDELOADER_MODPACK = "Sideloader Modpack"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

MODS_CACHE_FILE  = "kkafio_mods_cache.json"


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
        atomic_write_json(cache_path, data)
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
        fp = file_fp(zp)
        old = old_files.get(sp)
        if old is not None and old[:2] == fp:
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
                fp = file_fp(zp)
                new_files[sp] = [*fp, guid]
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


def local_mods_map(mods_dir: Path, include_modpack: bool = False,
                   use_cache: bool = True) -> dict[str, Path]:
    """{guid: Path} of the zipmods found by scanning mods_dir (see build_mods_cache).

    The one place the string paths of the cache turn into Paths — tasks that resolve many
    cards against the same folder build this once and reuse it."""
    return {guid: Path(p) for guid, p in
            build_mods_cache(mods_dir, include_modpack=include_modpack, use_cache=use_cache).items()}


def is_modpack_protected(guid: str, path: Path | None, mods_dir: Path | None,
                         modpack_index: dict[str, list[str]]) -> bool:
    """True if a mod belongs to the Sideloader Modpack and deletion tasks must leave it alone:
    its GUID is in the modpack index, or its file lives in a Sideloader Modpack folder."""
    if guid in modpack_index:
        return True
    return path is not None and mods_dir is not None and in_modpack_folder(path, mods_dir)


def resolve_guid(guid: str, local_map: dict[str, Path], modpack_index: dict[str, list[str]],
                 mods_dir: Path, include_modpack: bool) -> Path | None:
    """The zipmod providing `guid`, or None.

    A GUID in the Sideloader Modpack index is looked up at the path of each of its URLs first,
    which needs no zipmod to be opened. With include_modpack off such a GUID is deliberately
    not resolved (None). If none of those files is on disk (moved or renamed modpack folder,
    or the mod is installed elsewhere under mods_dir), it falls through to `local_map` like
    any other GUID.
    """
    if guid in modpack_index:
        if not include_modpack:
            return None
        for url in modpack_index[guid]:
            rel = url_to_rel_path(url)
            if rel is not None and (mods_dir / rel).exists():
                return mods_dir / rel
    p = local_map.get(guid)
    return p if p is not None and p.exists() else None


class ModResolution(NamedTuple):
    found: dict[str, Path]     # guid -> zipmod to use
    excluded: set[str]         # modpack GUIDs left out on purpose (include_modpack is off)
    missing: set[str]          # GUIDs that couldn't be found anywhere


def resolve_guids(required: Iterable[str], local_map: dict[str, Path],
                  modpack_index: dict[str, list[str]], mods_dir: Path,
                  include_modpack: bool) -> ModResolution:
    """resolve_guid() for many GUIDs, also telling the modpack GUIDs that were excluded on
    purpose (so they can be reported apart from the ones that are really missing)."""
    found: dict[str, Path] = {}
    excluded: set[str] = set()
    missing: set[str] = set()
    for guid in required:
        if not include_modpack and guid in modpack_index:
            excluded.add(guid)
            continue
        p = resolve_guid(guid, local_map, modpack_index, mods_dir, include_modpack)
        if p is not None:
            found[guid] = p
        else:
            missing.add(guid)
    return ModResolution(found, excluded, missing)


def scan_mods(mods_dir: Path, required: set[str],
              include_modpack: bool = False,
              use_cache: bool = False) -> dict[str, Path]:
    """Scan mods_dir for zipmods providing the required GUIDs.

    GUIDs the bundled modpack index can answer are resolved without any file I/O; the folder
    is only scanned (through the mods cache when use_cache is on) if something is left over.
    """
    if not mods_dir.exists():
        return {}

    modpack_index = load_modpack_index() or {}
    res = resolve_guids(required, {}, modpack_index, mods_dir, include_modpack)
    found = dict(res.found)
    if res.missing:
        local_map = local_mods_map(mods_dir, include_modpack, use_cache)
        found.update(resolve_guids(res.missing, local_map, modpack_index, mods_dir,
                                   include_modpack).found)
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
