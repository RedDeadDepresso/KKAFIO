"""
review_similar_chara.py — Find character cards that look like the same
character saved more than once, let the user pick which to remove in a dialog,
then send the picked cards to the Recycle Bin through Delete Cards.

Cards are grouped one of three ways (the "Mode" option):

  Similar cover     the cover images are perceptually similar (pHash, the same
                    kind of comparison the old fuzzy matching of Filter
                    Duplicate Contents used)
  Same name         the card's last name and first name are both the same
  Similar filename  the file names are equal once a trailing number is removed
                    ("file_1", "file_2", "data-05", "Rin (2)" -> "file", "data",
                    "Rin"). Only files in the same folder are grouped.

All modes scan the chara folder(s) recursively. This task never deletes
anything itself: the dialog returns the chosen cards and DeleteCards, with this
task's own copy of its options, does the deleting (shared-mod check, matching
coordinates, ...).
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from kkafio.cards.cache_io import file_fp
from kkafio.cards.classifier import CHARA_CARD_TYPES, get_card_type
from kkafio.cards.similarity import fuzzy_group, phash_of_file
from kkafio.core.errors import ConfigError
from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.system.similar_chara_dialog import ReviewGroup
from kkafio.tasks.base_task import BaseTask, resolve_chara_dirs
from kkafio.tasks.filter_duplicate_contents import (
    KEEP_FIRST_LEX, KEEP_LAST_LEX, KEEP_NEWEST, KEEP_NONE, KEEP_OLDEST,
    PNG_CACHE_FILE, _load_duplic_cache, _save_duplic_cache, _select_keep, _tiebreak,
)

TAG = "SIMILAR"

# ---------------------------------------------------------------------------
# Modes — must match the SimilarityMode case names in interface.json
# ---------------------------------------------------------------------------

MODE_COVER    = "Similar cover"
MODE_NAME     = "Same name"
MODE_FILENAME = "Similar filename"
MODES = (MODE_COVER, MODE_NAME, MODE_FILENAME)

# ---------------------------------------------------------------------------
# Keep strategies for the dialog's Auto-select: the ones Filter Duplicate
# Contents has, plus the two file-size ones (removed there, wanted here).
# ---------------------------------------------------------------------------

KEEP_BIGGEST  = "Biggest file size"
KEEP_SMALLEST = "Smallest file size"

def keep_choices() -> tuple[tuple[str, str], ...]:
    """(dropdown label, strategy key) pairs. A function, not a constant: the labels
    are translated, and the language is only known once the config has been read."""
    return (
        (t("dialog.review.keep_newest"),   KEEP_NEWEST),
        (t("dialog.review.keep_oldest"),   KEEP_OLDEST),
        (t("dialog.review.keep_biggest"),  KEEP_BIGGEST),
        (t("dialog.review.keep_smallest"), KEEP_SMALLEST),
        (t("dialog.review.keep_last"),     KEEP_LAST_LEX),
        (t("dialog.review.keep_first"),    KEEP_FIRST_LEX),
        (t("dialog.review.keep_none"),     KEEP_NONE),
    )


def select_keep(paths: list[Path], keep: str) -> Path | None:
    """The card to keep out of `paths` under strategy `keep`, or None to keep none."""
    if keep in (KEEP_BIGGEST, KEEP_SMALLEST):
        sizes = {p: p.stat().st_size for p in paths}
        best = (max if keep == KEEP_BIGGEST else min)(sizes.values())
        return _tiebreak([p for p in paths if sizes[p] == best])
    return _select_keep(paths, keep)


# ---------------------------------------------------------------------------
# Filename grouping
# ---------------------------------------------------------------------------

# A trailing number — 1 to 3 digits, optionally in () or [] — with any
# separators in front of it, possibly repeated ("file_1_2"). The digit run must
# be a whole number of at most 3 digits, so long digit strings such as the
# timestamps in the game's own names (KoiKatuChara_20200101123456789.png) or
# years are left alone instead of every such card collapsing into one group.
_TRAILING_NUMBER = re.compile(
    r"(?:[\s._\-]*(?:\(\d{1,3}\)|\[\d{1,3}\]|(?<!\d)\d{1,3}(?!\d)))+$"
)


def strip_number_suffix(stem: str) -> str:
    """`stem` without its trailing number and the separators before it
    ("data-05" -> "data"). A name that is nothing but a number is returned as is."""
    base = _TRAILING_NUMBER.sub("", stem).rstrip(" _-.")
    return base if base else stem


def _norm(text: str) -> str:
    """Case-, width- and whitespace-insensitive form for comparing names."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

CACHE_FILE = "kkafio_similar_chara_cache.json"   # per scanned folder
_SKIP_DIR = "_duplicates_"                        # where Filter Duplicate Contents puts copies


@dataclass
class _Card:
    path: Path
    phash: str | None = None
    first: str = ""
    last: str = ""


def _iter_pngs(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != _SKIP_DIR)
        for name in sorted(filenames):
            if name.lower().endswith(".png"):
                yield Path(dirpath, name)


def _read_names(path: Path) -> tuple[str, str]:
    from kkloader import KoikatuCharaData
    parameter = KoikatuCharaData.load(str(path))["Parameter"]
    return str(parameter["firstname"] or ""), str(parameter["lastname"] or "")


class _Scanner:
    """Finds the chara cards under some folders and reads what `mode` needs from each.

    What it learns about a file (is it a chara card, its cover hash, its name) is
    cached in CACHE_FILE in the scanned folder, keyed by the file's mtime + size, so
    a repeat scan only looks at new or changed files. Whether a PNG is a chara card
    is first looked up in Filter Duplicate Contents' PNG cache, which already knows.
    """

    def __init__(self, mode: str, use_cache: bool):
        self.mode = mode
        self.use_cache = use_cache

    def scan(self, roots: list[Path]) -> list[_Card]:
        cards: list[_Card] = []
        seen: set[str] = set()
        for root in roots:
            if not root.is_dir():
                logger.warning(TAG, f"Folder not found, skipping: {root}")
                continue
            for card in self._scan_root(root):
                key = os.path.normcase(os.path.realpath(card.path))
                if key not in seen:
                    seen.add(key)
                    cards.append(card)
        return cards

    def _scan_root(self, root: Path) -> list[_Card]:
        files = list(_iter_pngs(root))
        logger.info(TAG, f"Scanning {root}  ({len(files)} PNG file(s))")
        cache = _load_duplic_cache(root, CACHE_FILE) if self.use_cache else {}
        png_cache = _load_duplic_cache(root, PNG_CACHE_FILE) if self.use_cache else {}
        new_cache: dict[str, dict] = {}
        cards: list[_Card] = []

        def probe(path: Path) -> tuple[Path, dict | None]:
            sp, fp = str(path), file_fp(path)
            entry = cache.get(sp)
            entry = dict(entry) if entry and entry.get("fp") == fp else {"fp": fp}
            if "chara" not in entry:
                known = png_cache.get(sp)
                if known and known.get("fp") == fp and "category" in known:
                    entry["chara"] = known["category"] == "chara"
                else:
                    entry["chara"] = get_card_type(path) in CHARA_CARD_TYPES
            if entry["chara"]:
                if self.mode == MODE_COVER and not entry.get("phash"):
                    ph = phash_of_file(path)
                    if ph:                      # never cache a failed hash
                        entry["phash"] = ph
                elif self.mode == MODE_NAME and "first" not in entry:
                    entry["first"], entry["last"] = _read_names(path)
            return path, entry

        done = 0
        workers = min(8, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future, path in [(pool.submit(probe, p), p) for p in files]:
                done += 1
                if done % 100 == 0:
                    logger.info(TAG, f"Processed {done}/{len(files)}...")
                try:
                    _, entry = future.result()
                except Exception as e:
                    logger.warning(TAG, f"Could not read {path.name}: {e}")
                    continue
                new_cache[str(path)] = entry
                if entry["chara"]:
                    cards.append(_Card(path, entry.get("phash"),
                                       entry.get("first", ""), entry.get("last", "")))

        if self.use_cache:
            _save_duplic_cache(root, CACHE_FILE, new_cache)
        logger.info(TAG, f"  {len(cards)} character card(s)")
        return cards


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _path_key(p: Path) -> str:
    return str(p).casefold()


def _finish(label: str, description: str, members: list[Path]) -> ReviewGroup:
    return ReviewGroup(label, description, tuple(sorted(members, key=_path_key)))


def group_by_cover(cards: list[_Card]) -> list[ReviewGroup]:
    usable = sorted((c for c in cards if c.phash), key=lambda c: _path_key(c.path))
    skipped = len(cards) - len(usable)
    if skipped:
        logger.warning(TAG, f"{skipped} card(s) had no usable cover image and were not compared")
    clusters = fuzzy_group([c.path for c in usable], [c.phash for c in usable])
    return [_finish(t("dialog.review.group_cover"), t("dialog.review.desc_cover"), cluster)
            for cluster in clusters if len(cluster) > 1]


def group_by_name(cards: list[_Card]) -> list[ReviewGroup]:
    by_name: dict[tuple[str, str], list[_Card]] = defaultdict(list)
    for card in cards:
        key = (_norm(card.last), _norm(card.first))
        if key != ("", ""):                      # unnamed cards would all "match"
            by_name[key].append(card)
    groups = []
    for members in by_name.values():
        if len(members) > 1:
            first = min(members, key=lambda c: _path_key(c.path))
            name = f"{first.last} {first.first}".strip()
            groups.append(_finish(name, t("dialog.review.desc_name"),
                                  [c.path for c in members]))
    return groups


def group_by_filename(cards: list[_Card]) -> list[ReviewGroup]:
    by_name: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for card in cards:
        by_name[(os.path.normcase(str(card.path.parent)),
                 _norm(strip_number_suffix(card.path.stem)))].append(card.path)
    groups = []
    for members in by_name.values():
        if len(members) > 1:
            first = min(members, key=_path_key)
            groups.append(_finish(strip_number_suffix(first.stem),
                                  t("dialog.review.desc_filename", folder=first.parent),
                                  members))
    return groups


GROUPERS = {MODE_COVER: group_by_cover, MODE_NAME: group_by_name, MODE_FILENAME: group_by_filename}


def build_groups(mode: str, cards: list[_Card]) -> list[ReviewGroup]:
    """The groups of similar cards, ordered by their first file."""
    return sorted(GROUPERS[mode](cards), key=lambda g: _path_key(g.paths[0]))


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

# The Delete Cards options this task carries (its own copy of them).
_DELETE_CARDS_KEYS = ("CheckSharedMods", "IncludeCoordinates", "UseCache", "AutoResolve",
                      "ModsDir", "CharaDir", "SceneDir", "CoordDir")


class ReviewSimilarChara(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        self.cfg = self.config.review_similar_chara
        self.mode      : str  = self.cfg.get("Mode", MODE_COVER)
        self.use_cache : bool = self.cfg.get("UseCache", True)
        self.chara_dir_str : str = self.cfg.get("CharaDir", "")
        self._scan_folders : list[Path] = []
        if self.mode not in MODES:
            raise ConfigError(f"Unknown match mode '{self.mode}' (expected one of: {', '.join(MODES)})",
                              tag=TAG)

    def _delete_cards_settings(self, selected: list[Path]) -> dict:
        settings = {k: self.cfg[k] for k in _DELETE_CARDS_KEYS if k in self.cfg}
        settings["Enable"] = True
        settings["ContentPaths"] = [str(p) for p in selected]
        # The shared-mod check must see every card that could still use a mod: the folders
        # reviewed here AND the game's own chara folders. Passing only CharaDir would make a
        # review of one subfolder (e.g. from the context menu) blind to cards elsewhere.
        game = self.config.game_path
        dirs: list[str] = []
        seen: set[str] = set()
        for d in [*self._scan_folders, game.get("charaFemale"), game.get("charaMale")]:
            if d and (key := os.path.normcase(os.path.realpath(str(d)))) not in seen:
                seen.add(key)
                dirs.append(str(d))
        settings["CharaDirs"] = dirs
        return settings

    @staticmethod
    def _extra_details(path: Path) -> list[tuple[str, str]]:
        """Character name, for the dialog's Details window."""
        first, last = _read_names(path)
        return [(t("dialog.review.detail_character"),
                 f"{last} {first}".strip() or t("dialog.review.no_name"))]

    def run(self) -> None:
        folders = resolve_chara_dirs(self.config.game_path, self.chara_dir_str, TAG)
        self._scan_folders = folders
        self.log_start(TAG, ", ".join(str(f) for f in folders))
        logger.info(TAG, f"Match by  : {self.mode}")
        logger.info(TAG, f"Use cache : {self.use_cache}")

        cards = _Scanner(self.mode, self.use_cache).scan(folders)
        groups = build_groups(self.mode, cards)
        logger.line()
        if not groups:
            logger.success(TAG, f"No similar characters found among {len(cards)} card(s)")
            return
        logger.info(TAG, f"{len(groups)} group(s) of similar characters "
                         f"({sum(len(g.paths) for g in groups)} cards) - opening the review dialog...")

        from kkafio.system.similar_chara_dialog import review_dialog
        selected = review_dialog(t("dialog.review.title"), groups, keep_choices(), select_keep,
                                 self._extra_details)
        if not selected:
            logger.info(TAG, "Nothing selected - nothing was deleted")
            return

        logger.info(TAG, f"{len(selected)} card(s) selected - handing them to Delete Cards")
        from kkafio.tasks.delete_cards import DeleteCards
        DeleteCards(self.config, self.file_manager,
                    settings=self._delete_cards_settings(selected)).run()
