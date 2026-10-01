"""
rename_chara.py — Translate KK character card names to English using an LLM.

By default updates card metadata (Parameter.lastname/firstname/nickname).
Optionally also renames the PNG file on disk.

LLM response format — values are dicts:
  {
    "key | personality | hair": {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"},
    "unknown key | ...":        {"lastname": "", "firstname": "", "nickname": ""}
  }

Cache: kkafio_rename_cache.json in input folder, merged on every run.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from kkloader import KoikatuCharaData

from kkafio.tasks.base_task import BaseTask, validate_input_path
from kkafio.cards.chara_key import make_key
from kkafio.cards.classifier import CHARA_CARD_TYPES, get_card_type
from kkafio.core.logger import logger

CACHE_FILENAME = "kkafio_rename_cache.json"

PROMPT_TEMPLATE = """\
You will receive a JSON object whose keys identify Koikatsu character card files.
Each key has the format:  name | personality | hair_color

Your task: for every key fill in "lastname", "firstname", and "nickname" with the
character's well-known English name.

Rules:
- firstname = given name, lastname = family name, regardless of the order the
  name is written in the source material.
- Use the name the character is commonly known by, not a literal
  transliteration.
- "nickname" can be a common short form or the same as firstname.
- Use the personality and hair colour as additional hints to identify the character.
- All values must be valid Windows filenames
  (no  \\ / : * ? " < > |  characters, no leading/trailing spaces or dots).
- If you do not recognise the character or are not confident, leave all three
  fields as empty strings "".
- Return ONLY the completed JSON object — no explanation, no markdown fences,
  no extra text before or after.

JSON to fill in:
"""

_EMPTY_NAME: dict[str, str] = {"lastname": "", "firstname": "", "nickname": ""}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe(s: str) -> str:
    """Make a name safe to use as a Windows file name stem.

    Removes illegal characters and control characters, then strips trailing
    dots *and* spaces together (Windows silently drops both, so stripping
    them in one pass avoids e.g. "Name ." -> "Name " surviving), and
    prefixes reserved device names (CON, NUL, COM1, ...) with an underscore.
    """
    s = "".join(ch for ch in s if ch not in '\\/:*?"<>|' and ord(ch) >= 32)
    s = s.strip().rstrip(". ").lstrip()
    if s.split(".")[0].upper() in _WIN_RESERVED:
        s = f"_{s}"
    return s


def _fold(name: str) -> str:
    """Key for comparing file names the way a case-insensitive filesystem does."""
    return name.casefold()


def _rename_collides(png: Path, candidate: str, used: dict[str, int]) -> bool:
    """True if renaming `png` to `<candidate>.png` in its own folder would
    collide with another file.

    `used` maps the case-folded stem of every file in that folder (kept
    up to date as this run renames files) to how many files hold it.
    """
    target = png.parent / f"{candidate}.png"
    if target.exists():
        # On a case-insensitive filesystem "Alice.png" exists when we are
        # "alice.png" — that's this same file (a case-only rename), not a
        # collision. Anything else that exists is a real one and must never
        # be overwritten (a POSIX rename silently replaces the target).
        try:
            return not os.path.samefile(target, png)
        except OSError:
            return True
    key = _fold(candidate)
    # Stems held by other files here (sidecars, or cards renamed earlier this
    # run), compared case-insensitively. This card's own stem doesn't count
    # against it.
    return key != _fold(png.stem) and key in used


def _name_known(d: dict) -> bool:
    return any(d.get(k, "").strip() for k in ("lastname", "firstname", "nickname"))


def _stem_for(d: dict) -> str:
    last  = d.get("lastname", "").strip()
    first = d.get("firstname", "").strip()
    if last and first:
        return f"{last}_{first}"
    return last or first or ""


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cache(folder: Path) -> dict:
    try:
        return json.loads((folder / CACHE_FILENAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error("RENAME", f"Could not load cache: {e}")
        return {}


def _save_cache(folder: Path, cache: dict) -> None:
    try:
        # Atomic + compact — same reasoning as the other on-disk caches in
        # this codebase (see kkafio.cards.cache_io.atomic_write_json): a plain
        # write_text() can leave a truncated, unreadable cache behind if
        # interrupted, and indent=2 is pure size overhead for a file only
        # ever read back by json.loads(). Kept sorted by key still, since
        # that (unlike indentation) actually helps a human skim or diff it.
        from kkafio.cards.cache_io import atomic_write_json
        atomic_write_json(folder / CACHE_FILENAME, dict(sorted(cache.items())))
    except Exception as e:
        logger.error("RENAME", f"Could not save cache: {e}")


def _merge_cache(cache: dict, response: dict) -> dict:
    merged = dict(cache)
    for key, nd in response.items():
        if not isinstance(nd, dict):
            continue
        sanitised = {k: _safe(nd.get(k, "")) for k in ("lastname", "firstname", "nickname")}
        if _name_known(sanitised) or key not in merged:
            merged[key] = sanitised
    return merged


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(folder_path: Path, skip_already_renamed: bool = True) -> str:
    folder_path = Path(folder_path)
    cache       = _load_cache(folder_path)
    validate_input_path("RENAME", folder_path)
    known_stems = {_stem_for(v) for v in cache.values() if _name_known(v)}
    png_files   = list(folder_path.rglob("*.png"))
    logger.info("RENAME", f"Scanning {len(png_files)} PNG file(s) in {folder_path}")

    def _proc(png: Path):
        try:
            if skip_already_renamed and png.stem in known_stems:
                return png, "__skip__"
            raw = png.read_bytes()
            if get_card_type(raw) not in CHARA_CARD_TYPES:
                return png, None
            return png, make_key(KoikatuCharaData.load(str(png)))
        except Exception as e:
            return png, f"__error__{e}"

    workers = min(32, (os.cpu_count() or 4) * 2)
    to_translate: dict = {}
    skipped = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for future in as_completed({ex.submit(_proc, p): p for p in png_files}):
            png, result = future.result()
            if result == "__skip__":
                skipped += 1
            elif result is None:
                pass
            elif result.startswith("__error__"):
                logger.error("RENAME", f"Could not process {png.name}: {result[9:]}")
            else:
                cached = cache.get(result)
                if not (cached and _name_known(cached)):
                    to_translate[result] = dict(_EMPTY_NAME)

    if skipped:
        logger.info("RENAME", f"Skipped {skipped} already-renamed file(s)")

    if not to_translate:
        logger.success("RENAME", "All characters already known — nothing to send to LLM.")
        return ""

    logger.success("RENAME",
        f"{len(to_translate)} character(s) to translate "
        f"({len(cache)} already in cache).")
    # Return only the JSON — the caller (Rust or CLI) splices the prompt in front
    return json.dumps(to_translate, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------

def process(folder_path: Path, json_str: str,
            skip_already_renamed: bool = True,
            update_metadata: bool = True,
            rename_files: bool = False) -> None:
    folder_path = Path(folder_path)

    clean = json_str.strip()
    validate_input_path("RENAME", folder_path)
    if clean.startswith("```"):
        clean = "\n".join(clean.splitlines()[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.splitlines()[:-1])

    try:
        response: dict = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.error("RENAME", f"Could not parse response JSON: {e}")
        return

    cache = _merge_cache(_load_cache(folder_path), response)
    _save_cache(folder_path, cache)
    logger.info("RENAME", f"Cache updated: {len(cache)} entries")

    known_stems = {_stem_for(v) for v in cache.values() if _name_known(v)}
    png_files   = list(folder_path.rglob("*.png"))
    logger.info("RENAME", f"Processing {len(png_files)} PNG file(s)")

    updated = renamed = skipped = 0
    # Track used stems per directory so rename collision checks are folder-local.
    # Stems are stored case-folded ("Alice" and "alice" are the same name on
    # Windows and macOS, and would be on any case-insensitive share the cards
    # are later copied to), with a count so a card that shares its stem with a
    # sidecar file (Alice.png / Alice.txt) only frees the stem when nothing
    # else is still using it.
    used_stems_by_dir: dict[Path, dict[str, int]] = {}

    def _dir_stems(d: Path) -> dict[str, int]:
        if d not in used_stems_by_dir:
            counts: dict[str, int] = {}
            for p in d.iterdir():
                if p.is_file():
                    key = _fold(p.stem)
                    counts[key] = counts.get(key, 0) + 1
            used_stems_by_dir[d] = counts
        return used_stems_by_dir[d]

    for png in png_files:
        if skip_already_renamed and png.stem in known_stems:
            skipped += 1
            continue

        try:
            raw = png.read_bytes()
            orig_type = get_card_type(raw)
            if orig_type not in CHARA_CARD_TYPES:
                skipped += 1
                continue
            kc  = KoikatuCharaData.load(str(png))
            key = make_key(kc)
        except Exception as e:
            logger.error("RENAME", f"Could not read {png.name}: {e}")
            skipped += 1
            continue

        nd = cache.get(key)
        if not nd or not _name_known(nd):
            skipped += 1
            continue

        last     = nd.get("lastname", "").strip()
        first    = nd.get("firstname", "").strip()
        nickname = nd.get("nickname", "").strip()

        if update_metadata:
            # Save to a temp file next to the card and only replace the
            # original once the write has fully succeeded and the result
            # looks like a real, parseable chara card. kc.save() writes
            # in a single pass; saving in place means a crash, a disk-full
            # error, or a bad write partway through leaves the card
            # corrupted with the original gone for good. Going through a
            # temp file + os.replace means the original is only ever
            # touched by the atomic rename at the very end.
            tmp_path = png.with_name(png.name + ".kkafio_rename.tmp")
            try:
                kc["Parameter"]["lastname"]  = last
                kc["Parameter"]["firstname"] = first
                kc["Parameter"]["nickname"]  = nickname
                kc.save(str(tmp_path))

                # Sanity-check the written file before trusting it enough to
                # overwrite the original.
                # The type must come back exactly as it went in — in
                # particular a KKS card must still carry its KKS marker
                # (never silently turn into a KK card the game can't tell
                # apart from a real one).
                new_raw = tmp_path.read_bytes()
                if get_card_type(new_raw) != orig_type:
                    raise ValueError(
                        f"saved file is not the same card type as the original "
                        f"({orig_type.value} -> {get_card_type(new_raw).value})")

                os.replace(tmp_path, png)
                logger.info("RENAME",
                    f"Metadata: {png.name} → {last} {first} ({nickname})")
                updated += 1
            except Exception as e:
                logger.error("RENAME", f"Could not update metadata for {png.name}: {e}")
                skipped += 1
                continue
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

        if rename_files:
            stem = _stem_for(nd)
            if stem:
                subfolder = png.parent
                used      = _dir_stems(subfolder)

                candidate = stem
                counter   = 1
                while _rename_collides(png, candidate, used):
                    candidate = f"{stem}_{counter}"
                    counter  += 1
                new_path = subfolder / f"{candidate}.png"
                # Compare names exactly (not Path equality, which is
                # case-insensitive on Windows) so a case-only difference
                # such as alice.png -> Alice.png is still applied.
                if new_path.name != png.name:
                    try:
                        png.rename(new_path)
                        old_key = _fold(png.stem)
                        used[old_key] = used.get(old_key, 1) - 1
                        if used[old_key] <= 0:
                            used.pop(old_key, None)
                        new_key = _fold(candidate)
                        used[new_key] = used.get(new_key, 0) + 1
                        logger.success("RENAME", f"Renamed: {png.name} → {new_path.name}")
                        renamed += 1
                    except Exception as e:
                        logger.error("RENAME", f"Could not rename {png.name}: {e}")

    logger.line()
    parts = []
    if update_metadata: parts.append(f"metadata updated: {updated}")
    if rename_files:    parts.append(f"renamed: {renamed}")
    parts.append(f"skipped/unassigned: {skipped}")
    logger.success("RENAME", "Done — " + ", ".join(parts))


# ---------------------------------------------------------------------------
# Task class
# ---------------------------------------------------------------------------

class RenameChara(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.rename_chara
        self.input_path_str       : str  = cfg.get("InputPath", "")
        self.skip_already_renamed : bool = cfg.get("SkipAlreadyRenamed", True)
        self.update_metadata      : bool = cfg.get("UpdateMetadata", False)
        self.rename_files         : bool = cfg.get("RenameFiles", True)
        self.prompt               : str  = cfg.get("Prompt", "") or PROMPT_TEMPLATE

    def run(self) -> None:
        folder = Path(self.input_path_str or ".")
        validate_input_path("RENAME", folder)

        self.log_start("RENAME", str(folder))

        json_str = export(folder, skip_already_renamed=self.skip_already_renamed)
        if not json_str:
            return

        prompt_text = self.prompt.rstrip("\n") + "\n" + json_str

        from kkafio.system.llm_dialog import llm_dialog
        response = llm_dialog("KKAFIO — Rename Characters", prompt_text)
        if not response or not response.strip():
            logger.warning("RENAME", "Dialog cancelled or empty response — nothing to do.")
            return

        process(folder, response,
                skip_already_renamed=self.skip_already_renamed,
                update_metadata=self.update_metadata,
                rename_files=self.rename_files)