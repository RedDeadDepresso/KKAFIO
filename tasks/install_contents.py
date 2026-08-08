from pathlib import Path
from utils.config import Config, GameType
from utils.classifier import CardType, get_card_type, is_male, is_coordinate
from utils.file_manager import FileManager
from utils.logger import logger
from typing import Optional


class InstallContents:
    def __init__(self, config: Config, file_manager: FileManager):
        """Initializes the InstallContents module.

        Args:
            config (Config): KKAFIO Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.game_path = self.config.game_path
        self.input_path = Path(self.config.install_contents["InputPath"])
        self.extract_archive = self.config.install_contents.get("ExtractArchive", True)
        # KoikatsuSunshine installs KKS cards; all other variants install KK cards
        self.game_type = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        self.is_sunshine = self.game_type == GameType.KOIKATSU_SUNSHINE.value

    def _filter_convert_chara_shares_input(self) -> bool:
        """Return True if FilterConvertChara is enabled and uses the same input path."""
        fc = self.config.config_data.get("FilterConvertChara", {})
        if not fc.get("Enable", False):
            return False
        fc_path = fc.get("InputPath")
        if fc_path is None:
            return False
        return Path(fc_path) == self.input_path

    def resolve_png(self, image_path: Path):
        image_bytes = image_path.read_bytes()
        card_type = get_card_type(image_bytes)

        if card_type == CardType.SCENE:
            self.file_manager.copy_and_paste("SCENE", image_path, self.game_path["scene"])

        elif card_type == CardType.UNKNOWN:
            if is_coordinate(image_bytes):
                self.file_manager.copy_and_paste("COORD", image_path, self.game_path["coordinate"])
            else:
                self.file_manager.copy_and_paste("OVERLAYS", image_path, self.game_path["Overlays"])

        elif self.is_sunshine:
            # Koikatsu Sunshine install — accept KKS cards, skip KK/KKSP
            match card_type:
                case CardType.KKS:
                    if is_male(image_bytes):
                        self.file_manager.copy_and_paste("CHARA M", image_path, self.game_path["charaMale"])
                    else:
                        self.file_manager.copy_and_paste("CHARA F", image_path, self.game_path["charaFemale"])

                case CardType.KK | CardType.KKSP:
                    logger.skipped("CHARA", f"{image_path.name} is a {card_type.value} card (KK/KKSP not supported by {GameType.KOIKATSU_SUNSHINE.value})")

        else:
            # Koikatsu / Koikatsu Party install — accept KK/KKSP cards, skip KKS
            match card_type:
                case CardType.KK | CardType.KKSP:
                    if is_male(image_bytes):
                        self.file_manager.copy_and_paste("CHARA M", image_path, self.game_path["charaMale"])
                    else:
                        self.file_manager.copy_and_paste("CHARA F", image_path, self.game_path["charaFemale"])

                case CardType.KKS:
                    logger.skipped("CHARA", f"{image_path.name} is a KKS card (not supported by {self.game_type})")

    def run(self, folder_path: Optional[Path] = None, skip_extract: bool = False):
        if folder_path is None:
            folder_path = self.input_path
        folder_path = Path(folder_path)
        foldername = folder_path.name
        logger.line()
        logger.info("FOLDER", foldername)

        file_list, archive_list = self.file_manager.find_all_files(folder_path)

        for file in file_list:
            path, size, extension = file
            match extension:
                case ".zipmod":
                    self.file_manager.copy_and_paste("MODS", path, self.game_path["mods"])
                case ".png":
                    self.resolve_png(path)
                case _:
                    basename = Path(path).name
                    logger.error("UNKNOWN", f"Cannot classify {basename}")
        logger.line()

        # Determine whether to extract archives for this call:
        # - skip_extract=True means the caller (cmd_run) has decided to skip
        # - self.extract_archive=False means the user disabled it in config
        should_extract = self.extract_archive and not skip_extract

        if should_extract:
            for archive in archive_list:
                extract_path = self.file_manager.extract_archive(archive[0], self.config.install_contents)
                if extract_path is not None:
                    self.run(extract_path)
        elif archive_list:
            names = ", ".join(Path(a[0]).name for a in archive_list)
            logger.info("SKIP", f"Archive extraction skipped: {names}")
