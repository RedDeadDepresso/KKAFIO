# coding:utf-8
import os 
import sys
from enum import Enum
from importlib.metadata import version, PackageNotFoundError

from PySide6.QtCore import QLocale
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator,
                            OptionsValidator, RangeConfigItem, RangeValidator, Theme, FolderValidator, ConfigSerializer)


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
    gamePath = ConfigItem("Core", "GamePath", "C:/Program Files (x86)/Steam/steamapps/common/Koikatsu Party", FolderValidator())
    documentsPath = os.path.join(os.path.expanduser('~'), 'Documents')
    downloadsPath = os.path.join(os.path.expanduser('~'), 'Downloads')

    # createBackup
    backupEnable = ConfigItem(
        "CreateBackup", "Enable", False, BoolValidator()
    )
    backupPath = ConfigItem(
        "CreateBackup", "OutputPath", documentsPath, FolderValidator()
    )
    filename = ConfigItem(
        "CreateBackup", "Filename", "koikatsu_backup",
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
        "FilterConvertKKS", "Enable", False, BoolValidator()
    )
    fccksPath = ConfigItem(
        "FilterConvertKKS", "InputPath", downloadsPath, FolderValidator()
    )
    convert = ConfigItem(
        "FilterConvertKKS", "Convert", False, BoolValidator()
    )

    # installChara
    installEnable = ConfigItem(
        "InstallChara", "Enable", False, BoolValidator()
    )
    installPath = ConfigItem(
        "InstallChara", "InputPath", downloadsPath, FolderValidator())
    fileConflicts = OptionsConfigItem(
        "InstallChara", "FileConflicts", "Skip", OptionsValidator(["Skip", "Replace", "Rename"])
    )
    archivePassword = OptionsConfigItem(
        "InstallChara", "Password", "Skip", OptionsValidator(["Skip", "Request Password"])
    )
    
    # removeChara
    removeEnable = ConfigItem(
        "RemoveChara", "Enable", False, BoolValidator()
    )
    removePath = ConfigItem(
        "RemoveChara", "InputPath", downloadsPath, FolderValidator())

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


YEAR = 2023
AUTHOR = "RedDeadDepresso"

try:
    VERSION = version("kkafio")
except PackageNotFoundError:
    # Fallback for running directly from source without the package installed
    VERSION = "0.0.0"

HELP_URL = "https://github.com/RedDeadDepresso/KKAFIO/issues"
REPO_URL = "https://github.com/RedDeadDepresso/KKAFIO"
FEEDBACK_URL = "https://github.com/RedDeadDepresso/KKAFIO/issues"
RELEASE_URL = "https://github.com/RedDeadDepresso/KKAFIO/releases/latest"
ZH_SUPPORT_URL = "https://github.com/RedDeadDepresso/KKAFIO/issues"
EN_SUPPORT_URL = "https://github.com/RedDeadDepresso/KKAFIO/issues"


cfg = Config()
cfg.themeMode.value = Theme.AUTO
qconfig.load('app/config/config.json', cfg)