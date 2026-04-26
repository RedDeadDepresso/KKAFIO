#!/usr/bin/env python3
"""
get_gui.py — Download the latest MXU release and extract mxu.exe as KKAFIO.exe.

Usage:
    python get_gui.py              # latest stable, win-x86_64
    python get_gui.py --arch aarch64
    python get_gui.py --tag v1.2.3
    python get_gui.py --prerelease # include pre-releases

After running, KKAFIO.exe will be placed in the same directory as kkafio_cli.py
so MXU can load interface.json and spawn kkafio_cli.exe.

Requirements: Python 3.8+, stdlib only (urllib, zipfile, shutil, etc.)
"""

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REPO       = "RedDeadDepresso/MXU-KKAFIO"
API_BASE   = "https://api.github.com"
HERE       = Path(__file__).resolve().parent   # KKAFIO project root


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(url: str, token: str | None = None) -> bytes:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "KKAFIO/get_gui"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _get_json(url: str, token: str | None = None) -> dict | list:
    return json.loads(_get(url, token))


def _detect_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "aarch64"
    return "x86_64"   # fallback


def _detect_os() -> str:
    s = sys.platform
    if s == "win32":
        return "win"
    if s == "darwin":
        return "macos"
    return "linux"


def _find_asset(assets: list[dict], os_tag: str, arch: str) -> dict | None:
    """Pick the best matching asset for the given OS and arch."""
    candidates = [
        a for a in assets
        if os_tag in a["name"].lower() and arch in a["name"].lower()
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Prefer smallest file (skip debug/pdb bundles) — or the one named "mxu"
    mxu = [c for c in candidates if "mxu" in c["name"].lower()]
    return mxu[0] if mxu else min(candidates, key=lambda a: a["size"])


def _download(url: str, dest: Path, label: str, token: str | None = None) -> None:
    headers = {"User-Agent": "KKAFIO/get_gui"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk = 1024 * 64
        with open(dest, "wb") as f:
            while True:
                data = resp.read(chunk)
                if not data:
                    break
                f.write(data)
                downloaded += len(data)
                if total:
                    pct = downloaded * 100 // total
                    bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
                    print(f"\r  [{bar}] {pct:3d}%  {downloaded // 1024}KB / {total // 1024}KB",
                          end="", flush=True)
    if total:
        print()   # newline after progress bar


def _extract_mxu_exe(zip_path: Path, dest: Path, os_tag: str) -> Path | None:
    """Extract only mxu.exe (Windows) or mxu (Unix) from the zip.

    Returns the path it was written to, or None if not found.
    """
    exe_name = "mxu.exe" if os_tag == "win" else "mxu"

    with zipfile.ZipFile(zip_path) as zf:
        # Find the exe entry (may be at root or inside a single top-level folder)
        match = next(
            (e for e in zf.namelist()
             if e.split("/")[-1].lower() == exe_name and not e.endswith("/")),
            None,
        )
        if match is None:
            return None

        out = dest / exe_name
        with zf.open(match) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return out


def _extract_mxu_exe_tar(tar_path: Path, dest: Path) -> Path | None:
    """Extract only the mxu binary from a tar.gz archive."""
    import tarfile
    with tarfile.open(tar_path) as tf:
        match = next(
            (m for m in tf.getmembers()
             if m.name.split("/")[-1].lower() == "mxu" and m.isfile()),
            None,
        )
        if match is None:
            return None
        member = tf.extractfile(match)
        if member is None:
            return None
        out = dest / "mxu"
        with open(out, "wb") as dst:
            shutil.copyfileobj(member, dst)
        return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag",        default=None,  help="Specific release tag (e.g. v1.2.3). Default: latest.")
    ap.add_argument("--arch",       default=None,  help="Target arch: x86_64 or aarch64. Default: auto-detect.")
    ap.add_argument("--os",         default=None,  help="Target OS: win, macos, linux. Default: auto-detect.")
    ap.add_argument("--prerelease", action="store_true", help="Allow pre-release versions.")
    ap.add_argument("--token",      default=os.environ.get("GITHUB_TOKEN"),
                    help="GitHub PAT (or set GITHUB_TOKEN env var) to raise API rate limits.")
    ap.add_argument("--dest",       default=str(HERE), help=f"Extraction destination. Default: {HERE}")
    args = ap.parse_args()

    arch   = args.arch or _detect_arch()
    os_tag = args.os   or _detect_os()
    dest   = Path(args.dest).resolve()

    print(f"MXU downloader — target: {os_tag}-{arch} → {dest}")

    # ── Resolve release ──────────────────────────────────────────────────────
    if args.tag:
        print(f"Fetching release {args.tag} …")
        release = _get_json(f"{API_BASE}/repos/{REPO}/releases/tags/{args.tag}", args.token)
    else:
        print("Fetching latest release …")
        if args.prerelease:
            releases = _get_json(f"{API_BASE}/repos/{REPO}/releases?per_page=10", args.token)
            release  = next((r for r in releases if not r["draft"]), None)
        else:
            release = _get_json(f"{API_BASE}/repos/{REPO}/releases/latest", args.token)

    if not release or "tag_name" not in release:
        print("ERROR: Could not find a suitable release.", file=sys.stderr)
        sys.exit(1)

    tag     = release["tag_name"]
    assets  = release.get("assets", [])
    print(f"  Found release: {tag}  ({len(assets)} assets)")

    # ── Find matching asset ──────────────────────────────────────────────────
    asset = _find_asset(assets, os_tag, arch)
    if asset is None:
        print(f"\nERROR: No asset matching '{os_tag}' + '{arch}' in release {tag}.", file=sys.stderr)
        print("Available assets:", file=sys.stderr)
        for a in assets:
            print(f"  {a['name']}  ({a['size'] // 1024} KB)", file=sys.stderr)
        sys.exit(1)

    print(f"  Asset: {asset['name']}  ({asset['size'] // 1024} KB)")

    # ── Download ─────────────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        tmp_archive = Path(tmp) / asset["name"]
        print("Downloading …")
        _download(asset["browser_download_url"], tmp_archive, asset["name"], args.token)

        # ── Extract mxu.exe only ─────────────────────────────────────────────
        print("Extracting mxu.exe …")
        dest.mkdir(parents=True, exist_ok=True)

        if tmp_archive.suffix == ".zip":
            extracted = _extract_mxu_exe(tmp_archive, dest, os_tag)
        elif tmp_archive.name.endswith((".tar.gz", ".tgz")):
            extracted = _extract_mxu_exe_tar(tmp_archive, dest)
        else:
            print(f"ERROR: Unknown archive format: {tmp_archive.name}", file=sys.stderr)
            sys.exit(1)

        if extracted is None:
            print("ERROR: mxu.exe not found inside the archive.", file=sys.stderr)
            sys.exit(1)

        # ── Rename mxu.exe → KKAFIO.exe (or mxu → KKAFIO on Unix) ──────────
        final_name = "KKAFIO.exe" if os_tag == "win" else "KKAFIO"
        final_path = dest / final_name
        if final_path.exists():
            final_path.unlink()
        extracted.rename(final_path)
        print(f"  ✓ {final_path}")

    # ── Make executable on Unix ───────────────────────────────────────────────
    if os_tag != "win":
        final_path.chmod(final_path.stat().st_mode | 0o755)
        print(f"  chmod +x {final_path}")

    print(f"\nDone! MXU {tag} installed as {final_path}")
    print("  Run: mxu.exe is now KKAFIO.exe — launch it to open the MXU interface.")


if __name__ == "__main__":
    main()
