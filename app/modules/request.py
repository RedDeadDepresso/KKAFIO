import os

from app.common.config import cfg
from app.common.logger import logger

from app.modules.handler import Handler
from app.modules.create_backup import CreateBackup
from app.modules.fc_kks import FilterConvertKKS
from app.modules.install_chara import InstallChara
from app.modules.remove_chara import RemoveChara


class Request:
    def __init__(self):
        self._handlers = [CreateBackup(), FilterConvertKKS(), InstallChara(), RemoveChara()]
        self._config = cfg.toDict()
        self._isValid = True

        logger.info("SCRIPT", "Validating config")        
        self.validateGamepath()
        self._handlers = [x for x in self.handlers if self.isTaskEnabled(x)]

        if not self._handlers:
            logger.error("SCRIPT", "No task enabled")
        if not self._isValid:
            raise Exception()

    def validatePath(self, path, errorMsg):
        if not os.path.exists(path):
            logger.error("SCRIPT", errorMsg)
            self._isValid = False
            return False
        return True

    def validateGamepath(self):
        base = self.config['Core']['GamePath']
        self.config['Core']['GamePath'] = {
            "base": base,
            "UserData": os.path.join(base, "UserData"),
            "BepInEx": os.path.join(base, "BepInEx"),
            "mods": os.path.join(base, "mods"),
            "chara": os.path.join(base, "UserData\\chara\\female"),
            "coordinate": os.path.join(base, "UserData\\coordinate"),
            "Overlays": os.path.join(base, "UserData\\Overlays")
            }
        
        for directory, path in self.config['Core']['GamePath'].items():
            self.validatePath(path, f"Game path not valid. Missing {directory} directory.")
            
    def isTaskEnabled(self, handler: Handler):
        task = str(handler).replace(" ", "")
        taskConfig = self.config[task]
    
        if not taskConfig["Enable"]:
            return False

        if (path := taskConfig.get("InputPath")):
            self.validatePath(path, f"Invalid path for {str(handler)}: {path}")          

        if (path := taskConfig.get("OutputPath")):  
            self.validatePath(path, f"Invalid path for {str(handler)}: {path}")

        if self._isValid:
            handler.loadConfig(self.config)
            
        return True

    def removeHandler(self) -> Handler:
        if len(self._handlers) > 1:
            return self._handlers.pop(0)
        
    def process(self):
        if self._handlers:
            self._handlers[0].handle()


