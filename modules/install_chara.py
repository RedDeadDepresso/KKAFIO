import os
import codecs
from util.logger import Logger

class InstallChara:
    def __init__(self, config, file_manager):
        """Initializes the Bounty module.

        Args:
            config (Config): BAAuto Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.game_path = self.config.game_path
        self.input_path = self.config.install_chara["InputPath"]

    def resolve_png(self, image_path):        
        with codecs.open(image_path[0], "rb") as card:
            data = card.read()
        if data.find(b"KoiKatuChara") != -1:
            if data.find(b"KoiKatuCharaSP") != -1 or data.find(b"KoiKatuCharaSun") != -1:
                basename = os.path.basename(image_path[0])
                Logger.log_error("CHARA", f"{basename} is a KKS card")
                return
            self.file_manager.copy_and_paste("CHARA", image_path, self.game_path["chara"])
        elif data.find(b"KoiKatuClothes") != -1:
            self.file_manager.copy_and_paste("COORD",image_path, self.game_path["coordinate"])
        else:
            self.file_manager.copy_and_paste("OVERLAYS", image_path, self.game_path["Overlays"])

    def logic_wrapper(self, folder_path=None):
        if folder_path is None:
            folder_path = self.input_path
        foldername = os.path.basename(folder_path)
        Logger.log_msg("FOLDER", foldername)
        file_list, compressed_file_list = self.file_manager.find_all_files(folder_path)
        
        for file in file_list:
            file_extension = file[2]
            match file_extension:
                case ".zipmod":
                    self.file_manager.copy_and_paste("MODS", file, self.game_path["mods"])
                case ".png":
                    self.resolve_png(file)
                case _:
                    basename = os.path.basename(file[0])
                    Logger.log_error("UKNOWN", f"Cannot classify {basename}")
        print("[MSG]")
            
        for compressed in compressed_file_list:
            extract_path = self.file_manager.extract_archive(compressed[0])
            if extract_path is not None:
                self.logic_wrapper(extract_path)


    