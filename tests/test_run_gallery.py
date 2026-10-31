"""Run Gallery: config, registration, folder resolution and how the task assembles the tabs."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.core.config import _build_task_config
from kkafio.core.errors import InputError
from kkafio.dialogs.manual_group import CharaItem, GalleryTab
from kkafio.tasks import run_gallery as rg

ROOT = Path(__file__).resolve().parents[1]


def _task(tmp_path, game=None, **cfg):
    game_path = {"charaFemale": tmp_path / "f", "charaMale": tmp_path / "m", "coordinate": tmp_path / "c"}
    game_path.update(game or {})
    config = SimpleNamespace(run_gallery=cfg, game_path=game_path)
    return rg.RunGallery(config, None)


def test_options_to_config():
    cfg = _build_task_config("RunGallery", True, {
        "CharaDir": {"type": "folder", "path": "X"},
        "SceneDir": {"type": "folder", "path": "Y"},
        "CoordDir": {"type": "folder", "path": "Z"},
        "UseCache": {"type": "switch", "caseName": "No", "value": False}})
    assert (cfg["CharaDir"], cfg["SceneDir"], cfg["CoordDir"]) == ("X", "Y", "Z")
    assert _build_task_config("RunGallery", True, {})["UseCache"] is True


def test_interface_json_declares_the_task():
    interface = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    task = next(t for t in interface["task"] if t["name"] == "RunGallery")
    assert task["option"] == ["CharaDir", "SceneDir", "CoordDir", "UseCache"]


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


def _fake_tab(label, n, log):
    items = [CharaItem(Path(f"/x/{label}{i}.png"), Path("/x"), f"{label}{i}", None, 1, 0.0) for i in range(n)]
    return GalleryTab(label, items, lambda t: [], lambda i, t: None, None, 1.0, lambda: log.append(f"finish {label}"))


def _run(task, tmp_path, counts=(1, 1, 1), dialog=None, chara_error=None):
    (tmp_path / "c").mkdir(exist_ok=True)
    log = []
    shown = {}

    def fake_dialog(title, tabs, initial=0):
        shown.update(title=title, labels=[t.label for t in tabs], initial=initial)
        return [0] * len(tabs)

    def chara(*a, **k):
        if chara_error:
            raise chara_error
        return _fake_tab(a[-1] if isinstance(a[-1], str) else k["label"], counts[1], log)

    with mock.patch.object(rg, "scene_gallery_tab", lambda f, c, label="": _fake_tab(label, counts[0], log)), \
         mock.patch.object(rg, "chara_gallery_tab", lambda f, h, c, label="": chara_error and (_ for _ in ()).throw(chara_error)
                           or _fake_tab(label, counts[1], log)), \
         mock.patch.object(rg, "coordinate_gallery_tab", lambda cd, d, c, label="": _fake_tab(label, counts[2], log)), \
         mock.patch("kkafio.dialogs.manual_group.gallery_dialog", dialog or fake_dialog):
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
