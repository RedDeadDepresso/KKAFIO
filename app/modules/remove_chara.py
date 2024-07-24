import os
import codecs

from app.common.file_manager import fileManager
from app.common.logger import logger
from app.modules.handler import Handler


class RemoveChara(Handler):
    def __str__(self) -> str:
        return "Remove Chara"
    
    def loadConfig(self, config):
        super().configLoad(config)
        self.inputPath = self.config["InputPath"]

    def resolvePng(self, imagePath):        
        with codecs.open(imagePath[0], "rb") as card:
            data = card.read()
        if data.find(b"KoiKatuChara") != -1:
            if data.find(b"KoiKatuCharaSP") != -1 or data.find(b"KoiKatuCharaSun") != -1:
                return
            fileManager.findAndRemove("CHARA", imagePath, self.gamePath["chara"])
        elif data.find(b"KoiKatuClothes") != -1:
            fileManager.findAndRemove("COORD",imagePath, self.gamePath["coordinate"])
        else:
            fileManager.findAndRemove("OVERLAYS", imagePath, self.gamePath["Overlays"])

    def handle(self, request):
        foldername = os.path.basename(self.inputPath)
        logger.info("FOLDER", foldername)
        fileList, archiveList = fileManager.findAllFiles(self.inputPath)
        
        for file in fileList:
            extension = file[2]
            match extension:
                case ".zipmod":
                    fileManager.findAndRemove("MODS", file, self.game_path["mods"])
                case ".png":
                    self.resolvePng(file)
                case _:
                    pass
                
        logger.line()
        self.setNext(request)


    