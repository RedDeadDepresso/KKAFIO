"""
Config adapter: reads the MXU JSON config and exposes a flat dict that
looks like the old KKAFIO config so all existing task modules continue
to work unchanged.

MXU JSON structure:
  {
    "instances": [
      {
        "id": "...",
        "name": "My Config",
        "globalOptionValues": {"GamePath": {"type": "folder", "path": "..."}},
        "tasks": [
          {
            "taskName": "InstallContents",
            "enabled": true,
            "optionValues": {
              "InputPath":      {"type": "folder",   "path": "D:/cards"},
              "ExtractArchive": {"type": "switch",   "value": true},
              ...
            }
          },
          ...
        ]
      }
    ]
  }

NOTE: _TASK_KEY, _TASK_DEFAULTS, _build_task_config() and
_DEFAULT_TASK_PATHS below are generated from interface.json by
tools/generate_config.py — re-run that script instead of hand-editing them
after changing a task or option. Everything else in this file is
hand-written and untouched by the generator.
"""

import sys
import json
from pathlib import Path
from typing import Any
from kkafio.core.errors import ConfigError
from kkafio.core.logger import logger
from enum import Enum


class GameType(Enum):
    KOIKATSU = "Koikatsu"
    KOIKATSU_PARTY = "KoikatsuParty"
    KOIKATSU_SUNSHINE = "KoikatsuSunshine"


# ---------------------------------------------------------------------------
# Option-value extractors
# ---------------------------------------------------------------------------

def _extract_opt(opt_values: dict, key: str):
    """Return the raw Python value for an optionValue key, or None."""
    v = opt_values.get(key)
    if v is None:
        return None
    t = v.get("type")
    if t == "folder":
        return v.get("path", "")
    if t == "file_list":
        return v.get("paths", [])
    if t == "textarea":
        return v.get("text", "")
    if t == "switch":
        return v.get("value", False)
    if t == "checkbox":
        return v.get("caseNames", [])
    if t == "select":
        return v.get("caseName", "")
    if t == "input":
        vals = v.get("values", {})
        return next(iter(vals.values()), "") if vals else ""
    return None


# ---------------------------------------------------------------------------
# Special-task param extractor
# ---------------------------------------------------------------------------

def _extract_special_task_params(opt_values: dict) -> dict:
    """Flatten a special task's optionValues into a custom_action_param dict."""
    # (kind, param_name, default-if-the-switch-value-is-missing). The default
    # here MUST match the fallback special_tasks.py itself uses when the
    # whole param key is missing from `param` (e.g. an older saved pipeline
    # that predates this option existing) — otherwise "GUI sent the option
    # but with no explicit value" and "GUI never sent the option at all"
    # silently resolve to opposite behaviors for what is, from the user's
    # perspective, the exact same "I never touched this setting" state.
    # kill_self in particular defaults to True in special_tasks.py, since a
    # Kill Self task's entire purpose is to stop the running pipeline —
    # if it's in the pipeline at all, that's almost certainly the intent.
    KEY_MAP: dict[str, Any] = {
        "__MXU_SLEEP_OPTION__":         ("input",  None, None),
        "__MXU_WAITUNTIL_OPTION__":     ("input",  None, None),
        "__MXU_NOTIFY_OPTION__":        ("input",  None, None),
        "__MXU_WEBHOOK_OPTION__":       ("input",  None, None),
        "__MXU_LAUNCH_OPTION__":        ("input",  None, None),
        "__MXU_LAUNCH_WAIT_OPTION__":   ("switch", "wait_for_exit",   False),
        "__MXU_LAUNCH_SKIP_OPTION__":   ("switch", "skip_if_running", False),
        "__MXU_LAUNCH_CMD_OPTION__":    ("switch", "use_cmd",         False),
        "__MXU_KILLPROC_SELF_OPTION__": ("switch", "kill_self",       True),
        "__MXU_KILLPROC_NAME_OPTION__": ("input",  None, None),
        "__MXU_POWER_OPTION__":         ("select", "power_action",    ""),
    }

    params: dict = {}
    for opt_key, v in opt_values.items():
        if not isinstance(v, dict):
            continue
        mapping = KEY_MAP.get(opt_key)
        if mapping is None:
            # Generic fallback for unknown option keys
            t = v.get("type")
            if t == "input":
                params.update(v.get("values", {}))
            continue
        kind, param_name, default = mapping
        if kind == "input":
            params.update(v.get("values", {}))
        elif kind == "switch":
            params[param_name] = v.get("value", default)
        elif kind == "select":
            params[param_name] = v.get("caseName", default)

    return params


# ---------------------------------------------------------------------------
# Task-name -> config key mapping
# ---------------------------------------------------------------------------

_TASK_KEY = {
    "CreateBackup": "CreateBackup",
    "DownloadContents": "DownloadContents",
    "FilterConvertKKS": "FilterConvertKKS",
    "FilterDuplicateContents": "FilterDuplicateContents",
    "DownloadMissingMods": "DownloadMissingMods",
    "CompressCardsTextures": "CompressCardsTextures",
    "InstallContents": "InstallContents",
    "UninstallContents": "UninstallContents",
    "GroupScenes": "GroupScenes",
    "GroupChara": "GroupChara",
    "GroupCoordinates": "GroupCoordinates",
    "UngroupCards": "UngroupCards",
    "RenameChara": "RenameChara",
    "ArchiveCards": "ArchiveCards",
    "DeleteCards": "DeleteCards",
    "ReviewSimilarChara": "ReviewSimilarChara",
    "ExportMods": "ExportMods",
    "DeleteUnusedMods": "DeleteUnusedMods",
}

_TASK_DEFAULTS = {
    "CreateBackup": {"Enable": False, "OutputPath": "C:/KKAFIO/Backups", "Filename": "koikatsu_backup", "mods": False, "UserData": False, "BepInEx": False},
    "DownloadContents": {"Enable": False, "OutputDir": "C:/KKAFIO/Downloads", "Links": "", "SkipDownloaded": True},
    "FilterConvertKKS": {"Enable": False, "InputPath": "C:/KKAFIO/Downloads", "Convert": False, "KKAction": "Keep", "KKSAction": "Keep", "ExtractArchive": True, "Password": "Skip"},
    "FilterDuplicateContents": {"Enable": False, "Chara": True, "Mods": True, "Coords": True, "Scenes": True, "Overlays": True, "UseCache": True, "Keep": "Oldest", "DuplicateAction": "Move & Rename", "CharaDir": "", "SceneDir": "", "CoordDir": "", "ModsDir": "", "OverlaysDir": ""},
    "DownloadMissingMods": {"Enable": False, "ContentTypes": ["Chara", "Scene", "Coord"], "SideloaderModpack": "Skip", "TelegramSource": "No", "TelegramChatLinks": "# private - need to join it\n# invite link: https://t.me/+td__jctzxic0NGVi\nhttps://t.me/c/2549022984\n\n# public - no need to join them\nhttps://t.me/KK_archive_modlibrary\nhttps://t.me/KKDOC\nhttps://t.me/koikatu_card_download\nhttps://t.me/kknowcc", "UseCache": True, "OpenReport": True, "InputModsDir": "", "OutputModsDir": "", "CharaDir": "", "SceneDir": "", "CoordDir": ""},
    "CompressCardsTextures": {"Enable": False, "InputPath": "C:/KKAFIO/Downloads", "KoiCardTexToolPath": "C:/KoiCardTexTool", "DeleteOriginalCards": False},
    "InstallContents": {"Enable": False, "InputPath": "C:/KKAFIO/Downloads", "Chara": True, "Mods": True, "Coords": True, "Scenes": True, "Overlays": True, "FileConflicts": "Skip", "ExtractArchive": True, "Password": "Skip"},
    "UninstallContents": {"Enable": False, "InputPath": "C:/KKAFIO/Downloads", "Chara": True, "Mods": True, "Coords": True, "Scenes": True, "Overlays": True},
    "GroupScenes": {"Enable": False, "SceneDir": "", "UseCache": True, "IncludeSubfolders": False},
    "GroupChara": {"Enable": False, "CharaDir": "", "IncludeSubfolders": False, "UseCache": True, "Prompt": "You will receive a JSON object whose keys identify Koikatsu character card files.\nEach key has the format:  name | personality | hair_color\n\nYour task: for every key, write the English name of the anime/game series the character is from as the value.\n\nRules:\n- Values must be valid Windows folder names (no  \\ / : * ? \" < > |  characters).\n- Use the official title of the series.\n- If a character appears in multiple series, use the one they are most associated with.\n- Use the personality and hair colour as additional hints to identify the character.\n- If you are not sure or the character is an original creation, leave the value as an empty string \"\".\n- Return ONLY the completed JSON object — no explanation, no markdown code fences, no extra text before or after.\n\nJSON to fill in:\n"},
    "GroupCoordinates": {"Enable": False, "UseCache": True, "IncludeSubfolders": False, "AccessoryTolerance": "0", "CharaDir": "", "CoordDir": ""},
    "UngroupCards": {"Enable": False, "InputPath": "", "Chara": True, "Scenes": True, "Coords": True, "DeleteEmptyFolders": True},
    "RenameChara": {"Enable": False, "CharaDir": "", "SkipAlreadyRenamed": True, "UpdateMetadata": False, "RenameFiles": True, "UseCache": True, "Prompt": "You will receive a JSON object whose keys identify Koikatsu character card files.\nEach key has the format:  name | personality | hair_color\n\nYour task: for every key fill in \"lastname\", \"firstname\", and \"nickname\" with the character's well-known English name.\n\nRules:\n- firstname = given name, lastname = family name, regardless of the order the name is written in the source material.\n- Use the name the character is commonly known by, not a literal transliteration.\n- \"nickname\" can be a common short form or the same as firstname.\n- Use the personality and hair colour as additional hints to identify the character.\n- All values must be valid Windows filenames\n  (no  \\ / : * ? \" < > |  characters, no leading/trailing spaces or dots).\n- If you do not recognise the character or are not confident, leave all three\n  fields as empty strings \"\".\n- Return ONLY the completed JSON object — no explanation, no markdown fences,\n  no extra text before or after.\n\nJSON to fill in:\n"},
    "ArchiveCards": {"Enable": False, "OutputPath": "C:/KKAFIO/Archived Cards", "ContentPaths": [], "CombinedArchive": True, "Format": "7z", "IncludeModpack": False, "IncludeCoordinates": True, "UseCache": True, "AutoResolve": True, "ModsDir": "", "CoordDir": ""},
    "DeleteCards": {"Enable": False, "ContentPaths": [], "CheckSharedMods": True, "IncludeCoordinates": True, "UseCache": True, "AutoResolve": True, "ModsDir": "", "CharaDir": "", "SceneDir": "", "CoordDir": ""},
    "ReviewSimilarChara": {"Enable": False, "Mode": "Similar cover", "CheckSharedMods": True, "IncludeCoordinates": True, "UseCache": True, "AutoResolve": True, "ModsDir": "", "CharaDir": "", "SceneDir": "", "CoordDir": ""},
    "ExportMods": {"Enable": False, "OutputPath": "C:/KKAFIO/Exported Mods", "Guids": "", "RenameToGuid": True, "UseCache": True, "ModsDir": ""},
    "DeleteUnusedMods": {"Enable": False, "ExceptionList": "BetterPenetration\\\nClo\\", "UseCache": True, "ModsDir": "", "CharaDir": "", "SceneDir": "", "CoordDir": ""},
}


def _build_task_config(task_name: str, enabled: bool, opt_values: dict) -> dict:
    cfg = dict(_TASK_DEFAULTS.get(task_name, {}))
    cfg["Enable"] = enabled

    def _set(config_key, option_key):
        v = _extract_opt(opt_values, option_key)
        if v is not None:
            cfg[config_key] = v

    if task_name == "CreateBackup":
        _set("OutputPath", "BackupOutputPath")
        _set("Filename", "BackupFilename")
        selected = _extract_opt(opt_values, "BackupFolders")
        if selected is not None:
            cfg["mods"] = "Mods" in selected
            cfg["UserData"] = "UserData" in selected
            cfg["BepInEx"] = "BepInEx" in selected

    elif task_name == "DownloadContents":
        _set("OutputDir", "DownloadOutputDir")
        _set("Links", "DownloadLinks")
        _set("SkipDownloaded", "SkipDownloaded")

    elif task_name == "FilterConvertKKS":
        _set("InputPath", "DownloadsInputPath")
        _set("Convert", "Convert")
        _set("KKAction", "KKAction")
        _set("KKSAction", "KKSAction")
        _set("ExtractArchive", "ExtractArchive")
        _set("Password", "ArchivePassword")

    elif task_name == "FilterDuplicateContents":
        selected = _extract_opt(opt_values, "InstallContentTypes")
        if selected is not None:
            cfg["Chara"] = "Chara" in selected
            cfg["Mods"] = "Mods" in selected
            cfg["Coords"] = "Coords" in selected
            cfg["Scenes"] = "Scenes" in selected
            cfg["Overlays"] = "Overlays" in selected
        _set("UseCache", "UseCache")
        _set("Keep", "KeepStrategy")
        _set("DuplicateAction", "DuplicateAction")
        _set("CharaDir", "CharaDir")
        _set("SceneDir", "SceneDir")
        _set("CoordDir", "CoordDir")
        _set("ModsDir", "ModsDir")
        _set("OverlaysDir", "OverlaysDir")

    elif task_name == "DownloadMissingMods":
        _set("ContentTypes", "ContentTypes")
        _set("SideloaderModpack", "SideloaderModpack")
        _set("TelegramSource", "TelegramSource")
        _set("TelegramChatLinks", "TelegramChatLinks")
        _set("UseCache", "UseCache")
        _set("OpenReport", "OpenReport")
        _set("InputModsDir", "InputModsDir")
        _set("OutputModsDir", "OutputModsDir")
        _set("CharaDir", "CharaDir")
        _set("SceneDir", "SceneDir")
        _set("CoordDir", "CoordDir")

    elif task_name == "CompressCardsTextures":
        _set("InputPath", "DownloadsInputPath")
        _set("KoiCardTexToolPath", "KoiCardTexToolPath")
        _set("DeleteOriginalCards", "DeleteOriginalCards")

    elif task_name == "InstallContents":
        _set("InputPath", "DownloadsInputPath")
        selected = _extract_opt(opt_values, "InstallContentTypes")
        if selected is not None:
            cfg["Chara"] = "Chara" in selected
            cfg["Mods"] = "Mods" in selected
            cfg["Coords"] = "Coords" in selected
            cfg["Scenes"] = "Scenes" in selected
            cfg["Overlays"] = "Overlays" in selected
        _set("FileConflicts", "FileConflicts")
        _set("ExtractArchive", "ExtractArchive")
        _set("Password", "ArchivePassword")

    elif task_name == "UninstallContents":
        _set("InputPath", "DownloadsInputPath")
        selected = _extract_opt(opt_values, "InstallContentTypes")
        if selected is not None:
            cfg["Chara"] = "Chara" in selected
            cfg["Mods"] = "Mods" in selected
            cfg["Coords"] = "Coords" in selected
            cfg["Scenes"] = "Scenes" in selected
            cfg["Overlays"] = "Overlays" in selected

    elif task_name == "GroupScenes":
        _set("SceneDir", "SceneDir")
        _set("UseCache", "UseCache")
        _set("IncludeSubfolders", "GroupScenesIncludeSubfolders")

    elif task_name == "GroupChara":
        _set("CharaDir", "CharaDir")
        _set("IncludeSubfolders", "GroupCharaIncludeSubfolders")
        _set("UseCache", "UseCache")
        _set("Prompt", "GroupCharaPrompt")

    elif task_name == "GroupCoordinates":
        _set("UseCache", "UseCache")
        _set("IncludeSubfolders", "GroupCoordinatesIncludeSubfolders")
        _set("AccessoryTolerance", "GroupCoordinatesAccessoryTolerance")
        _set("CharaDir", "CharaDir")
        _set("CoordDir", "CoordDir")

    elif task_name == "UngroupCards":
        _set("InputPath", "InputPath")
        selected = _extract_opt(opt_values, "UngroupCardTypes")
        if selected is not None:
            cfg["Chara"] = "Chara" in selected
            cfg["Scenes"] = "Scenes" in selected
            cfg["Coords"] = "Coords" in selected
        _set("DeleteEmptyFolders", "DeleteEmptyFolders")

    elif task_name == "RenameChara":
        _set("CharaDir", "CharaDir")
        _set("SkipAlreadyRenamed", "SkipAlreadyRenamed")
        _set("UpdateMetadata", "UpdateMetadata")
        _set("RenameFiles", "RenameFiles")
        _set("UseCache", "UseCache")
        _set("Prompt", "RenameCharaPrompt")

    elif task_name == "ArchiveCards":
        _set("OutputPath", "ArchiveOutputPath")
        _set("ContentPaths", "ContentPaths")
        _set("CombinedArchive", "CombinedArchive")
        _set("Format", "ArchiveFormat")
        _set("IncludeModpack", "IncludeModpack")
        _set("IncludeCoordinates", "IncludeCoordinates")
        _set("UseCache", "UseCache")
        _set("AutoResolve", "AutoResolve")
        _set("ModsDir", "ModsDir")
        _set("CoordDir", "CoordDir")

    elif task_name == "DeleteCards":
        _set("ContentPaths", "ContentPaths")
        _set("CheckSharedMods", "CheckSharedMods")
        _set("IncludeCoordinates", "IncludeCoordinates")
        _set("UseCache", "UseCache")
        _set("AutoResolve", "AutoResolve")
        _set("ModsDir", "ModsDir")
        _set("CharaDir", "CharaDir")
        _set("SceneDir", "SceneDir")
        _set("CoordDir", "CoordDir")

    elif task_name == "ReviewSimilarChara":
        _set("Mode", "SimilarityMode")
        _set("CheckSharedMods", "CheckSharedMods")
        _set("IncludeCoordinates", "IncludeCoordinates")
        _set("UseCache", "UseCache")
        _set("AutoResolve", "AutoResolve")
        _set("ModsDir", "ModsDir")
        _set("CharaDir", "CharaDir")
        _set("SceneDir", "SceneDir")
        _set("CoordDir", "CoordDir")

    elif task_name == "ExportMods":
        _set("OutputPath", "ExportOutputPath")
        _set("Guids", "Guids")
        _set("RenameToGuid", "RenameToGuid")
        _set("UseCache", "UseCache")
        _set("ModsDir", "ModsDir")

    elif task_name == "DeleteUnusedMods":
        _set("ExceptionList", "ExceptionList")
        _set("UseCache", "UseCache")
        _set("ModsDir", "ModsDir")
        _set("CharaDir", "CharaDir")
        _set("SceneDir", "SceneDir")
        _set("CoordDir", "CoordDir")

    return cfg


# ---------------------------------------------------------------------------
# Public Config class
# ---------------------------------------------------------------------------

class Config:
    def __init__(self, config_file: str, instance_index: int = 0):
        logger.info("SCRIPT", "Initializing config module")
        self.config_file    = config_file
        self.instance_index = instance_index
        self.ok             = False
        self.initialized    = False
        self.config_data    = None
        self.task_order: list[dict] = []
        self.read()

    def read(self):
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                mxu = json.load(f)
        except FileNotFoundError:
            raise ConfigError(f"Config file '{self.config_file}' not found.", tag="SCRIPT") from None
        except json.JSONDecodeError:
            raise ConfigError(f"Invalid JSON format in '{self.config_file}'.", tag="SCRIPT") from None

        instances = mxu.get("instances", [])
        if not instances:
            raise ConfigError("Config has no instances.", tag="SCRIPT")

        if self.instance_index >= len(instances):
            raise ConfigError(
                f"Instance index {self.instance_index} out of range "
                f"(config has {len(instances)} instance(s)).", tag="SCRIPT")

        inst = instances[self.instance_index]
        logger.info("SCRIPT", f"Using instance [{self.instance_index}] '{inst.get('name', '?')}'")

        self.config_data, self.task_order = self._translate(inst, mxu)
        self.validate()

        if self.ok and not self.initialized:
            logger.info("SCRIPT", "Starting KKAFIO!")
            self.initialized = True
        elif not self.ok and not self.initialized:
            raise ConfigError("Invalid config. Please check your config file.", tag="SCRIPT")

    @staticmethod
    def _translate(inst: dict, mxu: dict) -> tuple[dict, list[dict]]:
        tasks_list = inst.get("tasks", [])

        # GamePath and GameType: read from this instance's globalOptionValues,
        # then fall back to other instances, then task optionValues.
        game_path = ""
        game_type = GameType.KOIKATSU.value  # default

        global_opt_vals = inst.get("globalOptionValues", {})

        # GameType
        gt_val = global_opt_vals.get("GameType")
        if gt_val and isinstance(gt_val, dict) and gt_val.get("type") == "select":
            game_type = gt_val.get("caseName", GameType.KOIKATSU.value)

        # GamePath — this instance
        gp_val = global_opt_vals.get("GamePath")
        if gp_val and isinstance(gp_val, dict) and gp_val.get("type") == "folder":
            game_path = gp_val.get("path", "")

        # GamePath — other instances fallback. The GameType travels with
        # the path: a path borrowed from another instance belongs to that
        # instance's game, so adopt its GameType too (instead of keeping
        # this instance's own/default type, which may describe a
        # different game install).
        if not game_path:
            for other in mxu.get("instances", []):
                if other is inst:
                    continue
                other_globals = other.get("globalOptionValues", {})
                other_gp = other_globals.get("GamePath")
                if other_gp and isinstance(other_gp, dict) and other_gp.get("type") == "folder":
                    game_path = other_gp.get("path", "")
                    if game_path:
                        other_gt = other_globals.get("GameType")
                        if other_gt and isinstance(other_gt, dict) and other_gt.get("type") == "select":
                            game_type = other_gt.get("caseName", GameType.KOIKATSU.value)
                        else:
                            game_type = GameType.KOIKATSU.value
                        break

        # GamePath — legacy task optionValues fallback
        if not game_path:
            for t in tasks_list:
                v = _extract_opt(t.get("optionValues", {}), "GamePath")
                if v:
                    game_path = v
                    break

        data: dict = {"Core": {"GamePath": game_path, "GameType": game_type}}

        # Start with all-disabled defaults
        for key, defaults in _TASK_DEFAULTS.items():
            data[key] = dict(defaults)

        from kkafio.system.special_tasks import is_special_task
        task_order: list[dict] = []
        seen_kkafio: set = set()

        for t in tasks_list:
            task_name = t.get("taskName", "")
            enabled   = bool(t.get("enabled", False))
            opt_vals  = t.get("optionValues", {})

            if is_special_task(task_name):
                if enabled:
                    params = _extract_special_task_params(opt_vals)
                    task_order.append({"name": task_name, "params": params})

            elif task_name in _TASK_KEY:
                data[task_name] = _build_task_config(task_name, enabled, opt_vals)
                seen_kkafio.add(task_name)
                if enabled:
                    task_order.append({"name": task_name, "params": {}})

        # Ensure all KKAFIO task keys are present with defaults
        for key in _TASK_KEY:
            if key not in seen_kkafio:
                data[key] = dict(_TASK_DEFAULTS[key])

        return data, task_order

    def validate(self):
        logger.info("SCRIPT", "Validating config")
        self.ok = True
        self.validate_gamepath()
        self.validate_tasks()

    def validate_gamepath(self):
        game_path_str = self.config_data.get("Core", {}).get("GamePath", "")

        if not game_path_str:
            raise ConfigError("GamePath is not set.", tag="SCRIPT")

        base = Path(game_path_str)

        # Required paths — must exist for KKAFIO to function
        required_paths = {
            "base":        base,
            "UserData":    base / "UserData",
            "BepInEx":     base / "BepInEx",
            "mods":        base / "mods",
            "charaMale":   base / "UserData" / "chara" / "male",
            "charaFemale": base / "UserData" / "chara" / "female",
            "coordinate":  base / "UserData" / "coordinate",
            "Overlays":    base / "UserData" / "Overlays",
        }

        # Optional paths — present only when Studio is installed
        optional_paths = {
            "scene": base / "UserData" / "Studio" / "scene",
        }

        self.game_path = dict(required_paths)

        for path in required_paths.values():
            if not path.exists():
                if path == required_paths["Overlays"]:
                    required_paths["Overlays"].mkdir(parents=True, exist_ok=True)
                else:
                    raise ConfigError(f"Game path not valid: {path}", tag="SCRIPT")

        for key, path in optional_paths.items():
            if path.exists():
                self.game_path[key] = path
            else:
                logger.info("SCRIPT", f"Optional path not found (skipping): {path}")

    # Per-task (InputPath/OutputPath) defaults shipped in interface.json —
    # generated by tools/generate_config.py from each option's own
    # "default". If a task's folder is unset by the user (still exactly
    # this default) and doesn't exist yet, it's created automatically
    # instead of failing validation; a folder the user chose themselves is
    # still treated as an error if missing, since that's more likely a typo
    # worth surfacing than something we should silently paper over.
    _DEFAULT_TASK_PATHS = {
        ("CreateBackup", "OutputPath"): "C:/KKAFIO/Backups",
        ("FilterConvertKKS", "InputPath"): "C:/KKAFIO/Downloads",
        ("CompressCardsTextures", "InputPath"): "C:/KKAFIO/Downloads",
        ("InstallContents", "InputPath"): "C:/KKAFIO/Downloads",
        ("UninstallContents", "InputPath"): "C:/KKAFIO/Downloads",
        ("ArchiveCards", "OutputPath"): "C:/KKAFIO/Archived Cards",
        ("ExportMods", "OutputPath"): "C:/KKAFIO/Exported Mods",
    }

    @staticmethod
    def _platform_default(default: str) -> Path:
        """Translate a shipped "C:/KKAFIO/..." default (interface.json's
        defaults are Windows paths, since that's where Koikatsu itself
        normally runs) into something sensible to actually create on a
        non-Windows platform.

        Blindly `Path("C:/KKAFIO/Downloads").mkdir()`-ing on Linux/macOS
        doesn't fail — `Path` there has no concept of a drive, so "C:" is
        just parsed as an ordinary folder name — but it silently creates a
        nonsensical "./C:/KKAFIO/Downloads" folder under whatever the
        current working directory happens to be, which is exactly the kind
        of confusing side effect auto-creating a default folder is
        supposed to avoid.
        """
        default_path = Path(default)
        if sys.platform == "win32":
            return default_path
        # Everything after "C:/KKAFIO" (e.g. "Downloads", "Archived
        # Cards"), rooted under this platform's home directory instead.
        tail = Path(*default_path.parts[2:])
        return Path.home() / "KKAFIO" / tail

    def validate_tasks(self):
        for task in _TASK_KEY:
            task_config = self.config_data.get(task, {})
            if not task_config.get("Enable", False):
                continue
            for key in ("InputPath", "OutputPath"):
                if key in task_config and task_config[key]:
                    path_obj = Path(task_config[key])
                    task_config[key] = path_obj
                    if not path_obj.exists():
                        default = self._DEFAULT_TASK_PATHS.get((task, key))
                        if default is not None and path_obj == Path(default):
                            create_path = self._platform_default(default)
                            task_config[key] = create_path
                            logger.info("SCRIPT",
                                f"{key} does not exist yet, creating default folder for {task}: {create_path}")
                            create_path.mkdir(parents=True, exist_ok=True)
                            continue
                        raise ConfigError(f"Path invalid for task {task}: {path_obj}", tag="SCRIPT")

        self.create_backup             = self.config_data["CreateBackup"]
        self.download_contents         = self.config_data["DownloadContents"]
        self.filter_convert_kks        = self.config_data["FilterConvertKKS"]
        self.filter_duplicate_contents = self.config_data["FilterDuplicateContents"]
        self.download_missing_mods     = self.config_data["DownloadMissingMods"]
        self.compress_cards_textures   = self.config_data["CompressCardsTextures"]
        self.install_contents          = self.config_data["InstallContents"]
        self.uninstall_contents        = self.config_data["UninstallContents"]
        self.group_scenes              = self.config_data["GroupScenes"]
        self.group_chara               = self.config_data["GroupChara"]
        self.group_coordinates         = self.config_data["GroupCoordinates"]
        self.ungroup_cards             = self.config_data["UngroupCards"]
        self.rename_chara              = self.config_data["RenameChara"]
        self.archive_cards             = self.config_data["ArchiveCards"]
        self.delete_cards              = self.config_data["DeleteCards"]
        self.review_similar_chara      = self.config_data["ReviewSimilarChara"]
        self.export_mods               = self.config_data["ExportMods"]
        self.delete_unused_mods        = self.config_data["DeleteUnusedMods"]


# ---------------------------------------------------------------------------
# Utility: list all instances
# ---------------------------------------------------------------------------

def list_instances(config_file: str) -> list[tuple[int, str]]:
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            mxu = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [(i, inst.get("name", f"Instance {i}"))
            for i, inst in enumerate(mxu.get("instances", []))]


def find_context_menu_instance(config_file: str) -> int | None:
    """Index of the instance marked "use in Explorer context menu" in MXU
    (its `useInContextMenu` flag), or None if no instance is marked.

    MXU only ever lets one tab hold the flag; if a hand-edited config has
    several, the first one wins.
    """
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            mxu = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    for i, inst in enumerate(mxu.get("instances", [])):
        if isinstance(inst, dict) and inst.get("useInContextMenu") is True:
            return i
    return None
