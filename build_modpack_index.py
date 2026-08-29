"""
build_modpack_index.py — Build (or incrementally update) a GUID index of all
zipmods inside Sideloader Modpack folders.

Incremental updates: on rebuild, files whose path, mtime, and size are
unchanged are reused from the previous index without being opened. Only new
or changed zipmods are scanned. Adding a handful of mods to a large Sideloader
Modpack takes seconds instead of minutes.

Output (kkafio_modpack_index_kk.json or kkafio_modpack_index_kks.json):
  {
    "generated": "2025-01-01T12:00:00",
    "mods_dir":  "C:/KK/mods",
    "count":     1234,
    "guids":     {"com.example.mod": "Sideloader Modpack/Author/mod.zipmod", ...},
    "files":     {"C:/KK/mods/...": [mtime_int, size, "guid_or_null"], ...}
  }

Usage:
  python build_modpack_index.py <mods_folder> [--game-type kk|kks] [--output PATH]
  python build_modpack_index.py "C:/KK Party/mods" --game-type kk
  python build_modpack_index.py "C:/KKS/mods"      --game-type kks
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


def build_index(mods_dir: Path, previous: dict) -> tuple[dict[str, str], dict]:
    """Scan Sideloader Modpack folders, return (guid_map, file_fingerprints).

    previous: the "files" dict from a prior run — {abs_path: [mtime, size, guid|null]}.
    Unchanged files (same mtime + size) are reused without opening the zipmod.
    Only new or changed files are read via ThreadPoolExecutor.
    """
    modpack_zips = [
        zp for zp in mods_dir.rglob("*.zipmod")
        if is_modpack_folder(zp, mods_dir)
    ]

    print(f"Found {len(modpack_zips)} zipmods in Sideloader Modpack folders")

    old_files: dict = previous  # {str_path: [mtime, size, guid|None]}

    guid_map:   dict[str, str] = {}
    new_files:  dict           = {}
    to_read:    list[Path]     = []
    reused = 0

    # Pass 1 — reuse unchanged files
    for zp in modpack_zips:
        sp = str(zp)
        try:
            st = zp.stat()
        except OSError:
            continue
        fp = (int(st.st_mtime), st.st_size)
        old = old_files.get(sp)
        if old is not None and (old[0], old[1]) == fp:
            guid = old[2]
            new_files[sp] = old
            if guid:
                rel = str(zp.relative_to(mods_dir))
                guid_map[guid] = rel
            reused += 1
        else:
            to_read.append(zp)

    if reused:
        print(f"  Reused (unchanged): {reused}")
    if to_read:
        print(f"  New/changed (will scan): {len(to_read)}")

    # Pass 2 — parallel read for new/changed files
    import os
    workers = min(32, (os.cpu_count() or 4) * 2)
    errors  = 0

    def _proc(zp: Path):
        return zp, guid_from_zipmod(zp)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_proc, zp): zp for zp in to_read}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 500 == 0 or done == len(to_read):
                print(f"  Scanned {done}/{len(to_read)}...", end="\r")
            try:
                zp, guid = future.result()
                sp = str(zp)
                try:
                    st  = zp.stat()
                    fp  = (int(st.st_mtime), st.st_size)
                except OSError:
                    fp = (0, 0)
                new_files[sp] = [fp[0], fp[1], guid]
                if guid:
                    rel = str(zp.relative_to(mods_dir))
                    guid_map[guid] = rel
                else:
                    errors += 1
            except Exception as e:
                errors += 1
                print(f"\nWARN: {futures[future].name}: {e}")

    if to_read:
        print()
    if errors:
        print(f"  {errors} zipmod(s) could not be read (no manifest or invalid)")

    return guid_map, new_files


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
    ap.add_argument("--full", action="store_true",
                    help="Force a full rescan, ignoring the previous index")
    args = ap.parse_args()

    mods_dir     = Path(args.mods_dir).resolve()
    default_name = f"kkafio_modpack_index_{args.game_type}.json"
    output       = Path(args.output).resolve() if args.output else mods_dir / default_name

    if not mods_dir.exists():
        print(f"ERROR: mods folder not found: {mods_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning : {mods_dir}")
    print(f"Game type: {args.game_type}")
    print(f"Output   : {output}")
    print()

    # Load previous index for incremental update
    previous_files: dict = {}
    if output.exists() and not args.full:
        try:
            prev = json.loads(output.read_text(encoding="utf-8"))
            previous_files = {sp: fp for sp, fp in prev.get("files", {}).items()
                              if isinstance(fp, list) and len(fp) == 3}
            print(f"Previous index loaded: {len(prev.get('guids', {}))} GUIDs, "
                  f"{len(previous_files)} file fingerprints")
        except Exception as e:
            print(f"Could not load previous index ({e}) — doing full scan")
    elif args.full:
        print("Full rescan requested (--full)")

    guid_map, new_files = build_index(mods_dir, previous_files)

    index = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "mods_dir":  str(mods_dir),
        "count":     len(guid_map),
        "guids":     dict(sorted(guid_map.items())),
        "files":     new_files,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nDone — {len(guid_map)} GUIDs indexed → {output}")


if __name__ == "__main__":
    main()
