"""Group Coordinates' manual gallery (opened from Run Gallery): the gallery's data (scan, folder names,
suggestions, moving) and the coordinate wording. Group Coordinates itself always groups automatically.
The gallery itself is shared with Group Characters (test_manual_group_dialog_gui.py)."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.core.config import _build_task_config
from kkafio.tasks import group_coordinates as gc


def _png(path: Path, data: bytes = b"png") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# --- names and suggestions -----------------------------------------------------------------------------

def test_safe_folder_name_keeps_dots_inside_a_typed_name():
    assert gc.safe_folder_name("Mr. X") == "Mr. X"                 # not cut like Path.stem would
    assert gc.safe_folder_name('a<b>:c') == "a_b__c"
    assert gc.safe_folder_name("  ..  ") == ""
    assert gc.safe_folder_name("CON") == "_CON"
    assert gc.folder_name_for(Path("x/Rin Tohsaka.png")) == "Rin Tohsaka"


def test_chara_folder_names_are_the_filenames_without_png(tmp_path):
    _png(tmp_path / "female" / "Rin.png")
    _png(tmp_path / "female" / "Fate" / "Saber.png")
    _png(tmp_path / "male" / "rin.png")                            # same name, other case: one entry
    _png(tmp_path / "female" / "notes.txt")
    known = gc.chara_folder_names([tmp_path / "female", tmp_path / "male"])
    assert sorted(known.values(), key=str.casefold) == ["Rin", "Saber"]
    assert known["rin"] == "Rin"                                   # first by filename wins


def test_existing_group_folders_skip_files_and_underscore_folders(tmp_path):
    (tmp_path / "Rin").mkdir()
    (tmp_path / "_duplicates_").mkdir()
    (tmp_path / "UNKNOWN_1").mkdir()
    _png(tmp_path / "loose.png")
    assert gc.existing_group_folders(tmp_path) == {"rin": "Rin", "unknown_1": "UNKNOWN_1"}
    assert gc.existing_group_folders(tmp_path / "missing") == {}


def test_suggest_prefix_matches_first_then_the_rest_and_is_capped():
    known = {n.casefold(): n for n in ("Saber", "Rin", "Sakura", "Illya", "Rinko")}
    suggest, _ = gc.manual_callbacks(known, {})
    assert suggest("") == ["Illya", "Rin", "Rinko", "Saber", "Sakura"]
    assert suggest("in") == ["Rin", "Rinko"]                       # substring matches count too
    assert suggest("ri")[:2] == ["Rin", "Rinko"]
    assert suggest("SA") == ["Saber", "Sakura"]
    assert suggest("zzz") == []
    capped, _ = gc.manual_callbacks({f"c{i}": f"C{i}" for i in range(20)}, {}, max_suggestions=5)
    assert len(capped("")) == 5


# --- scanning and moving -------------------------------------------------------------------------------

def test_scan_lists_coordinates_with_their_folder(tmp_path):
    a = _png(tmp_path / "loose.png")
    b = _png(tmp_path / "Rin" / "outfit.png")
    skipped = _png(tmp_path / "_duplicates_" / "dup.png")
    notcoord = _png(tmp_path / "other.png")
    keys = {str(a): "k1", str(b): "k2", str(skipped): "k3", str(notcoord): ""}
    with mock.patch.object(gc, "build_coord_cache", return_value=keys):
        items = gc.scan_manual_items(tmp_path, True)
    assert sorted((i.name, i.series, i.path) for i in items) == [("loose", None, a), ("outfit", "Rin", b)]
    assert all(i.root == tmp_path and i.size == 3 for i in items)


def _item(coord_dir: Path, rel: str):
    path = _png(coord_dir / rel)
    parts = Path(rel).parts
    return SimpleNamespace(path=path, root=coord_dir, name=path.stem, series=parts[0] if len(parts) > 1 else None)


def test_assign_moves_into_the_character_folder_keeps_spelling_and_records_it(tmp_path):
    known, moves = {"rin": "Rin"}, {}
    _s, assign = gc.manual_callbacks(known, moves)
    item = _item(tmp_path, "a.png")
    dest, folder = assign(item, "  rIN ")                          # spelled like the character's filename
    assert (dest, folder) == (tmp_path / "Rin" / "a.png", "Rin") and dest.exists() and not item.path.exists()
    assert moves == {str(tmp_path / "a.png"): str(dest)}

    second = _item(tmp_path, "b.png")
    assert assign(second, "New Girl") == (tmp_path / "New Girl" / "b.png", "New Girl")
    assert known["new girl"] == "New Girl"                         # the new folder is suggested from now on


def test_assign_collision_already_there_and_unusable_names(tmp_path):
    moves = {}
    _s, assign = gc.manual_callbacks({}, moves)
    _png(tmp_path / "Rin" / "a.png", b"old")
    clash = _item(tmp_path, "a.png")
    dest, _ = assign(clash, "Rin")
    assert dest == tmp_path / "Rin" / "a_1.png"                    # a taken name is numbered
    assert (tmp_path / "Rin" / "a.png").read_bytes() == b"old"

    inside = _item(tmp_path, "Rin/c.png")
    before = dict(moves)
    assert assign(inside, "rin") == (inside.path, "Rin") and moves == before     # already in that folder
    assert assign(_item(tmp_path, "d.png"), "...") is None
    assert assign(_item(tmp_path, "e.png"), "   ") is None


def test_assign_reports_a_failed_move(tmp_path):
    _s, assign = gc.manual_callbacks({}, {})
    item = _item(tmp_path, "a.png")
    with mock.patch.object(gc, "_move_unique", side_effect=OSError("locked")):
        assert assign(item, "Rin") is None


# --- the task: always automatic, no Mode option -------------------------------------------------------

def test_there_is_no_mode_option_any_more():
    cfg = _build_task_config("GroupCoordinates", True, {})
    assert "UseAutomatic" not in cfg and "UseManual" not in cfg
    # a stale Mode value from an old GUI config is ignored
    cfg = _build_task_config("GroupCoordinates", True,
                             {"GroupCoordinatesMode": {"type": "checkbox", "caseNames": ["Manual"]}})
    assert "UseAutomatic" not in cfg and "UseManual" not in cfg


def test_interface_json_has_no_mode_option():
    interface = json.loads((Path(__file__).resolve().parents[1] / "interface.json").read_text(encoding="utf-8"))
    assert "GroupCoordinatesMode" not in interface["option"]
    task = next(t for t in interface["task"] if t["name"] == "GroupCoordinates")
    assert "GroupCoordinatesMode" not in task["option"]


def _task(tmp_path, **cfg):
    config = SimpleNamespace(group_coordinates={"CharaDir": str(tmp_path), "CoordDir": str(tmp_path), **cfg},
                             game_path={})
    return gc.GroupCoordinates(config, None)


def test_run_always_groups_automatically(tmp_path):
    calls = []
    # old configs may still carry the removed keys: they change nothing
    for cfg in ({}, {"UseAutomatic": False, "UseManual": True}):
        with mock.patch.object(gc, "resolve_chara_dirs", return_value=[tmp_path]), \
             mock.patch.object(gc.GroupCoordinates, "_run_automatic", lambda self, *a: calls.append("auto")):
            _task(tmp_path, **cfg).run()
    assert calls == ["auto", "auto"]


def test_gallery_tab_opens_with_the_cards_and_updates_the_caches(tmp_path):
    chara, coord = tmp_path / "chara", tmp_path / "coord"
    _png(chara / "Rin.png")
    _png(chara / "Fate" / "Saber.png")
    loose = _png(coord / "a.png")
    (coord / "Old Folder").mkdir()

    with mock.patch.object(gc, "build_coord_cache", return_value={str(loose): "k"}), \
         mock.patch.object(gc, "_update_coord_cache") as update:
        tab = gc.coordinate_gallery_tab([chara], coord, True)
        try:
            names = [i.name for i in tab.items]
            suggested = tab.suggest("")
            moved = tab.assign(tab.items[0], "rin")
        finally:
            tab.finish()
    assert names == ["a"]
    assert suggested == ["Old Folder", "Rin", "Saber"]             # existing folders + chara filenames
    assert moved == (coord / "Rin" / "a.png", "Rin")
    assert tab.text.untagged == "No character"                     # the coordinate wording, not the series one
    update.assert_called_once_with(coord, {str(loose): str(coord / "Rin" / "a.png")})


def test_gallery_tab_with_no_coordinates_is_empty(tmp_path):
    with mock.patch.object(gc, "build_coord_cache", return_value={}):
        tab = gc.coordinate_gallery_tab([tmp_path], tmp_path, True)
        tab.finish()
    assert tab.items == []


def test_the_coordinate_wording_is_complete_and_formats():
    text = gc.coordinate_dialog_text()
    assert text.status.format(total=3, untagged=1) == "3 coordinates · 1 without a character"
    assert text.moved.format(name="a", series="Rin") == "Moved a → Rin"
    assert "{count}" in text.multi_prompt and "{failed}" in text.multi_moved_partial
    assert all(getattr(text, f) for f in text.__dataclass_fields__)
