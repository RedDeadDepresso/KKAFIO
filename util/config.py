import sys
import json
from pathlib import Path
from util.logger import logger


class Config:
    def __init__(self, config_file):
        logger.info("SCRIPT", "Initializing config module")
        self.config_file = config_file
        self.ok = False
        self.initialized = False
        self.config_data = None
        self.read()

    def read(self):
        try:
            with open(self.config_file, 'r') as f:
                self.config_data = json.load(f)
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
        elif not self.ok and not self.initialized:
            logger.error("SCRIPT", "Invalid config. Please check your config file.")
            sys.exit(1)

    def validate(self):
        logger.info("SCRIPT", "Validating config")
        self.ok = True
        self.validate_gamepath()
        self.validate_tasks()

    def validate_gamepath(self):
        base = Path(self.config_data["Core"]["GamePath"])
        self.game_path = {
            "base": base,
            "UserData": base / "UserData",
            "BepInEx": base / "BepInEx",
            "mods": base / "mods",
            "charaMale": base / "UserData" / "chara" / "male",
            "charaFemale": base / "UserData" / "chara" / "female",
            "coordinate": base / "UserData" / "coordinate",
            "Overlays": base / "UserData" / "Overlays"
        }
        
        for path in self.game_path.values():
            if not path.exists():
                logger.error("SCRIPT", "Game path not valid")
                raise Exception(f"Game path not valid: {path}")

    def validate_tasks(self):
        tasks = ["CreateBackup", "FilterConvertKKS", "InstallChara", "RemoveChara"]

        for task in tasks:
            task_config = self.config_data[task]
            if not task_config["Enable"]:
                continue

            if "InputPath" in task_config:
                path_obj = Path(task_config["InputPath"])
                task_config["InputPath"] = path_obj
                
            elif "OutputPath" in task_config:
                path_obj = Path(task_config["OutputPath"])
                task_config["OutputPath"] = path_obj

            if not path_obj.exists():
                logger.error("SCRIPT", f"Path invalid for task {task}")
                raise Exception()

        self.create_backup = self.config_data["CreateBackup"]
        self.fc_kks = self.config_data["FilterConvertKKS"]
        self.install_chara = self.config_data["InstallChara"]
        self.remove_chara = self.config_data["RemoveChara"]
