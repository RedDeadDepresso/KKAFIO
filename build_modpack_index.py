#!/usr/bin/env python3
"""
build_modpack_index.py — Scan a mods folder and build a JSON index of all
GUIDs found inside Sideloader Modpack folders.

The resulting JSON is consumed by KKAFIO's archive_chara and delete_chara
tasks. When a required GUID is found in the index, KKAFIO knows the mod is
part of the Sideloader Modpack and skips bundling/deleting it (unless
IncludeModpack is enabled). If the GUID is NOT in the index, KKAFIO falls
back to scanning local folders for the mod file.

Output format:
  {
    "generated": "2025-01-01T12:00:00",
    "mods_dir": "C:/KK/mods",
    "count": 1234,
    "guids": {
      "com.example.mymod": "Sideloader Modpack/Author/mymod.zipmod",
      ...
    }
  }

Usage:
  python build_modpack_index.py <mods_folder> [--game-type kk|kks] [--output PATH]
  python build_modpack_index.py "C:/KK Party/mods" --game-type kk
  python build_modpack_index.py "C:/KKS/mods" --game-type kks
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

_SIDELOADER_MODPACK = "Sideloader Modpack"


def is_modpack_folder(zp: Path, mods_dir: Path) -> bool:
    """Return True if zp lives inside a folder whose name contains
    'Sideloader Modpack' (case-insensitive, any nesting depth)."""
    try:
        rel = zp.relative_to(mods_dir)
    except ValueError:
        return False
    return any(_SIDELOADER_MODPACK.lower() in part.lower() for part in rel.parts[:-1])


def guid_from_zipmod(path: Path) -> str | None:
    """Return the GUID from a zipmod's manifest.xml, or None on failure."""
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


def build_index(mods_dir: Path) -> dict[str, str]:
    """Scan all zipmods in Sideloader Modpack folders and return
    {guid: relative_path_str}."""
    modpack_zips = [
        zp for zp in mods_dir.rglob("*.zipmod")
        if is_modpack_folder(zp, mods_dir)
    ]

    print(f"Found {len(modpack_zips)} zipmods in Sideloader Modpack folders")

    import os
    workers = min(32, (os.cpu_count() or 4) * 2)
    guid_map: dict[str, str] = {}
    errors = 0

    def _proc(zp: Path):
        guid = guid_from_zipmod(zp)
        rel  = str(zp.relative_to(mods_dir))
        return guid, rel

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_proc, zp): zp for zp in modpack_zips}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 500 == 0 or done == len(modpack_zips):
                print(f"  {done}/{len(modpack_zips)}...", end="\r")
            try:
                guid, rel = future.result()
                if guid:
                    guid_map[guid] = rel
                else:
                    errors += 1
            except Exception as e:
                errors += 1
                print(f"\nWARN: {futures[future].name}: {e}")

    print()
    if errors:
        print(f"  {errors} zipmod(s) could not be read (no manifest or invalid)")

    return guid_map


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("mods_dir", metavar="MODS_FOLDER",
                    help="Path to your game's mods folder")
    ap.add_argument("--game-type", "-g", default="kk", choices=["kk", "kks"],
                    help="Game type: kk (Koikatsu/KoikatsuParty) or kks (KoikatsuSunshine)")
    ap.add_argument("--output", "-o", default=None, metavar="PATH",
                    help="Output JSON path (default: <mods_folder>/kkafio_modpack_index_<type>.json)")
    args = ap.parse_args()

    mods_dir = Path(args.mods_dir).resolve()
    if not mods_dir.exists():
        print(f"ERROR: mods folder not found: {mods_dir}", file=sys.stderr)
        sys.exit(1)

    default_name = f"kkafio_modpack_index_{args.game_type}.json"
    output = Path(args.output).resolve() if args.output else mods_dir / default_name

    print(f"Scanning: {mods_dir}")
    print(f"Output  : {output}")
    print()

    guid_map = build_index(mods_dir)

    index = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "mods_dir":  str(mods_dir),
        "count":     len(guid_map),
        "guids":     dict(sorted(guid_map.items())),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    print(f"\nDone — {len(guid_map)} GUIDs indexed → {output}")


if __name__ == "__main__":
    main()
