"""Group Scenes' manual gallery (opened from Run Gallery): the authors file and the gallery's data (folder
names, suggestions, moving). Group Scenes itself always groups automatically. The gallery itself is shared
with Group Characters."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import card_factory as cf
from kkafio.core.config import _build_task_config
from kkafio.core.paths import ASSETS_DATA_DIR
from kkafio.indices import author as sa
from kkafio.indices.author import Author
from kkafio.tasks import group_scenes as gs
from kkafio.tasks.filter_duplicate_contents import PNG_CACHE_FILE

AUTHORS = [Author("Alice", ("Al", "wonder")), Author("Bob"), Author("Alucid", ("lucy",)), Author("Carl950")]


# --- the authors file ----------------------------------------------------------------------------------

def test_the_shipped_authors_file_loads():
    authors = sa.load_authors()
    assert len(authors) > 100 and all(a.name == a.name.strip() and a.name for a in authors)
    by_name = {a.name: a for a in authors}
    assert by_name["0864"].aliases == ("864",) and by_name["123sir"].aliases == ()
    assert "Skull Prince" in by_name                                 # stray whitespace is stripped
    assert (ASSETS_DATA_DIR / sa.AUTHORS_FILE).is_file()


def test_load_authors_is_forgiving(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"authors": [{"name": " X ", "alias": [" y ", "", 5]}, {"name": ""}, {"alias": ["z"]},
                                         "junk", {"name": "W", "alias": "oops"}]}), encoding="utf-8")
    assert sa.load_authors(p) == [Author("X", ("y",)), Author("W")]
    assert sa.load_authors(tmp_path / "missing.json") == []
    p.write_text("not json", encoding="utf-8")
    assert sa.load_authors(p) == []
    p.write_text(json.dumps({"authors": 3}), encoding="utf-8")
    assert sa.load_authors(p) == []


def test_suggest_authors_ranks_names_then_aliases_then_contains():
    assert sa.suggest_authors("", AUTHORS) == []
    assert sa.suggest_authors("al", AUTHORS) == ["Alice", "Alucid"]          # names starting with it
    assert sa.suggest_authors("w", AUTHORS) == ["Alice"]                     # via the alias "wonder"
    assert sa.suggest_authors("LUC", AUTHORS) == ["Alucid"]                  # contains in name beats alias-contains order
    assert sa.suggest_authors("ob", AUTHORS) == ["Bob"]
    assert sa.suggest_authors("zzz", AUTHORS) == []
    assert sa.suggest_authors("a", AUTHORS, limit=2) == ["Alice", "Alucid"]


def test_canonical_name_resolves_aliases_in_any_case():
    assert sa.canonical_name(" WONDER ", AUTHORS) == "Alice"
    assert sa.canonical_name("bob", AUTHORS) == "Bob"
    assert sa.canonical_name("  Nobody ", AUTHORS) == "Nobody"


# --- callbacks -----------------------------------------------------------------------------------------

def _item(folder: Path, rel: str):
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")
    parts = Path(rel).parts
    return SimpleNamespace(path=path, root=folder, name=path.stem, series=parts[0] if len(parts) > 1 else None)


def test_suggest_authors_first_then_matching_folders_and_empty_text_lists_folders():
    known = {"alicia": "Alicia", "zed": "Zed"}
    suggest, _ = gs.manual_callbacks(known, AUTHORS, {})
    assert suggest("wonder") == ["Alice"]
    assert suggest("ali") == ["Alice", "Alicia"]                              # the folder follows the known authors
    assert suggest("") == ["Alicia", "Zed"]                                   # nothing typed: the existing folders
    assert suggest("al")[:3] == ["Alice", "Alucid", "Alicia"]


def test_assign_resolves_aliases_spelling_and_records_moves(tmp_path):
    known, moves = {"bob": "bob"}, {}
    _s, assign = gs.manual_callbacks(known, AUTHORS, moves)
    a = _item(tmp_path, "a.png")
    assert assign(a, " wonder ") == (tmp_path / "Alice" / "a.png", "Alice")  # alias -> the author's name
    assert moves == {str(tmp_path / "a.png"): str(tmp_path / "Alice" / "a.png")}
    assert known["alice"] == "Alice"
    assert assign(_item(tmp_path, "b.png"), "BOB")[1] == "bob"               # an existing folder's spelling wins
    assert assign(_item(tmp_path, "c.png"), "Some/One?")[1] == "Some_One_"    # unknown authors are allowed, made safe


def test_assign_collision_already_there_and_unusable(tmp_path):
    moves = {}
    _s, assign = gs.manual_callbacks({}, AUTHORS, moves)
    (tmp_path / "Alice").mkdir()
    (tmp_path / "Alice" / "a.png").write_bytes(b"old")
    dest, _ = assign(_item(tmp_path, "a.png"), "Alice")
    assert dest == tmp_path / "Alice" / "a_1.png"
    inside = _item(tmp_path, "Alice/z.png")
    before = dict(moves)
    assert assign(inside, "al") == (inside.path, "Alice") and moves == before    # "al" is an alias of Alice: already there
    assert assign(_item(tmp_path, "d.png"), "...") is None
    with mock.patch.object(gs.shutil, "move", side_effect=OSError("locked")):
        assert assign(_item(tmp_path, "e.png"), "Bob") is None


# --- the task: always automatic, no Mode option -------------------------------------------------------

def test_there_is_no_mode_option_any_more():
    cfg = _build_task_config("GroupScenes", True, {})
    assert "UseAutomatic" not in cfg and "UseManual" not in cfg
    # a stale Mode value from an old GUI config is ignored
    cfg = _build_task_config("GroupScenes", True, {"GroupScenesMode": {"type": "checkbox", "caseNames": ["Manual"]}})
    assert "UseAutomatic" not in cfg and "UseManual" not in cfg


def test_interface_json_has_no_mode_option():
    interface = json.loads((Path(__file__).resolve().parents[1] / "interface.json").read_text(encoding="utf-8"))
    assert "GroupScenesMode" not in interface["option"]
    assert "GroupScenesMode" not in next(t for t in interface["task"] if t["name"] == "GroupScenes")["option"]


def _task(folder: Path, **kw):
    cfg = mock.Mock()
    cfg.game_path = {}
    cfg.group_scenes = {"SceneDir": str(folder), "UseCache": True, "IncludeSubfolders": False,
                        "SubgroupByYear": False, **kw}
    return gs.GroupScenes(cfg, None)


def test_run_always_groups_automatically(tmp_path):
    calls = []
    # old configs may still carry the removed keys: they change nothing
    for kw in ({}, {"UseAutomatic": False, "UseManual": True}):
        with mock.patch.object(gs.GroupScenes, "_run_automatic", lambda self, f: calls.append("auto")):
            _task(tmp_path, **kw).run()
    assert calls == ["auto", "auto"]


def test_gallery_tab_lists_scenes_only_moves_them_and_keeps_the_hash_cache(tmp_path):
    a, b = cf.scene_card([["a"]]), cf.scene_card([["b"]])
    cf.write(tmp_path / "s1.png", a)
    cf.write(tmp_path / "Bob" / "s2.png", b)
    cf.write(tmp_path / "_skipped_" / "s3.png", cf.scene_card([["c"]]))
    cf.write(tmp_path / "chara.png", cf.chara_card(["x"]))                    # not a scene

    with mock.patch.object(gs, "load_authors", return_value=AUTHORS), \
         mock.patch.object(gs, "load_scene_index", side_effect=AssertionError("Manual needs no index")):
        tab = gs.scene_gallery_tab(tmp_path, True)
        try:
            items = sorted((i.name, i.series) for i in tab.items)
            suggested = tab.suggest("al")
            moved = tab.assign(next(i for i in tab.items if i.name == "s1"), "wonder")
        finally:
            tab.finish()

    assert items == [("s1", None), ("s2", "Bob")]
    assert tab.cover_ratio == gs.SCENE_COVER_RATIO and tab.text.untagged == "No author"
    assert suggested[:2] == ["Alice", "Alucid"]
    assert moved == (tmp_path / "Alice" / "s1.png", "Alice")
    cache = json.loads((tmp_path / PNG_CACHE_FILE).read_text())["files"]
    assert str(tmp_path / "Alice" / "s1.png") in cache and str(tmp_path / "s1.png") not in cache


def test_gallery_tab_with_no_scenes_is_empty(tmp_path):
    cf.write(tmp_path / "chara.png", cf.chara_card(["x"]))
    tab = gs.scene_gallery_tab(tmp_path, True)
    tab.finish()
    assert tab.items == []


def test_the_scene_wording_is_complete():
    text = gs.scene_dialog_text()
    assert text.status.format(total=3, untagged=1) == "3 scenes · 1 without an author"
    assert all(getattr(text, f) for f in text.__dataclass_fields__)
