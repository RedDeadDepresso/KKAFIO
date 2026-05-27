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
            "taskName": "InstallChara",
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
"""

import sys
import json
from pathlib import Path
from typing import Any
from util.logger import logger
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
    KEY_MAP: dict[str, Any] = {
        "__MXU_SLEEP_OPTION__":         ("input",  None),
        "__MXU_WAITUNTIL_OPTION__":     ("input",  None),
        "__MXU_NOTIFY_OPTION__":        ("input",  None),
        "__MXU_WEBHOOK_OPTION__":       ("input",  None),
        "__MXU_LAUNCH_OPTION__":        ("input",  None),
        "__MXU_LAUNCH_WAIT_OPTION__":   ("switch", "wait_for_exit"),
        "__MXU_LAUNCH_SKIP_OPTION__":   ("switch", "skip_if_running"),
        "__MXU_LAUNCH_CMD_OPTION__":    ("switch", "use_cmd"),
        "__MXU_KILLPROC_SELF_OPTION__": ("switch", "kill_self"),
        "__MXU_KILLPROC_NAME_OPTION__": ("input",  None),
        "__MXU_POWER_OPTION__":         ("select", "power_action"),
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
        kind, param_name = mapping
        if kind == "input":
            params.update(v.get("values", {}))
        elif kind == "switch":
            params[param_name] = v.get("value", False)
        elif kind == "select":
            params[param_name] = v.get("caseName", "")

    return params


# ---------------------------------------------------------------------------
# Task-name → config key mapping
# ---------------------------------------------------------------------------

_TASK_KEY = {
    "InstallChara":     "InstallChara",
    "UninstallChara":      "UninstallChara",
    "FilterConvertChara": "FilterConvertChara",
    "DeleteChara":      "DeleteChara",
    "ArchiveChara":     "ArchiveChara",
    "GroupChara":       "GroupChara",
    "UngroupChara":     "UngroupChara",
    "FilterDuplicates": "FilterDuplicates",
    "CreateBackup":     "CreateBackup",
    "DownloadChara":    "DownloadChara",
}

_TASK_DEFAULTS = {
    "InstallChara":     {"Enable": False, "InputPath": "", "ExtractArchive": True,  "FileConflicts": "Skip", "Password": "Skip"},
    "UninstallChara":      {"Enable": False, "InputPath": ""},
    "FilterConvertChara": {"Enable": False, "InputPath": "", "ConvertKKS": False, "ConvertKK": False, "ExtractArchive": True, "Password": "Skip"},
    "DeleteChara":      {"Enable": False, "CharaPaths": [], "AutoResolve": True, "UseCache": False},
    "ArchiveChara":     {"Enable": False, "CharaPaths": [], "Format": "7z", "AutoResolve": True, "UseCache": False, "IncludeModpack": False, "CombinedArchive": True, "OutputPath": ""},
    "GroupChara":       {"Enable": False, "InputPath": "", "Prompt": "", "Response": ""},
    "UngroupChara":     {"Enable": False, "InputPath": "", "DeleteEmptyFolders": True},
    "FilterDuplicates": {"Enable": False, "InputPath": "", "FuzzyChara": False, "Keep": "Biggest file size", "Delete": False},
    "CreateBackup":     {"Enable": False, "OutputPath": "", "Filename": "koikatsu_backup", "mods": False, "UserData": False, "BepInEx": False},
    "DownloadChara":    {"Enable": False, "Links": "", "OutputDir": "", "SkipDownloaded": True},
}


def _build_task_config(task_name: str, enabled: bool, opt_values: dict) -> dict:
    cfg = dict(_TASK_DEFAULTS.get(task_name, {}))
    cfg["Enable"] = enabled

    def _set(config_key, option_key):
        v = _extract_opt(opt_values, option_key)
        if v is not None:
            cfg[config_key] = v

    if task_name == "InstallChara":
        _set("InputPath",      "InputPath")
        _set("ExtractArchive", "ExtractArchive")
        v = _extract_opt(opt_values, "FileConflicts")
        if v: cfg["FileConflicts"] = v
        v = _extract_opt(opt_values, "ArchivePassword")
        if v: cfg["Password"] = v

    elif task_name == "UninstallChara":
        _set("InputPath", "InputPath")

    elif task_name == "FilterConvertChara":
        _set("InputPath",      "InputPath")
        _set("ConvertKKS",     "ConvertKKS")
        _set("ConvertKK",      "ConvertKK")
        _set("ExtractArchive", "ExtractArchive")
        v = _extract_opt(opt_values, "ArchivePassword")
        if v: cfg["Password"] = v

    elif task_name == "DeleteChara":
        _set("CharaPaths",  "CharaPaths")
        _set("AutoResolve", "AutoResolve")
        _set("UseCache",    "UseCache")

    elif task_name == "ArchiveChara":
        _set("CharaPaths",      "CharaPaths")
        _set("AutoResolve",     "AutoResolve")
        _set("UseCache",        "UseCache")
        _set("IncludeModpack",  "IncludeModpack")
        _set("CombinedArchive", "CombinedArchive")
        _set("OutputPath",      "OutputPath")
        v = _extract_opt(opt_values, "ArchiveFormat")
        if v: cfg["Format"] = v

    elif task_name == "GroupChara":
        _set("InputPath", "InputPath")
        v = _extract_opt(opt_values, "GroupCharaPrompt")
        if v is not None: cfg["Prompt"] = v
        v = _extract_opt(opt_values, "GroupCharaResponse")
        if v is not None: cfg["Response"] = v

    elif task_name == "UngroupChara":
        _set("InputPath",          "InputPath")
        _set("DeleteEmptyFolders", "DeleteEmptyFolders")

    elif task_name == "FilterDuplicates":
        _set("InputPath",  "InputPath")
        _set("FuzzyChara", "FuzzyMatching")
        v = _extract_opt(opt_values, "KeepStrategy")
        if v: cfg["Keep"] = v
        _set("Delete", "DeleteDuplicates")

    elif task_name == "CreateBackup":
        _set("OutputPath", "OutputPath")
        v = _extract_opt(opt_values, "BackupFilename")
        if v: cfg["Filename"] = v
        _set("mods",     "BackupMods")
        _set("UserData", "BackupUserData")
        _set("BepInEx",  "BackupBepInEx")

    elif task_name == "DownloadChara":
        _set("Links",           "DownloadLinks")
        _set("OutputDir",       "DownloadOutputDir")
        _set("SkipDownloaded",  "SkipDownloaded")

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
            logger.error("SCRIPT", f"Config file '{self.config_file}' not found.")
            sys.exit(1)
        except json.JSONDecodeError:
            logger.error("SCRIPT", f"Invalid JSON format in '{self.config_file}'.")
            sys.exit(1)

        instances = mxu.get("instances", [])
        if not instances:
            logger.error("SCRIPT", "Config has no instances.")
            sys.exit(1)

        if self.instance_index >= len(instances):
            logger.error("SCRIPT",
                f"Instance index {self.instance_index} out of range "
                f"(config has {len(instances)} instance(s)).")
            sys.exit(1)

        inst = instances[self.instance_index]
        logger.info("SCRIPT", f"Using instance [{self.instance_index}] '{inst.get('name', '?')}'")

        self.config_data, self.task_order = self._translate(inst, mxu)
        self.validate()

        if self.ok and not self.initialized:
            logger.info("SCRIPT", "Starting KKAFIO!")
            self.initialized = True
        elif not self.ok and not self.initialized:
            logger.error("SCRIPT", "Invalid config. Please check your config file.")
            sys.exit(1)

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

        # GamePath — other instances fallback
        if not game_path:
            for other in mxu.get("instances", []):
                if other is inst:
                    continue
                other_gp = other.get("globalOptionValues", {}).get("GamePath")
                if other_gp and isinstance(other_gp, dict) and other_gp.get("type") == "folder":
                    game_path = other_gp.get("path", "")
                    if game_path:
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

        from util.special_tasks import is_special_task
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
            logger.error("SCRIPT", "GamePath is not set. Configure it in MXU.")
            raise Exception("GamePath is not set")

        base = Path(game_path_str)

        self.game_path = {
            "base":        base,
            "UserData":    base / "UserData",
            "BepInEx":     base / "BepInEx",
            "mods":        base / "mods",
            "charaMale":   base / "UserData" / "chara" / "male",
            "charaFemale": base / "UserData" / "chara" / "female",
            "coordinate":  base / "UserData" / "coordinate",
            "Overlays":    base / "UserData" / "Overlays",
        }

        for path in self.game_path.values():
            if not path.exists():
                logger.error("SCRIPT", f"Game path not valid: {path}")
                raise Exception(f"Game path not valid: {path}")

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
                        logger.error("SCRIPT", f"Path invalid for task {task}: {path_obj}")
                        raise Exception(f"Path invalid: {path_obj}")

        self.archive_chara    = self.config_data["ArchiveChara"]
        self.download_chara   = self.config_data["DownloadChara"]
        self.delete_chara     = self.config_data["DeleteChara"]
        self.create_backup    = self.config_data["CreateBackup"]
        self.filter_convert_chara           = self.config_data["FilterConvertChara"]
        self.filter_duplicates= self.config_data["FilterDuplicates"]
        self.group_chara      = self.config_data["GroupChara"]
        self.install_chara    = self.config_data["InstallChara"]
        self.ungroup_chara    = self.config_data["UngroupChara"]
        self.uninstall_chara     = self.config_data["UninstallChara"]


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
