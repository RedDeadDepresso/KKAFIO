"""Group Coordinates: per-character folders for matching coordinate cards."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kkafio.tasks import group_coordinates as gc


def _task(chara_dir: Path, coord_dir: Path, use_cache=True, sub=True):
    cfg = {"UseCache": use_cache, "IncludeSubfolders": sub, "CharaDir": str(chara_dir), "CoordDir": str(coord_dir)}
    config = SimpleNamespace(group_coordinates=cfg, game_path={
        "charaFemale": chara_dir, "charaMale": chara_dir, "coordinate": coord_dir})
    return gc.GroupCoordinates(config, None)


def _run(tmp_path, chara_digests, coord_map, use_cache=True, sub=True):
    chara_dir, coord_dir = tmp_path / "chara", tmp_path / "coord"
    chara_dir.mkdir(exist_ok=True)
    coord_dir.mkdir(exist_ok=True)
    with mock.patch.object(gc, "collect_chara_digests",
                           return_value={chara_dir / n: d for n, d in chara_digests.items()}), \
         mock.patch.object(gc, "build_coord_cache",
                           return_value={str(coord_dir / n): d for n, d in coord_map.items()}):
        _task(chara_dir, coord_dir, use_cache, sub).run()
    return coord_dir


def _coords(coord_dir, *names):
    for n in names:
        (coord_dir / n).parent.mkdir(parents=True, exist_ok=True)
        (coord_dir / n).write_bytes(b"png")


def test_matching_coord_moves_into_chara_named_folder(tmp_path):
    (tmp_path / "coord").mkdir()
    _coords(tmp_path / "coord", "a.png", "b.png", "c.png")
    out = _run(tmp_path, {"Alice.png": {"d1", "d2"}}, {"a.png": "d1", "b.png": "d2", "c.png": "zzz"})
    assert (out / "Alice" / "a.png").exists() and (out / "Alice" / "b.png").exists()
    assert (out / "c.png").exists()                       # no matching character: untouched


def test_non_coordinate_png_never_moves(tmp_path):
    (tmp_path / "coord").mkdir()
    _coords(tmp_path / "coord", "x.png")
    out = _run(tmp_path, {"Alice.png": {"d1"}}, {"x.png": ""})
    assert (out / "x.png").exists()


def test_multiple_matches_use_first_by_filename_and_stay_put_when_already_in_one(tmp_path):
    (tmp_path / "coord" / "Bob").mkdir(parents=True)
    _coords(tmp_path / "coord", "shared.png", "Bob/kept.png")
    out = _run(tmp_path, {"Bob.png": {"d"}, "Alice.png": {"d"}},
               {"shared.png": "d", "Bob/kept.png": "d"})
    assert (out / "Alice" / "shared.png").exists()
    assert (out / "Bob" / "kept.png").exists()


def test_name_collision_gets_suffix(tmp_path):
    (tmp_path / "coord" / "Alice").mkdir(parents=True)
    _coords(tmp_path / "coord", "a.png", "Alice/a.png")
    out = _run(tmp_path, {"Alice.png": {"d"}}, {"a.png": "d"})
    assert (out / "Alice" / "a_1.png").exists() and (out / "Alice" / "a.png").exists()


def test_coord_cache_follows_moves(tmp_path):
    (tmp_path / "coord").mkdir()
    _coords(tmp_path / "coord", "a.png")
    cache = tmp_path / "coord" / gc.COORD_CACHE_FILE
    old = str(tmp_path / "coord" / "a.png")
    cache.write_text(json.dumps({"version": gc.COORD_CACHE_VERSION, "coord_dir": str(tmp_path / "coord"),
                                 "files": {old: [1, 2]}, "coords": {old: "d"}}))
    _run(tmp_path, {"Alice.png": {"d"}}, {"a.png": "d"})
    data = json.loads(cache.read_text())
    new = str(tmp_path / "coord" / "Alice" / "a.png")
    assert new in data["coords"] and old not in data["coords"] and new in data["files"]


def test_folder_name_sanitised():
    assert gc.folder_name_for(Path("a:b?.png")) == "a_b_"
    assert gc.folder_name_for(Path("CON.png")) == "_CON"
    assert gc.folder_name_for(Path("...png")) == ""


def test_subfolders_ignored_unless_enabled(tmp_path):
    (tmp_path / "coord" / "Old").mkdir(parents=True)
    _coords(tmp_path / "coord", "top.png", "Old/deep.png")
    cm = {"top.png": "d", "Old/deep.png": "d"}
    out = _run(tmp_path, {"Alice.png": {"d"}}, cm, sub=False)
    assert (out / "Alice" / "top.png").exists() and (out / "Old" / "deep.png").exists()
    out = _run(tmp_path, {"Alice.png": {"d"}}, {"Old/deep.png": "d", "Alice/top.png": "d"}, sub=True)
    assert (out / "Alice" / "deep.png").exists()
