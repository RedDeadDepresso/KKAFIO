"""
FilterConvertKKS
================
Scans a folder for PNG character cards and separates them by type.

Filter     — move KK/KKSP cards into _KK_card_/ and KKS cards into
            _KKS_card_/. Off by default: cards are left where they are,
            which is what you want when staging cards for Install/Uninstall
            Contents (moving them here would make Uninstall Contents unable
            to reliably match them back up later).

Convert    — produce a KK-compatible copy of each KKS card (binary header
            patch). When Filter is on, copies go to _KKS_to_KK_/. When
            Filter is off, each copy is saved in the same directory as its
            source KKS card instead.
"""

import shutil
from pathlib import Path
from tasks.base_task import DEFAULT_DOWNLOADS_PATH, validate_input_path
from utils.config import Config
from utils.classifier import CardType, get_card_type
from utils.file_manager import FileManager
from utils.logger import logger

# Output folders this task creates. Excluded from the next run's scan so
# re-running it (e.g. as a repeating pipeline step) doesn't re-filter cards
# it already sorted, and doesn't sweep up a KKS2KK_ converted copy (which
# is itself a valid KK-type card once patched) into _KK_card_.
_OUTPUT_FOLDER_NAMES = {"_KKS_card_", "_KK_card_", "_KKS_to_KK_"}


class FilterConvertKKS:
    def __init__(self, config: Config, file_manager: FileManager):
        self.config          = config
        self.file_manager    = file_manager
        self.filter           = self.config.filter_convert_kks.get("Filter", False)
        self.convert          = self.config.filter_convert_kks.get("Convert", False)
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

    @staticmethod
    def _safe_move(src: Path, dest_folder: Path) -> Path | None:
        """Move src into dest_folder, handling name clashes and I/O errors
        instead of letting shutil.move raise/clobber. Returns the final
        path, or None if the move failed."""
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

    def _patch_kks_to_kk(self, card_path: Path, destination_path: Path) -> None:
        """Patch a KKS card binary so it loads as a KK card."""
        data = card_path.read_bytes()
        for old, new in [
            (b"\x15\xe3\x80\x90KoiKatuCharaSun", b"\x12\xe3\x80\x90KoiKatuChara"),
            (b"Parameter\xa7version\xa50.0.6",     b"Parameter\xa7version\xa50.0.5"),
            (b"version\xa50.0.6\xa3sex",            b"version\xa50.0.5\xa3sex"),
        ]:
            data = data.replace(old, new)
        out = destination_path / f"KKS2KK_{card_path.name}"
        out.write_bytes(data)

    # ------------------------------------------------------------------
    # Archive extraction
    # ------------------------------------------------------------------

    def _extract_archives(self, path: Path) -> list[Path]:
        """Extract every archive found directly under `path`. Returns the
        list of extraction folders so the caller can clean them up once
        their contents have actually been filtered — extracting leaves a
        `<name>_<timestamp>` copy of every file on disk that nothing else
        here deletes, so left alone it grows without bound on every run."""
        _, archive_list = self.file_manager.find_all_files(path)
        if not archive_list:
            return []
        logger.info("SCRIPT", f"Extracting {len(archive_list)} archive(s) before filtering")
        extract_paths: list[Path] = []
        for archive in archive_list:
            extract_path = self.file_manager.extract_archive(
                archive[0], task_config=self.config.filter_convert_kks)
            if extract_path is not None:
                extract_paths.append(extract_path)
        return extract_paths

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def run(self) -> None:
        path = Path(self.config.filter_convert_kks["InputPath"])

        validate_input_path("FILTER", path, default_path=DEFAULT_DOWNLOADS_PATH)

        extract_paths = self._extract_archives(path) if self.extract_archive else []

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

        # ── Handle KKS cards ─────────────────────────────────────────
        if kks_cards:
            if self.filter:
                kks_folder = path / "_KKS_card_"
                kks_folder.mkdir(exist_ok=True)

            if self.convert and self.filter:
                kks_to_kk_folder = path / "_KKS_to_KK_"
                kks_to_kk_folder.mkdir(exist_ok=True)

            for card in kks_cards:
                if self.convert:
                    # Filter on -> shared _KKS_to_KK_ folder.
                    # Filter off -> saved next to the original card, since
                    # there's no _KKS_card_ folder for it to live alongside.
                    dest = kks_to_kk_folder if self.filter else card.parent
                    self._patch_kks_to_kk(card, dest)
                if self.filter:
                    self._safe_move(card, kks_folder)

            if self.filter:
                if self.convert:
                    logger.success("SCRIPT",
                        f"[{len(kks_cards)}] KKS cards -> [{kks_folder.name}], "
                        f"converted copies -> [{kks_to_kk_folder.name}]")
                else:
                    logger.success("SCRIPT",
                        f"[{len(kks_cards)}] KKS cards -> [{kks_folder.name}]")
            else:
                if self.convert:
                    logger.success("SCRIPT",
                        f"[{len(kks_cards)}] KKS card(s) found, converted copies saved alongside originals "
                        "(Filter disabled — not moved)")
                else:
                    logger.success("SCRIPT",
                        f"[{len(kks_cards)}] KKS card(s) found (Filter disabled — not moved)")
        else:
            logger.success("SCRIPT", "No KKS cards found")

        # ── Handle KK/KKSP cards ──────────────────────────────────────
        if kk_cards:
            if self.filter:
                kk_folder = path / "_KK_card_"
                kk_folder.mkdir(exist_ok=True)

                moved_ok = 0
                for card in kk_cards:
                    if self._safe_move(card, kk_folder) is not None:
                        moved_ok += 1

                logger.success("SCRIPT",
                    f"[{moved_ok}] KK/KKSP card(s) -> [{kk_folder.name}]"
                    + (f"  ({len(kk_cards) - moved_ok} failed)" if moved_ok < len(kk_cards) else ""))
            else:
                logger.success("SCRIPT",
                    f"[{len(kk_cards)}] KK/KKSP card(s) found (Filter disabled — not moved)")
        else:
            logger.success("SCRIPT", "No KK/KKSP cards found")

        for extract_path in extract_paths:
            remaining = any(extract_path.rglob("*"))
            if remaining:
                continue
            try:
                shutil.rmtree(extract_path)
                logger.info("SCRIPT", f"Cleaned up extracted folder: {extract_path.name}")
            except OSError as e:
                logger.warning("SCRIPT",
                    f"Could not remove extracted folder {extract_path.name}: {e}")
