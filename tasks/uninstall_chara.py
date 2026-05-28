from pathlib import Path
from util.config import Config, GameType
from util.classifier import CardType, get_card_type, is_male, is_coordinate
from util.file_manager import FileManager
from util.logger import logger


class UninstallChara:
    def __init__(self, config: Config, file_manager: FileManager):
        """Initializes the UninstallChara module.

        Args:
            config (Config): KKAFIO Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.game_path = self.config.game_path
        self.input_path = Path(self.config.uninstall_chara["InputPath"])
        # KoikatsuSunshine removes KKS cards; all other variants remove KK cards
        self.game_type = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        self.is_sunshine = self.game_type == GameType.KOIKATSU_SUNSHINE.value

    def resolve_png(self, image_path: Path):
        image_bytes = image_path.read_bytes()
        card_type = get_card_type(image_bytes)

        if card_type == CardType.SCENE:
            self.file_manager.find_and_remove("SCENE", image_path, self.game_path["scene"])

        elif card_type == CardType.UNKNOWN:
            if is_coordinate(image_bytes):
                self.file_manager.find_and_remove("COORD", image_path, self.game_path["coordinate"])
            else:
                self.file_manager.find_and_remove("OVERLAYS", image_path, self.game_path["Overlays"])

        elif self.is_sunshine:
            # Koikatsu Sunshine — remove KKS cards only
            match card_type:
                case CardType.KKS:
                    if is_male(image_bytes):
                        self.file_manager.find_and_remove("CHARA M", image_path, self.game_path["charaMale"])
                    else:
                        self.file_manager.find_and_remove("CHARA F", image_path, self.game_path["charaFemale"])

                case CardType.KK | CardType.KKSP:
                    logger.skipped("CHARA", f"{image_path.name} is a {card_type.value} card (KK/KKSP not in {GameType.KOIKATSU_SUNSHINE.value} install)")

        else:
            # Koikatsu / Koikatsu Party — remove KK/KKSP cards only
            match card_type:
                case CardType.KK | CardType.KKSP:
                    if is_male(image_bytes):
                        self.file_manager.find_and_remove("CHARA M", image_path, self.game_path["charaMale"])
                    else:
                        self.file_manager.find_and_remove("CHARA F", image_path, self.game_path["charaFemale"])

                case CardType.KKS:
                    logger.skipped("CHARA", f"{image_path.name} is a KKS card (not in {self.game_type} install)")

    def run(self):
        folder_path = self.input_path
        foldername = folder_path.name
        logger.line()
        logger.info("FOLDER", foldername)

        file_list, _ = self.file_manager.find_all_files(folder_path)

        for file in file_list:
            path, size, extension = file
            match extension:
                case ".zipmod":
                    self.file_manager.find_and_remove("MODS", path, self.game_path["mods"])
                case ".png":
                    self.resolve_png(path)
                case _:
                    pass
        logger.line()
