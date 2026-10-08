"""Coordinate outfits: normalised digests of a card's clothes + accessories, the coordinate
cache, and matching a character's outfits to coordinate cards.
"""

import copy
import json as _json
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable
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
#  * Serialisation noise. Some cards carry `"ExtendedSaveData": null` at every
#    level of their outfit data, which coordinate files don't have. Null ones
#    are dropped. And a file can list more accessory slots than another because
#    it was saved with more MoreAccessories slots (e.g. 92 vs 20); the extra
#    slots are empty, so trailing empty slots are dropped.
#  * Invisible leftovers. A clothes slot with nothing worn (id 0) can still hold
#    colours or emblems from an earlier item; they're dropped so they can't
#    make otherwise identical outfits differ.
#
# Each normalised outfit is reduced to an xxh3-128 digest of its canonical
# JSON, which is what the on-disk cache stores (small, and independent of
# floats surviving a JSON round-trip).

COORD_CACHE_VERSION = 4

_CLOTHES_KINDS = ("Top", "Bot", "Bra", "Shorts", "Gloves", "Pants", "Socks",
                  "ShoesInner", "ShoesOuter")   # indexes of ClothesKind

_SUB_PARTS = "ABC"

_OUTFIT_PREFIX = re.compile(r"^outfit(\d+)\.")

_DEFAULT_COLOR_OFFSET = [0.5, 0.5]

_DEFAULT_COLOR_ROTATE = 0.5

_EMPTY_ACCESSORY_TYPE = 120      # ChaFileAccessory.PartsInfo.type of an unused slot (ao_none)


def _is_empty_accessory(part) -> bool:
    return isinstance(part, dict) and part.get("type") == _EMPTY_ACCESSORY_TYPE and part.get("id", 0) == 0


def _drop_null_extended_data(obj) -> None:
    """Remove every `"ExtendedSaveData": null` entry from nested dicts/lists, in place."""
    if isinstance(obj, dict):
        if "ExtendedSaveData" in obj and obj["ExtendedSaveData"] is None:
            del obj["ExtendedSaveData"]
        for value in obj.values():
            _drop_null_extended_data(value)
    elif isinstance(obj, list):
        for value in obj:
            _drop_null_extended_data(value)


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
        if type(part["id"]) is int and part["id"] == 0:
            # Nothing worn in this slot: its colours, emblems etc. are invisible leftovers.
            for key in [k for k in part if k != "id"]:
                del part[key]

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

    parts = accessory.get("parts")
    if isinstance(parts, list):
        while parts and _is_empty_accessory(parts[-1]):    # after resolving: slot numbers matter there
            parts.pop()
    _drop_null_extended_data(clothes)
    _drop_null_extended_data(accessory)

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


# ---------------------------------------------------------------------------
# Fuzzy matching: same clothes, a few accessories different
# ---------------------------------------------------------------------------
#
# An outfit digest is all-or-nothing, so it can't tell "the same outfit with one
# accessory swapped" from a different outfit. For that, an outfit is also reduced
# to a *signature*: (digest of its normalised clothes, sorted digests of each of
# its non-empty accessories). Two outfits are then compared by requiring identical
# clothes and counting how many accessories differ.
#
# Accessories are digested one by one *after* the same UAR normalisation the
# outfit digest uses, and without their slot number, so moving an accessory to
# another slot doesn't count as a difference. Only the clothes and accessory
# parts are compared (as for the digest); other accessory-block fields are ignored.

SIG_CACHE_FILE = "kkafio_coord_sig_cache.json"

SIG_CACHE_VERSION = 3

# (clothes digest, sorted accessory digests)
OutfitSignature = tuple[str, tuple[str, ...]]


def _canonical(obj) -> bytes:
    return _json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")


def outfit_signature(outfit: dict, infos: list[dict], outfit_index: int | None = None) -> OutfitSignature:
    """(clothes digest, sorted accessory digests) of an outfit; see the section comment above.
    `infos` / `outfit_index` are as for outfit_digest()."""
    import xxhash

    clothes, accessory = _normalize_outfit(outfit, _resolve_lookup(infos, outfit_index))
    parts = [p for p in accessory.get("parts", []) if isinstance(p, dict) and not _is_empty_accessory(p)]
    return (xxhash.xxh3_128_hexdigest(_canonical(clothes)),
            tuple(sorted(xxhash.xxh3_64_hexdigest(_canonical(p)) for p in parts)))


def chara_outfit_signatures(kc) -> list[OutfitSignature]:
    """Distinct signatures of every outfit in a loaded chara card (KoikatuCharaData), sorted."""
    try:
        infos = uar_resolve_infos(kc["KKEx"].data)
    except (KeyError, ValueError):
        infos = []
    return sorted({outfit_signature(outfit, infos, n)
                   for n, outfit in enumerate(kc["Coordinate"].data)})


def accessory_difference(a: Iterable[str], b: Iterable[str]) -> int:
    """How many accessories must be added, removed or swapped to turn accessory list
    `a` into `b`: a swap counts once, as does an addition or a removal."""
    ca, cb = Counter(a), Counter(b)
    return max(sum((ca - cb).values()), sum((cb - ca).values()))


def signature_difference(a: OutfitSignature, b: OutfitSignature) -> int | None:
    """Accessory difference between two outfits, or None if their clothes differ."""
    if a[0] != b[0]:
        return None
    return accessory_difference(a[1], b[1])


def _read_coord_outfit(path: Path) -> tuple[dict, list[dict]] | None:
    """(outfit data, UAR resolve infos) of a coordinate card file, or None if it isn't
    a Koikatu coordinate card (e.g. a chara card or stray PNG in the folder).
    May raise on a corrupt file."""
    from kkloader.KoikatuCharaData import CoordinateEntry

    raw = path.read_bytes()
    entry = CoordinateEntry.load(raw, contains_png=True)
    if entry.header != CoordinateEntry.default_header:
        return None
    png_end = _find_iend_end(raw)
    kkex = _coord_kkex_bytes(raw[png_end:]) if 0 <= png_end < len(raw) else None
    infos = uar_resolve_infos(_unpack_kkex(kkex)) if kkex else []
    return entry.data, infos


def _coord_file_digest(path: Path) -> str | None:
    """Digest of a coordinate card file, or None if it isn't a readable
    Koikatu coordinate card (e.g. a chara card or stray PNG in the folder)."""
    try:
        loaded = _read_coord_outfit(path)
        return outfit_digest(*loaded) if loaded else None
    except Exception:
        return None


def _coord_file_signature(path: Path) -> list | None:
    """[clothes digest, [accessory digests]] of a coordinate card file (a list, so it
    survives the JSON cache unchanged), or None if it isn't a readable coordinate card."""
    try:
        loaded = _read_coord_outfit(path)
        if not loaded:
            return None
        clothes, accessories = outfit_signature(*loaded)
        return [clothes, list(accessories)]
    except Exception:
        return None


def _build_cache(coord_dir: Path, use_cache: bool, *, file_name: str, version: int, section: str,
                 parse: Callable[[Path], object], missing: object) -> dict[str, object]:
    """Scan every PNG under coord_dir with `parse` and return {str(path): result}, where
    `missing` stands for PNGs that aren't coordinate cards. With `use_cache` results are
    persisted in coord_dir/<file_name> under `section` and only new or changed files (by
    mtime + size) are re-parsed."""
    cache_path = coord_dir / file_name
    old_files:  dict = {}
    old_values: dict = {}
    if use_cache:
        try:
            prev = _json.loads(cache_path.read_text(encoding="utf-8"))
            if (prev.get("version") == version
                    and prev.get("coord_dir") == str(coord_dir)):
                old_files  = prev.get("files", {})
                old_values = prev.get(section, {})
        except FileNotFoundError:
            pass                     # first run — nothing cached yet
        except Exception as e:
            logger.debug("CACHE", f"Ignoring unreadable {cache_path}: {e}")

    all_pngs = sorted(coord_dir.rglob("*.png"))
    values: dict[str, object] = {}
    new_files: dict[str, list[int]] = {}
    to_parse: list[Path] = []

    for png in all_pngs:
        sp = str(png)
        fp = file_fp(png)
        if old_files.get(sp) == fp and sp in old_values:
            values[sp] = old_values[sp]
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
            futures = {ex.submit(parse, png): png for png in to_parse}
            for future in as_completed(futures):
                sp = str(futures[future])
                # `missing` marks a PNG that isn't a coordinate card, so it is
                # remembered as such instead of being re-parsed every run.
                result = future.result()
                values[sp] = missing if result is None else result
                new_files[sp] = file_fp(futures[future])

    if use_cache:
        try:
            atomic_write_json(cache_path, {
                "version":   version,
                "coord_dir": str(coord_dir),
                "files":     new_files,
                section:     values,
            })
        except Exception as e:
            logger.debug("CACHE", f"Could not save {cache_path}: {e}")
    return values


def build_coord_cache(coord_dir: Path, use_cache: bool = True) -> dict[str, str]:
    """Return {str(path): outfit digest} for every PNG under coord_dir
    (digest is "" for PNGs that aren't coordinate cards).

    With `use_cache`, digests are persisted in coord_dir/kkafio_coord_cache.json
    and only new or changed files (by mtime + size) are re-parsed; deleted
    files are pruned since only files present on disk are scanned. Without
    it, every file is parsed and the cache file is neither read nor written.
    """
    return _build_cache(coord_dir, use_cache, file_name=COORD_CACHE_FILE, version=COORD_CACHE_VERSION,
                        section="coords", parse=_coord_file_digest, missing="")


def build_coord_sig_cache(coord_dir: Path, use_cache: bool = True) -> dict[str, OutfitSignature | None]:
    """Return {str(path): outfit signature} for every PNG under coord_dir (None for PNGs
    that aren't coordinate cards). Cached like build_coord_cache(), in
    coord_dir/kkafio_coord_sig_cache.json."""
    raw = _build_cache(coord_dir, use_cache, file_name=SIG_CACHE_FILE, version=SIG_CACHE_VERSION,
                       section="sigs", parse=_coord_file_signature, missing=None)
    return {sp: (v[0], tuple(v[1])) if v else None for sp, v in raw.items()}


def find_matching_coords(kc, coord_map: dict[str, str]) -> list[Path]:
    """Return the coordinate cards in `coord_map` (from build_coord_cache())
    that are a copy of any outfit of the loaded chara card `kc`."""
    wanted = chara_outfit_digests(kc)
    return sorted(Path(sp) for sp, digest in coord_map.items()
                  if digest in wanted and Path(sp).exists())
