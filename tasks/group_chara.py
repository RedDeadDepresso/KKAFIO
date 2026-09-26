"""
group_chara.py — Group character cards into folders by series using an LLM.

Workflow (all handled inside run()):
1. export() scans the folder for KK chara PNGs and builds a JSON dict
   {key: ""} where key encodes name + personality + hair colour.
2. run() shows a native Copy/Paste dialog (utils/llm_dialog.py) with the
   prompt + JSON. The user copies it into their LLM, pastes the reply back
   into the dialog, and clicks Paste.
3. process(folder_path, json_str) takes that JSON response (key → series
   folder name), finds all matching chara PNGs, and moves each one into
   <input_folder>/<series>/<filename>.
"""


import json
import shutil
from pathlib import Path

from kkloader import KoikatuCharaData

from tasks.base_task import BaseTask, validate_input_path
from utils.chara_key import make_key
from utils.classifier import CardType, get_card_type
from utils.logger import logger

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You will receive a JSON object whose keys identify Koikatsu character card files.
Each key has the format:  name | personality | hair_color

Your task: for every key, write the English name of the anime/game series the character \
is from as the value.

Rules:
- Values must be valid Windows folder names (no  \\ / : * ? " < > |  characters).
- Use the official title of the series.
- If a character appears in multiple series, use the one they are most associated with.
- Use the personality and hair colour as additional hints to identify the character.
- If you are not sure or the character is an original creation, leave the value as an empty string "".
- Return ONLY the completed JSON object — no explanation, no markdown code fences, \
no extra text before or after.

JSON to fill in:
"""

# ---------------------------------------------------------------------------
# Colour helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Export — build prompt + JSON, return as string for the clipboard
# ---------------------------------------------------------------------------

def export(folder_path: Path, include_subfolders: bool = False) -> str:
    """Scan folder_path for chara PNGs and return the character-key JSON.

    Args:
        include_subfolders: When False (default) only scans the top-level folder,
                            skipping already-sorted cards in subfolders.
                            When True scans recursively.

    Returns only the JSON block (no prompt) — the caller splices its own
    prompt text in front, same pattern as rename_chara.export().
    """
    folder_path = Path(folder_path)
    validate_input_path("GROUP", folder_path)
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
            return png, make_key(kc)
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

    logger.success("GROUP",
        f"Found {len(characters)} unique character(s). Copy the text and paste it into your LLM.")
    return json_str


# ---------------------------------------------------------------------------
# Process — read the LLM JSON response and move files
# ---------------------------------------------------------------------------

# Windows reserved device names — illegal as a folder name with or without
# an extension (CON, CON.txt, com3, etc. all fail to create on Windows).
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{d}" for d in "123456789"),
    *(f"LPT{d}" for d in "123456789"),
}
_MAX_FOLDER_NAME_LEN = 100  # generous but keeps well under Windows' 260-char path limit


def _safe_folder_name(name: str) -> str:
    """Turn an LLM-supplied series name into a folder name that is safe to
    join onto an existing path with a plain `/`.

    The series name comes from an LLM response, which is essentially
    untrusted input: it could echo something like ".." or a reserved
    device name, whether by accident (a garbled reply) or a maliciously
    crafted one (e.g. via a prompt-injected card / cache file). Handles:
      - path separators and other Windows-illegal characters
      - "." / ".." and any name that is only dots/spaces (path traversal —
        `Path(x) / ".."` would otherwise move files OUT of the input folder)
      - trailing dots/spaces (silently stripped by Windows, so left in place
        they'd make two different-looking names collide on disk)
      - reserved device names (CON, COM1, ...), with or without a suffix
      - an empty result after cleanup, and overly long names
    Returns "" if nothing safe survives — the caller treats that as "no
    assignment for this card" instead of moving it into folder_path itself
    or its parent.
    """
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "")
    # Strip ASCII control characters too (illegal on Windows, invisible/
    # confusing elsewhere).
    name = "".join(ch for ch in name if ord(ch) >= 0x20)
    name = name.strip()
    # Windows trims trailing dots and spaces off folder names, so do it here
    # rather than let two names that only differ in that regard collide.
    name = name.rstrip(". ")

    if not name or set(name) <= {"."}:
        return ""  # "", ".", "..", "...", etc.

    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        return ""

    return name[:_MAX_FOLDER_NAME_LEN]


def process(folder_path: Path, json_str: str, include_subfolders: bool = False) -> None:
    """Move chara PNGs into series subfolders based on the LLM JSON response.

    include_subfolders must match what export() was given: when True, cards
    already sitting in subfolders are regrouped too (moved into
    <folder_path>/<series>/); when False only top-level cards are touched.
    """
    folder_path = Path(folder_path)
    validate_input_path("GROUP", folder_path)

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

    if include_subfolders:
        png_files = list(folder_path.rglob("*.png"))
    else:
        png_files = list(folder_path.glob("*.png"))
    moved = 0
    skipped = 0

    for png in png_files:
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
            key = make_key(kc)
        except Exception as e:
            logger.error("GROUP", f"Could not parse {png.name}: {e}")
            skipped += 1
            continue

        series_folder = dest_map.get(key)
        if not series_folder:
            skipped += 1
            continue

        dest_dir = folder_path / series_folder
        if png.parent == dest_dir:
            skipped += 1          # already in the right series folder
            continue
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


# ---------------------------------------------------------------------------
# Task class
# ---------------------------------------------------------------------------

class GroupChara(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.group_chara
        self.input_path_str     : str  = cfg.get("InputPath", "")
        self.include_subfolders : bool = cfg.get("IncludeSubfolders", False)
        self.prompt              : str  = cfg.get("Prompt", "") or PROMPT_TEMPLATE

    def run(self) -> None:
        folder = Path(self.input_path_str or ".")
        validate_input_path("GROUP", folder)

        self.log_start("GROUP", str(folder))

        json_str = export(folder, include_subfolders=self.include_subfolders)
        if not json_str:
            return

        prompt_text = self.prompt.rstrip("\n") + "\n" + json_str

        from utils.llm_dialog import llm_dialog
        response = llm_dialog("KKAFIO — Group Characters", prompt_text)
        if not response or not response.strip():
            logger.warning("GROUP", "Dialog cancelled or empty response — nothing to do.")
            return

        process(folder, response, include_subfolders=self.include_subfolders)
