"""
archive_chara.py — Bundle KK character cards with their used zipmods and
                   matching coordinate cards into a 7z or zip archive.

Auto-resolution logic
---------------------
When a chara PNG lives inside the game folder (i.e. its path starts with
GamePath), the module infers:
    mods_dir  → GamePath / mods
    coord_dir → GamePath / UserData / coordinate

Otherwise (card is outside the game folder) it falls back to the chara's own
parent directory.

If explicit overrides are provided they always take precedence.

Coordinate matching
-------------------
Direct comparison is impossible because Sideloader remaps GUID-based mod IDs
to runtime local integers when saving the chara slot.  The module compares:
  • Clothes color fingerprint (baseColor, patternColor, pattern, tiling per part)
  • Accessory occupied slot indices
  • Accessory color per occupied slot
This is the same approach validated in find_coordinates.py.
"""

from __future__ import annotations

from datetime import datetime
import io
import zipfile
import msgpack
import struct
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from util.config import Config
from util.file_manager import FileManager
from util.logger import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PNG_SIG  = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"IEND"

UAR_EXT_IDS = {
    "com.bepis.sideloader.universalautoresolver",
    "EC.Core.Sideloader.UniversalAutoResolver",
}

KNOWN_CHARA_MARKERS = {
    "【KoiKatuChara】", "【KoiKatuCharaS】", "【KoiKatuCharaSP】",
    "【EroMakeChara】", "【AIS_Chara】", "【KoiKatuCharaSun】",
    "【RG_Chara】", "【HCChara】", "【HCPChara】", "【SVChara】",
    "【ACChara】",
}

KNOWN_COORD_MARKERS = {
    "【KoiKatuClothes】", "【AIS_Clothes】", "【SVClothes】", "【ACClothes】",
}

ArchiveFormat = Literal["7z", "zip"]

# ---------------------------------------------------------------------------
# Binary reading helpers (.NET BinaryReader style)
# ---------------------------------------------------------------------------

def _find_iend_end(data: bytes) -> int:
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        pos += 12 + length
        if chunk_type == PNG_IEND:
            return pos
    return -1


def _read_7bit_int(s: io.BytesIO) -> int:
    result, shift = 0, 0
    while True:
        b = s.read(1)
        if not b:
            raise EOFError("Unexpected EOF reading 7-bit int")
        byte = b[0]
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result


def _read_str(s: io.BytesIO) -> str:
    return s.read(_read_7bit_int(s)).decode("utf-8", errors="replace")


def _ri(s: io.BytesIO) -> int:
    return struct.unpack("<i", s.read(4))[0]


def _ri_be(s: io.BytesIO) -> int:
    return struct.unpack(">I", s.read(4))[0]


def _rq(s: io.BytesIO) -> int:
    return struct.unpack("<q", s.read(8))[0]


def _rb(s: io.BytesIO) -> bytes:
    n = struct.unpack("b", s.read(1))[0]
    return s.read(n)


# ---------------------------------------------------------------------------
# GUID extraction from KKEx block
# ---------------------------------------------------------------------------

def _extract_guids_from_kkex(kkex_bytes: bytes) -> list[str]:
    guids: list[str] = []
    try:
        outer = msgpack.unpackb(kkex_bytes, raw=False, strict_map_key=False)
    except Exception:
        return guids

    if not isinstance(outer, dict):
        return guids

    for plugin_key, plugin_data_raw in outer.items():
        key_str = plugin_key if isinstance(plugin_key, str) else plugin_key.decode("utf-8", errors="replace")
        if key_str not in UAR_EXT_IDS:
            continue

        data_dict = None
        if isinstance(plugin_data_raw, dict):
            data_dict = plugin_data_raw.get(1)
        elif isinstance(plugin_data_raw, (list, tuple)) and len(plugin_data_raw) >= 2:
            data_dict = plugin_data_raw[1]

        if not isinstance(data_dict, dict):
            continue

        info_val = data_dict.get("info")
        if not isinstance(info_val, (list, tuple)):
            continue

        for item in info_val:
            if not isinstance(item, (bytes, bytearray, memoryview)):
                continue
            try:
                resolve_info = msgpack.unpackb(bytes(item), raw=False)
                if isinstance(resolve_info, dict):
                    guid = resolve_info.get("ModID")
                    if guid:
                        guids.append(str(guid))
            except Exception:
                pass

    return guids


# ---------------------------------------------------------------------------
# Chara card parser → GUIDs
# ---------------------------------------------------------------------------

def _parse_chara_guids(path: Path) -> list[str]:
    data = path.read_bytes()
    png_end = _find_iend_end(data)
    if png_end < 0 or png_end >= len(data):
        return []

    s = io.BytesIO(data[png_end:])
    _ri(s)                              # product_no
    marker = _read_str(s)
    if marker not in KNOWN_CHARA_MARKERS:
        return []

    _read_str(s)                        # version
    face_len = _ri(s)
    if face_len > 0:
        s.seek(face_len, io.SEEK_CUR)  # skip face thumbnail

    bh_len = _ri(s)
    block_header = msgpack.unpackb(s.read(bh_len), raw=False)
    lst_info = block_header.get("lstInfo", []) if isinstance(block_header, dict) else []

    blocks: dict[str, tuple[int, int]] = {}
    for info in lst_info:
        if isinstance(info, dict):
            name = info.get("name")
            if name:
                blocks[str(name)] = (int(info.get("pos", 0)), int(info.get("size", 0)))

    _rq(s)                              # total data size
    base_pos = s.tell()

    if "KKEx" not in blocks:
        return []

    pos, size = blocks["KKEx"]
    s.seek(base_pos + pos)
    return sorted(set(_extract_guids_from_kkex(s.read(size))))


# ---------------------------------------------------------------------------
# Coordinate card parser → GUIDs
# ---------------------------------------------------------------------------

def _parse_coord_guids(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
        png_end = _find_iend_end(data)
        if png_end < 0 or png_end >= len(data):
            return []

        s = io.BytesIO(data[png_end:])
        product_no = struct.unpack("<i", s.read(4))[0]
        if product_no != 100:
            return []

        marker = _read_str(s)
        if marker not in KNOWN_COORD_MARKERS:
            return []

        _read_str(s)                    # version
        if "AIS" in marker:
            s.read(4)                   # AI/HS2 language field
        _read_str(s)                    # coordinate name

        blob_len = _ri(s)
        s.seek(blob_len, io.SEEK_CUR)

        try:
            kkex_marker = _read_str(s)
        except Exception:
            return []

        if kkex_marker != "KKEx":
            return []

        s.read(4)                       # ext version
        ext_len = _ri(s)
        if ext_len <= 0:
            return []

        return sorted(set(_extract_guids_from_kkex(s.read(ext_len))))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Coordinate matching (color fingerprint, same strategy as find_coordinates.py)
# ---------------------------------------------------------------------------

def _parse_coord_outfit(path: Path) -> dict | None:
    """Parse coordinate PNG and return clothes/accessory dicts, or None."""
    try:
        data = path.read_bytes()
        png_end = _find_iend_end(data)
        if png_end < 0 or png_end >= len(data):
            return None

        payload = data[png_end:]
        s = io.BytesIO(payload)

        product_no = struct.unpack("<i", s.read(4))[0]
        if product_no != 100:
            return None

        header = _rb(s)
        if b"KoiKatuClothes" not in header:
            return None

        _rb(s)                         # version
        name_bytes = _rb(s)
        name = name_bytes.decode("utf-8", errors="replace")

        hiroin_no = struct.unpack("<i", s.read(4))[0]

        clothes_len = struct.unpack("<i", s.read(4))[0]
        clothes     = msgpack.unpackb(s.read(clothes_len), raw=False)
        acc_len     = struct.unpack("<i", s.read(4))[0]
        acc         = msgpack.unpackb(s.read(acc_len), raw=False)

        return {"path": path, "name": name, "hiroin_no": hiroin_no,
                "clothes": clothes, "accessory": acc}
    except Exception:
        return None


def _clothes_fp(clothes: dict) -> list[tuple]:
    fp = []
    for part in clothes.get("parts", []):
        colors = []
        for ci in part.get("colorInfo", []):
            if not isinstance(ci, dict):
                continue
            colors.append((
                tuple(ci["baseColor"])    if ci.get("baseColor")    else None,
                tuple(ci["patternColor"]) if ci.get("patternColor") else None,
                ci.get("pattern"),
                tuple(ci["tiling"])       if ci.get("tiling")       else None,
            ))
        fp.append(tuple(colors))
    return fp


def _acc_fp(acc: dict) -> tuple[frozenset, dict]:
    occupied, colors = [], {}
    for i, part in enumerate(acc.get("parts", [])):
        if not isinstance(part, dict) or part.get("id", 0) == 0:
            continue
        occupied.append(i)
        colors[i] = tuple(
            (tuple(ci["baseColor"])    if ci.get("baseColor")    else None,
             tuple(ci["patternColor"]) if ci.get("patternColor") else None)
            for ci in part.get("colorInfo", [])
            if isinstance(ci, dict)
        )
    return frozenset(occupied), colors


def _coord_matches_slot(slot: dict, coord: dict, threshold: float = 0.90) -> bool:
    c_fp  = _clothes_fp(slot["clothes"])
    co_fp = _clothes_fp(coord["clothes"])
    n     = max(len(c_fp), len(co_fp), 1)
    hits  = sum(1 for a, b in zip(c_fp, co_fp) if a == b)
    if hits / n < threshold:
        return False

    c_occ, c_col   = _acc_fp(slot["accessory"])
    co_occ, co_col = _acc_fp(coord["accessory"])
    if not co_occ:
        return True
    if c_occ != co_occ:
        return False
    shared = c_occ & co_occ
    return all(c_col.get(i) == co_col.get(i) for i in shared)


def _find_matching_coords(chara_coords: list[dict], coord_dir: Path) -> list[Path]:
    """Return coord PNGs in coord_dir that match any slot in the chara."""
    matched: list[Path] = []
    for png in sorted(coord_dir.rglob("*.png")):
        outfit = _parse_coord_outfit(png)
        if outfit is None:
            continue
        for slot in chara_coords:
            if _coord_matches_slot(slot, outfit):
                matched.append(png)
                break
    return matched


# ---------------------------------------------------------------------------
# Zipmod scanner
# ---------------------------------------------------------------------------

def _guid_from_zipmod(path: Path) -> str | None:
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


_SIDELOADER_MODPACK = "Sideloader Modpack"


def _in_modpack_folder(zp: Path, mods_dir: Path) -> bool:
    """Return True if zp is inside a first-level subfolder named like Sideloader Modpack."""
    try:
        rel = zp.relative_to(mods_dir)
    except ValueError:
        return False
    return len(rel.parts) >= 2 and _SIDELOADER_MODPACK in rel.parts[0]


def _scan_mods(mods_dir: Path, required: set[str],
               include_modpack: bool = False) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if not mods_dir.exists():
        return found
    for zp in mods_dir.rglob("*.zipmod"):
        if len(found) == len(required):
            break
        if not include_modpack and _in_modpack_folder(zp, mods_dir):
            continue
        guid = _guid_from_zipmod(zp)
        if guid and guid in required:
            found[guid] = zp
    return found


# ---------------------------------------------------------------------------
# Path auto-resolution
# ---------------------------------------------------------------------------

def _resolve_paths(
    chara_path: Path,
    game_base: Path,
    auto_resolve: bool,
    mods_override: Path | None,
    coord_override: Path | None,
) -> tuple[Path | None, Path | None]:
    """Return (mods_dir, coord_dir).

    When auto_resolve is True the paths are always inferred from the card
    location — overrides are ignored entirely.
    When auto_resolve is False the explicit overrides are used as-is.
    """
    if not auto_resolve:
        return mods_override, coord_override

    # Auto-resolve: check if chara lives inside the game folder
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

    return (mods_dir if mods_dir.exists() else None,
            coord_dir if coord_dir.exists() else None)


# ---------------------------------------------------------------------------
# 7-Zip archive creation
# ---------------------------------------------------------------------------

def _create_archive_7z(files: list[Path], output_path: Path) -> None:
    path_to_7zip = FileManager.find_7zip()
    if not path_to_7zip:
        raise RuntimeError("7-Zip not found. Install 7-Zip and ensure '7z' is on PATH.")
    cmd = [path_to_7zip, "a", "-t7z", str(output_path)] + [str(f) for f in files]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"7-Zip failed:\n{result.stderr}")


def _create_archive_zip(files: list[Path], output_path: Path) -> None:
    path_to_7zip = FileManager.find_7zip()
    if not path_to_7zip:
        raise RuntimeError("7-Zip not found. Install 7-Zip and ensure '7z' is on PATH.")
    cmd = [path_to_7zip, "a", "-tzip", str(output_path)] + [str(f) for f in files]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"7-Zip failed:\n{result.stderr}")


# ---------------------------------------------------------------------------
# Main module class
# ---------------------------------------------------------------------------

class ArchiveChara:
    def __init__(self, config: Config, file_manager: FileManager):
        self.config       = config
        self.file_manager = file_manager
        cfg = self.config.archive_chara
        self.chara_paths       : list[str] = cfg.get("CharaPaths", [])
        self.format            : str       = cfg.get("Format", "7z")
        self.auto_resolve      : bool      = cfg.get("AutoResolve", True)
        self.include_modpack   : bool      = cfg.get("IncludeModpack", False)
        self.combined_archive  : bool      = cfg.get("CombinedArchive", True)
        self.mods_dir_str      : str       = cfg.get("ModsDir", "")
        self.coord_dir_str     : str       = cfg.get("CoordDir", "")
        self.output_dir_str    : str       = cfg.get("OutputDir", "")

    def _process_one(self, chara_path: Path, game_base: Path,
                     mods_ov: Path | None, coord_ov: Path | None
                     ) -> tuple[list[Path], list[Path], list[Path]]:
        """Gather (coord_paths, zipmod_paths) for a single chara card.
        Returns (chara_files, coord_paths, zipmod_paths).
        """
        logger.info("ARCHV", f"Processing: {chara_path.name}")

        mods_dir, coord_dir = _resolve_paths(
            chara_path, game_base, self.auto_resolve, mods_ov, coord_ov)

        # Gather chara GUIDs
        chara_guids = _parse_chara_guids(chara_path)
        logger.info("ARCHV", f"  Zipmod GUIDs in chara : {len(chara_guids)}")

        # Find matching coordinate cards
        coord_paths: list[Path] = []
        coord_guids_all: set[str] = set()

        if coord_dir and coord_dir.exists():
            from kkloader import KoikatuCharaData
            try:
                kc = KoikatuCharaData.load(str(chara_path))
                chara_slots = kc["Coordinate"].data
                coord_paths = _find_matching_coords(chara_slots, coord_dir)
                logger.info("ARCHV", f"  Matching coordinates  : {len(coord_paths)}")
                for cp in coord_paths:
                    logger.info("ARCHV", f"    {cp.name}")
                    coord_guids_all.update(_parse_coord_guids(cp))
            except Exception as e:
                logger.error("ARCHV", f"  Could not match coords: {e}")
        else:
            logger.info("ARCHV", "  Coordinate directory not available — skipping")

        # Scan for zipmods
        all_guids = set(chara_guids) | coord_guids_all
        zipmod_paths: list[Path] = []

        if mods_dir and mods_dir.exists() and all_guids:
            logger.info("ARCHV",
                f"  Scanning mods ({len(all_guids)} GUIDs needed): {mods_dir}")
            guid_map = _scan_mods(mods_dir, all_guids,
                                  include_modpack=self.include_modpack)
            if not self.include_modpack:
                skipped = len([zp for zp in mods_dir.rglob("*.zipmod")
                               if _in_modpack_folder(zp, mods_dir)])
                if skipped:
                    logger.info("ARCHV",
                        f"  Skipped {skipped} zipmod(s) in Sideloader Modpack folder(s)")
            zipmod_paths = list(guid_map.values())
            missing = all_guids - set(guid_map.keys())
            logger.info("ARCHV",
                f"  Zipmods found: {len(zipmod_paths)}  missing: {len(missing)}")
            for m in sorted(missing):
                logger.warning("ARCHV", f"    (missing) {m}")
        elif not mods_dir:
            logger.info("ARCHV", "  Mods directory not available — skipping mod lookup")

        return coord_paths, zipmod_paths

    def run(self) -> None:
        chara_paths = [Path(p) for p in self.chara_paths if p]
        if not chara_paths:
            logger.error("ARCHV", "No character cards specified")
            return

        game_base  = Path(self.config.config_data["Core"]["GamePath"])
        mods_ov    = Path(self.mods_dir_str)   if self.mods_dir_str  else None
        coord_ov   = Path(self.coord_dir_str)  if self.coord_dir_str else None
        output_dir = Path(self.output_dir_str) if self.output_dir_str else None
        ext        = ".7z" if self.format == "7z" else ".zip"

        logger.line()

        if self.combined_archive:
            # All cards, their coords, and their mods in one archive
            all_files: list[Path] = []
            seen: set[Path] = set()

            for chara_path in chara_paths:
                if not chara_path.is_file():
                    logger.error("ARCHV", f"Not found: {chara_path}")
                    continue
                coord_paths, zipmod_paths = self._process_one(
                    chara_path, game_base, mods_ov, coord_ov)
                for f in [chara_path] + coord_paths + zipmod_paths:
                    if f not in seen:
                        seen.add(f)
                        all_files.append(f)

            if not all_files:
                logger.error("ARCHV", "No files to archive")
                return

            out_dir = output_dir or chara_paths[0].parent
            out_dir.mkdir(parents=True, exist_ok=True)
            # Name: first chara stem + _bundle (or just bundle if multiple)
            if len(chara_paths) == 1:
                archive_name = f"{chara_paths[0].stem}_bundle{ext}"
            else:
                archive_name = f"bundle__{datetime.now().strftime('%Y%m%d%H%M%S%f')}{ext}"
            archive_path = out_dir / archive_name

            logger.line()
            logger.info("ARCHV",
                f"Creating combined {self.format} archive: {archive_name} "
                f"({len(all_files)} file(s))")
            try:
                if self.format == "7z":
                    _create_archive_7z(all_files, archive_path)
                else:
                    _create_archive_zip(all_files, archive_path)
                logger.success("ARCHV", f"Done: {archive_path}")
            except Exception as e:
                logger.error("ARCHV", f"Archive creation failed: {e}")

        else:
            # One archive per chara card
            for chara_path in chara_paths:
                if not chara_path.is_file():
                    logger.error("ARCHV", f"Not found: {chara_path}")
                    continue
                logger.line()
                coord_paths, zipmod_paths = self._process_one(
                    chara_path, game_base, mods_ov, coord_ov)
                all_files = [chara_path] + coord_paths + zipmod_paths
                out_dir = output_dir or chara_path.parent
                out_dir.mkdir(parents=True, exist_ok=True)
                archive_path = out_dir / f"{chara_path.stem}_bundle{ext}"

                logger.info("ARCHV",
                    f"  Creating {self.format} archive: {archive_path.name} "
                    f"({len(all_files)} file(s))")
                try:
                    if self.format == "7z":
                        _create_archive_7z(all_files, archive_path)
                    else:
                        _create_archive_zip(all_files, archive_path)
                    logger.success("ARCHV", f"  Done: {archive_path}")
                except Exception as e:
                    logger.error("ARCHV", f"  Archive creation failed: {e}")
