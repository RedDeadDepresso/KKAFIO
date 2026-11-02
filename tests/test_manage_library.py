"""Manage Library: config, registration, folder resolution and how the task assembles the tabs."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.core.config import _build_task_config
from kkafio.core.errors import InputError
from kkafio.dialogs.library import CharaItem, LibraryTab
from kkafio.tasks import manage_library as rg

ROOT = Path(__file__).resolve().parents[1]


def _task(tmp_path, game=None, **cfg):
    game_path = {"charaFemale": tmp_path / "f", "charaMale": tmp_path / "m", "coordinate": tmp_path / "c"}
    game_path.update(game or {})
    config = SimpleNamespace(manage_library=cfg, game_path=game_path)
    return rg.ManageLibrary(config, None)


def test_options_to_config():
    cfg = _build_task_config("ManageLibrary", True, {
        "CharaDir": {"type": "folder", "path": "X"},
        "SceneDir": {"type": "folder", "path": "Y"},
        "CoordDir": {"type": "folder", "path": "Z"},
        "InputModsDir": {"type": "folder", "path": "M"},
        "OutputModsDir": {"type": "folder", "path": "O"},
        "UseCache": {"type": "switch", "caseName": "No", "value": False}})
    assert (cfg["CharaDir"], cfg["SceneDir"], cfg["CoordDir"], cfg["InputModsDir"], cfg["OutputModsDir"]) \
        == ("X", "Y", "Z", "M", "O")
    assert _build_task_config("ManageLibrary", True, {})["UseCache"] is True
    assert _build_task_config("ManageLibrary", True, {})["CheckSharedMods"] is True
    assert _build_task_config("ManageLibrary", True, {})["IncludeCoordinates"] is True
    both = _build_task_config("ManageLibrary", True, {
        "ManageLibraryDeleteOptions": {"type": "checkbox", "caseNames": ["CheckSharedMods", "IncludeCoordinates"]}})
    assert (both["CheckSharedMods"], both["IncludeCoordinates"]) == (True, True)
    one = _build_task_config("ManageLibrary", True, {
        "ManageLibraryDeleteOptions": {"type": "checkbox", "caseNames": ["IncludeCoordinates"]}})
    assert (one["CheckSharedMods"], one["IncludeCoordinates"]) == (False, True)
    none = _build_task_config("ManageLibrary", True, {
        "ManageLibraryDeleteOptions": {"type": "checkbox", "caseNames": []}})
    assert (none["CheckSharedMods"], none["IncludeCoordinates"]) == (False, False)


def test_interface_json_declares_the_task():
    interface = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    task = next(t for t in interface["task"] if t["name"] == "ManageLibrary")
    opt = interface["option"]["ManageLibraryDeleteOptions"]
    assert opt["type"] == "checkbox" and opt["default_case"] == ["CheckSharedMods", "IncludeCoordinates"]
    assert [c["name"] for c in opt["cases"]] == ["CheckSharedMods", "IncludeCoordinates"]
    for c in opt["cases"]:                       # labels and descriptions are the Delete Cards ones
        assert c["label"] == f"$option.{c['name']}.label" and c["description"] == f"$option.{c['name']}.description"
    assert task["option"] == ["ManageLibraryDeleteOptions", "CharaDir", "SceneDir", "CoordDir", "InputModsDir",
                              "OutputModsDir", "UseCache"]


def test_scene_dir_custom_default_and_missing(tmp_path):
    scenes = tmp_path / "s"
    scenes.mkdir()
    assert _task(tmp_path, SceneDir=str(scenes))._scene_dir() == scenes
    assert _task(tmp_path, game={"scene": scenes})._scene_dir() == scenes
    assert _task(tmp_path)._scene_dir() is None                    # no Studio, nothing given
    with pytest.raises(InputError):
        _task(tmp_path, SceneDir=str(tmp_path / "nope"))._scene_dir()


def test_coord_dir_must_exist(tmp_path):
    (tmp_path / "c").mkdir()
    assert _task(tmp_path)._coord_dir() == tmp_path / "c"
    with pytest.raises(InputError):
        _task(tmp_path, CoordDir=str(tmp_path / "nope"))._coord_dir()


def test_mods_dirs_custom_default_and_missing(tmp_path):
    mods, out = tmp_path / "mods", tmp_path / "out"
    mods.mkdir()
    out.mkdir()
    assert _task(tmp_path, InputModsDir=str(mods))._mods_dirs() == (mods, None)
    assert _task(tmp_path, game={"mods": mods})._mods_dirs() == (mods, mods)
    assert _task(tmp_path, game={"mods": mods}, InputModsDir=str(tmp_path))._mods_dirs() == (tmp_path, mods)
    assert _task(tmp_path, game={"mods": mods}, OutputModsDir=str(out))._mods_dirs() == (mods, out)
    assert _task(tmp_path)._mods_dirs() == (None, None)              # nothing known: the sidebar still opens
    assert _task(tmp_path, game={"mods": tmp_path / "gone"})._mods_dirs() == (None, None)
    # an output folder that isn't there yet is left out; a missing input folder is an error
    assert _task(tmp_path, InputModsDir=str(mods), OutputModsDir=str(tmp_path / "later"))._mods_dirs() == (mods, None)
    with pytest.raises(InputError):
        _task(tmp_path, InputModsDir=str(tmp_path / "nope"))._mods_dirs()


def test_the_dialog_gets_the_mods_lookup(tmp_path):
    mods, out = tmp_path / "mods", tmp_path / "out"
    mods.mkdir()
    out.mkdir()
    shown, _ = _run(_task(tmp_path, game={"scene": tmp_path}, InputModsDir=str(mods), OutputModsDir=str(out),
                          UseCache=False), tmp_path)
    lookup = shown["mods"].lookup
    assert lookup.mods_dir == mods.resolve() and lookup.use_cache is False
    assert lookup.mods_dirs == [mods.resolve(), out.resolve()]
    shown, _ = _run(_task(tmp_path), tmp_path)
    assert not shown["mods"].lookup.available


def _fake_tab(label, n, log):
    items = [CharaItem(Path(f"/x/{label}{i}.png"), Path("/x"), f"{label}{i}", None, 1, 0.0) for i in range(n)]
    return LibraryTab(label, items, lambda t: [], lambda i, t: None, None, 1.0, lambda: log.append(f"finish {label}"))


def _run(task, tmp_path, counts=(1, 1, 1), dialog=None, chara_error=None):
    (tmp_path / "c").mkdir(exist_ok=True)
    log = []
    shown = {}

    def fake_dialog(title, tabs, initial=0, mods=None, on_trash=None):
        shown.update(title=title, labels=[t.label for t in tabs], initial=initial, mods=mods, on_trash=on_trash)
        return [0] * len(tabs)

    def chara(*a, **k):
        if chara_error:
            raise chara_error
        return _fake_tab(a[-1] if isinstance(a[-1], str) else k["label"], counts[1], log)

    with mock.patch.object(rg, "scene_library_tab", lambda f, c, label="": _fake_tab(label, counts[0], log)), \
         mock.patch.object(rg, "chara_library_tab", lambda f, h, c, label="": chara_error and (_ for _ in ()).throw(chara_error)
                           or _fake_tab(label, counts[1], log)), \
         mock.patch.object(rg, "coordinate_library_tab", lambda cd, d, c, label="": _fake_tab(label, counts[2], log)), \
         mock.patch("kkafio.dialogs.library.library_dialog", dialog or fake_dialog):
        task.run()
    return shown, log


def test_tabs_are_scene_character_outfit(tmp_path):
    shown, _ = _run(_task(tmp_path, game={"scene": tmp_path}), tmp_path)
    assert shown["labels"] == ["Scene", "Character", "Outfit"] and shown["initial"] == 0


def test_without_studio_the_scene_tab_is_left_out(tmp_path):
    shown, _ = _run(_task(tmp_path), tmp_path)
    assert shown["labels"] == ["Character", "Outfit"]


def test_opens_on_the_first_tab_with_cards(tmp_path):
    shown, _ = _run(_task(tmp_path, game={"scene": tmp_path}), tmp_path, counts=(0, 0, 2))
    assert shown["initial"] == 2


def test_no_cards_opens_nothing_but_still_saves_caches(tmp_path):
    shown, log = _run(_task(tmp_path, game={"scene": tmp_path}), tmp_path, counts=(0, 0, 0))
    assert shown == {} and sorted(log) == ["finish Character", "finish Outfit", "finish Scene"]


def test_a_failing_tab_still_finishes_the_ones_already_read(tmp_path):
    task = _task(tmp_path, game={"scene": tmp_path})
    with pytest.raises(RuntimeError):
        _run(task, tmp_path, chara_error=RuntimeError("boom"))


def test_trashed_cards_are_handed_to_delete_cards_with_the_output_mods_dir(tmp_path):
    mods, out, scenes = tmp_path / "mods", tmp_path / "out", tmp_path / "s"
    for d in (mods, out, scenes):
        d.mkdir()
    task = _task(tmp_path, game={"scene": scenes, "mods": mods}, InputModsDir=str(mods), OutputModsDir=str(out),
                 CheckSharedMods=False, IncludeCoordinates=False, UseCache=False)
    shown, _ = _run(task, tmp_path)
    seen = {}

    class FakeDelete:
        def __init__(self, config, fm, settings=None):
            seen["settings"] = settings

        def run(self):
            seen["ran"] = True

    with mock.patch("kkafio.tasks.delete_cards.DeleteCards", FakeDelete):
        shown["on_trash"]([tmp_path / "a.png", tmp_path / "b.png"])
    st = seen["settings"]
    assert seen["ran"] and st["ContentPaths"] == [str(tmp_path / "a.png"), str(tmp_path / "b.png")]
    assert st["ModsDir"] == str(out) and st["AutoResolve"] is False
    assert st["CheckSharedMods"] is False and st["IncludeCoordinates"] is False and st["UseCache"] is False
    assert st["CoordDir"] == str(tmp_path / "c") and st["SceneDir"] == str(scenes)
    assert str(tmp_path / "f") in st["CharaDirs"]

    # no output folder: Delete Cards gets no mods folder (and, not auto-resolving, leaves mods alone)
    shown, _ = _run(_task(tmp_path), tmp_path)
    with mock.patch("kkafio.tasks.delete_cards.DeleteCards", FakeDelete):
        shown["on_trash"]([tmp_path / "a.png"])
    assert seen["settings"]["ModsDir"] == ""
