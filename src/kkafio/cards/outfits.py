"""Coordinate outfits: normalised digests of a card's clothes + accessories, the coordinate
cache, and matching a character's outfits to coordinate cards.
"""

import copy
import json as _json
import os
import re
from concurrent.futures import as_completed, ThreadPoolExecutor
from pathlib import Path

from kkafio.cards.cache_io import atomic_write_json, file_fp
from kkafio.cards.parsing import _coord_kkex_bytes, _find_iend_end, _unpack_kkex, uar_resolve_infos
from kkafio.core.logger import logger


COORD_CACHE_FILE = "kkafio_coord_cache.json"


# ---------------------------------------------------------------------------
# Coordinate matching
# ---------------------------------------------------------------------------
#
# A chara card's `Coordinate` block holds the same clothes/accessory data a
# standalone coordinate card does (kkloader decodes both with the same
# routine), so a coordinate saved from one of the card's outfits is a copy
# of that outfit — but not a byte-for-byte one. Two things differ:
#
#  * Modded item IDs. The IDs stored in a file's clothes/accessory data are
#    only meaningful together with that file's own Sideloader
#    UniversalAutoResolver (UAR) info in KKEx. That info maps each
#    modded field to (ModID, Slot, LocalSlot), and depending on who saved
#    the file the body holds either the mod's Slot or an install-specific
#    LocalSlot (e.g. 5856141 in a card vs 100011542 in a coordinate for the
#    same accessory). Every modded ID is therefore replaced by
#    "ModID:Slot" before comparing.
#  * Format additions. Newer coordinate files (clothes version 0.0.2) carry
#    per-colour `offset`/`rotate` fields older cards lack. When those hold
#    their default values they're dropped, so the outfit still matches.
#
# Each normalised outfit is reduced to an xxh3-128 digest of its canonical
# JSON, which is what the on-disk cache stores (small, and independent of
# floats surviving a JSON round-trip).

COORD_CACHE_VERSION = 2

_CLOTHES_KINDS = ("Top", "Bot", "Bra", "Shorts", "Gloves", "Pants", "Socks",
                  "ShoesInner", "ShoesOuter")   # indexes of ClothesKind

_SUB_PARTS = "ABC"

_OUTFIT_PREFIX = re.compile(r"^outfit(\d+)\.")

_DEFAULT_COLOR_OFFSET = [0.5, 0.5]

_DEFAULT_COLOR_ROTATE = 0.5


def _resolve_lookup(infos: list[dict], outfit: int | None) -> dict[str, dict]:
    """{Property: ResolveInfo} for one outfit. Cards prefix properties with
    "outfitN."; coordinate cards don't, so pass outfit=None for those."""
    lookup: dict[str, dict] = {}
    for info in infos:
        prop = str(info.get("Property", ""))
        m = _OUTFIT_PREFIX.match(prop)
        if m:
            if outfit is None or int(m.group(1)) != outfit:
                continue
            prop = prop[m.end():]
        elif outfit is not None:
            continue
        lookup[prop] = info
    return lookup


def _resolved(lookup: dict[str, dict], prop: str, value):
    info = lookup.get(prop)
    if info is not None and value in (info.get("Slot"), info.get("LocalSlot")):
        return f"{info.get('ModID')}:{info.get('Slot')}"
    return value


def _normalize_outfit(outfit: dict, lookup: dict[str, dict]) -> list:
    clothes = copy.deepcopy(outfit["clothes"])
    accessory = copy.deepcopy(outfit["accessory"])
    clothes.pop("version", None)
    accessory.pop("version", None)

    for i, part in enumerate(clothes.get("parts", [])):
        if i >= len(_CLOTHES_KINDS):
            break
        base = "ChaFileClothes.Clothes" + _CLOTHES_KINDS[i]
        part["id"] = _resolved(lookup, base, part.get("id"))
        for key, suffix in (("emblemeId", "Emblem"), ("emblemeId2", "Emblem2")):
            if key in part:
                part[key] = _resolved(lookup, base + suffix, part[key])
        for j, color in enumerate(part.get("colorInfo", [])):
            if not isinstance(color, dict):
                continue
            if "pattern" in color:
                color["pattern"] = _resolved(lookup, f"{base}Pattern{j}", color["pattern"])
            if color.get("offset") == _DEFAULT_COLOR_OFFSET:
                del color["offset"]
            if color.get("rotate") == _DEFAULT_COLOR_ROTATE:
                del color["rotate"]

    sub_ids = clothes.get("subPartsId", [])
    for j, value in enumerate(sub_ids[:len(_SUB_PARTS)]):
        # Jacket and sailor sub-parts share the same slots.
        for kind in ("Jacket", "Sailor"):
            resolved = _resolved(lookup, f"ChaFileClothes.Clothes{kind}Sub{_SUB_PARTS[j]}", value)
            if resolved != value:
                sub_ids[j] = resolved
                break

    for i, part in enumerate(accessory.get("parts", [])):
        if isinstance(part, dict) and "id" in part:
            part["id"] = _resolved(lookup, f"accessory{i}.ChaFileAccessory.PartsInfo.id", part["id"])

    return [clothes, accessory]


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    raise TypeError(f"Unserialisable type in coordinate data: {type(o).__name__}")


def outfit_digest(outfit: dict, infos: list[dict], outfit_index: int | None = None) -> str:
    """128-bit xxh3 digest of an outfit's normalised clothes + accessory data.

    `infos` are the UAR ResolveInfo entries of the file the outfit came from
    (see uar_resolve_infos()); `outfit_index` is the outfit's slot in a chara
    card, or None for a standalone coordinate card.
    """
    import xxhash

    canonical = _json.dumps(
        _normalize_outfit(outfit, _resolve_lookup(infos, outfit_index)),
        sort_keys=True, separators=(",", ":"), default=_json_default)
    return xxhash.xxh3_128_hexdigest(canonical.encode("utf-8"))


def chara_outfit_digests(kc) -> set[str]:
    """Digests of every outfit in a loaded chara card (KoikatuCharaData)."""
    try:
        infos = uar_resolve_infos(kc["KKEx"].data)
    except (KeyError, ValueError):   # kkloader raises ValueError for a missing block
        infos = []
    return {outfit_digest(outfit, infos, n)
            for n, outfit in enumerate(kc["Coordinate"].data)}


def _coord_file_digest(path: Path) -> str | None:
    """Digest of a coordinate card file, or None if it isn't a readable
    Koikatu coordinate card (e.g. a chara card or stray PNG in the folder)."""
    from kkloader.KoikatuCharaData import CoordinateEntry

    try:
        raw = path.read_bytes()
        entry = CoordinateEntry.load(raw, contains_png=True)
        if entry.header != CoordinateEntry.default_header:
            return None
        png_end = _find_iend_end(raw)
        kkex = _coord_kkex_bytes(raw[png_end:]) if 0 <= png_end < len(raw) else None
        infos = uar_resolve_infos(_unpack_kkex(kkex)) if kkex else []
        return outfit_digest(entry.data, infos)
    except Exception:
        return None


def build_coord_cache(coord_dir: Path, use_cache: bool = True) -> dict[str, str]:
    """Return {str(path): outfit digest} for every PNG under coord_dir
    (digest is "" for PNGs that aren't coordinate cards).

    With `use_cache`, digests are persisted in coord_dir/kkafio_coord_cache.json
    and only new or changed files (by mtime + size) are re-parsed; deleted
    files are pruned since only files present on disk are scanned. Without
    it, every file is parsed and the cache file is neither read nor written.
    """
    cache_path = coord_dir / COORD_CACHE_FILE
    old_files:  dict = {}
    old_coords: dict = {}
    if use_cache:
        try:
            prev = _json.loads(cache_path.read_text(encoding="utf-8"))
            if (prev.get("version") == COORD_CACHE_VERSION
                    and prev.get("coord_dir") == str(coord_dir)):
                old_files  = prev.get("files", {})
                old_coords = prev.get("coords", {})
        except FileNotFoundError:
            pass                     # first run — nothing cached yet
        except Exception as e:
            logger.debug("CACHE", f"Ignoring unreadable {cache_path}: {e}")

    all_pngs = sorted(coord_dir.rglob("*.png"))
    coord_map: dict[str, str] = {}
    new_files: dict[str, list[int]] = {}
    to_parse: list[Path] = []

    for png in all_pngs:
        sp = str(png)
        fp = file_fp(png)
        if old_files.get(sp) == fp and sp in old_coords:
            coord_map[sp] = old_coords[sp]
            new_files[sp] = fp
        else:
            to_parse.append(png)

    reused = len(all_pngs) - len(to_parse)
    if reused:
        logger.info("CACHE", f"Coord cache: {reused} unchanged, {len(to_parse)} new/changed")
    elif to_parse:
        logger.info("CACHE", f"Parsing {len(to_parse)} coordinate PNG(s)...")

    if to_parse:
        workers = min(32, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_coord_file_digest, png): png for png in to_parse}
            for future in as_completed(futures):
                sp = str(futures[future])
                # "" marks a PNG that isn't a coordinate card, so it is
                # remembered as such instead of being re-parsed every run.
                coord_map[sp] = future.result() or ""
                new_files[sp] = file_fp(futures[future])

    if use_cache:
        try:
            atomic_write_json(cache_path, {
                "version":   COORD_CACHE_VERSION,
                "coord_dir": str(coord_dir),
                "files":     new_files,
                "coords":    coord_map,
            })
        except Exception as e:
            logger.debug("CACHE", f"Could not save {cache_path}: {e}")
    return coord_map


def find_matching_coords(kc, coord_map: dict[str, str]) -> list[Path]:
    """Return the coordinate cards in `coord_map` (from build_coord_cache())
    that are a copy of any outfit of the loaded chara card `kc`."""
    wanted = chara_outfit_digests(kc)
    return sorted(Path(sp) for sp, digest in coord_map.items()
                  if digest in wanted and Path(sp).exists())
