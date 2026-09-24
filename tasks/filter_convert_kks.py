"""
FilterConvertKKS
================
Scans a folder for PNG character cards, optionally converts Koikatsu
Sunshine (KKS) cards to Koikatsu (KK) format, then optionally sorts each
type into its own folder or sends it to the Recycle Bin.

Convert    — produce a KK-compatible copy of each KKS card (binary header
            patch). The copy is written alongside its source card; whether
            it then gets moved, deleted, or left in place is decided by
            KKAction below, exactly like any other KK card.

KKAction   — what to do with every KK/KKSP card found (including any KKS
            card just converted to KK by the option above):
              • Keep   — do nothing, leave it where it is (default)
              • Move   — move it into _KK_card_/
              • Delete — send it to the Recycle Bin

KKSAction  — the same three choices, applied to the original KKS cards
            (never to their converted copies, which KKAction covers).
"""

from pathlib import Path
from send2trash import send2trash

from tasks.base_task import DEFAULT_DOWNLOADS_PATH, validate_input_path
from utils.config import Config
from utils.classifier import CardType, get_card_type
from utils.file_manager import FileManager
from utils.logger import logger

ACTION_KEEP   = "Keep"
ACTION_MOVE   = "Move"
ACTION_DELETE = "Delete"

# Output folders this task creates. Excluded from the next run's scan so
# re-running it (e.g. as a repeating pipeline step) doesn't re-process
# cards it already sorted on a previous run.
_OUTPUT_FOLDER_NAMES = {"_KKS_card_", "_KK_card_"}


class FilterConvertKKS:
    def __init__(self, config: Config, file_manager: FileManager):
        self.config          = config
        self.file_manager    = file_manager
        self.convert          = self.config.filter_convert_kks.get("Convert", False)
        self.kk_action        = self.config.filter_convert_kks.get("KKAction", ACTION_KEEP)
        self.kks_action       = self.config.filter_convert_kks.get("KKSAction", ACTION_KEEP)
        self.extract_archive  = self.config.filter_convert_kks.get("ExtractArchive", True)

    # ------------------------------------------------------------------
    # Card-type helpers
    # ------------------------------------------------------------------

    def get_list(self, folder_path: Path) -> list[Path]:
        out: list[Path] = []
        for f in folder_path.rglob("*.png"):
            if _OUTPUT_FOLDER_NAMES & {p.name for p in f.relative_to(folder_path).parents}:
                continue
            out.append(f)
        return out

    def check_png(self, card_path: Path) -> CardType:
        return get_card_type(card_path.read_bytes())

    # ------------------------------------------------------------------
    # Binary conversion helper
    # ------------------------------------------------------------------

    def _patch_kks_to_kk(self, card_path: Path) -> Path | None:
        """Patch a KKS card binary so it loads as a KK card, saving the
        result alongside the source card. Returns the new file's path, or
        None if it couldn't be written."""
        data = card_path.read_bytes()
        for old, new in [
            (b"\x15\xe3\x80\x90KoiKatuCharaSun", b"\x12\xe3\x80\x90KoiKatuChara"),
            (b"Parameter\xa7version\xa50.0.6",     b"Parameter\xa7version\xa50.0.5"),
            (b"version\xa50.0.6\xa3sex",            b"version\xa50.0.5\xa3sex"),
        ]:
            data = data.replace(old, new)
        out = card_path.parent / f"KKS2KK_{card_path.name}"
        try:
            out.write_bytes(data)
            return out
        except OSError as e:
            logger.error("SCRIPT", f"Could not write converted copy of {card_path.name}: {e}")
            return None

    # ------------------------------------------------------------------
    # Sorting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_move(src: Path, dest_folder: Path) -> Path | None:
        """Move src into dest_folder, handling name clashes and I/O errors
        instead of letting shutil.move raise/clobber. Returns the final
        path, or None if the move failed."""
        import shutil
        dest = dest_folder / src.name
        if dest.exists() and not dest.samefile(src):
            stem, suffix = src.stem, src.suffix
            n = 1
            while dest.exists():
                dest = dest_folder / f"{stem}_{n}{suffix}"
                n += 1
        try:
            shutil.move(str(src), str(dest))
            return dest
        except OSError as e:
            logger.error("SCRIPT", f"Could not move {src.name}: {e}")
            return None

    def _apply_action(self, cards: list[Path], action: str, base_path: Path,
                       dest_folder_name: str, label: str) -> None:
        """Apply Keep/Move/Delete to a list of cards, logging one summary
        line for the whole batch. `base_path` is always the top-level
        folder this run scanned — not each card's own parent folder — so
        every KK/KKSP (or KKS) card ends up in exactly one shared
        `base_path/_KK_card_` (or `_KKS_card_`), including cards found
        inside an extracted archive's subfolder. Anchoring on a card's own
        parent instead would scatter cards into a different destination
        folder per source subfolder."""
        if not cards:
            logger.success("SCRIPT", f"No {label} cards found")
            return

        if action == ACTION_KEEP:
            logger.success("SCRIPT", f"[{len(cards)}] {label} card(s) found (kept in place)")
            return

        if action == ACTION_MOVE:
            dest_folder = base_path / dest_folder_name
            dest_folder.mkdir(exist_ok=True)
            moved_ok = sum(1 for c in cards if self._safe_move(c, dest_folder) is not None)
            logger.success("SCRIPT",
                f"[{moved_ok}] {label} card(s) -> [{dest_folder_name}]"
                + (f"  ({len(cards) - moved_ok} failed)" if moved_ok < len(cards) else ""))
            return

        if action == ACTION_DELETE:
            deleted_ok = 0
            for c in cards:
                try:
                    send2trash(str(c))
                    deleted_ok += 1
                except Exception as e:
                    logger.error("SCRIPT", f"Could not delete {c.name}: {e}")
            logger.success("SCRIPT",
                f"[{deleted_ok}] {label} card(s) sent to the Recycle Bin"
                + (f"  ({len(cards) - deleted_ok} failed)" if deleted_ok < len(cards) else ""))
            return

        logger.warning("SCRIPT", f"Unknown action '{action}' for {label} cards — leaving them in place")

    # ------------------------------------------------------------------
    # Archive extraction
    # ------------------------------------------------------------------

    def _extract_archives(self, path: Path) -> None:
        """Extract every archive found under `path` into a folder named after
        it, skipping archives whose folder already exists. The extracted
        folders are left in place."""
        _, archive_list = self.file_manager.find_all_files(path)
        if not archive_list:
            return
        logger.info("SCRIPT", f"Extracting {len(archive_list)} archive(s) before filtering")
        for archive in archive_list:
            self.file_manager.extract_archive(
                archive[0], task_config=self.config.filter_convert_kks)

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def run(self) -> None:
        path = Path(self.config.filter_convert_kks["InputPath"])

        validate_input_path("FILTER", path, default_path=DEFAULT_DOWNLOADS_PATH)

        # 1. Extract archives, if enabled.
        if self.extract_archive:
            self._extract_archives(path)

        png_list = self.get_list(path)
        if not png_list:
            logger.success("SCRIPT", "No PNG files found")
            return

        logger.info("SCRIPT", "KK: Koikatsu / KKSP: Koikatsu Special / KKS: Koikatsu Sunshine")
        logger.line()
        logger.info("FOLDER", str(path))

        kks_cards: list[Path] = []
        kk_cards:  list[Path] = []

        for png in png_list:
            card_type = self.check_png(png)
            if card_type == CardType.KKS:
                logger.info(card_type.value, png.name)
                kks_cards.append(png)
            elif card_type in (CardType.KK, CardType.KKSP):
                logger.info(card_type.value, png.name)
                kk_cards.append(png)

        logger.line()

        # 2. Convert KKS -> KK, if enabled. Each converted copy is written
        # next to its source and joins kk_cards, so KKAction below applies
        # to it exactly like any other KK card.
        if self.convert and kks_cards:
            converted = 0
            for card in kks_cards:
                new_card = self._patch_kks_to_kk(card)
                if new_card is not None:
                    kk_cards.append(new_card)
                    converted += 1
            logger.success("SCRIPT", f"[{converted}] KKS card(s) converted to KK")
            if converted < len(kks_cards):
                logger.warning("SCRIPT",
                    f"[{len(kks_cards) - converted}] KKS card(s) could not be converted")

        # 3. Run KKSAction / KKAction, unless left at Keep.
        self._apply_action(kks_cards, self.kks_action, path, "_KKS_card_", "KKS")
        self._apply_action(kk_cards,  self.kk_action,  path, "_KK_card_",  "KK/KKSP")
