from pathlib import Path
from utils.config import Config, GameType
from utils.classifier import CardType, get_card_type, is_male, is_coordinate
from utils.file_manager import FileManager
from utils.logger import logger
from typing import Optional


class InstallContents:
    def __init__(self, config: Config, file_manager: FileManager):
        self.config          = config
        self.file_manager    = file_manager
        self.game_path       = self.config.game_path
        self.input_path      = Path(self.config.install_contents["InputPath"])
        self.extract_archive = self.config.install_contents.get("ExtractArchive", True)
        self.game_type       = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        self.is_sunshine     = self.game_type == GameType.KOIKATSU_SUNSHINE.value

        cfg = self.config.install_contents
        self.install_chara     : bool = cfg.get("Chara",    True)
        self.install_mods      : bool = cfg.get("Mods",     True)
        self.install_coords    : bool = cfg.get("Coords",   True)
        self.install_scenes    : bool = cfg.get("Scenes",   True)
        self.install_overlays  : bool = cfg.get("Overlays", True)

    def _filter_convert_chara_shares_input(self) -> bool:
        fc = self.config.config_data.get("FilterConvertChara", {})
        if not fc.get("Enable", False):
            return False
        fc_path = fc.get("InputPath")
        if fc_path is None:
            return False
        return Path(fc_path) == self.input_path

    def resolve_png(self, image_path: Path):
        image_bytes = image_path.read_bytes()
        card_type   = get_card_type(image_bytes)

        if self.is_sunshine:
            match card_type:
                case CardType.KKS:
                    if not self.install_chara:
                        return
                    if is_male(image_bytes):
                        self.file_manager.copy_and_paste("CHARA M", image_path, self.game_path["charaMale"])
                    else:
                        self.file_manager.copy_and_paste("CHARA F", image_path, self.game_path["charaFemale"])

                case CardType.KK | CardType.KKSP:
                    logger.skipped("CHARA", f"{image_path.name} is a {card_type.value} card "
                                            f"(KK/KKSP not supported by {GameType.KOIKATSU_SUNSHINE.value})")

                case CardType.SCENE:
                    if not self.install_scenes:
                        return
                    if "scene" in self.game_path:
                        self.file_manager.copy_and_paste("SCENE", image_path, self.game_path["scene"])
                    else:
                        logger.skipped("SCENE", f"{image_path.name} — Studio not installed, skipping")

                case CardType.UNKNOWN:
                    if is_coordinate(image_bytes):
                        if not self.install_coords    :
                            return
                        self.file_manager.copy_and_paste("COORD", image_path, self.game_path["coordinate"])
                    else:
                        if not self.install_overlays:
                            return
                        self.file_manager.copy_and_paste("OVERLAYS", image_path, self.game_path["Overlays"])
        else:
            match card_type:
                case CardType.KK | CardType.KKSP:
                    if not self.install_chara:
                        return
                    if is_male(image_bytes):
                        self.file_manager.copy_and_paste("CHARA M", image_path, self.game_path["charaMale"])
                    else:
                        self.file_manager.copy_and_paste("CHARA F", image_path, self.game_path["charaFemale"])

                case CardType.KKS:
                    logger.skipped("CHARA", f"{image_path.name} is a KKS card "
                                            f"(not supported by {self.game_type})")

                case CardType.SCENE:
                    if not self.install_scenes:
                        return
                    if "scene" in self.game_path:
                        self.file_manager.copy_and_paste("SCENE", image_path, self.game_path["scene"])
                    else:
                        logger.skipped("SCENE", f"{image_path.name} — Studio not installed, skipping")

                case CardType.UNKNOWN:
                    if is_coordinate(image_bytes):
                        if not self.install_coords    :
                            return
                        self.file_manager.copy_and_paste("COORD", image_path, self.game_path["coordinate"])
                    else:
                        if not self.install_overlays:
                            return
                        self.file_manager.copy_and_paste("OVERLAYS", image_path, self.game_path["Overlays"])

    def run(self, folder_path: Optional[Path] = None, skip_extract: bool = False):
        if folder_path is None:
            folder_path = self.input_path
        folder_path = Path(folder_path)

        if not str(folder_path).strip() or str(folder_path) == ".":
            logger.error("INSTALL", "InputPath is not set. Configure it in MXU.")
            raise Exception("InputPath is not set")
        if not folder_path.exists():
            logger.error("INSTALL", f"InputPath does not exist: {folder_path}")
            raise Exception(f"InputPath does not exist: {folder_path}")

        foldername = folder_path.name
        logger.line()
        logger.info("FOLDER", foldername)

        file_list, archive_list = self.file_manager.find_all_files(folder_path)

        for file in file_list:
            path, size, extension = file
            match extension:
                case ".zipmod":
                    if self.install_mods:
                        self.file_manager.copy_and_paste("MODS", path, self.game_path["mods"])
                case ".png":
                    self.resolve_png(path)
                case _:
                    basename = Path(path).name
                    logger.error("UNKNOWN", f"Cannot classify {basename}")
        logger.line()

        should_extract = self.extract_archive and not skip_extract
        if should_extract:
            for archive in archive_list:
                extract_path = self.file_manager.extract_archive(archive[0], self.config.install_contents)
                if extract_path is not None:
                    self.run(extract_path)
        elif archive_list:
            names = ", ".join(Path(a[0]).name for a in archive_list)
            logger.info("SKIP", f"Archive extraction skipped: {names}")
