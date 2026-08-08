"""
group_chara.py — Group character cards into folders by series using an LLM.

Workflow
--------
1. export(folder_path) — scans the folder for KK chara PNGs, builds a JSON dict
   {key: ""} where key encodes name + personality + hair colour, then returns a
   prompt string ready to be pasted into your LLM.

2. process(folder_path, json_str) — takes the LLM response JSON (key → series
   folder name), finds all matching chara PNGs, and moves each one into
   <input_folder>/<series>/<filename>.

The two steps are intentionally decoupled so the user can inspect and edit
the LLM response before committing any file moves.
"""


import json
import shutil
from pathlib import Path

from kkloader import KoikatuCharaData

from utils.classifier import CardType, get_card_type, PERSONALITIES

from utils.logger import logger

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You will receive a JSON object whose keys identify Koikatsu character card files.
Each key has the format:  name | personality | hair_rgb

Your task: for every key, write the name of the anime/game series the character \
is from as the value.

Rules:
- Values must be valid Windows folder names (no  \\ / : * ? " < > |  characters).
- Use the official English title of the series.
- If a character appears in multiple series, use the one they are most associated with.
- If you are not sure or the character is an original creation, leave the value as an empty string "".
- Return ONLY the completed JSON object — no explanation, no markdown code fences, \
no extra text before or after.

JSON to fill in:
"""

# ---------------------------------------------------------------------------
# Colour helper
# ---------------------------------------------------------------------------

def _unity_to_rgb(r: float, g: float, b: float) -> tuple[int, int, int]:
    return (int(r * 255), int(g * 255), int(b * 255))


def _hair_color(kc: KoikatuCharaData) -> tuple[int, int, int]:
    parts = kc["Custom"]["hair"]["parts"]
    for i, part in enumerate(parts):
        if part.get("id", 0) == 0 and i != 1:
            continue
        if i == 3:
            continue
        base = part.get("baseColor")
        if not base:
            continue
        # baseColor is a list [r, g, b, a] of 0.0-1.0 floats
        if isinstance(base, (list, tuple)) and len(base) >= 3:
            return _unity_to_rgb(base[0], base[1], base[2])
        # Fallback: dict with r/g/b keys
        if isinstance(base, dict):
            vals = list(base.values())
            if len(vals) >= 3:
                return _unity_to_rgb(vals[0], vals[1], vals[2])
    return (0, 0, 0)


# ---------------------------------------------------------------------------
# Key builder — stable, deterministic, used in both export and process
# ---------------------------------------------------------------------------

def _make_key(kc: KoikatuCharaData) -> str:
    name = kc._repr_name()
    personality_idx = kc["Parameter"]["personality"]
    personality = PERSONALITIES[personality_idx] if personality_idx < len(PERSONALITIES) else str(personality_idx)
    hair = _hair_color(kc)
    return f"{name} | {personality} | hair_rgb{hair}"


# ---------------------------------------------------------------------------
# Export — build prompt + JSON, return as string for the clipboard
# ---------------------------------------------------------------------------

def export(folder_path: Path, include_subfolders: bool = False) -> str:
    """Scan folder_path for chara PNGs and return a LLM-ready prompt string.

    Args:
        include_subfolders: When False (default) only scans the top-level folder,
                            skipping already-sorted cards in subfolders.
                            When True scans recursively.
    """
    folder_path = Path(folder_path)
    characters: dict[str, str] = {}

    if include_subfolders:
        png_files = list(folder_path.rglob("*.png"))
    else:
        png_files = list(folder_path.glob("*.png"))

    logger.info("GROUP", f"Scanning {len(png_files)} PNG file(s) in {folder_path}"
                         + (" (top-level only)" if not include_subfolders else " (recursive)"))

    def _process_png(png: Path) -> tuple[Path, str | None]:
        """Read, classify and build the key for one PNG. Runs in a thread pool worker."""
        try:
            raw = png.read_bytes()
            if get_card_type(raw) not in (CardType.KK, CardType.KKSP):
                return png, None  # not a chara card — skip silently
            kc  = KoikatuCharaData.load(str(png))
            return png, _make_key(kc)
        except Exception as e:
            return png, f"__error__{e}"

    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    workers = min(32, (os.cpu_count() or 4) * 2)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_process_png, png): png for png in png_files}
        for future in as_completed(futures):
            png, result = future.result()
            if result is None:
                pass                    # not a chara card — skip
            elif result.startswith("__error__"):
                logger.error("GROUP", f"Could not process {png.name}: {result[9:]}")
            elif result not in characters:
                characters[result] = ""

    if not characters:
        logger.error("GROUP", "No readable character cards found")
        return ""

    json_str = json.dumps(characters, indent=4, ensure_ascii=False)
    result   = PROMPT_TEMPLATE + json_str

    logger.success("GROUP",
        f"Found {len(characters)} unique character(s). Copy the text and paste it into your LLM.")
    return result


# ---------------------------------------------------------------------------
# Process — read the LLM JSON response and move files
# ---------------------------------------------------------------------------

def _safe_folder_name(name: str) -> str:
    """Strip characters that are illegal in Windows folder names."""
    illegal = r'\/:*?"<>|'
    for ch in illegal:
        name = name.replace(ch, "")
    return name.strip()


def process(folder_path: Path, json_str: str) -> None:
    """Move chara PNGs into series subfolders based on the LLM JSON response."""
    folder_path = Path(folder_path)

    # Parse the LLM response — strip markdown fences if the user forgot
    clean = json_str.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.splitlines()[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.splitlines()[:-1])

    try:
        mapping: dict[str, str] = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.error("GROUP", f"Could not parse response JSON: {e}")
        return

    # Build reverse map: key -> destination folder name (skip empty values)
    dest_map = {
        key: _safe_folder_name(series)
        for key, series in mapping.items()
        if series and series.strip()
    }

    if not dest_map:
        logger.error("GROUP", "No series assignments found in response — nothing to do")
        return

    logger.info("GROUP",
        f"Processing {len(dest_map)} assignment(s) in {folder_path}")

    png_files = list(folder_path.rglob("*.png"))
    moved = 0
    skipped = 0

    for png in png_files:
        # Skip files already inside a series subfolder
        if png.parent != folder_path:
            continue
        # Pre-filter: skip non-KK/KKSP files before passing to kkloader
        try:
            raw = png.read_bytes()
            card_type = get_card_type(raw)
            if card_type not in (CardType.KK, CardType.KKSP):
                skipped += 1
                continue
        except Exception as e:
            logger.error("GROUP", f"Could not read {png.name}: {e}")
            skipped += 1
            continue
        try:
            kc  = KoikatuCharaData.load(str(png))
            key = _make_key(kc)
        except Exception as e:
            logger.error("GROUP", f"Could not parse {png.name}: {e}")
            skipped += 1
            continue

        series_folder = dest_map.get(key)
        if not series_folder:
            skipped += 1
            continue

        dest_dir = folder_path / series_folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / png.name

        # Handle filename collision
        if dest.exists():
            stem, suffix = png.stem, png.suffix
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        try:
            shutil.move(str(png), str(dest))
            logger.success("GROUP", f"Moved {png.name} -> {series_folder}/")
            moved += 1
        except Exception as e:
            logger.error("GROUP", f"Could not move {png.name}: {e}")
            skipped += 1

    logger.line()
    logger.success("GROUP", f"Done — moved: {moved}, skipped/unassigned: {skipped}")
