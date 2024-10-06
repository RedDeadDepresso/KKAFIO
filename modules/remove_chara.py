from pathlib import Path
from util.classifier import CardType, get_card_type, is_male, is_coordinate
from util.logger import logger


class RemoveChara:
    def __init__(self, config, file_manager):
        """Initializes the RemoveChara module.

        Args:
            config (Config): KKAFIO Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.game_path = self.config.game_path
        self.input_path = self.config.remove_chara["InputPath"]

    def resolve_png(self, image_path: Path):        
        image_bytes = image_path.read_bytes()
        card_type = get_card_type(image_bytes)

        match card_type:
            case CardType.KK:
                if is_male(image_bytes):
                    self.file_manager.find_and_remove("CHARA M", image_path, self.game_path["charaMale"])
                else:
                    self.file_manager.find_and_remove("CHARA F", image_path, self.game_path["charaFemale"])

            case CardType.UNKNOWN:
                if is_coordinate(image_bytes):
                    self.file_manager.find_and_remove("COORD", image_path, self.game_path["coordinate"])
                else:
                    self.file_manager.find_and_remove("OVERLAYS", image_path, self.game_path["Overlays"])

    def run(self):
        foldername = self.input_path.name
        logger.info("FOLDER", foldername)
        
        file_list, archive_list = self.file_manager.find_all_files(self.input_path)
        
        for path, size, extension in file_list:
            match extension:
                case ".zipmod":
                    self.file_manager.find_and_remove("MODS", path, self.game_path["mods"])
                case ".png":
                    self.resolve_png(path)
                case _:
                    pass
        logger.line()
