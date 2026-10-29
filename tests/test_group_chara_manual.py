"""Group Characters, Manual mode: the gallery's data (scan, series folder names, suggestions, moving),
the Mode option (config + which workflows run) and the gallery model. The window itself is in
test_manual_group_dialog_gui.py."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from kkafio.indices import agv
from kkafio.indices.text import norm
from kkafio.cards.chara.chara_key_cache import CACHE_FILE, CharaKeyCache
from kkafio.core.config import _build_task_config
from kkafio.dialogs.manual_group import (
    SORT_CREATED, SORT_FILENAME, SORT_NAME, SORT_SIZE, SORT_UPDATED, VIEW_SERIES, VIEW_UNTAGGED, CharaItem, GalleryModel, ellipsize)
from kkafio.tasks import group_chara

from test_agv import _index
from test_chara_key_cache import _cards, _file, fakes


def _key(card):
    """A key in the real make_key() layout: last | first | nick | personality | hair."""
    return f"{card.display_name()} |  | {card.display_name()} | calm | black hair"


@contextmanager
def manual_fakes():
    with fakes(), mock.patch.object(group_chara, "make_key", _key):
        yield


@pytest.fixture
def agv_file(tmp_path):
    p = tmp_path / "agv_character_index.json"
    p.write_text(json.dumps(_index(), ensure_ascii=False), encoding="utf-8")
    agv.set_index_path(p)
    yield
    agv.set_index_path(None)


def _item(name, series=None, size=1, mtime=0.0, folder=Path("/chara")):
    return CharaItem(path=folder / f"{name}.png", root=folder, name=name, series=series, size=size, mtime=mtime)


# ---------------------------------------------------------------------------
# the gallery model
# ---------------------------------------------------------------------------

def test_model_views_counts_and_series_list():
    m = GalleryModel([_item("Rin", "Fate"), _item("Saber", "fate"), _item("Asuka"), _item("Kirito", "SAO")])
    assert m.counts() == (4, 1)
    assert m.series_list() == [("Fate", 2), ("SAO", 1)]               # Fate / fate are one series
    assert m.series_list("sa") == [("SAO", 1)]
    m.set_view(VIEW_UNTAGGED)
    assert [i.name for i in m.visible()] == ["Asuka"]
    m.set_view(VIEW_SERIES, "FATE")
    assert {i.name for i in m.visible()} == {"Rin", "Saber"}
    assert m.series_count("fate") == 2 and m.series_count("nope") == 0


def test_model_search_looks_at_name_filename_and_series_and_sorts():
    a = _item("Rin", "Fate", size=30, mtime=1)
    b = _item("Asuka", None, size=10, mtime=3)
    c = _item("Zed", "Other", size=20, mtime=2)
    c.path = c.path.with_name("rin_copy.png")
    m = GalleryModel([a, b, c])
    assert [i.name for i in m.visible()] == ["Asuka", "Zed", "Rin"]   # default: updated date, newest first
    m.sort_key, m.descending = SORT_NAME, False
    assert [i.name for i in m.visible()] == ["Asuka", "Rin", "Zed"]
    m.sort_key, m.descending = SORT_SIZE, True
    assert [i.name for i in m.visible()] == ["Rin", "Zed", "Asuka"]
    m.search = "RIN"                                                    # name of a, filename of c
    assert {i.name for i in m.visible()} == {"Rin", "Zed"}
    m.search = "other"                                                  # series of c
    assert [i.name for i in m.visible()] == ["Zed"]
    m.search = ""
    m.sort_key, m.descending = SORT_FILENAME, False
    assert [i.path.name for i in m.visible()] == ["Asuka.png", "Rin.png", "rin_copy.png"]
    a.ctime, b.ctime, c.ctime = 30, 10, 20
    m.sort_key, m.descending = SORT_CREATED, True
    assert [i.name for i in m.visible()] == ["Rin", "Zed", "Asuka"]
    c.ctime = 0                                                         # unknown creation time: falls back to updated
    assert [i.name for i in m.visible()] == ["Rin", "Asuka", "Zed"]
    m.sort_key = SORT_UPDATED


def test_model_assigned_updates_the_item(tmp_path):
    item = _item("Rin")
    m = GalleryModel([item])
    m.assigned(item, tmp_path / "Fate" / "Rin.png", "Fate")
    assert item.series == "Fate" and item.path == tmp_path / "Fate" / "Rin.png"
    assert m.counts() == (1, 0)


def test_ellipsize():
    width = lambda s: len(s)
    assert ellipsize(width, "abc", 5) == "abc"
    assert ellipsize(width, "abcdefgh", 5) == "abcd…"
    assert ellipsize(width, "abc", 0) == "…"


# ---------------------------------------------------------------------------
# series folder names
# ---------------------------------------------------------------------------

def test_resolve_series_folder_is_safe_and_reuses_existing_spelling():
    known = {"fate": "Fate", "genshin impact": "Genshin Impact"}
    assert group_chara.resolve_series_folder("  fate ", known, None) == "Fate"
    assert group_chara.resolve_series_folder("New Series", known, None) == "New Series"
    assert group_chara.resolve_series_folder("a/b:c", known, None) == "abc"
    for bad in ("", "   ", "..", ".", "CON", "nul.txt", "???"):
        assert group_chara.resolve_series_folder(bad, known, None) == "", bad


def test_resolve_series_folder_snaps_to_the_index_title(agv_file):
    known = {}
    assert group_chara.resolve_series_folder("sword art online", known, "en_us") == "Sword Art Online"
    assert group_chara.resolve_series_folder("SWORD-ART ONLINE", known, "en_us") == "Sword Art Online"
    assert group_chara.resolve_series_folder("My Own Series", known, "en_us") == "My Own Series"
    # no language = the user chose not to use the indexes: the text is kept
    assert group_chara.resolve_series_folder("sword art online", known, None) == "sword art online"
    # "/" is not allowed in a folder name, exactly as for the LLM's answers
    assert group_chara.resolve_series_folder("fate stay night", known, "en_us") == "Fatestay night"


# ---------------------------------------------------------------------------
# suggestions and moving
# ---------------------------------------------------------------------------

def test_suggest_existing_folders_first_then_the_index(agv_file):
    known = {"fate grand order": "Fate Grand Order", "sao": "SAO"}
    suggest, _assign = group_chara.manual_callbacks(known, CharaKeyCache([], False), "en_us")
    assert suggest("") == ["Fate Grand Order", "SAO"]                   # nothing typed: the existing folders
    assert suggest("fate") == ["Fate Grand Order", "Fatestay night"]    # folder, then the index (as a folder name)
    assert suggest("sword") == ["Sword Art Online"]
    assert suggest("zzz") == []
    known["fatestay night"] = "Fatestay night"                          # once the folder exists it isn't listed twice
    assert suggest("fate") == ["Fate Grand Order", "Fatestay night"]


def test_suggest_without_a_language_only_lists_folders(agv_file):
    suggest, _ = group_chara.manual_callbacks({"fate": "Fate"}, CharaKeyCache([], False), None)
    assert suggest("sword") == [] and suggest("fa") == ["Fate"]


def test_suggest_is_capped():
    known = {f"s{i:02}": f"S{i:02}" for i in range(30)}
    suggest, _ = group_chara.manual_callbacks(known, CharaKeyCache([], False), None, max_suggestions=5)
    assert len(suggest("")) == 5


def test_assign_moves_into_a_new_series_folder_and_keeps_the_cache(tmp_path):
    cards = _cards(tmp_path, a="Rin", b="Saber")
    with manual_fakes():
        cache = CharaKeyCache([tmp_path], True, "GROUP")
        items = group_chara.scan_manual_items([tmp_path], False, cache)
        known = group_chara.existing_series([tmp_path])
        _suggest, assign = group_chara.manual_callbacks(known, cache, None)
        rin = next(i for i in items if i.name == "Rin")

        new_path, folder = assign(rin, "Fate")
        assert (new_path, folder) == (tmp_path / "Fate" / "a.png", "Fate")
        assert new_path.exists() and not cards["a"].exists()
        assert known == {"fate": "Fate"}                                  # a later "fate" reuses it

        saber = next(i for i in items if i.name == "Saber")
        assert assign(saber, "FATE")[0] == tmp_path / "Fate" / "b.png"

        cache.save()
        files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
        assert files[str(new_path)]["key"] == "Rin |  | Rin | calm | black hair"   # the entry followed the card
        assert str(cards["a"]) not in files


def test_assign_collision_same_folder_and_unusable_names(tmp_path):
    _cards(tmp_path, a="Rin")
    _file(tmp_path / "Fate" / "a.png", b"CHARA|Someone")                  # name already taken there
    with manual_fakes():
        cache = CharaKeyCache([tmp_path], True, "GROUP")
        items = group_chara.scan_manual_items([tmp_path], True, cache)
        rin = next(i for i in items if i.name == "Rin")
        assert rin.series is None
        _s, assign = group_chara.manual_callbacks(group_chara.existing_series([tmp_path]), cache, None)

        assert assign(rin, "..") is None and assign(rin, "   ") is None
        assert rin.path.exists()                                          # nothing moved

        new_path, _ = assign(rin, "fate")                                 # existing folder, other case
        assert new_path == tmp_path / "Fate" / "a_1.png"
        rin.path = new_path
        assert assign(rin, "Fate") == (new_path, "Fate")                  # already there: untouched


def test_assign_reports_a_failed_move(tmp_path):
    _cards(tmp_path, a="Rin")
    with manual_fakes():
        cache = CharaKeyCache([tmp_path], False)
        rin = group_chara.scan_manual_items([tmp_path], False, cache)[0]
        _s, assign = group_chara.manual_callbacks({}, cache, None)
        with mock.patch.object(group_chara.shutil, "move", side_effect=OSError("locked")):
            assert assign(rin, "Fate") is None
        assert rin.path.exists()


# ---------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------

def test_scan_series_from_folders_and_skipped_ones(tmp_path):
    _cards(tmp_path, top="Rin")
    _file(tmp_path / "Fate" / "x.png", b"CHARA|Saber")
    _file(tmp_path / "Fate" / "deeper" / "y.png", b"CHARA|Archer")
    _file(tmp_path / "_duplicates_" / "z.png", b"CHARA|Dup")
    _file(tmp_path / ".hidden" / "w.png", b"CHARA|Hidden")
    _file(tmp_path / "pic.png", b"just a picture")
    with manual_fakes():
        cache = CharaKeyCache([tmp_path], False)
        top_only = group_chara.scan_manual_items([tmp_path], False, cache)
        assert [(i.name, i.series) for i in top_only] == [("Rin", None)]

        everything = group_chara.scan_manual_items([tmp_path], True, cache)
        assert sorted((i.name, i.series) for i in everything) == [
            ("Archer", "Fate"), ("Rin", None), ("Saber", "Fate")]         # _duplicates_, dot folders, non-cards out
        assert all(i.root == tmp_path and i.size > 0 and i.mtime > 0 for i in everything)
    assert group_chara.existing_series([tmp_path]) == {"fate": "Fate"}


def test_scan_name_falls_back_to_nickname_then_filename(tmp_path):
    _file(tmp_path / "a.png", b"CHARA|x")
    with mock.patch.object(group_chara, "get_card_type", lambda raw: "KK"), \
         mock.patch.object(group_chara, "KoikatuCharaData", mock.MagicMock()), \
         mock.patch.object(group_chara, "make_key", side_effect=[" |  | Nick | calm | black hair"]), \
         mock.patch.object(group_chara, "CHARA_CARD_TYPES", ("KK",)):
        (item,) = group_chara.scan_manual_items([tmp_path], False, CharaKeyCache([], False))
    assert item.name == "Nick"


# ---------------------------------------------------------------------------
# the Mode option
# ---------------------------------------------------------------------------

def _cfg(modes):
    values = {} if modes is None else {"GroupCharaMode": {"type": "checkbox", "caseNames": modes}}
    return _build_task_config("GroupChara", True, values)


def test_mode_option_to_config():
    assert (_cfg(None)["UseLlm"], _cfg(None)["UseManual"]) == (True, False)           # unchanged behaviour
    assert (_cfg(["LLM"])["UseLlm"], _cfg(["LLM"])["UseManual"]) == (True, False)
    assert (_cfg(["Manual"])["UseLlm"], _cfg(["Manual"])["UseManual"]) == (False, True)
    assert (_cfg(["LLM", "Manual"])["UseLlm"], _cfg(["LLM", "Manual"])["UseManual"]) == (True, True)
    assert (_cfg([])["UseLlm"], _cfg([])["UseManual"]) == (False, False)


def _task(tmp_path, **cfg):
    config = SimpleNamespace(group_chara=cfg, game_path={})
    return group_chara.GroupChara(config, None)


def _run(task, tmp_path, llm_result=True):
    calls = []
    with mock.patch.object(group_chara, "resolve_chara_dirs", return_value=[tmp_path]), \
         mock.patch.object(group_chara.hint_language, "resolve", return_value="en_us"), \
         mock.patch.object(group_chara.GroupChara, "_run_llm",
                           lambda self, f, h: calls.append("llm") or llm_result), \
         mock.patch.object(group_chara.GroupChara, "_run_manual",
                           lambda self, f, h: calls.append("manual")):
        task.run()
    return calls


def test_run_dispatches_on_the_modes(tmp_path):
    assert _run(_task(tmp_path), tmp_path) == ["llm"]                                  # default: as before
    assert _run(_task(tmp_path, UseLlm=True, UseManual=False), tmp_path) == ["llm"]
    assert _run(_task(tmp_path, UseLlm=False, UseManual=True), tmp_path) == ["manual"]
    assert _run(_task(tmp_path, UseLlm=True, UseManual=True), tmp_path) == ["llm", "manual"]
    assert _run(_task(tmp_path, UseLlm=False, UseManual=False), tmp_path) == []        # nothing selected


def test_with_both_modes_a_cancelled_llm_run_does_not_open_the_gallery(tmp_path):
    assert _run(_task(tmp_path, UseLlm=True, UseManual=True), tmp_path, llm_result=False) == ["llm"]


def test_run_manual_opens_the_gallery_and_saves_the_cache(tmp_path):
    _cards(tmp_path, a="Rin")
    shown = {}

    def fake_dialog(title, items, suggest, assign):
        shown["names"] = [i.name for i in items]
        shown["moved"] = assign(items[0], "Fate")
        return 1

    task = _task(tmp_path, UseLlm=False, UseManual=True)
    with manual_fakes(), \
         mock.patch.object(group_chara, "resolve_chara_dirs", return_value=[tmp_path]), \
         mock.patch.object(group_chara.hint_language, "resolve", return_value=None), \
         mock.patch("kkafio.dialogs.manual_group.manual_dialog", fake_dialog):
        task.run()
    assert shown["names"] == ["Rin"] and shown["moved"][1] == "Fate"
    assert (tmp_path / "Fate" / "a.png").exists()
    files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
    assert str(tmp_path / "Fate" / "a.png") in files                                  # cache saved after the moves


def test_run_manual_with_no_cards_does_not_open_the_gallery(tmp_path):
    task = _task(tmp_path, UseLlm=False, UseManual=True)
    opened = []
    with manual_fakes(), \
         mock.patch.object(group_chara, "resolve_chara_dirs", return_value=[tmp_path]), \
         mock.patch.object(group_chara.hint_language, "resolve", return_value=None), \
         mock.patch("kkafio.dialogs.manual_group.manual_dialog", lambda *a: opened.append(a)):
        task.run()
    assert opened == []


def test_round_corners_and_polygon_points():
    from PIL import Image
    from kkafio.dialogs.manual_group import round_corners, rounded_rect_points
    out = round_corners(Image.new("RGB", (60, 80), (200, 10, 10)), 12)
    assert out.mode == "RGBA" and out.size == (60, 80)
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((59, 79))[3] == 0     # corners cut away
    assert out.getpixel((30, 40)) == (200, 10, 10, 255) and out.getpixel((30, 0))[3] == 255
    pts = rounded_rect_points(0, 0, 10, 20, 3)
    assert len(pts) == 24 and min(pts[0::2]) == 0 and max(pts[0::2]) == 10 and max(pts[1::2]) == 20


def test_manual_mode_always_includes_subfolders(tmp_path):
    _cards(tmp_path, top="Rin")
    _file(tmp_path / "Fate" / "x.png", b"CHARA|Saber")
    seen = {}

    def fake_dialog(title, items, suggest, assign):
        seen["items"] = sorted((i.name, i.series) for i in items)
        return 0

    task = _task(tmp_path, UseLlm=False, UseManual=True, IncludeSubfolders=False)     # the option is off
    with manual_fakes(), \
         mock.patch.object(group_chara, "resolve_chara_dirs", return_value=[tmp_path]), \
         mock.patch.object(group_chara.hint_language, "resolve", return_value=None), \
         mock.patch("kkafio.dialogs.manual_group.manual_dialog", fake_dialog):
        task.run()
    assert seen["items"] == [("Rin", None), ("Saber", "Fate")]


def test_maximize_uses_zoomed_state_then_the_x11_attribute():
    import tkinter as tk
    from kkafio.dialogs.common import maximize

    class Win:
        def __init__(self, zoomed_ok):
            self.calls, self.zoomed_ok = [], zoomed_ok

        def wm_state(self, s):
            self.calls.append(("state", s))
            if not self.zoomed_ok:
                raise tk.TclError("bad argument")

        def wm_attributes(self, *a):
            self.calls.append(("attr",) + a)

    win = Win(True)
    maximize(win)
    assert win.calls == [("state", "zoomed")]
    win = Win(False)
    maximize(win)
    assert win.calls == [("state", "zoomed"), ("attr", "-zoomed", True)]

    class Neither(Win):
        def wm_attributes(self, *a):
            raise tk.TclError("nope")
    maximize(Neither(False))                                                           # no error
