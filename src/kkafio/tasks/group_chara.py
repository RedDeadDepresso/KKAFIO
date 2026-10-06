"""
group_chara.py — Group character cards into folders by series using an LLM.

Workflow (all handled inside run()):
1. export() scans the folder for KK chara PNGs and builds a JSON dict
   {key: ""} where key encodes name + personality + hair colour. With the
   default (game) chara folders, both female/ and male/ are scanned.
2. run() shows a native Copy/Paste dialog (src/kkafio/system/llm_dialog.py) with the
   prompt + JSON. The user copies it into their LLM, pastes the reply back
   into the dialog, and clicks Paste.
3. process(folder_path, json_str) takes that JSON response (key → series
   folder name), finds all matching chara PNGs, and moves each one into
   <chara_folder>/<series>/<filename>, inside the same folder (female/ or
   male/) the card was found in.
"""


import json
import shutil
from pathlib import Path
from typing import Sequence

from kkloader import KoikatuCharaData

from kkafio.tasks.base_task import BaseTask, resolve_chara_dirs
from kkafio.cards.cache_io import file_fp
from kkafio.cards.chara_key import make_key
from kkafio.cards.chara_key_cache import CharaKeyCache
from kkafio.cards.classifier import CHARA_CARD_TYPES, get_card_type
from kkafio.core.logger import logger

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

def _as_folder_list(folders: Path | Sequence[Path]) -> list[Path]:
    if isinstance(folders, (str, Path)):
        return [Path(folders)]
    return [Path(f) for f in folders]


def _list_pngs(folders: Sequence[Path], include_subfolders: bool) -> list[Path]:
    pngs: list[Path] = []
    for folder in folders:
        pngs.extend(folder.rglob("*.png") if include_subfolders else folder.glob("*.png"))
    return pngs


# ---------------------------------------------------------------------------
# Export — build prompt + JSON, return as string for the clipboard
# ---------------------------------------------------------------------------

def export(folders: Path | Sequence[Path], include_subfolders: bool = False,
           use_cache: bool = True) -> str:
    """Scan the given chara folder(s) for chara PNGs and return the character-key JSON.

    Args:
        include_subfolders: When False (default) only scans the top-level folder,
                            skipping already-sorted cards in subfolders.
                            When True scans recursively.
        use_cache: Reuse each card's key from kkafio_chara_key_cache.json when the file
                   hasn't changed (mtime + size), instead of re-parsing it.

    Returns only the JSON block (no prompt) — the caller splices its own
    prompt text in front, same pattern as rename_chara.export().
    """
    folder_list = _as_folder_list(folders)
    characters: dict[str, str] = {}

    png_files = _list_pngs(folder_list, include_subfolders)

    logger.info("GROUP", f"Scanning {len(png_files)} PNG file(s) in "
                         + ", ".join(str(f) for f in folder_list)
                         + (" (top-level only)" if not include_subfolders else " (recursive)"))

    key_cache = CharaKeyCache(folder_list, use_cache, "GROUP")

    def _process_png(png: Path) -> tuple[Path, str | None]:
        """Read, classify and build the key for one PNG (or take it from the cache).
        Runs in a thread pool worker."""
        try:
            fp = file_fp(png)
            hit, key = key_cache.lookup(png, fp)
            if hit:
                return png, key           # None: not a chara card
            raw = png.read_bytes()
            if get_card_type(raw) not in CHARA_CARD_TYPES:
                key_cache.put(png, fp, None)
                return png, None  # not a chara card — skip silently
            kc  = KoikatuCharaData.load(str(png))
            key = make_key(kc)
            key_cache.put(png, fp, key)
            return png, key
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
                logger.warning("GROUP", f"Could not process {png.name}: {result[9:]}")
            elif result not in characters:
                characters[result] = ""

    if use_cache:
        logger.info("GROUP", f"Key cache: {key_cache.summary()}")
        key_cache.save()

    if not characters:
        logger.warning("GROUP", "No readable character cards found")
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


def process(folders: Path | Sequence[Path], json_str: str, include_subfolders: bool = False,
            use_cache: bool = True) -> None:
    """Move chara PNGs into series subfolders based on the LLM JSON response.

    Each card is moved into <its own folder>/<series>/ — so with the game's
    female/ and male/ folders, a card never leaves the one it's in.

    include_subfolders must match what export() was given: when True, cards
    already sitting in subfolders are regrouped too; when False only
    top-level cards are touched.

    use_cache: take each card's key from kkafio_chara_key_cache.json when the file is
    unchanged, and keep that cache in step with the moves (a moved card's entry follows
    it to its new folder) so the next run doesn't have to read the cards again.
    """
    folder_list = _as_folder_list(folders)

    # Parse the LLM response — strip markdown fences if the user forgot
    clean = json_str.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.splitlines()[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.splitlines()[:-1])

    try:
        mapping: dict[str, str] = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.warning("GROUP", f"Could not parse response JSON: {e}")
        return

    # Build reverse map: key -> destination folder name (skip empty values)
    dest_map = {
        key: _safe_folder_name(series)
        for key, series in mapping.items()
        if series and series.strip()
    }

    if not dest_map:
        logger.warning("GROUP", "No series assignments found in response — nothing to do")
        return

    logger.info("GROUP",
        f"Processing {len(dest_map)} assignment(s) in "
        + ", ".join(str(f) for f in folder_list))

    # Pair every card with the root folder it was found under, so it's
    # regrouped inside that same root.
    png_files = [(root, png)
                 for root in folder_list
                 for png in (root.rglob("*.png") if include_subfolders else root.glob("*.png"))]
    moved = 0
    skipped = 0
    key_cache = CharaKeyCache(folder_list, use_cache, "GROUP")

    try:
        for folder_path, png in png_files:
            # Pre-filter: skip non-chara-card files before passing to kkloader
            try:
                fp = file_fp(png)
                hit, key = key_cache.lookup(png, fp)
                if not hit:
                    raw = png.read_bytes()
                    if get_card_type(raw) not in CHARA_CARD_TYPES:
                        key_cache.put(png, fp, None)
                        skipped += 1
                        continue
                    kc  = KoikatuCharaData.load(str(png))
                    key = make_key(kc)
                    key_cache.put(png, fp, key)
                elif key is None:
                    skipped += 1          # cached as "not a chara card"
                    continue
            except Exception as e:
                logger.warning("GROUP", f"Could not read {png.name}: {e}")
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
                key_cache.moved(png, dest)
                logger.success("GROUP", f"Moved {png.name} -> {series_folder}/")
                moved += 1
            except Exception as e:
                logger.warning("GROUP", f"Could not move {png.name}: {e}")
                skipped += 1
    finally:
        # Saved even if the run is stopped part-way, so what was moved/learned so far isn't lost.
        if use_cache:
            logger.info("GROUP", f"Key cache: {key_cache.summary()}")
            key_cache.save()

    logger.line()
    logger.success("GROUP", f"Done — moved: {moved}, skipped/unassigned: {skipped}")


# ---------------------------------------------------------------------------
# Task class
# ---------------------------------------------------------------------------

class GroupChara(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.group_chara
        self.chara_dir_str      : str  = cfg.get("CharaDir", "")
        self.include_subfolders : bool = cfg.get("IncludeSubfolders", False)
        self.prompt              : str  = cfg.get("Prompt", "") or PROMPT_TEMPLATE
        self.use_cache           : bool = cfg.get("UseCache", True)

    def run(self) -> None:
        folders = resolve_chara_dirs(self.config.game_path, self.chara_dir_str, "GROUP")

        self.log_start("GROUP", ", ".join(str(f) for f in folders))
        logger.info("GROUP", f"Use cache: {self.use_cache}")

        json_str = export(folders, include_subfolders=self.include_subfolders,
                          use_cache=self.use_cache)
        if not json_str:
            return

        prompt_text = self.prompt.rstrip("\n") + "\n" + json_str

        from kkafio.system.llm_dialog import llm_dialog
        response = llm_dialog("KKAFIO — Group Characters", prompt_text)
        if not response or not response.strip():
            logger.warning("GROUP", "Dialog cancelled or empty response — nothing to do.")
            return

        process(folders, response, include_subfolders=self.include_subfolders,
                use_cache=self.use_cache)
