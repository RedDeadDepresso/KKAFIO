import os

from app.common.file_manager import fileManager
from app.modules.handler import Handler


class CreateBackup(Handler):
    def __str__(self) -> str:
        return "Create Backup"
    
    def loadConfig(self, config):
        super().loadConfig(config)
        folders = ["mods", "UserData", "BepInEx"]
        self.folders = [self.gamePath[f] for f in folders if self.config[f]]
        self.outputPath = self.config["OutputPath"]
        self.filename = self.config["Filename"]
    
    def handle(self, request):
        outputPath = os.path.join(self.outputPath, self.filename)
        fileManager.createArchive(self.folders, outputPath)

        self.setNext(request)




    