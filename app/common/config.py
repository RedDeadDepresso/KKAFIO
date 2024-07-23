# coding:utf-8
import os 
import sys
from enum import Enum

from PySide6.QtCore import QLocale
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator,
                            OptionsValidator, RangeConfigItem, RangeValidator,
                            FolderListValidator, Theme, FolderValidator, ConfigSerializer, __version__)


class Language(Enum):
    """ Language enumeration """

    CHINESE_SIMPLIFIED = QLocale(QLocale.Chinese, QLocale.China)
    CHINESE_TRADITIONAL = QLocale(QLocale.Chinese, QLocale.HongKong)
    ENGLISH = QLocale(QLocale.English)
    AUTO = QLocale()


class LanguageSerializer(ConfigSerializer):
    """ Language serializer """

    def serialize(self, language):
        return language.value.name() if language != Language.AUTO else "Auto"

    def deserialize(self, value: str):
        return Language(QLocale(value)) if value != "Auto" else Language.AUTO


def isWin11():
    return sys.platform == 'win32' and sys.getwindowsversion().build >= 22000


class Config(QConfig):
    """ Config of application """
    
    # core
    gamePath = ConfigItem("Core", "GamePath", "", FolderValidator())
    
    # createBackup
    backupEnable = ConfigItem(
        "CreateBackup", "Enable", False, BoolValidator()
    )
    backupPath = ConfigItem(
        "CreateBackup", "OutputPath", "", FolderValidator()
    )
    filename = ConfigItem(
        "CreateBackup", "Filename", "",
    )
    userData = ConfigItem(
        "CreateBackup", "UserData", False, BoolValidator()
    )
    mods = ConfigItem(
        "CreateBackup", "mods", False, BoolValidator()
    )
    bepInEx = ConfigItem(
        "CreateBackup", "BepInEx", False, BoolValidator()
    )

    # fckks
    fckksEnable = ConfigItem(
        "FCKKS", "Enable", False, BoolValidator()
    )
    fccksPath = ConfigItem(
        "FCKKS", "InputPath", "", FolderValidator()
    )
    convert = ConfigItem(
        "FCKKS", "Convert", False, BoolValidator()
    )

    # installChara
    installEnable = ConfigItem(
        "InstallChara", "Enable", False, BoolValidator()
    )
    gamePath = ConfigItem(
        "InstallChara", "InputPath", "", FolderValidator())
    fileConflicts = OptionsConfigItem(
        "InstallChara", "FileConflicts", "Skip", OptionsValidator(["Skip", "Replace", "Rename"])
    )
    archivePassword = OptionsConfigItem(
        "InstallChara", "FileConflicts", "Skip", OptionsValidator(["Skip", "Request Password"])
    )
    
    # removeChara
    removeEnable = ConfigItem(
        "RemoveChara", "Enable", False, BoolValidator()
    )
    gamePath = ConfigItem(
        "InstallChara", "InputPath", "", FolderValidator())

    # main window
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", isWin11(), BoolValidator())
    dpiScale = OptionsConfigItem(
        "MainWindow", "DpiScale", "Auto", OptionsValidator([1, 1.25, 1.5, 1.75, 2, "Auto"]), restart=True)
    language = OptionsConfigItem(
        "MainWindow", "Language", Language.AUTO, OptionsValidator(Language), LanguageSerializer(), restart=True)

    # Material
    blurRadius  = RangeConfigItem("Material", "AcrylicBlurRadius", 15, RangeValidator(0, 40))

    # software update
    checkUpdateAtStartUp = ConfigItem("Update", "CheckUpdateAtStartUp", True, BoolValidator())

    # def validate(self):
    #     Logger.log_info("SCRIPT", "Validating config")
    #     self.ok = True
    #     self.tasks = self.config_data["Core"]["Tasks"]
    #     self.create_gamepath()

    #     for task in self.tasks:
    #         if self.config_data[task]["Enable"]:
    #             if "InputPath" in self.config_data[task]:
    #                 path = self.config_data[task]["InputPath"]
    #             elif "OutputPath" in self.config_data[task]:
    #                 path = self.config_data[task]["OutputPath"]
    #             if not os.path.exists(path):
    #                 Logger.log_error("SCRIPT", f"Path invalid for task {task}")
    #                 raise Exception()
                
    #     self.install_chara = self.config_data.get("InstallChara", {})
    #     self.create_backup = self.config_data.get("CreateBackup", {})
    #     self.remove_chara = self.config_data.get("RemoveChara", {})
    #     self.fc_kks = self.config_data.get("FCKKS", {})

    # def create_gamepath(self):
    #     base = self.config_data["Core"]["GamePath"] 
    #     self.game_path = {
    #         "base": base,
    #         "UserData": os.path.join(base, "UserData"),
    #         "BepInEx": os.path.join(base, "BepInEx"),
    #         "mods": os.path.join(base, "mods"),
    #         "chara": os.path.join(base, "UserData\\chara\\female"),
    #         "coordinate": os.path.join(base, "UserData\coordinate"),
    #         "Overlays": os.path.join(base, "UserData\Overlays")
    #         }
        
    #     for path in self.game_path.values():
    #         if not os.path.exists(path):
    #             Logger.log_error("SCRIPT", "Game path not valid")
    #             raise Exception()


YEAR = 2023
AUTHOR = "zhiyiYo"
VERSION = __version__
HELP_URL = "https://qfluentwidgets.com"
REPO_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets"
EXAMPLE_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets/tree/PySide6/examples"
FEEDBACK_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets/issues"
RELEASE_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets/releases/latest"
ZH_SUPPORT_URL = "https://qfluentwidgets.com/zh/price/"
EN_SUPPORT_URL = "https://qfluentwidgets.com/price/"


cfg = Config()
cfg.themeMode.value = Theme.AUTO
qconfig.load('app/config/config.json', cfg)