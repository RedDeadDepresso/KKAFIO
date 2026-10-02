"""Group Scenes: scene index loading and per-author grouping."""

import json
from pathlib import Path
from unittest import mock

import card_factory as cf
from kkafio.services import scene_index as si
from kkafio.tasks import group_scenes as gs
from kkafio.tasks.filter_duplicate_contents import PNG_CACHE_FILE, _get_png_payload, _xxh


def _digest(data: bytes) -> str:
    return _xxh(_get_png_payload(data))


class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


class _Client:
    """Fake httpx client: commits API -> [{"sha": sha}], index URL -> index."""
    def __init__(self, sha, index):
        self.sha, self.index, self.urls = sha, index, []
    def get(self, url, **kw):
        self.urls.append(url)
        return _Resp([{"sha": self.sha}] if url == si.SCENE_INDEX_COMMITS_API else self.index)


def test_index_downloaded_only_when_commit_changes(tmp_path):
    index = {"AAAA": "Author One"}
    c = _Client("sha1", index)
    assert si.load_scene_index(c, tmp_path) == {"aaaa": "Author One"}
    assert si.SCENE_INDEX_URL in c.urls
    assert (tmp_path / si.COMMIT_FILENAME).read_text() == "sha1"

    same = _Client("sha1", {"BBBB": "Other"})            # same commit -> cached copy, no download
    assert si.load_scene_index(same, tmp_path) == {"aaaa": "Author One"}
    assert si.SCENE_INDEX_URL not in same.urls

    new = _Client("sha2", {"BBBB": "Other"})             # new commit -> re-download
    assert si.load_scene_index(new, tmp_path) == {"bbbb": "Other"}
    assert (tmp_path / si.COMMIT_FILENAME).read_text() == "sha2"


def test_normalize_index_tolerates_odd_values():
    raw = {"AB": "x", "cd": {"author": "y"}, "ef": "", "gh": 5, "ij": {"name": "z"}}
    assert si.normalize_index(raw) == {"ab": "x", "cd": "y", "ij": "z"}


def test_author_folder_name():
    assert gs.author_folder_name('A/B:C?') == "A_B_C_"
    assert gs.author_folder_name("name. ") == "name"
    assert gs.author_folder_name("CON") == "_CON"
    assert gs.author_folder_name("...") == ""


def _task(folder: Path, **kw):
    cfg = mock.Mock()
    cfg.game_path = {}
    cfg.group_scenes = {"SceneDir": str(folder), "UseCache": True, "IncludeSubfolders": False, **kw}
    return gs.GroupScenes(cfg, None)


def test_scenes_in_index_move_to_author_folder(tmp_path):
    a, b, other = cf.scene_card([["a"]]), cf.scene_card([["b"]]), cf.scene_card([["c"]])
    cf.write(tmp_path / "s1.png", a)
    cf.write(tmp_path / "s2.png", b)
    cf.write(tmp_path / "unknown.png", other)
    cf.write(tmp_path / "chara.png", cf.chara_card(["x"]))           # not a scene -> untouched
    cf.write(tmp_path / "deep" / "s3.png", cf.scene_card([["d"]]))   # subfolder -> ignored by default
    index = si.normalize_index({_digest(a).upper(): "Alice", _digest(b): "Bob/The:Builder"})

    with mock.patch.object(gs, "load_scene_index", return_value=index):
        _task(tmp_path).run()

    assert (tmp_path / "Alice" / "s1.png").exists()
    assert (tmp_path / "Bob_The_Builder" / "s2.png").exists()
    assert (tmp_path / "unknown.png").exists() and (tmp_path / "chara.png").exists()
    assert (tmp_path / "deep" / "s3.png").exists()

    # moved files keep a valid entry in the shared duplicate-filter hash cache
    cache = json.loads((tmp_path / PNG_CACHE_FILE).read_text())["files"]
    assert str(tmp_path / "Alice" / "s1.png") in cache and str(tmp_path / "s1.png") not in cache
    assert cache[str(tmp_path / "Alice" / "s1.png")]["xxh"] == _digest(a)


def test_rerun_is_a_noop_and_collisions_get_suffix(tmp_path):
    a = cf.scene_card([["a"]])
    cf.write(tmp_path / "s1.png", a)
    index = {_digest(a): "Alice"}
    with mock.patch.object(gs, "load_scene_index", return_value=index):
        _task(tmp_path).run()
        _task(tmp_path).run()                                        # already in place
        cf.write(tmp_path / "s1.png", a)                             # same name again
        _task(tmp_path).run()
    assert sorted(p.name for p in (tmp_path / "Alice").iterdir()) == ["s1.png", "s1_1.png"]


def test_include_subfolders_regroups_into_author_folder(tmp_path):
    a = cf.scene_card([["a"]])
    cf.write(tmp_path / "old" / "s1.png", a)
    with mock.patch.object(gs, "load_scene_index", return_value={_digest(a): "Alice"}):
        _task(tmp_path, IncludeSubfolders=True, UseCache=False).run()
    assert (tmp_path / "Alice" / "s1.png").exists() and not (tmp_path / "old" / "s1.png").exists()
    assert not (tmp_path / PNG_CACHE_FILE).exists()                  # cache off -> none written


def test_missing_index_fails_and_moves_nothing(tmp_path):
    a = cf.scene_card([["a"]])
    cf.write(tmp_path / "s1.png", a)
    with mock.patch.object(gs, "load_scene_index", return_value={}):
        try:
            _task(tmp_path).run()
            raise AssertionError("expected TaskFailedError")
        except gs.TaskFailedError:
            pass
    assert (tmp_path / "s1.png").exists()
