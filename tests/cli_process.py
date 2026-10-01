"""
Runs the real CLI as a subprocess for failure scenarios, recording what a user (or the GUI)
actually gets: the exit code, the error-level output, and whether a traceback file was written.

In-process tests can't see this: it depends on how errors travel from the raise site through
the CLI's handlers to the console. Goldens are in golden/error_behavior.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cli_harness import World, _scrub, opt_folder

REPO = Path(__file__).resolve().parents[1]

_STATUS_NOISE = ("INFO", "DEBUG", "SUCCESS")


def _write_config(world: World, kind: str) -> Path:
    root = world.root
    game = str(world.game)
    path = root / f"config_{kind}.json"
    inst = {"name": "t", "globalOptionValues": {"GamePath": opt_folder(game)}, "tasks": []}
    if kind == "missing":
        return root / "does_not_exist.json"
    if kind == "badjson":
        path.write_text("{not json", encoding="utf-8")
    elif kind == "noinstances":
        path.write_text(json.dumps({"instances": []}), encoding="utf-8")
    elif kind == "nogame":
        inst["globalOptionValues"] = {}
        path.write_text(json.dumps({"instances": [inst]}), encoding="utf-8")
    elif kind == "badgame":
        inst["globalOptionValues"] = {"GamePath": opt_folder(root / "no_such_game")}
        path.write_text(json.dumps({"instances": [inst]}), encoding="utf-8")
    elif kind == "badtask":     # an enabled task whose folder does not exist
        inst["tasks"] = [{"taskName": "UngroupChara", "enabled": True, "optionValues": {
            "InputPath": opt_folder(root / "missing_dir")}}]
        path.write_text(json.dumps({"instances": [inst]}), encoding="utf-8")
    else:                        # "ok"
        path.write_text(json.dumps({"instances": [inst]}), encoding="utf-8")
    return path


# name -> (config kind, argv after --config, env overrides)
def scenarios() -> dict[str, tuple[str, list[str], dict[str, str]]]:
    no_tools = {"PATH": "{ROOT}/emptybin"}      # nothing on PATH: 7z cannot be found
    return {
        "config file missing":                ("missing", ["run"], {}),
        "config is not JSON":                 ("badjson", ["run"], {}),
        "config has no instances":            ("noinstances", ["run"], {}),
        "instance index out of range":        ("ok", ["--instance", "5", "run"], {}),
        "GamePath not set (run)":             ("nogame", ["run"], {}),
        "GamePath not valid (run)":           ("badgame", ["run"], {}),
        "GamePath not valid (single task)":   ("badgame", ["ungroup-chara"], {}),
        "enabled task folder missing (run)":  ("badtask", ["run"], {}),
        "input folder missing (ungroup)":     ("ok", ["ungroup-chara", "--input", "{ROOT}/missing_dir"], {}),
        "input folder missing (install)":     ("ok", ["install-contents", "--input", "{ROOT}/missing_dir"], {}),
        "input folder blank (compress)":      ("ok", ["compress-cards-textures", "--input", ""], {}),
        "no content (archive)":               ("ok", ["archive-cards"], {}),
        "nothing found to archive":           ("ok", ["archive-cards", "{ROOT}/x.png", "--format", "copy"], {}),
        "no content (delete) logs and returns": ("ok", ["delete-cards"], {}),
        "7-Zip missing (backup)":             ("ok", ["create-backup", "--output", "{ROOT}/bk", "--mods"], no_tools),
        # Documents a known crash: with no --output, the default is a str but CreateBackup needs a Path.
        "backup without --output (known bug)": ("ok", ["create-backup", "--mods"], {}),
        "success control (ungroup)":          ("ok", ["ungroup-chara", "--input", "{ROOT}/ok_dir"], {}),
    }


def run_scenario(world: World, name: str) -> dict:
    from kkafio.core.paths import CONFIG_DIR

    kind, argv, env_extra = scenarios()[name]
    root = str(world.root)
    (world.root / "emptybin").mkdir(exist_ok=True)
    (world.root / "bk").mkdir(exist_ok=True)
    (world.root / "ok_dir").mkdir(exist_ok=True)
    cfg = _write_config(world, kind)
    sub = lambda s: s.replace("{ROOT}", root)                                    # noqa: E731
    env = {**os.environ, **{k: sub(v) for k, v in env_extra.items()}}
    tb = CONFIG_DIR / "traceback.log"
    tb.unlink(missing_ok=True)

    proc = subprocess.run([sys.executable, str(REPO / "kkafio_cli.py"), "--config", str(cfg), *map(sub, argv)],
                          capture_output=True, text=True, env=env, cwd=REPO, timeout=120)

    lines = []
    for raw in (proc.stdout + proc.stderr).splitlines():
        if not raw.strip() or raw.startswith(("-", "=")) or raw.split("|")[0].strip() in _STATUS_NOISE:
            continue
        lines.append(_scrub(raw, root).rstrip())
    result = {"exit": proc.returncode, "output": lines, "traceback": None}
    if tb.exists():
        text = [l for l in tb.read_text(encoding="utf-8").splitlines() if l.strip()]
        result["traceback"] = {"first": _scrub(text[0], root), "last": _scrub(text[-1], root)}
    return result
