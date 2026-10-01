"""Incremental, per-folder collection of the mod GUIDs referenced by chara cards, Studio scenes
and coordinate cards, with on-disk caches.
"""

import json as _json
import os
from concurrent.futures import as_completed, ThreadPoolExecutor
from pathlib import Path

from kkafio.cards.cache_io import atomic_write_json, file_fp
from kkafio.cards.parsing import parse_chara_guids, parse_coord_guids, parse_scene_guids
from kkafio.core.logger import logger


# ---------------------------------------------------------------------------
# Generic incremental PNG GUID collector (chara / scene / coordinate)
# ---------------------------------------------------------------------------
#
# Shared by any task that needs "every mod GUID referenced by every card of a
# given type in a folder" — e.g. DownloadMissingMods (to find what's missing)
# and DeleteCards (to find what's still in use elsewhere before deleting a
# zipmod). One cache file per content type *per folder*: each scanned folder
# keeps its own cache file, keyed by that folder alone.
#
# It used to be a single file inside dirs[0], keyed by the whole folder set
# ("charaFemale|charaMale"). Tasks scan different combinations of folders
# (a user-configured override, female-only, both, ...), and any change in the
# combination invalidated the file and forced a full rescan — after which the
# next task with a different combination invalidated it again. Per-folder
# caches are independent of which combination a task asks for, so they are
# always reusable.

CHARA_GUID_CACHE_FILE = "kkafio_chara_guid_cache.json"

SCENE_GUID_CACHE_FILE = "kkafio_scene_guid_cache.json"

COORD_GUID_CACHE_FILE = "kkafio_coord_guid_cache.json"


def _load_png_guid_cache(cache_path: Path, d: Path) -> tuple[dict, dict[str, list[str]]]:
    """Read one folder's cache. Returns ({path: [mtime, size]}, {path: [guids]}),
    or two empty dicts if it's missing, unreadable, or belongs to another folder.

    Accepts both the current "dir" key and the old "dirs" key: a cache the
    previous single-file scheme wrote for a lone folder used exactly
    str(folder) as its key, so it is still valid here.
    """
    try:
        prev = _json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}            # first run — nothing cached yet
    except Exception as e:
        logger.debug("CACHE", f"Ignoring unreadable {cache_path}: {e}")
        return {}, {}
    if not isinstance(prev, dict) or (prev.get("dir") or prev.get("dirs")) != str(d):
        return {}, {}
    files = {sp: fp for sp, fp in (prev.get("files") or {}).items()
             if isinstance(fp, list) and len(fp) == 2}
    by_file = prev.get("guids_by_file") or {}
    if not isinstance(by_file, dict):
        return {}, {}
    return files, by_file


def collect_png_guids(
    dirs: list[Path],
    use_cache: bool,
    cache_file: str,
    label: str,
    is_valid,
    parse_guids,
) -> tuple[set[str], dict[str, list[str]]]:
    """Incrementally collect GUIDs from a set of PNG-based card files.

    Unchanged PNG files (same mtime + size) reuse their cached GUIDs.
    Only new or changed PNGs are fully read. Shared implementation used for
    chara cards, Studio scenes, and coordinate cards — `is_valid(raw_bytes)`
    decides whether a given PNG belongs to this collector's content type,
    and `parse_guids` extracts the GUIDs from files that pass.

    Each folder in `dirs` has its own cache file (`<folder>/<cache_file>`)
    holding only the files found under that folder, so the cache is reusable
    no matter which combination of folders a task scans. When `use_cache` is
    False, this does a full scan every time and does not read or write any
    cache — the complete per-file scan still happens either way, so the
    returned `guids_by_file` is always accurate for every currently-existing
    file in `dirs`, regardless of the `use_cache` setting.

    Returns (guids, guids_by_file):
      guids         — the union of every GUID across every file
      guids_by_file — {absolute_path_str: [guid, ...]} for every file, so
                      callers can exclude specific files (e.g. cards about
                      to be deleted) from the union after the fact, instead
                      of needing a separate uncached scan that skips them.
    """
    if not dirs:
        return set(), {}

    workers = min(32, (os.cpu_count() or 4) * 2)

    def _proc(png: Path):
        try:
            raw = png.read_bytes()
            if is_valid(raw):
                return png, [g for g in parse_guids(png) if g]
        except Exception as e:
            # The file is left out of the results (and the cache), exactly as
            # before — but say why, so a card whose mods silently aren't
            # counted can be diagnosed (run with KKAFIO_DEBUG=1).
            logger.debug("CACHE", f"Could not parse {png}: {type(e).__name__}: {e}")
        return png, None

    guids:     set[str]             = set()
    all_by_file: dict[str, list[str]] = {}
    # {path: guids | None (not this content type)} for every file resolved so
    # far in this call. If one folder is nested inside another, a file under
    # both is parsed once but still recorded in BOTH folders' caches — so the
    # scan order can never leave a folder's cache missing files it contains.
    memo: dict[str, list[str] | None] = {}
    total_reused = total_scanned = 0

    for d in dict.fromkeys(Path(x) for x in dirs):   # de-duplicate, keep order
        if not d.exists():
            continue

        cache_path = d / cache_file
        old_files: dict = {}
        old_guids_by_file: dict[str, list[str]] = {}
        if use_cache:
            old_files, old_guids_by_file = _load_png_guid_cache(cache_path, d)

        new_files: dict = {}
        dir_by_file: dict[str, list[str]] = {}
        to_read: list[Path] = []

        for png in d.rglob("*.png"):
            sp = str(png)
            fp = file_fp(png)
            if sp in memo:                       # already resolved via an overlapping folder
                if memo[sp] is not None:
                    dir_by_file[sp] = memo[sp]
                    new_files[sp]   = fp
                continue
            old = old_files.get(sp)
            if old is not None and old[:2] == fp and sp in old_guids_by_file:
                dir_by_file[sp] = old_guids_by_file[sp]
                new_files[sp]   = old
                memo[sp]        = old_guids_by_file[sp]
            else:
                to_read.append(png)

        total_reused  += len(dir_by_file)
        total_scanned += len(to_read)

        if to_read:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for future in as_completed([ex.submit(_proc, png) for png in to_read]):
                    png, file_guids = future.result()
                    sp = str(png)
                    memo[sp] = file_guids
                    if file_guids is None:
                        continue
                    fp = file_fp(png)
                    new_files[sp]   = fp
                    dir_by_file[sp] = file_guids

        if use_cache:
            dir_guids: set[str] = set()
            for g in dir_by_file.values():
                dir_guids.update(g)
            try:
                atomic_write_json(cache_path, {
                    "dir":           str(d),
                    "file_count":    len(new_files),
                    "guids":         sorted(dir_guids),
                    "files":         new_files,
                    "guids_by_file": dir_by_file,
                })
            except Exception as e:
                logger.debug("CACHE", f"Could not save {cache_path}: {e}")

        for g in dir_by_file.values():
            guids.update(g)
        all_by_file.update(dir_by_file)

    if use_cache:
        logger.info("CACHE",
            f"{label} cache: {total_reused} unchanged, {total_scanned} new/changed "
            f"— {len(guids)} GUIDs from {len(all_by_file)} files")
    else:
        logger.info("CACHE", f"{label} scan complete: {len(guids)} GUIDs")

    return guids, all_by_file


def collect_chara_guids(chara_dirs: list[Path], use_cache: bool) -> set[str]:
    """Incrementally collect GUIDs referenced by chara cards in chara_dirs."""
    from kkafio.cards.classifier import CardType, get_card_type
    guids, _ = collect_png_guids(
        chara_dirs, use_cache, CHARA_GUID_CACHE_FILE, "Chara",
        lambda raw: get_card_type(raw) in (CardType.KK, CardType.KKSP, CardType.KKS),
        parse_chara_guids,
    )
    return guids


def collect_chara_guids_by_file(chara_dirs: list[Path], use_cache: bool) -> dict[str, list[str]]:
    """Same as collect_chara_guids, but returns the per-file GUID mapping
    ({absolute_path_str: [guid, ...]}) instead of the aggregated set."""
    from kkafio.cards.classifier import CardType, get_card_type
    _, by_file = collect_png_guids(
        chara_dirs, use_cache, CHARA_GUID_CACHE_FILE, "Chara",
        lambda raw: get_card_type(raw) in (CardType.KK, CardType.KKSP, CardType.KKS),
        parse_chara_guids,
    )
    return by_file


def collect_scene_guids(scene_dirs: list[Path], use_cache: bool) -> set[str]:
    """Incrementally collect GUIDs referenced by Studio scenes in scene_dirs."""
    from kkafio.cards.classifier import CardType, get_card_type
    guids, _ = collect_png_guids(
        scene_dirs, use_cache, SCENE_GUID_CACHE_FILE, "Scene",
        lambda raw: get_card_type(raw) == CardType.SCENE,
        parse_scene_guids,
    )
    return guids


def collect_scene_guids_by_file(scene_dirs: list[Path], use_cache: bool) -> dict[str, list[str]]:
    """Same as collect_scene_guids, but returns the per-file GUID mapping
    ({absolute_path_str: [guid, ...]}) instead of the aggregated set."""
    from kkafio.cards.classifier import CardType, get_card_type
    _, by_file = collect_png_guids(
        scene_dirs, use_cache, SCENE_GUID_CACHE_FILE, "Scene",
        lambda raw: get_card_type(raw) == CardType.SCENE,
        parse_scene_guids,
    )
    return by_file


def collect_coord_guids(coord_dirs: list[Path], use_cache: bool) -> set[str]:
    """Incrementally collect GUIDs referenced by coordinate cards in coord_dirs."""
    from kkafio.cards.classifier import CardType, get_card_type, is_coordinate
    guids, _ = collect_png_guids(
        coord_dirs, use_cache, COORD_GUID_CACHE_FILE, "Coord",
        lambda raw: get_card_type(raw) == CardType.UNKNOWN and is_coordinate(raw),
        parse_coord_guids,
    )
    return guids


def collect_coord_guids_by_file(coord_dirs: list[Path], use_cache: bool) -> dict[str, list[str]]:
    """Same as collect_coord_guids, but returns the per-file GUID mapping
    ({absolute_path_str: [guid, ...]}) instead of the aggregated set."""
    from kkafio.cards.classifier import CardType, get_card_type, is_coordinate
    _, by_file = collect_png_guids(
        coord_dirs, use_cache, COORD_GUID_CACHE_FILE, "Coord",
        lambda raw: get_card_type(raw) == CardType.UNKNOWN and is_coordinate(raw),
        parse_coord_guids,
    )
    return by_file
