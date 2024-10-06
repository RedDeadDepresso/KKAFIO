from pathlib import Path
from util.classifier import CardType, get_card_type, is_male, is_coordinate
from util.logger import logger
from typing import Optional


class InstallChara:
    def __init__(self, config, file_manager):
        """Initializes the InstallChara module.

        Args:
            config (Config): KKAFIO Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.game_path = self.config.game_path
        self.input_path = Path(self.config.install_chara["InputPath"])

    def resolve_png(self, image_path: Path):        
        image_bytes = image_path.read_bytes()
        card_type = get_card_type(image_bytes)

        match card_type:
            case CardType.KK:
                if is_male(image_bytes):
                    self.file_manager.copy_and_paste("CHARA M", image_path, self.game_path["charaMale"])
                else:
                    self.file_manager.copy_and_paste("CHARA F", image_path, self.game_path["charaFemale"])

            case CardType.KKS | CardType.KKSP:
                logger.error("CHARA", f"{image_path.name} is a {card_type.value} card")

            case CardType.UNKNOWN:
                if is_coordinate(image_bytes):
                    self.file_manager.copy_and_paste("COORD", image_path, self.game_path["coordinate"])
                else:
                    self.file_manager.copy_and_paste("OVERLAYS", image_path, self.game_path["Overlays"])

    def run(self, folder_path: Optional[Path] = None):
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
            
        for archive in archive_list:
            extract_path = self.file_manager.extract_archive(archive[0])
            if extract_path is not None:
                self.run(extract_path)
