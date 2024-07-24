import os
import codecs

from app.common.file_manager import fileManager
from app.common.logger import logger
from app.modules.handler import Handler


class InstallChara(Handler):
    def __str__(self) -> str:
        return "Install Chara"
    
    def loadConfig(self, config):
        super().loadConfig(config)
        self.inputPath = self.config["InputPath"]

    def resolvePng(self, imagePath):        
        with codecs.open(imagePath[0], "rb") as card:
            data = card.read()
        if data.find(b"KoiKatuChara") != -1:
            if data.find(b"KoiKatuCharaSP") != -1 or data.find(b"KoiKatuCharaSun") != -1:
                basename = os.path.basename(imagePath[0])
                logger.error("CHARA", f"{basename} is a KKS card")
                return
            fileManager.copyAndPaste("CHARA", imagePath, self.gamePath["chara"])
        elif data.find(b"KoiKatuClothes") != -1:
            fileManager.copyAndPaste("COORD",imagePath, self.gamePath["coordinate"])
        else:
            fileManager.copyAndPaste("OVERLAYS", imagePath, self.gamePath["Overlays"])

    def handle(self, request, folderPath=None):
        if folderPath is None:
            folderPath = self.inputPath
        foldername = os.path.basename(folderPath)
        logger.log_msg("FOLDER", foldername)
        fileList, archiveList = fileManager.findAllFiles(folderPath)
        
        for file in fileList:
            extension = file[2]
            match extension:
                case ".zipmod":
                    fileManager.copyAndPaste("MODS", file, self.gamePath["mods"])
                case ".png":
                    self.resolvePng(file)
                case _:
                    basename = os.path.basename(file[0])
                    logger.error("UKNOWN", f"Cannot classify {basename}")
            
        for archive in archiveList:
            extractPath = fileManager.extractArchive(archive[0])
            if extractPath is not None:
                self.handle(extractPath)

        self.setNext(request)


    