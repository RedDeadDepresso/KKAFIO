import sys
import json
import os
from app.common.logger import logger

class Config:
    def __init__(self, config_file):
        logger.info("SCRIPT", "Initializing config module")
        self.config_file = config_file
        self.ok = False
        self.initialized = False
        self.config_data = None
        self.changed = False
        self.read()

    def read(self):
        backup_config = self._deepcopy_dict(self.__dict__)

        try:
            with open(self.config_file, 'r') as json_file:
                self.config_data = json.load(json_file)
        except FileNotFoundError:
            logger.error("SCRIPT", f"Config file '{self.config_file}' not found.")
            sys.exit(1)
        except json.JSONDecodeError:
            logger.error("SCRIPT", f"Invalid JSON format in '{self.config_file}'.")
            sys.exit(1)

        self.validate()

        if self.ok and not self.initialized:
            logger.info("SCRIPT", "Starting KKAFIO!")
            self.initialized = True
            self.changed = True
        elif not self.ok and not self.initialized:
            logger.error("SCRIPT", "Invalid config. Please check your config file.")
            sys.exit(1)
        elif not self.ok and self.initialized:
            logger.warning("SCRIPT", "Config change detected, but with problems. Rolling back config.")
            self._rollback_config(backup_config)
        elif self.ok and self.initialized:
            if backup_config != self.__dict__:
                logger.warning("SCRIPT", "Config change detected. Hot-reloading.")
                self.changed = True

    def validate(self):
        logger.info("SCRIPT", "Validating config")
        self.ok = True
        self.tasks = ["CreateBackup", "FilterConvertKKS", "InstallChara", "RemoveChara"]
        self.create_gamepath()

        for task in self.tasks:
            if self.config_data[task]["Enable"]:
                if "InputPath" in self.config_data[task]:
                    path = self.config_data[task]["InputPath"]
                elif "OutputPath" in self.config_data[task]:
                    path = self.config_data[task]["OutputPath"]
                if not os.path.exists(path):
                    logger.error("SCRIPT", f"Path invalid for task {task}")
                    raise Exception()
                
        self.install_chara = self.config_data.get("InstallChara", {})
        self.create_backup = self.config_data.get("CreateBackup", {})
        self.remove_chara = self.config_data.get("RemoveChara", {})
        self.fc_kks = self.config_data.get("FilterConvertKKS", {})

    def create_gamepath(self):
        base = self.config_data["Core"]["GamePath"] 
        self.game_path = {
            "base": base,
            "UserData": os.path.join(base, "UserData"),
            "BepInEx": os.path.join(base, "BepInEx"),
            "mods": os.path.join(base, "mods"),
            "chara": os.path.join(base, "UserData\\chara\\female"),
            "coordinate": os.path.join(base, "UserData\\coordinate"),
            "Overlays": os.path.join(base, "UserData\\Overlays")
            }
        
        for path in self.game_path.values():
            if not os.path.exists(path):
                logger.error("SCRIPT", "Game path not valid")
                raise Exception()
            
    def _deepcopy_dict(self, dictionary):
        from copy import deepcopy
        return deepcopy(dictionary)

    def _rollback_config(self, config):
        for key, value in config.items():
            setattr(self, key, value)
