import os
import codecs
from util.logger import Logger

class RemoveChara:
    def __init__(self, config, file_manager):
        """Initializes the Bounty module.

        Args:
            config (Config): BAAuto Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.game_path = self.config.game_path
        self.input_path = self.config.remove_chara["InputPath"]

    def resolve_png(self, image_path):        
        with codecs.open(image_path[0], "rb") as card:
            data = card.read()
        if data.find(b"KoiKatuChara") != -1:
            if data.find(b"KoiKatuCharaSP") != -1 or data.find(b"KoiKatuCharaSun") != -1:
                return
            self.file_manager.find_and_remove("CHARA", image_path, self.game_path["chara"])
        elif data.find(b"KoiKatuClothes") != -1:
            self.file_manager.find_and_remove("COORD",image_path, self.game_path["coordinate"])
        else:
            self.file_manager.find_and_remove("OVERLAYS", image_path, self.game_path["Overlays"])

    def logic_wrapper(self):
        foldername = os.path.basename(self.input_path)
        Logger.log_msg("FOLDER", foldername)
        file_list, archive_list = self.file_manager.find_all_files(self.input_path)
        
        for file in file_list:
            file_extension = file[2]
            match file_extension:
                case ".zipmod":
                    self.file_manager.find_and_remove("MODS", file, self.game_path["mods"])
                case ".png":
                    self.resolve_png(file)
                case _:
                    pass
        print("[MSG]")



    