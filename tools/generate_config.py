"""
generate_config.py — Regenerate utils/config.py's interface.json-driven
sections from interface.json.

utils/config.py adapts the GUI's raw MXU JSON config into the flat dict
every task module expects. Most of that file is generic scaffolding
(the Config class, path validation, etc.) that has nothing to do with
interface.json — but five pieces exist purely to mirror it, and have to be
hand-kept in sync every time a task/option is added, renamed, or removed:

  - `_TASK_KEY`          — every task name interface.json declares
  - `_TASK_DEFAULTS`     — the all-disabled default config for each task,
                           one entry per option the task declares
  - `_build_task_config` — per-task `elif` branch that pulls each option's
                           GUI value into KKAFIO's own config keys
  - `_DEFAULT_TASK_PATHS`— the (task, InputPath/OutputPath) -> default
                           folder table used to auto-create default folders
  - the `self.<snake_case_task_name> = self.config_data["TaskName"]`
    accessor properties at the end of `validate_tasks()`

This script derives all five straight from interface.json instead, using
the same signal doc 03 calls "decorative" for the GUI but still describes
as documentation of "this option ends up as config key X": each option's
`pipeline_override` (top-level, or the union of its cases' overrides). In
this codebase that signal already agrees with the hand-written mapping for
every option except one (see CHECKBOX_AS_LIST below), so it's treated as
authoritative and any option pipeline_override can't resolve is reported as
a warning rather than guessed at. The accessor property names are derived
by converting each task's PascalCase name to snake_case.

Everything else in utils/config.py (docstring, imports, GameType enum,
_extract_opt, _extract_special_task_params, the Config class itself) is
genuine hand-written logic with no interface.json equivalent, so it's kept
as a fixed template and never touched by this script.

Usage:
  python tools/generate_config.py                # write utils/config.py
  python tools/generate_config.py --dry-run       # print the file, don't write it
  python tools/generate_config.py --check         # exit 1 if utils/config.py is stale
  python tools/generate_config.py --diff          # show a unified diff against the current file
"""

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _camel_to_snake(name: str) -> str:
    """PascalCase -> snake_case, keeping acronym runs intact
    (e.g. "FilterConvertKKS" -> "filter_convert_kks")."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


# Checkbox options whose selection should be kept as the raw list of
# selected case names (stored under the option's own ID) instead of being
# expanded into one boolean config key per case. This is the one place
# where pipeline_override's case data ("ScanChara", "ScanScene", ...)
# doesn't match what the Python side actually stores — DownloadMissingMods
# keeps the raw ["Chara", "Scene", ...] list under "ContentTypes" rather
# than three separate Scan* booleans.
CHECKBOX_AS_LIST = {"ContentTypes"}

# Manual (option_id -> config_key) overrides, for the rare option whose
# pipeline_override can't be resolved to a single target key automatically.
# ContentTypes is here because it's in CHECKBOX_AS_LIST above: its
# pipeline_override describes three separate booleans (ScanChara, ...) but
# the real config key is the option's own ID, holding the raw list.
CONFIG_KEY_OVERRIDES: dict[str, str] = {
    "ContentTypes": "ContentTypes",
}


# ---------------------------------------------------------------------------
# Option -> config-key / default resolution
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Small helper to render Python literals with double-quoted strings,
# matching this codebase's style (repr() defaults to single quotes).
# ---------------------------------------------------------------------------

def _pyrepr(value) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return "[" + ", ".join(_pyrepr(v) for v in value) + "]"
    return repr(value)


class Unresolvable(Exception):
    pass


def _override_target_key(option_id: str, option: dict) -> str:
    """Return the single config key an option's value should be stored
    under, resolved from its pipeline_override (top-level, else the union
    of its cases')."""
    if option_id in CONFIG_KEY_OVERRIDES:
        return CONFIG_KEY_OVERRIDES[option_id]

    top = option.get("pipeline_override")
    if isinstance(top, dict) and len(top) == 1:
        return next(iter(top.keys()))

    keys: set[str] = set()
    for case in option.get("cases", []):
        po = case.get("pipeline_override")
        if isinstance(po, dict):
            keys |= set(po.keys())
    if len(keys) == 1:
        return next(iter(keys))

    raise Unresolvable(
        f"option '{option_id}': can't resolve a single config key from "
        f"pipeline_override (top-level={top!r}, case keys={keys!r}). "
        f"Add it to CONFIG_KEY_OVERRIDES in this script."
    )


def _default_for(option: dict):
    """Best-effort default value for an option, in the shape _extract_opt()
    would return it (so it can be dropped straight into _TASK_DEFAULTS)."""
    t = option["type"]
    if t == "input":
        inputs = option.get("inputs", [])
        return inputs[0].get("default", "") if inputs else ""
    if t == "switch":
        return option.get("default_case") == "Yes"
    if t in ("select", "checkbox"):
        return option.get("default_case")
    # folder / textarea / file_list
    return option.get("default", "")


class ResolvedOption:
    """What one option contributes to a single task's config."""

    __slots__ = ("kind", "targets", "set_lines", "default_entries")

    def __init__(self, kind, targets, set_lines, default_entries):
        self.kind = kind                      # "scalar" | "checkbox_bools"
        self.targets = targets                # config keys this touches
        self.set_lines = set_lines             # lines for _build_task_config
        self.default_entries = default_entries # [(config_key, default_value)]


def resolve_option(option_id: str, option: dict) -> ResolvedOption:
    t = option["type"]

    if t == "checkbox" and option_id not in CHECKBOX_AS_LIST:
        # Expand into one boolean config key per case.
        case_names = [c["name"] for c in option.get("cases", [])]
        case_targets = {}
        for case in option.get("cases", []):
            po = case.get("pipeline_override") or {}
            if len(po) != 1:
                raise Unresolvable(
                    f"checkbox option '{option_id}' case '{case['name']}' "
                    f"doesn't have exactly one pipeline_override key: {po!r}"
                )
            case_targets[case["name"]] = next(iter(po.keys()))

        default_selected = set(option.get("default_case") or [])
        lines = [f'selected = _extract_opt(opt_values, "{option_id}")',
                  "if selected is not None:"]
        defaults = []
        for name in case_names:
            key = case_targets[name]
            lines.append(f'    cfg["{key}"] = {_pyrepr(name)} in selected')
            defaults.append((key, name in default_selected))
        return ResolvedOption("checkbox_bools", list(case_targets.values()), lines, defaults)

    # Every other type (including ContentTypes-style "keep the raw list"
    # checkboxes) is a single scalar/list value under one config key.
    config_key = _override_target_key(option_id, option)
    default = _default_for(option)
    lines = [f'_set("{config_key}", "{option_id}")']
    return ResolvedOption("scalar", [config_key], lines, [(config_key, default)])


# ---------------------------------------------------------------------------
# Per-task generation
# ---------------------------------------------------------------------------

def build_task_key(tasks: list[dict]) -> str:
    lines = ["_TASK_KEY = {"]
    for t in tasks:
        name = t["name"]
        lines.append(f'    "{name}": "{name}",')
    lines.append("}")
    return "\n".join(lines)


def build_task_defaults(tasks: list[dict], options: dict, warnings: list[str]) -> tuple[str, dict[str, dict]]:
    """Returns (source text, {task_name: {config_key: default}}) — the
    latter is reused to derive _DEFAULT_TASK_PATHS."""
    lines = ["_TASK_DEFAULTS = {"]
    per_task_defaults: dict[str, dict] = {}

    for t in tasks:
        name = t["name"]
        defaults = {"Enable": False}
        for opt_id in t.get("option", []):
            option = options.get(opt_id)
            if option is None:
                warnings.append(
                    f"task '{name}' references undefined option '{opt_id}' — skipped"
                )
                continue
            try:
                resolved = resolve_option(opt_id, option)
            except Unresolvable as e:
                warnings.append(f"task '{name}': {e}")
                continue
            for key, value in resolved.default_entries:
                defaults[key] = value
        per_task_defaults[name] = defaults

        entries = ", ".join(f"{_pyrepr(k)}: {_pyrepr(v)}" for k, v in defaults.items())
        lines.append(f'    "{name}": {{{entries}}},')

    lines.append("}")
    return "\n".join(lines), per_task_defaults


def build_task_config_fn(tasks: list[dict], options: dict, warnings: list[str]) -> str:
    lines = [
        "def _build_task_config(task_name: str, enabled: bool, opt_values: dict) -> dict:",
        "    cfg = dict(_TASK_DEFAULTS.get(task_name, {}))",
        '    cfg["Enable"] = enabled',
        "",
        "    def _set(config_key, option_key):",
        "        v = _extract_opt(opt_values, option_key)",
        "        if v is not None:",
        "            cfg[config_key] = v",
        "",
    ]

    for i, t in enumerate(tasks):
        name = t["name"]
        keyword = "if" if i == 0 else "elif"
        lines.append(f'    {keyword} task_name == "{name}":')
        body_lines: list[str] = []
        for opt_id in t.get("option", []):
            option = options.get(opt_id)
            if option is None:
                continue  # already warned about in build_task_defaults
            try:
                resolved = resolve_option(opt_id, option)
            except Unresolvable:
                continue  # already warned about in build_task_defaults
            body_lines.extend(resolved.set_lines)
        if not body_lines:
            body_lines = ["pass"]
        for bl in body_lines:
            lines.append(f"        {bl}")
        lines.append("")

    lines.append("    return cfg")
    return "\n".join(lines)


def build_default_task_paths(per_task_defaults: dict[str, dict]) -> str:
    lines = ["    _DEFAULT_TASK_PATHS = {"]
    for task_name, defaults in per_task_defaults.items():
        for key in ("InputPath", "OutputPath"):
            value = defaults.get(key)
            if isinstance(value, str) and value:
                lines.append(f'        ("{task_name}", "{key}"): {_pyrepr(value)},')
    lines.append("    }")
    return "\n".join(lines)


def build_accessor_properties(tasks: list[dict]) -> str:
    names = [(t["name"], _camel_to_snake(t["name"])) for t in tasks]
    width = max(len(n) for _, n in names)
    lines = []
    for task_name, attr in names:
        pad = " " * (width - len(attr))
        lines.append(f'        self.{attr}{pad} = self.config_data["{task_name}"]')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fixed template — everything that has no interface.json equivalent
# ---------------------------------------------------------------------------

HEADER = '''"""
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
from utils.logger import logger
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
# Task-name -> config key mapping
# ---------------------------------------------------------------------------

'''

FOOTER = '''

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

        from utils.special_tasks import is_special_task
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
            logger.error("SCRIPT", "GamePath is not set.")
            raise Exception("GamePath is not set")

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

        for key, path in required_paths.items():
            if not path.exists():
                logger.error("SCRIPT", f"Game path not valid: {path}")
                raise Exception(f"Game path not valid: {path}")

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
{DEFAULT_TASK_PATHS}

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
                            logger.info("SCRIPT", f"{key} does not exist yet, creating default folder for {task}: {path_obj}")
                            path_obj.mkdir(parents=True, exist_ok=True)
                            continue
                        logger.error("SCRIPT", f"Path invalid for task {task}: {path_obj}")
                        raise Exception(f"Path invalid: {path_obj}")

{ACCESSOR_PROPERTIES}
'''


def generate(interface: dict) -> tuple[str, list[str]]:
    warnings: list[str] = []
    tasks = interface.get("task", [])
    options = interface.get("option", {})

    task_key_src = build_task_key(tasks)
    task_defaults_src, per_task_defaults = build_task_defaults(tasks, options, warnings)
    build_fn_src = build_task_config_fn(tasks, options, warnings)
    default_paths_src = build_default_task_paths(per_task_defaults)
    accessor_props_src = build_accessor_properties(tasks)

    body = (
        HEADER
        + task_key_src + "\n\n"
        + task_defaults_src + "\n\n\n"
        + build_fn_src + "\n"
        + FOOTER.replace("{DEFAULT_TASK_PATHS}", default_paths_src)
                .replace("{ACCESSOR_PROPERTIES}", accessor_props_src)
    )
    return body, warnings


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--interface", default=None, metavar="PATH",
                     help="Path to interface.json (default: <repo root>/interface.json)")
    ap.add_argument("--output", default=None, metavar="PATH",
                     help="Path to write (default: <repo root>/utils/config.py)")
    ap.add_argument("--dry-run", action="store_true", help="Print the generated file, don't write it")
    ap.add_argument("--check", action="store_true", help="Exit 1 if the output file is stale; changes nothing")
    ap.add_argument("--diff", action="store_true", help="Print a unified diff against the current output file")
    args = ap.parse_args()

    interface_path = Path(args.interface).resolve() if args.interface else REPO_ROOT / "interface.json"
    output_path = Path(args.output).resolve() if args.output else REPO_ROOT / "utils" / "config.py"

    if not interface_path.exists():
        print(f"ERROR: interface.json not found: {interface_path}", file=sys.stderr)
        sys.exit(1)

    interface = json.loads(interface_path.read_text(encoding="utf-8"))
    generated, warnings = generate(interface)

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    current = output_path.read_text(encoding="utf-8") if output_path.exists() else None
    stale = current != generated

    if args.diff:
        current_lines = (current or "").splitlines(keepends=True)
        generated_lines = generated.splitlines(keepends=True)
        sys.stdout.writelines(difflib.unified_diff(
            current_lines, generated_lines,
            fromfile=str(output_path), tofile="<generated>",
        ))
        if not stale:
            print("(no differences)")
        return

    if args.check:
        if stale:
            print(f"{output_path} is stale — run tools/generate_config.py to regenerate it.")
            sys.exit(1)
        print(f"{output_path} is up to date with {interface_path}.")
        return

    if args.dry_run:
        print(generated)
        return

    output_path.write_text(generated, encoding="utf-8")
    print(f"Wrote {output_path}" + (" (unchanged)" if not stale else ""))
    if warnings:
        print(f"{len(warnings)} warning(s) above need a manual look — "
              f"affected options were skipped rather than guessed at.")


if __name__ == "__main__":
    main()