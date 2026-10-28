"""Group Scenes, Manual mode: the authors file, the gallery's data (folder names, suggestions, moving), the
Mode option and the task. The gallery itself is shared with Group Characters."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import card_factory as cf
from kkafio.core.config import _build_task_config
from kkafio.core.paths import ASSETS_DATA_DIR
from kkafio.indices import authors as sa
from kkafio.indices.authors import Author
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


# --- the Mode option and the task ----------------------------------------------------------------------

def _cfg(modes):
    values = {} if modes is None else {"GroupScenesMode": {"type": "checkbox", "caseNames": modes}}
    return _build_task_config("GroupScenes", True, values)


def test_mode_option_to_config():
    pair = lambda c: (c["UseAutomatic"], c["UseManual"])           # noqa: E731
    assert pair(_cfg(None)) == (True, False)
    assert pair(_cfg(["Manual"])) == (False, True)
    assert pair(_cfg(["Automatic", "Manual"])) == (True, True)
    assert pair(_cfg([])) == (False, False)


def test_interface_json_declares_the_mode_option():
    interface = json.loads((Path(__file__).resolve().parents[1] / "interface.json").read_text(encoding="utf-8"))
    option = interface["option"]["GroupScenesMode"]
    assert option["type"] == "checkbox" and option["default_case"] == ["Automatic"]
    assert {c["name"]: c["pipeline_override"] for c in option["cases"]} == {
        "Automatic": {"UseAutomatic": True}, "Manual": {"UseManual": True}}
    assert "GroupScenesMode" in next(t for t in interface["task"] if t["name"] == "GroupScenes")["option"]


def _task(folder: Path, **kw):
    cfg = mock.Mock()
    cfg.game_path = {}
    cfg.group_scenes = {"SceneDir": str(folder), "UseCache": True, "IncludeSubfolders": False,
                        "SubgroupByYear": False, **kw}
    return gs.GroupScenes(cfg, None)


def test_run_dispatches_on_the_modes(tmp_path):
    def dispatch(**kw):
        calls = []
        with mock.patch.object(gs.GroupScenes, "_run_automatic", lambda self, f: calls.append("auto")), \
             mock.patch.object(gs.GroupScenes, "_run_manual", lambda self, f: calls.append("manual")):
            _task(tmp_path, **kw).run()
        return calls
    assert dispatch() == ["auto"]
    assert dispatch(UseAutomatic=False, UseManual=True) == ["manual"]
    assert dispatch(UseAutomatic=True, UseManual=True) == ["auto", "manual"]
    assert dispatch(UseAutomatic=False, UseManual=False) == []


def test_manual_lists_scenes_only_moves_them_and_keeps_the_hash_cache(tmp_path):
    a, b = cf.scene_card([["a"]]), cf.scene_card([["b"]])
    cf.write(tmp_path / "s1.png", a)
    cf.write(tmp_path / "Bob" / "s2.png", b)
    cf.write(tmp_path / "_skipped_" / "s3.png", cf.scene_card([["c"]]))
    cf.write(tmp_path / "chara.png", cf.chara_card(["x"]))                    # not a scene
    shown = {}

    def fake_dialog(title, items, suggest, assign, text=None, cover_ratio=None):
        shown.update(items=sorted((i.name, i.series) for i in items), ratio=cover_ratio, text=text,
                     suggested=suggest("al"))
        s1 = next(i for i in items if i.name == "s1")
        shown["moved"] = assign(s1, "wonder")
        return 1

    with mock.patch.object(gs, "load_authors", return_value=AUTHORS), \
         mock.patch.object(gs, "load_scene_index", side_effect=AssertionError("Manual needs no index")), \
         mock.patch("kkafio.dialogs.manual_group.manual_dialog", fake_dialog):
        _task(tmp_path, UseAutomatic=False, UseManual=True).run()

    assert shown["items"] == [("s1", None), ("s2", "Bob")]
    assert shown["ratio"] == gs.SCENE_COVER_RATIO and shown["text"].untagged == "No author"
    assert shown["suggested"][:2] == ["Alice", "Alucid"]
    assert shown["moved"] == (tmp_path / "Alice" / "s1.png", "Alice")
    cache = json.loads((tmp_path / PNG_CACHE_FILE).read_text())["files"]
    assert str(tmp_path / "Alice" / "s1.png") in cache and str(tmp_path / "s1.png") not in cache


def test_manual_with_no_scenes_does_not_open_the_gallery(tmp_path):
    opened = []
    cf.write(tmp_path / "chara.png", cf.chara_card(["x"]))
    with mock.patch("kkafio.dialogs.manual_group.manual_dialog", lambda *a, **k: opened.append(a)):
        _task(tmp_path, UseAutomatic=False, UseManual=True).run()
    assert opened == []


def test_the_scene_wording_is_complete():
    text = gs.scene_dialog_text()
    assert text.status.format(total=3, untagged=1) == "3 scenes · 1 without an author"
    assert all(getattr(text, f) for f in text.__dataclass_fields__)
