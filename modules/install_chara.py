from pathlib import Path
import codecs
from util.logger import logger


class InstallChara:
    def __init__(self, config, file_manager):
        """Initializes the InstallChara module.

        Args:
            config (Config): KKAFIO Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.game_path = self.config.game_path
        self.input_path = Path(self.config.install_chara["InputPath"])  # Using Path for input path

    def resolve_png(self, image_path):        
        with codecs.open(image_path[0], "rb") as card:
            data = card.read()
        if b"KoiKatuChara" in data:
            if b"KoiKatuCharaSP" in data or b"KoiKatuCharaSun" in data:
                basename = Path(image_path[0]).name  # Use Path's .name to get the basename
                logger.error("CHARA", f"{basename} is a KKS card")
                return
            self.file_manager.copy_and_paste("CHARA", image_path, self.game_path["chara"])
        elif b"KoiKatuClothes" in data:
            self.file_manager.copy_and_paste("COORD", image_path, self.game_path["coordinate"])
        else:
            self.file_manager.copy_and_paste("OVERLAYS", image_path, self.game_path["Overlays"])

    def run(self, folder_path=None):
        if folder_path is None:
            folder_path = self.input_path
        folder_path = Path(folder_path)
        foldername = folder_path.name
        logger.line()
        logger.info("FOLDER", foldername)
        
        file_list, archive_list = self.file_manager.find_all_files(folder_path)
        
        for file in file_list:
            file_extension = file[2]
            match file_extension:
                case ".zipmod":
                    self.file_manager.copy_and_paste("MODS", file, self.game_path["mods"])
                case ".png":
                    self.resolve_png(file)
                case _:
                    basename = Path(file[0]).name
                    logger.error("UNKNOWN", f"Cannot classify {basename}")
        logger.line()
            
        for archive in archive_list:
            extract_path = self.file_manager.extract_archive(archive[0])
            if extract_path is not None:
                self.run(extract_path)
