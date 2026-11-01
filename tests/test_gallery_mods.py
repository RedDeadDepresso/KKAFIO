"""The gallery's mod dependencies sidebar: the lookup behind it (kkafio/dialogs/gallery/mods_panel.py)
and the sidebar itself, driven with synthetic events (the GUI part needs a display: xvfb-run is
enough; it is skipped where Tk can't open one)."""

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from card_factory import zipmod
from kkafio.dialogs.gallery import mods_panel as mp
from kkafio.dialogs.gallery.mods_panel import CardMods, ModRow, ModsLookup
from kkafio.dialogs.gallery.viewstate import GalleryViewState
from kkafio.core.session_store import SessionStore


# ---------------------------------------------------------------------------
# the lookup (no GUI)
# ---------------------------------------------------------------------------

def test_google_search_url_quotes_the_guid():
    assert mp.google_search_url("com.gf.acc") == "https://www.google.com/search?q=com.gf.acc"
    assert mp.google_search_url("a b&c") == "https://www.google.com/search?q=a+b%26c"


def test_rows_are_missing_first_then_by_guid_and_counted():
    rows = [ModRow("b", Path("/m/b")), ModRow("Z", None), ModRow("a", Path("/m/a")), ModRow("c", None)]
    ordered = mp.sort_rows(rows)
    assert [r.guid for r in ordered] == ["c", "Z", "a", "b"]
    assert mp.summarize(rows) == (2, 2)


def test_mods_dir_for_prefers_the_configured_folder():
    assert mp.mods_dir_for("  ", None) is None
    assert mp.mods_dir_for("", "G/mods") == Path("G/mods")
    assert mp.mods_dir_for("C/mods", "G/mods") == Path("C/mods")


def test_lookup_finds_installed_mods_and_reports_the_rest(tmp_path):
    zipmod(tmp_path / "sub" / "a.zipmod", "com.have")
    zipmod(tmp_path / "plain.zip", "com.zip")
    lookup = ModsLookup(tmp_path, use_cache=False)
    assert lookup.available
    rows = {r.guid: r.path for r in lookup.resolve(["com.have", "com.zip", "com.gone"])}
    assert rows["com.have"] == tmp_path.resolve() / "sub" / "a.zipmod"
    assert rows["com.zip"] is not None and rows["com.gone"] is None


def test_a_modpack_mod_installed_outside_its_indexed_path_is_still_found(tmp_path):
    zipmod(tmp_path / "Elsewhere" / "x.zipmod", "com.pack")
    with mock.patch.object(mp, "load_modpack_index",
                           return_value={"com.pack": "Sideloader Modpack/x.zipmod"}):
        lookup = ModsLookup(tmp_path, use_cache=False)
        assert lookup.find("com.pack") == tmp_path.resolve() / "Elsewhere" / "x.zipmod"


def test_rows_for_resolves_known_guids_without_a_card_file(tmp_path):
    zipmod(tmp_path / "a.zipmod", "com.have")
    cards = CardMods(ModsLookup(tmp_path, use_cache=False))
    rows = cards.rows_for(["com.have", "com.gone", "com.have"])          # duplicates collapse
    assert [(r.guid, r.path is not None) for r in rows] == [("com.gone", False), ("com.have", True)]
    assert cards.rows_for([]) == []


def test_a_modpack_mod_is_checked_through_the_index_not_by_scanning(tmp_path):
    pack = zipmod(tmp_path / "Sideloader Modpack" / "x.zipmod", "com.pack")
    with mock.patch.object(mp, "load_modpack_index",
                           return_value={"com.pack": "Sideloader Modpack/x.zipmod", "com.pack.gone": "Sideloader Modpack/y.zipmod"}):
        lookup = ModsLookup(tmp_path, use_cache=False)
        assert lookup.find("com.pack") == tmp_path.resolve() / "Sideloader Modpack" / "x.zipmod"
        assert lookup.find("com.pack.gone") is None                 # in the index, not on disk
    assert pack.exists()


def test_without_a_mods_folder_nothing_can_be_said(tmp_path):
    for folder in (None, tmp_path / "missing"):
        lookup = ModsLookup(folder)
        assert not lookup.available
        assert [r.path for r in lookup.resolve(["a"])] == [None]


def test_a_cards_guids_are_parsed_once_while_the_file_is_unchanged(tmp_path):
    zipmod(tmp_path / "mods" / "a.zipmod", "com.have")
    card = tmp_path / "card.png"
    card.write_bytes(b"1")
    calls = []

    def parse(path):
        calls.append(path)
        return ["com.gone", "com.have", "com.gone"]                 # a repeated GUID is listed once

    cards = CardMods(ModsLookup(tmp_path / "mods", use_cache=False))
    first = cards.rows(card, parse)
    assert [(r.guid, r.path is not None) for r in first] == [("com.gone", False), ("com.have", True)]
    cards.rows(card, parse)
    assert len(calls) == 1
    card.write_bytes(b"22")                                         # changed: read again
    cards.rows(card, parse)
    assert len(calls) == 2


def test_a_failing_parser_raises_so_the_window_can_say_so(tmp_path):
    card = tmp_path / "card.png"
    card.write_bytes(b"1")

    def parse(_path):
        raise ValueError("corrupt")

    with pytest.raises(ValueError):
        CardMods(ModsLookup(None)).rows(card, parse)


def test_the_sidebar_state_is_remembered(tmp_path):
    path = tmp_path / "v.json"
    s = GalleryViewState(SessionStore(path, "T"))
    assert s.mods_open is True                                      # open until the user collapses it
    s.remember("chara", {}, mods_open=False)
    assert GalleryViewState(SessionStore(path, "T")).mods_open is False
    s.remember("chara", {})                                         # not told: stays as it was
    assert GalleryViewState(SessionStore(path, "T")).mods_open is False
    path.write_text('{"mods_open": "yes"}', encoding="utf-8")
    assert GalleryViewState(SessionStore(path, "T")).mods_open is True      # junk: the default


def test_the_mods_filter_is_remembered_per_tab(tmp_path):
    from kkafio.dialogs.gallery.viewstate import TabView
    path = tmp_path / "v.json"
    s = GalleryViewState(SessionStore(path, "T"))
    assert s.view("chara").mods == "all"
    s.remember("chara", {"chara": TabView(mods="missing"), "scene": TabView(mods="complete")})
    again = GalleryViewState(SessionStore(path, "T"))
    assert (again.view("chara").mods, again.view("scene").mods, again.view("outfit").mods) == ("missing", "complete", "all")
    path.write_text('{"tabs": {"chara": {"mods": "bogus"}}}', encoding="utf-8")
    assert GalleryViewState(SessionStore(path, "T")).view("chara").mods == "all"


# ---------------------------------------------------------------------------
# the sidebar in the window (needs a display)
# ---------------------------------------------------------------------------

tk = pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")
pytest.importorskip("PIL.ImageTk")
try:
    _probe = tk.Tk()
    _probe.destroy()
except tk.TclError:
    pytest.skip("no display available for Tk", allow_module_level=True)

import test_gallery_gui as g                                        # noqa: E402
from kkafio.dialogs.gallery import mods_sidebar as ms               # noqa: E402
from kkafio.dialogs.gallery import window as mgd                    # noqa: E402


def _run(monkeypatch, tabs, mods, script, state=None):
    errors, holder = [], {}
    real_init = ms.ModsSidebar.__init__

    def init(self, *a, **k):
        real_init(self, *a, **k)
        holder["sb"] = self

    def fake_show(root, focus_widget=None, on_shown=None):
        def go():
            try:
                g.pump(root, 0.6)
                script(root, holder["sb"])
            except BaseException as e:
                errors.append(e)
            finally:
                root.quit()
        g.real_show_and_wait(root, focus_widget, on_shown=lambda: root.after(150, go))

    monkeypatch.setattr(ms.ModsSidebar, "__init__", init)
    monkeypatch.setattr(mgd, "show_and_wait", fake_show)
    monkeypatch.setattr(mgd.GalleryViewState, "from_user_config", classmethod(lambda cls: state or cls()))   # nothing saved
    mgd.gallery_dialog("test", tabs, 0, mods)
    if errors:
        raise errors[0]


def _tab(tmp_path, wanted):
    items = g.make_items(tmp_path / "cards", len(wanted))
    guids = {item.path: w for item, w in zip(items, wanted)}
    return mgd.GalleryTab("Character", items, lambda t: [], g.mover([]), None, mgd.COVER_RATIO, None, "chara",
                          lambda p: guids[p])


def _shown(sb):
    return [sb._canvas.itemcget(i, "text") for i in sb._canvas.find_all() if sb._canvas.type(i) == "text"]


def test_the_sidebar_follows_the_card_under_the_cursor(tmp_path, monkeypatch):
    zipmod(tmp_path / "mods" / "a.zipmod", "com.have")
    mods = CardMods(ModsLookup(tmp_path / "mods", use_cache=False))
    tab = _tab(tmp_path, [["com.have", "com.gone"], []])

    def script(root, sb):
        cv = g.gallery_canvas(root)
        g.show_all(root)
        assert _shown(sb) == ["Select a card to see its mods"]      # nothing under the cursor yet
        g.click_cover(cv, g.texts(cv, "Char 000")[0])
        g.pump(root, 0.8)
        assert [r.guid for r in sb._rows] == ["com.gone", "com.have"]            # missing first
        assert sb._summary.cget("text") == "1 installed · 1 missing"
        assert _shown(sb) == ["com.gone", "com.have"]
        g.click_cover(cv, g.texts(cv, "Char 001")[0])
        g.pump(root, 0.8)
        assert _shown(sb) == ["This card uses no mods"] and sb._summary.cget("text") == ""

    _run(monkeypatch, [tab], mods, script)


def test_icons_search_a_missing_guid_and_reveal_an_installed_mod(tmp_path, monkeypatch):
    zipmod(tmp_path / "mods" / "a.zipmod", "com.have")
    mods = CardMods(ModsLookup(tmp_path / "mods", use_cache=False))
    tab = _tab(tmp_path, [["com.have", "com.gone"]])
    opened, revealed = [], []
    monkeypatch.setattr(ms.webbrowser, "open", opened.append)
    monkeypatch.setattr(ms, "reveal_in_file_manager", revealed.append)

    def script(root, sb):
        cv = g.gallery_canvas(root)
        g.show_all(root)
        g.click_cover(cv, g.texts(cv, "Char 000")[0])
        g.pump(root, 0.8)
        w, rh = sb._canvas.winfo_width(), sb._row_h
        sb._on_click(SimpleNamespace(x=10, y=rh // 2))               # the row, not its icon: nothing
        assert opened == [] and revealed == []
        sb._on_click(SimpleNamespace(x=w - 12, y=rh // 2))           # missing row: its search icon
        assert opened == ["https://www.google.com/search?q=com.gone"]
        sb._on_click(SimpleNamespace(x=w - 12, y=rh + rh // 2))      # installed row: its open icon
        assert revealed == [tmp_path.resolve() / "mods" / "a.zipmod"]

    _run(monkeypatch, [tab], mods, script)


def test_collapsing_hides_the_list_and_stops_reading_cards(tmp_path, monkeypatch):
    mods = CardMods(ModsLookup(tmp_path / "mods", use_cache=False))     # no such folder: nothing is "installed"
    tab = _tab(tmp_path, [["com.a"], ["com.b"]])

    def script(root, sb):
        cv = g.gallery_canvas(root)
        g.show_all(root)
        g.click_cover(cv, g.texts(cv, "Char 000")[0])
        g.pump(root, 0.8)
        assert sb.is_open and sb._panel.winfo_ismapped()
        assert sb._summary.cget("text") == "1 mod(s) · mods folder not found"
        assert sb._canvas.find_all() and not any(sb._canvas.type(i) == "polygon" for i in sb._canvas.find_all())
        sb._toggle.invoke()                                          # collapse
        g.pump(root, 0.2)
        assert not sb.is_open and not sb._panel.winfo_ismapped()
        g.click_cover(cv, g.texts(cv, "Char 001")[0])
        g.pump(root, 0.8)
        assert [r.guid for r in sb._rows] == ["com.a"] or sb._rows == []     # nothing was read meanwhile
        assert "com.b" not in _shown(sb)
        sb._toggle.invoke()                                          # expand: the cursor's card is read now
        g.pump(root, 0.8)
        assert sb.is_open and [r.guid for r in sb._rows] == ["com.b"]

    _run(monkeypatch, [tab], mods, script)


def test_an_unreadable_card_says_so(tmp_path, monkeypatch):
    mods = CardMods(ModsLookup(None))
    items = g.make_items(tmp_path / "cards", 1)

    def boom(_p):
        raise ValueError("corrupt")

    tab = mgd.GalleryTab("Character", items, lambda t: [], g.mover([]), None, mgd.COVER_RATIO, None, "chara", boom)

    def script(root, sb):
        cv = g.gallery_canvas(root)
        g.show_all(root)
        g.click_cover(cv, g.texts(cv, "Char 000")[0])
        g.pump(root, 0.8)
        assert _shown(sb) == ["Could not read this card"]

    _run(monkeypatch, [tab], mods, script)


def _menu(root, label):
    return next(w for w in g.walk(root) if w.__class__.__name__ == "CTkOptionMenu" and label in w.cget("values"))


def test_the_mods_filter_shows_cards_with_all_mods_or_with_missing_ones(tmp_path, monkeypatch):
    zipmod(tmp_path / "mods" / "a.zipmod", "com.have")
    mods = CardMods(ModsLookup(tmp_path / "mods", use_cache=False))
    tab = _tab(tmp_path, [["com.have"], ["com.have", "com.gone"], []])       # Char 000, 001, 002

    def names(root):
        cv = g.gallery_canvas(root)
        return sorted(cv.itemcget(i, "text") for i in cv.find_all()
                      if cv.type(i) == "text" and cv.itemcget(i, "text").startswith("Char "))

    def script(root, sb):
        g.show_all(root)
        menu = _menu(root, "Missing mods")
        assert len(names(root)) == 3
        menu.set("Missing mods"); menu.cget("command")("Missing mods")
        g.pump(root, 1.2)
        assert names(root) == ["Char 001"]
        menu.set("All mods installed"); menu.cget("command")("All mods installed")
        g.pump(root, 0.8)
        assert names(root) == ["Char 000", "Char 002"]            # a card without mods is complete
        menu.set("All cards"); menu.cget("command")("All cards")
        g.pump(root, 0.8)
        assert len(names(root)) == 3

    _run(monkeypatch, [tab], mods, script)


def test_the_mods_filter_reads_the_guid_index_and_only_parses_cards_missing_from_it(tmp_path, monkeypatch):
    zipmod(tmp_path / "mods" / "a.zipmod", "com.have")
    mods = CardMods(ModsLookup(tmp_path / "mods", use_cache=False))
    items = g.make_items(tmp_path / "cards", 3)                          # Char 000, 001, 002
    index = {str(items[0].path): ["com.have"], str(items[1].path): ["com.have", "com.gone"]}
    parsed = []

    def parse(path):                                                     # only the card the index lacks
        parsed.append(path)
        return ["com.gone"]

    tab = mgd.GalleryTab("Character", items, lambda t: [], g.mover([]), None, mgd.COVER_RATIO, None, "chara",
                         parse, lambda: index)

    def script(root, sb):
        g.show_all(root)
        menu = _menu(root, "Missing mods")
        menu.set("Missing mods"); menu.cget("command")("Missing mods")
        g.pump(root, 1.2)
        cv = g.gallery_canvas(root)
        shown = sorted(cv.itemcget(i, "text") for i in cv.find_all()
                       if cv.type(i) == "text" and cv.itemcget(i, "text").startswith("Char "))
        assert shown == ["Char 001", "Char 002"]
        assert parsed == [items[2].path]

    _run(monkeypatch, [tab], mods, script)


def test_there_is_no_mods_filter_without_a_mods_folder(tmp_path, monkeypatch):
    tab = _tab(tmp_path, [["com.a"]])

    def script(root, sb):
        assert not [w for w in g.walk(root) if w.__class__.__name__ == "CTkOptionMenu"
                    and "Missing mods" in w.cget("values")]

    _run(monkeypatch, [tab], CardMods(ModsLookup(None)), script)


def test_the_mods_filter_is_restored_and_saved(tmp_path, monkeypatch):
    from kkafio.dialogs.gallery.viewstate import TabView
    zipmod(tmp_path / "mods" / "a.zipmod", "com.have")
    mods = CardMods(ModsLookup(tmp_path / "mods", use_cache=False))
    tab = _tab(tmp_path, [["com.have"], ["com.gone"]])
    state = GalleryViewState()
    state.tabs["chara"] = TabView(mods="missing")
    saved = {}
    monkeypatch.setattr(GalleryViewState, "remember", lambda self, last, views, mods_open=None:
                        saved.update(views) or True)

    def script(root, sb):
        cv = g.gallery_canvas(root)
        g.show_all(root)
        g.pump(root, 1.0)
        menu = _menu(root, "Missing mods")
        assert menu.get() == "Missing mods"                         # opened with the saved filter, already applied
        assert sorted(cv.itemcget(i, "text") for i in cv.find_all()
                      if cv.type(i) == "text" and cv.itemcget(i, "text").startswith("Char ")) == ["Char 001"]
        menu.set("All cards"); menu.cget("command")("All cards")

    _run(monkeypatch, [tab], mods, script, state)
    assert saved["chara"].mods == "all"


def test_the_sidebar_uses_the_guid_index_before_parsing(tmp_path):
    import time
    card = tmp_path / "card.png"
    card.write_bytes(b"1")
    other = tmp_path / "other.png"
    other.write_bytes(b"1")
    calls = []

    def parse(path):
        calls.append(path)
        return ["com.parsed"]

    cards = CardMods(ModsLookup(None))
    assert [r.guid for r in cards.rows(other, parse)] == ["com.parsed"]      # no index yet: parsed
    cards.set_index({str(card): ["com.b", "com.a", "com.b"]}, time.time_ns())
    assert [r.guid for r in cards.rows(card, parse)] == ["com.a", "com.b"]   # from the index
    assert calls == [other]
    assert [r.guid for r in cards.rows(tmp_path / "other.png", parse)] == ["com.parsed"]
    assert calls == [other]                                                  # memory cache still works


def test_the_sidebar_parses_a_card_changed_after_the_index_was_read(tmp_path):
    card = tmp_path / "card.png"
    card.write_bytes(b"1")
    cards = CardMods(ModsLookup(None))
    cards.set_index({str(card): ["com.old"]}, read_at_ns=0)                  # the file is newer than the index
    assert [r.guid for r in cards.rows(card, lambda _p: ["com.new"])] == ["com.new"]
