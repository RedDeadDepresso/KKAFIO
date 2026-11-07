"""`--game-folders`: use the game's own folders instead of the ones saved in the config."""

from pathlib import Path

import pytest

from kkafio import cli
from kkafio.core.config import _TASK_DEFAULTS
from kkafio.registry import GameFolders
from kkafio.task_specs import TASK_SPECS, TASKS_BY_NAME

ROOT = Path(__file__).resolve().parents[2]

# Tasks whose folder settings fall back to the game's folder when blank, with the settings
# `--game-folders` blanks. The other tasks work on an input / output folder the user gives.
EXPECTED = {
    "DownloadMissingMods": {"CharaDir", "SceneDir", "CoordDir", "InstalledModsDir", "DownloadedModsDir"},
    "ExportMods": {"ModsDir"},
    "DeleteUnusedMods": {"ModsDir", "CharaDir", "SceneDir", "CoordDir"},
    "DeleteCards": {"CharaDir", "SceneDir", "CoordDir"},
    "ReviewSimilarChara": {"CharaDir", "SceneDir", "CoordDir"},
    "GroupCoordinates": {"CharaDir", "CoordDir"},
    "OrganizeLibrary": {"CharaDir", "SceneDir", "CoordDir", "InstalledModsDir", "DownloadedModsDir"},
    "RenameChara": {"CharaDir"},
    "GroupScenes": {"SceneDir"},
    "GroupChara": {"CharaDir"},
    "FilterDuplicateContents": {"CharaDir", "SceneDir", "CoordDir", "ModsDir", "OverlaysDir"},
}


def _overrides(argv):
    """What `kkafio_cli <argv>` hands to the task: option overrides, prepare hook, --game-folders."""
    args = cli.build_parser().parse_args(argv)
    spec = next(s for s in TASK_SPECS if s.command == args.command)
    overrides = spec.overrides_from(args)
    if spec.prepare is not None:
        spec.prepare(args, overrides)
    spec.finish_overrides(args, overrides)
    return overrides


def _flag_help(command):
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and command in a.choices)
    return sub.choices[command].format_help()


def test_exactly_the_folder_tasks_accept_the_flag():
    declared = {s.name: {k for o in s.options if isinstance(o, GameFolders) for k in o.keys}
                for s in TASK_SPECS}
    assert {name: keys for name, keys in declared.items() if keys} == EXPECTED
    for spec in TASK_SPECS:
        assert ("--game-folders" in _flag_help(spec.command)) == (spec.name in EXPECTED), spec.command


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_keys_are_real_folder_settings_with_a_command_line_option(name):
    spec = TASKS_BY_NAME[name]
    assert EXPECTED[name] <= set(_TASK_DEFAULTS[name])
    given = {o.key for o in spec.options if getattr(o, "key", None)}
    # every blanked key is one the task also lets you set on the command line (the two mods
    # keys of the two mods-folder tasks are set through the combined --mods-dir hook instead)
    assert (EXPECTED[name] - {"InstalledModsDir", "DownloadedModsDir"}) <= given


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_without_the_flag_nothing_is_overridden(name):
    assert _overrides([TASKS_BY_NAME[name].command]) == {}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_flag_blanks_every_folder_setting_of_the_task(name):
    assert _overrides([TASKS_BY_NAME[name].command, "--game-folders"]) == {k: "" for k in EXPECTED[name]}


def test_a_folder_given_on_the_command_line_wins():
    ov = _overrides(["organize-library", "--game-folders", "--chara-dir", r"D:\cards"])
    assert ov["CharaDir"] == r"D:\cards"
    assert ov["SceneDir"] == ov["CoordDir"] == ""
    # order on the command line does not matter
    assert _overrides(["organize-library", "--chara-dir", r"D:\cards", "--game-folders"]) == ov


def test_the_flag_does_not_undo_what_a_prepare_hook_sets():
    # --mods-dir is the legacy shorthand that a hook expands into the two mods folders
    ov = _overrides(["organize-library", "--game-folders", "--mods-dir", r"D:\mods"])
    assert ov["InstalledModsDir"] == ov["DownloadedModsDir"] == r"D:\mods"
    assert ov["CharaDir"] == ""
    ov = _overrides(["download-missing-mods", "--game-folders", "--downloaded-mods-dir", r"D:\dl"])
    assert ov["DownloadedModsDir"] == r"D:\dl" and ov["InstalledModsDir"] == ""
    # --input is a shorthand for every folder of filter-duplicate-contents
    ov = _overrides(["filter-duplicate-contents", "--game-folders", "--input", r"D:\stage"])
    assert set(ov.values()) == {r"D:\stage"} and set(ov) == EXPECTED["FilterDuplicateContents"]
    ov = _overrides(["filter-duplicate-contents", "--game-folders", "--input", r"D:\stage", "--mods-dir", r"D:\m"])
    assert ov["ModsDir"] == r"D:\m" and ov["CharaDir"] == r"D:\stage"


def test_an_alias_of_a_folder_option_counts_as_giving_it():
    assert _overrides(["group-scenes", "--game-folders", "-i", r"D:\scenes"]) == {"SceneDir": r"D:\scenes"}
    assert _overrides(["group-chara", "--game-folders", "--input", r"D:\c"]) == {"CharaDir": r"D:\c"}


def test_a_blank_folder_option_does_not_stand_in_for_the_flag():
    # `--chara-dir ""` is "not given" (and shells drop empty arguments): the saved folder stays
    assert _overrides(["group-chara", "--chara-dir", ""]) == {}


def test_the_help_names_the_folders_of_the_task():
    assert "chara, scene and coordinate folder(s)" in _flag_help("delete-cards")
    assert "scene folder(s)" in _flag_help("group-scenes")
    assert "chara, scene, coordinate, mods and overlays folder(s)" in _flag_help("filter-duplicate-contents")


def test_the_saved_folder_is_ignored_end_to_end(monkeypatch):
    """Through the real command handler: the task is handed blank folders, not the saved ones."""
    import kkafio.registry as registry

    class _Config:
        config_data = {"OrganizeLibrary": {"Enable": False, "CharaDir": r"D:\saved", "SceneDir": r"D:\saved",
                                           "CoordDir": r"D:\saved", "InstalledModsDir": r"D:\saved",
                                           "DownloadedModsDir": r"D:\saved", "UseCache": True}}

    seen = {}

    def fake_run_task(spec, config, file_manager, overrides=None, **kw):
        config.config_data[spec.name].update(overrides or {})
        seen.update(config.config_data[spec.name])

    monkeypatch.setattr(cli, "_load_core", lambda *a, **k: (_Config(), object()))
    monkeypatch.setattr(registry, "run_task", fake_run_task)

    def run(*argv):
        _Config.config_data["OrganizeLibrary"].update(CharaDir=r"D:\saved", SceneDir=r"D:\saved",
                                                      CoordDir=r"D:\saved", InstalledModsDir=r"D:\saved",
                                                      DownloadedModsDir=r"D:\saved")
        args = cli.build_parser().parse_args(["organize-library", *argv])
        args.func(args)
        return dict(seen)

    assert run()["CharaDir"] == r"D:\saved"                                  # no flag: the saved folder
    blank = run("--game-folders")
    assert [blank[k] for k in ("CharaDir", "SceneDir", "CoordDir", "InstalledModsDir", "DownloadedModsDir")] == [""] * 5
    assert run("--game-folders", "--scene-dir", r"D:\s")["SceneDir"] == r"D:\s"


def test_the_game_folders_context_menu_entry_passes_the_flag():
    script = (ROOT / "scripts" / "register_context_menu.ps1").read_text(encoding="utf-8-sig")
    line = next(l for l in script.splitlines() if l.startswith("$GameLibraryTask"))
    assert "Cli = 'organize-library'" in line and "ArgsTemplate = '--game-folders'" in line
