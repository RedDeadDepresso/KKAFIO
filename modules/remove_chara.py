from pathlib import Path
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
        with image_path.open("rb") as card:
            data = card.read()
        if b"KoiKatuChara" in data:
            if b"KoiKatuCharaSP" in data or b"KoiKatuCharaSun" in data:
                return
            self.file_manager.find_and_remove("CHARA", image_path, self.game_path["chara"])
        elif b"KoiKatuClothes" in data:
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
