"""
FilterConvertKKS
================
Scans a folder for PNG character cards and separates them by type.

ConvertKKS — move KKS cards to _KKS_card_/ and produce a KK-compatible
            copy in _KKS_to_KK_/  (binary header patch)
"""

import shutil
from pathlib import Path
from utils.config import Config
from utils.classifier import CardType, get_card_type
from utils.file_manager import FileManager
from utils.logger import logger


class FilterConvertKKS:
    def __init__(self, config: Config, file_manager: FileManager):
        self.config          = config
        self.file_manager    = file_manager
        self.convert_kks     = self.config.filter_convert_kks.get("ConvertKKS", False)
        self.extract_archive = self.config.filter_convert_kks.get("ExtractArchive", True)

    # ------------------------------------------------------------------
    # Card-type helpers
    # ------------------------------------------------------------------

    def get_list(self, folder_path: Path) -> list[Path]:
        return [f for f in folder_path.rglob("*.png")]

    def check_png(self, card_path: Path) -> CardType:
        return get_card_type(card_path.read_bytes())

    # ------------------------------------------------------------------
    # Binary conversion helper
    # ------------------------------------------------------------------

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

    def _extract_archives(self, path: Path) -> None:
        _, archive_list = self.file_manager.find_all_files(path)
        if not archive_list:
            return
        logger.info("SCRIPT", f"Extracting {len(archive_list)} archive(s) before filtering")
        for archive in archive_list:
            self.file_manager.extract_archive(archive[0], task_config=self.config.filter_convert_kks)

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def run(self) -> None:
        path = Path(self.config.filter_convert_kks["InputPath"])

        if not str(path).strip() or str(path) == ".":
            logger.error("FILTER", "InputPath is not set.")
            raise Exception("InputPath is not set")
        if not path.exists():
            logger.error("FILTER", f"InputPath does not exist: {path}")
            raise Exception(f"InputPath does not exist: {path}")

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

        # ── Handle KKS cards ─────────────────────────────────────────
        if kks_cards:
            kks_folder = path / "_KKS_card_"
            kks_folder.mkdir(exist_ok=True)

            if self.convert_kks:
                kks_to_kk_folder = path / "_KKS_to_KK_"
                kks_to_kk_folder.mkdir(exist_ok=True)

            for card in kks_cards:
                if self.convert_kks:
                    self._patch_kks_to_kk(card, kks_to_kk_folder)
                shutil.move(str(card), str(kks_folder / card.name))

            if self.convert_kks:
                logger.success("SCRIPT",
                    f"[{len(kks_cards)}] KKS cards -> [{kks_folder.name}], "
                    f"converted copies -> [{kks_to_kk_folder.name}]")
            else:
                logger.success("SCRIPT",
                    f"[{len(kks_cards)}] KKS cards -> [{kks_folder.name}]")
        else:
            logger.success("SCRIPT", "No KKS cards found")

        # ── Handle KK/KKSP cards ──────────────────────────────────────
        if kk_cards:
            kk_folder = path / "_KK_card_"
            kk_folder.mkdir(exist_ok=True)

            for card in kk_cards:
                shutil.move(str(card), str(kk_folder / card.name))

            logger.success("SCRIPT",
                f"[{len(kk_cards)}] KK/KKSP cards -> [{kk_folder.name}]")
        else:
            if self.convert_kk:
                logger.success("SCRIPT", "No KK/KKSP cards found to convert")
