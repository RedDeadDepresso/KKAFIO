"""
group_chara.py — Group character cards into folders by series, with an LLM and/or by hand.

Two modes (the "Mode" option; both can be on): LLM runs the workflow below; Manual opens a
gallery dialog (src/kkafio/system/manual_group_dialog.py) where the user types each character's
series, with suggestions from the anime, games, vtubers index and the series folders that already
exist; a confirmed card is moved into <chara_folder>/<series>/ at once. With both on, the LLM
goes first and the gallery then opens for whatever it left without a series.

With "Resolve Locally" on (and Hints Language not "no"), a card whose name matches exactly one
character in the indexes, or one of several with the card's own birthMonth / birthDay, is
assigned that character's series without asking the LLM; see resolve_locally().

Workflow (all handled inside run()):
1. export() scans the folder for KK chara PNGs and builds a JSON dict
   {key: ""} where key encodes lastname + firstname + nickname + personality + hair colour. With the
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
from typing import Optional, Sequence

from kkloader import KoikatuCharaData

from kkafio.tasks.base_task import BaseTask, DEFAULT_LLM_WEBSITE_URL, resolve_chara_dirs
from kkafio.cards.cache_io import file_fp
from kkafio.cards.chara_key import birthday, make_key
from kkafio.cards.chara_key_cache import CharaKeyCache
from kkafio.cards.agv_index import Candidate
from kkafio.cards import hint_language
from kkafio.cards.classifier import CHARA_CARD_TYPES, get_card_type
from kkafio.core.i18n import t
from kkafio.core.logger import logger

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You will receive a JSON object whose keys identify Koikatsu character card files.
Each key has the format:  lastname | firstname | nickname | personality | hair_color

Your task: for every key, write the English name of the anime/game series the character \
is from.

A value is either an empty string "" or an object {"series": "", "hints": [...]}. "hints" \
lists up to 5 candidate characters from a character database, written \
"Last, First (Series)", most popular first. They are only name matches and can be wrong: \
use the one that fits the personality and hair colour, and ignore the list if none does.

Rules:
- Use the official title of the series.
- If a character appears in multiple series, use the one they are most associated with.
- Some characters may be VTubers (virtual YouTubers). Use "VTubers" as the series for them.
- If you use a hint whose series is not in English, translate it into English.
- Use the personality and hair colour as additional hints to identify the character.
- If you do not recognise a character and can search the web, look it up online first.
- If you are still not sure or the character is an original creation, leave the value as an empty string "".
- Return ONLY a JSON object that maps every key to the series name as a plain string \
(do not repeat "hints") — no explanation, no markdown code fences, no extra text \
before or after.

JSON to fill in:
"""

def add_index_hints(json_str: str, lang: str,
                    narrowed: dict[str, list[Candidate]] | None = None) -> str:
    """Turn export()'s {key: ""} into {key: {"series": "", "hints": [...]}} for the LLM.

    "hints" lists up to 5 candidate characters from the character indexes (gacha first, then the
    anime, games, vtubers index) as "Last, First (Series)", most popular first, in the hint language `lang`
    (English where an index has none). Every card is sent, and a card the indexes don't know
    gets an empty list. `narrowed` (from resolve_locally) maps a key to the only candidates
    to list for it, instead of everything the indexes have under that name."""
    from kkafio.cards import char_index
    if not json_str or not char_index.available():
        return json_str
    out: dict[str, dict] = {}
    hinted = 0
    for key, value in json.loads(json_str).items():
        h = [c.hint for c in narrowed[key]] if narrowed and key in narrowed else char_index.hints(key, lang)
        hinted += bool(h)
        out[key] = {"series": value, "hints": h}
    logger.info("GROUP", f"Character index: {hinted} of {len(out)} character(s) have hints")
    return json.dumps(out, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Resolve Locally — decide the easy cards without the LLM
# ---------------------------------------------------------------------------

Birth = tuple[Optional[int], Optional[int]]          # (birthMonth, birthDay) of a card


def resolve_locally(cards: dict[str, set[Birth]], lang: str,
                    ) -> tuple[dict[tuple[str, Birth], str], dict[str, list[Candidate]]]:
    """Decide which cards need no LLM.

    `cards` maps every key to the birthdays of the cards that have it (a key is not unique
    to one card). For each (key, birthday) the indexes' candidates are weighed:

    - exactly one candidate -> it is the character;
    - several -> the character is the one candidate whose birthMonth and birthDay are the
      card's (none, or more than one, matching decides nothing).

    A character's series is its first one, in `lang`. If the index has no title for it in
    `lang`, the card is not resolved; the LLM is given that character as the only hint instead.

    Returns (resolved: {(key, birthday): series}, narrowed: {key: [the only candidates to hint]}).
    `narrowed` only holds keys whose every undecided card was narrowed this way; the other
    keys keep their full hint list.
    """
    from kkafio.cards import char_index
    resolved: dict[tuple[str, Birth], str] = {}
    narrowed: dict[str, list[Candidate]] = {}
    for key, births in cards.items():
        cands = char_index.lookup(key, lang, limit=char_index.RESOLVE_CANDIDATES)
        if not cands:
            continue
        undecided_full = False
        picked: list[Candidate] = []
        for birth in births:
            c = char_index.decide(cands, birth)
            if c is None or not c.series:
                undecided_full = True
            elif c.series_native:
                resolved[(key, birth)] = c.series[0]
            elif c not in picked:
                picked.append(c)
        if picked and not undecided_full:
            narrowed[key] = picked
    return resolved, narrowed


def keys_left_for_llm(json_str: str, cards: dict[str, set[Birth]],
                      resolved: dict[tuple[str, Birth], str]) -> str:
    """export()'s JSON without the keys whose every card was resolved locally."""
    keep = {k for k, births in cards.items() if any((k, b) not in resolved for b in births)}
    return json.dumps({k: v for k, v in json.loads(json_str).items() if k in keep},
                      indent=4, ensure_ascii=False)


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


def _read_card(png: Path, key_cache: CharaKeyCache) -> tuple[Path, str | None, Birth | None]:
    """Read, classify and build the key and birthday for one PNG, or take them from the
    cache. Returns (png, key, birthday): key None = not a chara card, "__error__<why>" = it
    couldn't be read. Runs in a thread pool worker."""
    try:
        fp = file_fp(png)
        hit, key = key_cache.lookup(png, fp, birth=True)
        if hit:
            return png, key, key_cache.birth_of(png)      # key None: not a chara card
        raw = png.read_bytes()
        if get_card_type(raw) not in CHARA_CARD_TYPES:
            key_cache.put(png, fp, None)
            return png, None, None  # not a chara card — skip silently
        kc  = KoikatuCharaData.load(str(png))
        key = make_key(kc)
        birth = birthday(kc)
        key_cache.put(png, fp, key, birth)
        return png, key, birth
    except Exception as e:
        return png, f"__error__{e}", None


# ---------------------------------------------------------------------------
# Export — build prompt + JSON, return as string for the clipboard
# ---------------------------------------------------------------------------

def export(folders: Path | Sequence[Path], include_subfolders: bool = False,
           use_cache: bool = True, birthdays: dict[str, set[Birth]] | None = None) -> str:
    """Scan the given chara folder(s) for chara PNGs and return the character-key JSON.

    Args:
        include_subfolders: When False (default) only scans the top-level folder,
                            skipping already-sorted cards in subfolders.
                            When True scans recursively.
        use_cache: Reuse each card's key from kkafio_chara_key_cache.json when the file
                   hasn't changed (mtime + size), instead of re-parsing it.
        birthdays: If given (an empty dict), it is filled with {key: {(birthMonth, birthDay),
                   ...}} for the cards found. Each card's birthday is always cached next to its key.

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

    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    workers = min(32, (os.cpu_count() or 4) * 2)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_read_card, png, key_cache): png for png in png_files}
        for future in as_completed(futures):
            png, result, birth = future.result()
            if result is None:
                pass                    # not a chara card — skip
            elif result.startswith("__error__"):
                logger.warning("GROUP", f"Could not process {png.name}: {result[9:]}")
            else:
                characters.setdefault(result, "")
                if birthdays is not None:
                    birthdays.setdefault(result, set()).add(birth or (None, None))

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


def _move_card(png: Path, dest_dir: Path, series_folder: str, key_cache: CharaKeyCache) -> Path | None:
    """Move `png` into the existing folder `dest_dir` (a file name already taken there gets
    _1, _2, ... added), keeping the key cache in step. Returns where it went, or None (after
    a warning) if it couldn't be moved."""
    dest = dest_dir / png.name
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
        return dest
    except Exception as e:
        logger.warning("GROUP", f"Could not move {png.name}: {e}")
        return None


def process(folders: Path | Sequence[Path], json_str: str, include_subfolders: bool = False,
            use_cache: bool = True,
            hint_language: str | None = None,
            local: dict[tuple[str, Birth], str] | None = None) -> None:
    """Move chara PNGs into series subfolders based on the LLM JSON response.

    Each card is moved into <its own folder>/<series>/ — so with the game's
    female/ and male/ folders, a card never leaves the one it's in.

    include_subfolders must match what export() was given: when True, cards
    already sitting in subfolders are regrouped too; when False only
    top-level cards are touched.

    use_cache: take each card's key from kkafio_chara_key_cache.json when the file is
    unchanged, and keep that cache in step with the moves (a moved card's entry follows
    it to its new folder) so the next run doesn't have to read the cards again.

    hint_language: snap every series name to its canonical gacha-game or anime, games, vtubers index title in
    this language (unknown names are left as they are). None: leave the names alone.

    local: {(key, (birthMonth, birthDay)): series} from resolve_locally(). A card with such an
    entry goes to that series whatever the response says; the others follow the response.
    """
    folder_list = _as_folder_list(folders)

    # Parse the LLM response — strip markdown fences if the user forgot
    clean = json_str.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.splitlines()[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.splitlines()[:-1])

    try:
        raw = json.loads(clean) if clean else {}
    except json.JSONDecodeError as e:
        logger.warning("GROUP", f"Could not parse response JSON: {e}")
        return
    # A value is the series name; an LLM that echoed {"series": ..., "hints": ...} is fine too.
    mapping: dict[str, str] = {k: v.get("series", "") if isinstance(v, dict) else v
                               for k, v in raw.items()}
    if hint_language:
        from kkafio.cards import char_index
        mapping = {k: char_index.canonical_series(v, hint_language) if isinstance(v, str) else v
                   for k, v in mapping.items()}

    # Build reverse map: key -> destination folder name (skip empty values)
    dest_map = {
        key: _safe_folder_name(series)
        for key, series in mapping.items()
        if series and series.strip()
    }

    local_dest = {kb: _safe_folder_name(series) for kb, series in (local or {}).items()}
    local_dest = {kb: folder for kb, folder in local_dest.items() if folder}

    if not dest_map and not local_dest:
        logger.warning("GROUP", "No series assignments found in response — nothing to do")
        return

    logger.info("GROUP",
        f"Processing {len(dest_map)} assignment(s)"
        + (f" and {len(local_dest)} resolved locally" if local_dest else "") + " in "
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
                hit, key = key_cache.lookup(png, fp, birth=True)
                birth: Birth | None = key_cache.birth_of(png) if hit else None
                if not hit:
                    raw = png.read_bytes()
                    if get_card_type(raw) not in CHARA_CARD_TYPES:
                        key_cache.put(png, fp, None)
                        skipped += 1
                        continue
                    kc  = KoikatuCharaData.load(str(png))
                    key = make_key(kc)
                    birth = birthday(kc)
                    key_cache.put(png, fp, key, birth)
                elif key is None:
                    skipped += 1          # cached as "not a chara card"
                    continue
            except Exception as e:
                logger.warning("GROUP", f"Could not read {png.name}: {e}")
                skipped += 1
                continue

            series_folder = (local_dest.get((key, birth or (None, None)))
                             if local_dest else None) or dest_map.get(key)
            if not series_folder:
                skipped += 1
                continue

            dest_dir = folder_path / series_folder
            if png.parent == dest_dir:
                skipped += 1          # already in the right series folder
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            if _move_card(png, dest_dir, series_folder, key_cache) is not None:
                moved += 1
            else:
                skipped += 1
    finally:
        # Saved even if the run is stopped part-way, so what was moved/learned so far isn't lost.
        if use_cache:
            logger.info("GROUP", f"Key cache: {key_cache.summary()}")
            key_cache.save()

    logger.line()
    logger.success("GROUP", f"Done — moved: {moved}, skipped/unassigned: {skipped}")


# ---------------------------------------------------------------------------
# Manual mode — the gallery dialog's data and callbacks
# ---------------------------------------------------------------------------

def _is_series_dir(name: str) -> bool:
    """Folders starting with "_" or "." (e.g. _duplicates_) are not series."""
    return not name.startswith(("_", "."))


def existing_series(folders: Sequence[Path]) -> dict[str, str]:
    """{casefolded name: name} of the series folders that exist directly inside the chara folders."""
    known: dict[str, str] = {}
    for root in folders:
        try:
            for entry in sorted(root.iterdir(), key=lambda e: e.name.casefold()):
                if entry.is_dir() and _is_series_dir(entry.name):
                    known.setdefault(entry.name.casefold(), entry.name)
        except OSError:
            pass
    return known


def scan_manual_items(folders: Sequence[Path], include_subfolders: bool,
                      key_cache: CharaKeyCache) -> list:
    """The character cards for the gallery dialog, as CharaItems. A card directly in a chara
    folder has no series; one inside a folder has that (first) folder as its series."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    from kkafio.cards.agv_index import split_key
    from kkafio.system.manual_group_dialog import CharaItem

    found: list[tuple[Path, Path]] = []
    for root in folders:
        for png in (root.rglob("*.png") if include_subfolders else root.glob("*.png")):
            if all(_is_series_dir(part) for part in png.relative_to(root).parts[:-1]):
                found.append((root, png))
    logger.info("GROUP", f"Scanning {len(found)} PNG file(s) for the gallery")

    unique = dict.fromkeys(png for _root, png in found)
    root_of = {png: root for root, png in found}
    items: list[CharaItem] = []
    with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 2)) as ex:
        for png, key, _birth in ex.map(lambda p: _read_card(p, key_cache), unique):
            if key is None:
                continue                                  # not a chara card
            if key.startswith("__error__"):
                logger.warning("GROUP", f"Could not process {png.name}: {key[9:]}")
                continue
            try:
                st = png.stat()
            except OSError:
                continue
            last, first, nick = split_key(key)
            parts = png.relative_to(root_of[png]).parts
            items.append(CharaItem(path=png, root=root_of[png],
                                   name=f"{last} {first}".strip() or nick or png.stem,
                                   series=parts[0] if len(parts) > 1 else None,
                                   size=st.st_size, mtime=st.st_mtime))
    return items


def resolve_series_folder(text: str, known: dict[str, str], lang: str | None) -> str:
    """The folder name for a series the user typed: snapped to the index's title for it (when
    `lang` is set), made safe to use as a folder name, and spelled like the existing folder
    when one with the same name (in any case) exists. "" if nothing usable is left."""
    name = text.strip()
    if lang and name:
        from kkafio.cards import agv_index
        name = agv_index.canonical_series(name, lang)
    name = _safe_folder_name(name)
    return known.get(name.casefold(), name) if name else ""


def manual_callbacks(known: dict[str, str], key_cache: CharaKeyCache, lang: str | None,
                     max_suggestions: int = 8):
    """(suggest, assign) for the gallery dialog. `known` ({casefolded: name}) holds the series
    folders that exist and grows as new ones are created; `lang` is the language of the index's
    series titles (None: no index, only the existing folders are suggested)."""

    def suggest(text: str) -> list[str]:
        q = text.strip().casefold()
        local = sorted(known.values(), key=str.casefold)
        if q:
            local = sorted((n for n in local if q in n.casefold()),
                           key=lambda n: (not n.casefold().startswith(q), n.casefold()))
        out = list(local)
        if lang and q:
            from kkafio.cards import agv_index
            seen = {n.casefold() for n in out}
            for title in agv_index.suggest_series(text, lang, limit=max_suggestions):
                folder = _safe_folder_name(title)          # shown as the folder it would become
                if folder and folder.casefold() not in seen:
                    seen.add(folder.casefold())
                    out.append(folder)
        return out[:max_suggestions]

    def assign(item, text: str):
        folder = resolve_series_folder(text, known, lang)
        if not folder:
            logger.warning("GROUP", f"'{text}' can't be used as a series folder name")
            return None
        dest_dir = item.root / folder
        if item.path.parent == dest_dir:
            return item.path, folder                      # already there
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("GROUP", f"Could not create {dest_dir}: {e}")
            return None
        dest = _move_card(item.path, dest_dir, folder, key_cache)
        if dest is None:
            return None
        known.setdefault(folder.casefold(), folder)
        return dest, folder

    return suggest, assign


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
        self.use_llm             : bool = cfg.get("UseLlm", True)
        self.use_manual          : bool = cfg.get("UseManual", False)
        self.hints_language      : str  = cfg.get("HintsLanguage", hint_language.DETECT)
        self.resolve_locally     : bool = cfg.get("ResolveLocally", False)
        self.open_llm_website : bool = cfg.get("OpenLlmWebsite", True)
        self.llm_website_url  : str  = cfg.get("LlmWebsiteUrl", "") or DEFAULT_LLM_WEBSITE_URL

    def run(self) -> None:
        folders = resolve_chara_dirs(self.config.game_path, self.chara_dir_str, "GROUP")

        self.log_start("GROUP", ", ".join(str(f) for f in folders))
        if not (self.use_llm or self.use_manual):
            logger.warning("GROUP", "Neither LLM nor Manual is selected — nothing to do.")
            return
        logger.info("GROUP", f"Use cache: {self.use_cache}")
        logger.info("GROUP", f"Mode: {' + '.join(m for m, on in (('LLM', self.use_llm), ('Manual', self.use_manual)) if on)}")

        hint_lang = hint_language.resolve(self.hints_language, self.prompt, "GROUP")
        # With both on, the LLM goes first; the gallery then shows what it left without a series.
        if self.use_llm and not self._run_llm(folders, hint_lang):
            return
        if self.use_manual:
            self._run_manual(folders, hint_lang)

    def _run_llm(self, folders: list[Path], hint_lang: str | None) -> bool:
        """The LLM workflow. False if there was nothing to do or the dialog was cancelled."""
        prompt = self.prompt
        resolve = self.resolve_locally
        if resolve and not hint_lang:
            logger.info("GROUP", "Resolve Locally needs the character indexes (Hints Language is "
                                 "'no') — every card goes to the LLM.")
            resolve = False
        logger.info("GROUP", f"Resolve locally: {resolve}")

        birthdays: dict[str, set[Birth]] | None = {} if resolve else None
        json_str = export(folders, include_subfolders=self.include_subfolders,
                          use_cache=self.use_cache, birthdays=birthdays)
        if not json_str:
            return False

        local: dict[tuple[str, Birth], str] = {}
        narrowed: dict[str, list[Candidate]] = {}
        if hint_lang:
            from kkafio.cards import char_index
            char_index.refresh("GROUP")
            if not char_index.supports(hint_lang):
                logger.info("GROUP", f"The character indexes have no {hint_lang} text — hints are in English.")
            if resolve and char_index.available():
                local, narrowed = resolve_locally(birthdays, hint_lang)
                cards_n = sum(len(b) for b in birthdays.values())
                logger.info("GROUP", f"Resolved locally: {len(local)} of {cards_n} character/birthday "
                                     f"combination(s); {len(narrowed)} key(s) hinted with only the "
                                     f"matching character")
                json_str = keys_left_for_llm(json_str, birthdays, local)
            if json.loads(json_str):
                json_str = add_index_hints(json_str, hint_lang, narrowed)

        if not json.loads(json_str):
            logger.success("GROUP", "Every character was resolved locally — the LLM is not needed.")
            process(folders, "{}", include_subfolders=self.include_subfolders,
                    use_cache=self.use_cache, hint_language=hint_lang, local=local)
            return True

        prompt_text = prompt.rstrip("\n") + "\n" + json_str

        from kkafio.system.llm_dialog import llm_dialog, open_llm_website
        if self.open_llm_website:
            open_llm_website("GROUP", self.llm_website_url)
        response = llm_dialog(t("dialog.llm.group_title"), prompt_text)
        if not response or not response.strip():
            logger.warning("GROUP", "Dialog cancelled or empty response — nothing to do.")
            return False

        process(folders, response, include_subfolders=self.include_subfolders,
                use_cache=self.use_cache, hint_language=hint_lang, local=local)
        return True

    def _run_manual(self, folders: list[Path], hint_lang: str | None) -> None:
        """Open the gallery: the user types a series per character and each card is moved at once."""
        from kkafio.system.manual_group_dialog import manual_dialog
        if hint_lang:
            from kkafio.cards import agv_index
            agv_index.refresh("GROUP")
            if not agv_index.available():
                logger.info("GROUP", "No anime, games, vtubers index — only existing series folders are suggested.")
        else:
            logger.info("GROUP", "Hints Language is 'no' — only existing series folders are suggested.")

        key_cache = CharaKeyCache(folders, self.use_cache, "GROUP")
        try:
            items = scan_manual_items(folders, self.include_subfolders, key_cache)
            if not items:
                logger.warning("GROUP", "No readable character cards found")
                return
            known = existing_series(folders)
            for item in items:
                if item.series:
                    known.setdefault(item.series.casefold(), item.series)
            suggest, assign = manual_callbacks(known, key_cache, hint_lang)
            logger.info("GROUP", f"Opening the gallery with {len(items)} character(s)")
            moved = manual_dialog(t("dialog.manual.group_title"), items, suggest, assign)
        finally:
            if self.use_cache:
                logger.info("GROUP", f"Key cache: {key_cache.summary()}")
                key_cache.save()
        logger.line()
        logger.success("GROUP", f"Done — moved: {moved}")
