# coding:utf-8
import os 
import sys
from enum import Enum
from importlib.metadata import version, PackageNotFoundError

from PySide6.QtCore import QLocale
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator,
                            OptionsValidator, RangeConfigItem, RangeValidator, Theme, FolderValidator, ConfigSerializer)

from util.constants import CONFIG_PATH


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
    fckksExtractArchive = ConfigItem(
        "FilterConvertKKS", "ExtractArchive", True, BoolValidator()
    )
    fckksArchivePassword = OptionsConfigItem(
        "FilterConvertKKS", "Password", "Skip", OptionsValidator(["Skip", "Request Password"])
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
    installExtractArchive = ConfigItem(
        "InstallChara", "ExtractArchive", True, BoolValidator()
    )
    
    # filterDuplicates
    filterDuplicatesEnable = ConfigItem(
        "FilterDuplicates", "Enable", False, BoolValidator()
    )
    filterDuplicatesPath = ConfigItem(
        "FilterDuplicates", "InputPath", downloadsPath, FolderValidator()
    )
    filterDuplicatesFuzzy = ConfigItem(
        "FilterDuplicates", "FuzzyChara", False, BoolValidator()
    )
    filterDuplicatesKeep = OptionsConfigItem(
        "FilterDuplicates", "Keep", "Biggest file size",
        OptionsValidator(["None — move all copies", "Newest", "Oldest",
                          "Biggest file size", "Smallest file size",
                          "Last alphabetically", "First alphabetically"])
    )
    filterDuplicatesDelete = ConfigItem(
        "FilterDuplicates", "Delete", False, BoolValidator()
    )

    # deleteChara
    deleteCharaEnable = ConfigItem(
        "DeleteChara", "Enable", False, BoolValidator()
    )
    deleteCharaPaths = ConfigItem(
        "DeleteChara", "CharaPaths", []
    )
    deleteCharaAutoResolve = ConfigItem(
        "DeleteChara", "AutoResolve", True, BoolValidator()
    )
    deleteCharaModsDir = ConfigItem(
        "DeleteChara", "ModsDir", "", FolderValidator()
    )
    deleteCharaCoordDir = ConfigItem(
        "DeleteChara", "CoordDir", "", FolderValidator()
    )

    # archiveChara
    archiveCharaEnable = ConfigItem(
        "ArchiveChara", "Enable", False, BoolValidator()
    )
    archiveCharaPaths = ConfigItem(
        "ArchiveChara", "CharaPaths", []
    )
    archiveCharaFormat = OptionsConfigItem(
        "ArchiveChara", "Format", "7z", OptionsValidator(["7z", "zip"])
    )
    archiveCharaAutoResolve = ConfigItem(
        "ArchiveChara", "AutoResolve", True, BoolValidator()
    )
    archiveCharaIncludeModpack = ConfigItem(
        "ArchiveChara", "IncludeModpack", False, BoolValidator()
    )
    archiveCharaCombined = ConfigItem(
        "ArchiveChara", "CombinedArchive", True, BoolValidator()
    )
    archiveCharaModsDir = ConfigItem(
        "ArchiveChara", "ModsDir", "", FolderValidator()
    )
    archiveCharaCoordDir = ConfigItem(
        "ArchiveChara", "CoordDir", "", FolderValidator()
    )
    archiveCharaOutputDir = ConfigItem(
        "ArchiveChara", "OutputDir", downloadsPath, FolderValidator()
    )

    # groupChara
    groupCharaEnable = ConfigItem(
        "GroupChara", "Enable", False, BoolValidator()
    )
    groupCharaPath = ConfigItem(
        "GroupChara", "InputPath", downloadsPath, FolderValidator()
    )
    groupCharaPrompt = ConfigItem(
        "GroupChara", "Prompt",
        "You will receive a JSON object whose keys identify Koikatsu character card files. "
        "Each key has the format:  name | personality | hair_rgb\n"
        "Your task: for every key, write the name of the anime/game series the character is from as the value.\n"
        "Rules:\n"
        "- Values must be valid Windows folder names (no \\ / : * ? \" < > | characters).\n"
        "- Use the official English title of the series.\n"
        "- If a character appears in multiple series, use the one they are most associated with.\n"
        "- If you are not sure or the character is an original creation, leave the value as an empty string \"\".\n"
        "- Return ONLY the completed JSON object — no explanation, no markdown code fences, no extra text before or after.\n"
        "\nJSON to fill in:\n"
    )
    groupCharaResponse = ConfigItem(
        "GroupChara", "Response", ""
    )
    groupCharaIncludeSubfolders = ConfigItem(
        "GroupChara", "IncludeSubfolders", False, BoolValidator()
    )

    # ungroupChara
    ungroupCharaEnable = ConfigItem(
        "UngroupChara", "Enable", False, BoolValidator()
    )
    ungroupCharaPath = ConfigItem(
        "UngroupChara", "InputPath", downloadsPath, FolderValidator()
    )
    ungroupCharaDeleteEmpty = ConfigItem(
        "UngroupChara", "DeleteEmptyFolders", True, BoolValidator()
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
qconfig.load(str(CONFIG_PATH), cfg)