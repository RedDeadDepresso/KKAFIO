from pathlib import Path
from utils.config import Config, GameType
from utils.classifier import CardType, get_card_type, is_male, is_coordinate
from utils.file_manager import FileManager
from utils.logger import logger


class UninstallContents:
    def __init__(self, config: Config, file_manager: FileManager):
        self.config       = config
        self.file_manager = file_manager
        self.game_path    = self.config.game_path
        self.input_path   = Path(self.config.uninstall_contents["InputPath"])
        self.game_type    = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        self.is_sunshine  = self.game_type == GameType.KOIKATSU_SUNSHINE.value

        cfg = self.config.uninstall_contents
        self.uninstall_chara     : bool = cfg.get("Chara",    True)
        self.uninstall_mods       : bool = cfg.get("Mods",     True)
        self.uninstall_coords    : bool = cfg.get("Coords",   True)
        self.uninstall_scenes     : bool = cfg.get("Scenes",   True)
        self.uninstall_overlays   : bool = cfg.get("Overlays", True)

    def resolve_png(self, image_path: Path):
        image_bytes = image_path.read_bytes()
        card_type   = get_card_type(image_bytes)

        if self.is_sunshine:
            match card_type:
                case CardType.KKS:
                    if not self.uninstall_chara:
                        return
                    if is_male(image_bytes):
                        self.file_manager.find_and_remove("CHARA M", image_path, self.game_path["charaMale"])
                    else:
                        self.file_manager.find_and_remove("CHARA F", image_path, self.game_path["charaFemale"])

                case CardType.KK | CardType.KKSP:
                    logger.skipped("CHARA", f"{image_path.name} is a {card_type.value} card "
                                            f"(KK/KKSP not in {GameType.KOIKATSU_SUNSHINE.value} install)")

                case CardType.SCENE:
                    if not self.uninstall_scenes:
                        return
                    if "scene" in self.game_path:
                        self.file_manager.find_and_remove("SCENE", image_path, self.game_path["scene"])
                    else:
                        logger.skipped("SCENE", f"{image_path.name} — Studio not installed, skipping")

                case CardType.UNKNOWN:
                    if is_coordinate(image_bytes):
                        if not self.uninstall_coords    :
                            return
                        self.file_manager.find_and_remove("COORD", image_path, self.game_path["coordinate"])
                    else:
                        if not self.uninstall_overlays:
                            return
                        self.file_manager.find_and_remove("OVERLAYS", image_path, self.game_path["Overlays"])
        else:
            match card_type:
                case CardType.KK | CardType.KKSP:
                    if not self.uninstall_chara:
                        return
                    if is_male(image_bytes):
                        self.file_manager.find_and_remove("CHARA M", image_path, self.game_path["charaMale"])
                    else:
                        self.file_manager.find_and_remove("CHARA F", image_path, self.game_path["charaFemale"])

                case CardType.KKS:
                    logger.skipped("CHARA", f"{image_path.name} is a KKS card "
                                            f"(not in {self.game_type} install)")

                case CardType.SCENE:
                    if not self.uninstall_scenes:
                        return
                    if "scene" in self.game_path:
                        self.file_manager.find_and_remove("SCENE", image_path, self.game_path["scene"])
                    else:
                        logger.skipped("SCENE", f"{image_path.name} — Studio not installed, skipping")

                case CardType.UNKNOWN:
                    if is_coordinate(image_bytes):
                        if not self.uninstall_coords    :
                            return
                        self.file_manager.find_and_remove("COORD", image_path, self.game_path["coordinate"])
                    else:
                        if not self.uninstall_overlays:
                            return
                        self.file_manager.find_and_remove("OVERLAYS", image_path, self.game_path["Overlays"])

    def run(self):
        folder_path = self.input_path
        if not str(folder_path).strip() or str(folder_path) == ".":
            logger.error("UNINST", "InputPath is not set. Configure it in MXU.")
            raise Exception("InputPath is not set")
        if not folder_path.exists():
            logger.error("UNINST", f"InputPath does not exist: {folder_path}")
            raise Exception(f"InputPath does not exist: {folder_path}")

        foldername = folder_path.name
        logger.line()
        logger.info("FOLDER", foldername)

        file_list, _ = self.file_manager.find_all_files(folder_path)

        for file in file_list:
            path, size, extension = file
            match extension:
                case ".zipmod":
                    if self.uninstall_mods:
                        self.file_manager.find_and_remove("MODS", path, self.game_path["mods"])
                case ".png":
                    self.resolve_png(path)
                case _:
                    pass
        logger.line()
