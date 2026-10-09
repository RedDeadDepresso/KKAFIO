"""
build_default_clothes.py — Record a folder of "default clothes" coordinate cards, so Group
Coordinates leaves those coordinates alone.

Default clothes (a costume pack's stock outfits, say) say nothing about whose outfit a
coordinate is. Every coordinate card that is one of them is skipped by Group Coordinates: it is
neither matched to a character nor moved.

Each PNG under the folder that is a coordinate card is identified by its outfit digest (the
same one Group Coordinates compares), so a default coordinate is still recognised after it was
renamed, moved or copied, whatever its picture. Other PNGs in the folder are skipped.

Output (assets/data/kkafio_default_clothes.json):
  {
    "generated":   "2025-01-01T12:00:00",
    "sources":     ["C:/KK/default clothes"],
    "coordinates": 42,
    "digests":     {"1b877395e080604c4777991d82ccaabc": "Christmas_01.png", ...}
  }
  ("digests" maps each outfit digest to the first file it was read from, for reference.)

Usage:
  python tools/build_default_clothes.py <coordinates_folder> [--output PATH] [--merge]
  python tools/build_default_clothes.py "C:/KK/default clothes"
  python tools/build_default_clothes.py "C:/KK/more defaults" --merge

  --merge   Add to the existing file instead of replacing it.
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from kkafio.cards.outfits import DEFAULT_CLOTHES_FILE, coord_digest  # noqa: E402

DEFAULT_OUTPUT = _REPO_ROOT / "assets" / "data" / DEFAULT_CLOTHES_FILE


def _load_existing(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"Warning: could not read existing {path}: {e}", file=sys.stderr)
        return {}


def build(folder: Path, output: Path, merge: bool) -> int:
    pngs = sorted(folder.rglob("*.png"))
    if not pngs:
        print(f"No PNG files found under {folder}", file=sys.stderr)
        return 1
    print(f"Reading {len(pngs)} PNG file(s) under {folder} ...")

    workers = min(32, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(coord_digest, pngs))

    digests: dict[str, str] = {}
    skipped: list[Path] = []
    coordinates = 0
    for png, digest in zip(pngs, results):
        if not digest:
            skipped.append(png)
            continue
        coordinates += 1
        digests.setdefault(digest, png.name)

    if not coordinates:
        print("None of the PNG files is a coordinate card — nothing written.", file=sys.stderr)
        return 1

    sources = [str(folder)]
    total = coordinates
    if merge:
        old = _load_existing(output)
        old_digests = old.get("digests", {})
        digests = {**{str(k): str(v) for k, v in old_digests.items()}, **digests} if isinstance(old_digests, dict) else digests
        sources = [*[s for s in old.get("sources", []) if s != str(folder)], str(folder)]
        total += int(old.get("coordinates", 0))

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "generated":   datetime.now().isoformat(timespec="seconds"),
        "sources":     sources,
        "coordinates": total,
        "digests":     dict(sorted(digests.items())),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(output)

    print(f"{coordinates} coordinate card(s) read ({coordinates - len(set(results) - {None})} duplicate(s)), "
          f"{len(skipped)} other PNG file(s) skipped")
    print(f"Wrote {output}: {len(digests)} default outfit(s)")
    for png in skipped[:10]:
        print(f"  skipped: {png.name}")
    if len(skipped) > 10:
        print(f"  ... and {len(skipped) - 10} more")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, help="Folder of default-clothes coordinate cards (searched recursively)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Data file to write (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--merge", action="store_true", help="Add to the existing data file instead of replacing it")
    args = parser.parse_args()
    if not args.folder.is_dir():
        print(f"Not a folder: {args.folder}", file=sys.stderr)
        return 1
    return build(args.folder, args.output, args.merge)


if __name__ == "__main__":
    sys.exit(main())
