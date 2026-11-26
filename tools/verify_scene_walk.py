"""
verify_scene_walk.py — Check the scene walker against the full scan on real scenes.

`parsing.scan_scene_guids` reads the mod GUIDs of a Studio scene by walking its object tree and
seeking over everything that cannot hold one, instead of reading the whole file. Delete Cards'
shared-mod check deletes mods no scene uses, so a walker that missed a GUID would be a bug with
a real cost. This script runs the walker and the old full scan on every scene it is given and
reports, per file, whether they agree:

  OK        same GUIDs, and every UAR extension id in the file lies inside a KKEx block the walker
            decoded (nothing hides where the walker does not look)
  FALLBACK  the walker refused the file (new scene version, unknown object, damaged data, ...);
            the full scan is used for it, so the result is still right, just not faster
  MISMATCH  the walker's answer differs from the full scan, or an extension id sits outside the
            blocks it decoded. This is the one that matters: send the file and the output.

Usage:
    python tools/verify_scene_walk.py <scene.png | folder> [...] [--quiet] [--no-coverage]

It reads every scene fully (twice, or three times with the coverage check), so a big library
takes about as long as the old scan did. Exit status is 1 if any file MISMATCHes.

Set KKAFIO_SCENE_SCAN=verify to get the same comparison inside a normal run of Delete Cards or
Download Missing Mods, and KKAFIO_SCENE_SCAN=full to switch the walker off.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kkafio.cards.mods.parsing import compare_scene_scans  # noqa: E402


def _scenes(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        found.extend(sorted(p.rglob("*.png")) if p.is_dir() else [p])
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="scene files or folders (searched recursively for .png)")
    ap.add_argument("--quiet", action="store_true", help="only print files that are not OK")
    ap.add_argument("--no-coverage", action="store_true",
                    help="skip the extra pass that checks every extension id lies in a decoded KKEx")
    args = ap.parse_args()

    files = _scenes(args.paths)
    counts = {"OK": 0, "FALLBACK": 0, "MISMATCH": 0, "NOT A SCENE": 0}
    read = total = 0
    t0 = time.perf_counter()
    for i, p in enumerate(files, 1):
        try:
            cmp = compare_scene_scans(p, coverage=not args.no_coverage)
        except Exception as e:
            counts["MISMATCH"] += 1
            print(f"MISMATCH     {p}: {type(e).__name__}: {e}")
            continue
        w = cmp.walk
        if w is not None:
            read += w.bytes_read
            total += w.payload_size
        if cmp.full is None and w is None:
            status = "NOT A SCENE"
        elif cmp.agree:
            status = "OK"
        elif cmp.fell_back and not cmp.uncovered:
            status = "FALLBACK"
        else:
            status = "MISMATCH"
        counts[status] += 1
        if args.quiet and status == "OK":
            continue
        if w is not None:
            info = (f"v{'.'.join(map(str, w.version))} chars={w.chars} items={w.items} "
                    f"read {w.bytes_read / 1e6:.2f} of {w.payload_size / 1e6:.1f} MB "
                    f"({100 * w.bytes_read / max(w.payload_size, 1):.1f}%) guids={len(w.guids)}")
        else:
            info = cmp.describe()
        detail = "" if status == "OK" else f"  <- {cmp.describe()}"
        print(f"{status:<12} [{i}/{len(files)}] {p.name}: {info}{detail}")

    print()
    print(f"{len(files)} file(s) in {time.perf_counter() - t0:.1f}s: "
          + ", ".join(f"{n} {k}" for k, n in counts.items() if n))
    if total:
        print(f"Where the walker succeeded it read {read / 1e6:.1f} of {total / 1e6:.1f} MB "
              f"({100 * read / total:.1f}%).")
    if counts["MISMATCH"]:
        print("MISMATCH means the walker would have returned a different answer than the full "
              "scan: do not rely on it until that file is understood.")
    return 1 if counts["MISMATCH"] else 0


if __name__ == "__main__":
    sys.exit(main())
