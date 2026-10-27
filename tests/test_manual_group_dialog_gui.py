"""The Manual-mode gallery window, driven with synthetic events. Needs a display (xvfb-run is
enough); skipped where Tk can't open one."""

import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")
pytest.importorskip("PIL.ImageTk")

try:                                    # is there a display at all?
    _probe = tk.Tk()
    _probe.destroy()
except tk.TclError:
    pytest.skip("no display available for Tk", allow_module_level=True)

from PIL import Image

from kkafio.system import manual_group_dialog as mgd
from kkafio.system.dialog_common import show_and_wait as real_show_and_wait


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_items(folder: Path, count: int, tagged: dict[int, str] | None = None) -> list[mgd.CharaItem]:
    items = []
    for i in range(count):
        series = (tagged or {}).get(i)
        path = folder / (series or "") / f"card_{i:03}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (252, 352), (i * 7 % 256, 90, 160)).save(path)
        items.append(mgd.CharaItem(path, folder, f"Char {i:03}", series, path.stat().st_size, 1_700_000_000 + i))
    return items


def mover(calls: list):
    """assign(): records the call and really moves the card, like group_chara.manual_callbacks."""
    def assign(item, text):
        calls.append((item.name, text))
        if not text.strip("."):
            return None
        dest = item.root / text / item.path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        item.path.rename(dest)
        return dest, text
    return assign


def pump(root, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def walk(w):
    yield w
    for child in w.winfo_children():
        yield from walk(child)


def gallery_canvas(root):
    return max((w for w in walk(root) if isinstance(w, tk.Canvas)), key=lambda w: w.winfo_width() * w.winfo_height())


def texts(cv, text):
    return [i for i in cv.find_all() if cv.type(i) == "text" and cv.itemcget(i, "text") == text]


def first_unknown(cv):
    """The "Unknown series" text of the top-left tile (item order on the canvas isn't tile order)."""
    return min(texts(cv, "Unknown series"), key=lambda i: (cv.bbox(i)[1], cv.bbox(i)[0]))


def click(cv, item_id):
    x1, y1, x2, y2 = cv.bbox(item_id)
    cv.event_generate("<Button-1>", x=int((x1 + x2) / 2 - cv.canvasx(0)), y=int((y1 + y2) / 2 - cv.canvasy(0)))


def editor(cv):
    found = [w for w in cv.winfo_children() if isinstance(w, tk.Entry)]
    return found[0] if found else None


def suggestions(cv):
    found = [w for w in cv.winfo_children() if isinstance(w, tk.Listbox)]
    return list(found[0].get(0, "end")) if found else []


def type_into(entry, text):
    entry.focus_force()
    entry.update()
    entry.delete(0, "end")
    entry.insert(0, text)
    entry.event_generate("<KeyRelease>", keysym="x")


def label_text(root, start):
    return [w.cget("text") for w in walk(root) if w.__class__.__name__ == "CTkLabel"
            and str(w.cget("text")).startswith(start)]


def button(root, start):
    return next(w for w in walk(root) if w.__class__.__name__ == "CTkButton" and str(w.cget("text")).startswith(start))


def run_dialog(monkeypatch, items, suggest, assign, script):
    """Open the gallery; `script(root)` runs once it is up (and must not close it)."""
    errors = []

    def fake_show(root, focus_widget=None, on_shown=None):
        def go():
            try:
                pump(root, 0.6)
                script(root)
            except BaseException as e:               # reported after the window is gone
                errors.append(e)
            finally:
                root.quit()
        real_show_and_wait(root, focus_widget, on_shown=lambda: root.after(150, go))

    monkeypatch.setattr(mgd, "show_and_wait", fake_show)
    moved = mgd.manual_dialog("test", items, suggest, assign)
    if errors:
        raise errors[0]
    return moved


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_tiles_show_name_unknown_series_and_covers_even_when_they_load_late(tmp_path, monkeypatch):
    items = make_items(tmp_path, 12, tagged={0: "Fate"})
    real = mgd.load_cover
    monkeypatch.setattr(mgd, "load_cover", lambda p: (time.sleep(0.2), real(p))[1])   # slower than the first draw

    def script(root):
        cv = gallery_canvas(root)
        pump(root, 1.5)
        assert len(texts(cv, "Unknown series")) == 11 and texts(cv, "Fate")
        assert texts(cv, "Char 011")                                  # newest first by default
        images = [i for i in cv.find_all() if cv.type(i) == "image"]
        assert len(images) == 12 and all(cv.itemcget(i, "image") for i in images)    # every cover arrived
        assert label_text(root, "12 characters") == ["12 characters · 11 unknown series"]

    assert run_dialog(monkeypatch, items, lambda t: [], mover([]), script) == 0


def test_click_type_pick_a_suggestion_and_enter_moves_the_card(tmp_path, monkeypatch):
    items = make_items(tmp_path, 4)
    calls = []

    def script(root):
        cv = gallery_canvas(root)
        click(cv, first_unknown(cv))
        pump(root, 0.2)
        entry = editor(cv)
        assert entry is not None and entry.get() == ""
        type_into(entry, "fa")
        pump(root, 0.2)
        assert suggestions(cv) == ["Fate", "Fate Grand Order"]
        entry.event_generate("<Down>")
        entry.event_generate("<Down>")
        entry.event_generate("<Return>")
        pump(root, 0.4)
        assert calls == [("Char 003", "Fate Grand Order")]            # the 2nd suggestion, not the typed "fa"
        assert editor(cv) is None
        assert texts(cv, "Fate Grand Order")                           # the tile now shows its series
        assert label_text(root, "Moved") == ["Moved Char 003 → Fate Grand Order"]
        assert button(root, "Unknown series").cget("text").endswith("3")

    moved = run_dialog(monkeypatch, items, lambda t: ["Fate", "Fate Grand Order"] if t else [], mover(calls), script)
    assert moved == 1
    assert (tmp_path / "Fate Grand Order" / "card_003.png").exists()


def test_enter_without_choosing_uses_the_typed_text_and_tab_completes(tmp_path, monkeypatch):
    items = make_items(tmp_path, 3)
    calls = []

    def script(root):
        cv = gallery_canvas(root)
        click(cv, first_unknown(cv))
        pump(root, 0.2)
        entry = editor(cv)
        type_into(entry, "My Own Series")
        entry.event_generate("<Return>")
        pump(root, 0.3)
        click(cv, first_unknown(cv))
        pump(root, 0.2)
        entry = editor(cv)
        type_into(entry, "fa")
        pump(root, 0.2)
        entry.event_generate("<Tab>")                                  # completes with the first suggestion
        assert entry.get() == "Fate"
        entry.event_generate("<Return>")
        pump(root, 0.3)

    suggest = lambda t: ["Fate"] if t.startswith("fa") else []
    assert run_dialog(monkeypatch, items, suggest, mover(calls), script) == 2
    assert calls == [("Char 002", "My Own Series"), ("Char 001", "Fate")]


def test_escape_blank_text_and_failed_moves_change_nothing(tmp_path, monkeypatch):
    items = make_items(tmp_path, 3)
    calls = []

    def script(root):
        cv = gallery_canvas(root)
        click(cv, first_unknown(cv))
        pump(root, 0.2)
        type_into(editor(cv), "Fate")
        editor(cv).event_generate("<Escape>")
        pump(root, 0.2)
        assert editor(cv) is None and calls == []                      # Esc only closes the input

        click(cv, first_unknown(cv))
        pump(root, 0.2)
        type_into(editor(cv), "   ")
        editor(cv).event_generate("<Return>")
        pump(root, 0.2)
        assert calls == []                                             # blank: nothing to assign

        click(cv, first_unknown(cv))
        pump(root, 0.2)
        type_into(editor(cv), "..")                                    # the mover refuses this one
        editor(cv).event_generate("<Return>")
        pump(root, 0.3)
        assert calls == [("Char 002", "..")]
        assert len(texts(cv, "Unknown series")) == 3                   # still untagged
        assert label_text(root, "Could not move") == ["Could not move Char 002"]

    assert run_dialog(monkeypatch, items, lambda t: [], mover(calls), script) == 0
    assert all(i.series is None for i in items)


def test_sidebar_views_search_and_sort(tmp_path, monkeypatch):
    items = make_items(tmp_path, 6, tagged={0: "Fate", 1: "Fate", 2: "SAO"})

    def script(root):
        cv = gallery_canvas(root)
        assert button(root, "All characters").cget("text").endswith("6")
        assert button(root, "Unknown series").cget("text").endswith("3")
        series_box = next(w for w in walk(root) if isinstance(w, tk.Listbox) and w.master.master.__class__.__name__ == "CTkFrame")
        assert list(series_box.get(0, "end")) == ["Fate   (2)", "SAO   (1)"]

        button(root, "Unknown series").invoke()
        pump(root, 0.3)
        assert len(texts(cv, "Unknown series")) == 3 and not texts(cv, "Fate")

        series_box.selection_set(0)
        series_box.event_generate("<<ListboxSelect>>")
        pump(root, 0.3)
        assert texts(cv, "Fate") and not texts(cv, "Unknown series") and not texts(cv, "SAO")

        button(root, "All characters").invoke()
        search = next(w for w in walk(root) if w.__class__.__name__ == "CTkEntry")
        search._entry.focus_force()
        search.update()
        search.insert(0, "char 004")
        search._entry.event_generate("<KeyRelease>", keysym="4")
        pump(root, 0.6)
        assert texts(cv, "Char 004") and not texts(cv, "Char 003")
        assert label_text(root, "showing") == ["showing 1 of 6 characters · 3 unknown series"]

        search._entry.focus_force()
        search.delete(0, "end")
        search._entry.event_generate("<KeyRelease>", keysym="BackSpace")
        pump(root, 0.5)
        first = min((i for i in cv.find_all() if cv.type(i) == "text" and cv.itemcget(i, "text").startswith("Char ")),
                    key=lambda i: (cv.bbox(i)[1], cv.bbox(i)[0]))
        assert cv.itemcget(first, "text") == "Char 005"                  # newest first
        button(root, "Descending").invoke()
        pump(root, 0.4)
        first = min((i for i in cv.find_all() if cv.type(i) == "text" and cv.itemcget(i, "text").startswith("Char ")),
                    key=lambda i: (cv.bbox(i)[1], cv.bbox(i)[0]))
        assert cv.itemcget(first, "text") == "Char 000"                  # oldest first

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_a_big_library_only_draws_what_is_in_view_and_loads_covers_on_scroll(tmp_path, monkeypatch):
    items = make_items(tmp_path, 300)

    def script(root):
        cv = gallery_canvas(root)
        pump(root, 1.0)
        assert 0 < len(texts(cv, "Unknown series")) < 40               # not 300 tiles' worth of items
        cv.yview_moveto(0.6)
        pump(root, 1.5)
        shown = texts(cv, "Unknown series")
        assert 0 < len(shown) < 40
        names = {cv.itemcget(i, "text") for i in cv.find_all() if cv.type(i) == "text"
                 and cv.itemcget(i, "text").startswith("Char ")}
        assert "Char 299" not in names and "Char 000" not in names or len(names) < 40
        images = [i for i in cv.find_all() if cv.type(i) == "image"]
        assert images and all(cv.itemcget(i, "image") for i in images)  # covers of the scrolled-to tiles

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_a_card_without_a_cover_says_so(tmp_path, monkeypatch):
    items = make_items(tmp_path, 1)
    items[0].path.write_bytes(b"not a png")

    def script(root):
        cv = gallery_canvas(root)
        pump(root, 0.8)
        assert texts(cv, "no\npreview")

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def _highlighted(cv):
    """Name of the card whose tile has the highlight border (None if no tile does)."""
    accents = {mgd._LIGHT["accent"], mgd._DARK["accent"]}
    boxes = [cv.bbox(i) for i in cv.find_all()
             if cv.type(i) == "polygon" and cv.itemcget(i, "outline") in accents and not cv.itemcget(i, "fill")]
    assert len(boxes) <= 1
    if not boxes:
        return None
    x1, y1, x2, y2 = boxes[0]
    inside = [cv.itemcget(i, "text") for i in cv.find_all() if cv.type(i) == "text"
              and cv.itemcget(i, "text").startswith("Char ") and x1 <= cv.bbox(i)[0] <= x2 and y1 <= cv.bbox(i)[1] <= y2]
    return inside[0] if inside else None


def _press(cv, keysym):
    cv.focus_force()
    cv.update()
    cv.event_generate("<KeyPress>", keysym=keysym)
    pump(cv.winfo_toplevel(), 0.25)


def test_click_selects_and_keys_move_the_selection_and_enter_edits(tmp_path, monkeypatch):
    items = make_items(tmp_path, 14)
    calls = []

    def script(root):
        cv = gallery_canvas(root)
        assert _highlighted(cv) is None
        name_id = texts(cv, "Char 013")[0]                           # newest = first tile
        click(cv, name_id)                                           # click the card, not its series
        pump(root, 0.2)
        assert _highlighted(cv) == "Char 013" and editor(cv) is None

        _press(cv, "d")
        assert _highlighted(cv) == "Char 012"
        _press(cv, "Right")
        assert _highlighted(cv) == "Char 011"
        _press(cv, "a")
        _press(cv, "Left")
        assert _highlighted(cv) == "Char 013"
        _press(cv, "Left")
        assert _highlighted(cv) == "Char 013"                        # no wrap off the start
        _press(cv, "s")                                              # one row down (same column)
        below = _highlighted(cv)
        assert below not in (None, "Char 013")
        _press(cv, "Up")
        assert _highlighted(cv) == "Char 013"
        _press(cv, "w")
        assert _highlighted(cv) == "Char 013"                        # nothing above the first row

        _press(cv, "d")
        _press(cv, "Return")                                         # Enter: the series input of Char 012
        pump(root, 0.2)
        entry = editor(cv)
        assert entry is not None and root.focus_get() is entry
        type_into(entry, "Fate")
        entry.event_generate("<Return>")
        pump(root, 0.4)
        assert calls == [("Char 012", "Fate")] and _highlighted(cv) == "Char 012"

        _press(cv, "Return")                                         # focus is back on the gallery
        pump(root, 0.2)
        assert editor(cv) is not None
        editor(cv).event_generate("<Escape>")
        pump(root, 0.2)
        assert editor(cv) is None and root.focus_get() is cv
        _press(cv, "d")
        assert _highlighted(cv) == "Char 011"

    run_dialog(monkeypatch, items, lambda t: [], mover(calls), script)


def test_keyboard_selection_scrolls_and_skips_to_the_next_card_after_leaving_a_view(tmp_path, monkeypatch):
    items = make_items(tmp_path, 60)

    def script(root):
        cv = gallery_canvas(root)
        for _ in range(3):
            _press(cv, "Down")                                       # first press selects, the rest move
        assert cv.canvasy(0) > 0 or _highlighted(cv) is not None
        for _ in range(12):
            _press(cv, "s")
        name = _highlighted(cv)
        assert name is not None                                      # still visible: the view followed it
        assert cv.canvasy(0) > 0

        button(root, "Unknown series").invoke()
        pump(root, 0.3)
        _press(cv, "Return")
        pump(root, 0.2)
        before = _highlighted(cv)
        type_into(editor(cv), "Fate")
        editor(cv).event_generate("<Return>")
        pump(root, 0.5)
        after = _highlighted(cv)
        assert after is not None and after != before                 # the assigned card left Unknown series; next selected

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


# ---------------------------------------------------------------------------
# several cards at once
# ---------------------------------------------------------------------------

def _picked(cv):
    """Names of the cards with the solid (selected) border, in reading order; dashed = cursor only."""
    accents = {mgd._LIGHT["accent"], mgd._DARK["accent"]}
    names = []
    for i in cv.find_all():
        if cv.type(i) == "polygon" and cv.itemcget(i, "outline") in accents and not cv.itemcget(i, "fill") \
                and not cv.itemcget(i, "dash"):
            x1, y1, x2, y2 = cv.bbox(i)
            names += [(cv.bbox(t)[1], cv.bbox(t)[0], cv.itemcget(t, "text")) for t in cv.find_all()
                      if cv.type(t) == "text" and cv.itemcget(t, "text").startswith("Char ")
                      and x1 <= cv.bbox(t)[0] <= x2 and y1 <= cv.bbox(t)[1] <= y2]
    return [n for _y, _x, n in sorted(names)]


def _cursor_only(cv):
    return [i for i in cv.find_all() if cv.type(i) == "polygon" and cv.itemcget(i, "dash")]


def _ctrl_click(cv, item_id):
    x1, y1, x2, y2 = cv.bbox(item_id)
    cv.event_generate("<Control-Button-1>", x=int((x1 + x2) / 2 - cv.canvasx(0)), y=int((y1 + y2) / 2 - cv.canvasy(0)))


def _ctrl_press(cv, keysym):
    cv.focus_force()
    cv.update()
    cv.event_generate(f"<Control-Key-{keysym}>")
    pump(cv.winfo_toplevel(), 0.25)


def _series_dialog(root):
    found = [w for w in walk(root) if w is not root and w.__class__.__name__ == "CTkToplevel"]
    return found[0] if found else None


def test_ctrl_click_adds_cards_and_a_plain_click_goes_back_to_one(tmp_path, monkeypatch):
    items = make_items(tmp_path, 8)

    def script(root):
        cv = gallery_canvas(root)
        click(cv, texts(cv, "Char 007")[0])
        _ctrl_click(cv, texts(cv, "Char 005")[0])
        _ctrl_click(cv, texts(cv, "Char 004")[0])
        pump(root, 0.2)
        assert _picked(cv) == ["Char 007", "Char 005", "Char 004"]
        assert label_text(root, "8 characters")[0].endswith("3 selected")
        _ctrl_click(cv, texts(cv, "Char 005")[0])                    # Ctrl+click again removes it
        pump(root, 0.2)
        assert _picked(cv) == ["Char 007", "Char 004"]
        click(cv, texts(cv, "Char 006")[0])
        pump(root, 0.2)
        assert _picked(cv) == ["Char 006"]

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_ctrl_arrows_space_ctrl_a_and_escape(tmp_path, monkeypatch):
    items = make_items(tmp_path, 8)

    def script(root):
        cv = gallery_canvas(root)
        click(cv, texts(cv, "Char 007")[0])
        _ctrl_press(cv, "Right")                                     # cursor moves, selection stays
        assert _picked(cv) == ["Char 007"] and len(_cursor_only(cv)) == 1
        _press(cv, "space")                                          # Space picks the card under the cursor
        assert _picked(cv) == ["Char 007", "Char 006"]
        _ctrl_press(cv, "Right")
        _ctrl_press(cv, "space")
        assert _picked(cv) == ["Char 007", "Char 006", "Char 005"]
        _press(cv, "Right")                                          # a plain move selects just that card
        assert _picked(cv) == ["Char 004"]

        _ctrl_press(cv, "a")                                         # Ctrl+A: everything shown
        assert len(_picked(cv)) == 8
        cv.event_generate("<Key>", keysym="Escape")                  # Esc: back to the cursor card only
        pump(root, 0.2)
        assert _picked(cv) == ["Char 004"]

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_enter_with_several_cards_opens_a_dialog_and_enter_moves_them_all(tmp_path, monkeypatch):
    items = make_items(tmp_path, 8)
    calls = []

    def script(root):
        cv = gallery_canvas(root)
        click(cv, texts(cv, "Char 007")[0])
        _ctrl_click(cv, texts(cv, "Char 005")[0])
        _ctrl_click(cv, texts(cv, "Char 002")[0])
        _press(cv, "Return")
        pump(root, 0.5)
        dlg = _series_dialog(root)
        assert dlg is not None and editor(cv) is None                # a dialog, not the inline input
        entry = next(w for w in walk(dlg) if w.__class__.__name__ == "CTkEntry")
        entry._entry.focus_force()
        pump(root, 0.1)
        entry.insert(0, "fa")
        entry._entry.event_generate("<KeyRelease>", keysym="a")
        pump(root, 0.2)
        box = next(w for w in walk(dlg) if isinstance(w, tk.Listbox))
        assert list(box.get(0, "end")) == ["Fate", "Fate Grand Order"]
        entry._entry.event_generate("<Down>")
        entry._entry.event_generate("<Return>")
        pump(root, 0.5)
        assert _series_dialog(root) is None
        assert calls == [("Char 007", "Fate"), ("Char 005", "Fate"), ("Char 002", "Fate")]   # in reading order
        assert label_text(root, "Moved") == ["Moved 3 characters → Fate"]
        assert root.focus_get() is cv
        assert len(texts(cv, "Fate")) == 3
        assert len(_picked(cv)) == 1                                  # selection collapses to one card

    suggest = lambda t: ["Fate", "Fate Grand Order"] if t else []
    assert run_dialog(monkeypatch, items, suggest, mover(calls), script) == 3
    assert sorted(p.name for p in (tmp_path / "Fate").iterdir()) == ["card_002.png", "card_005.png", "card_007.png"]


def test_the_series_dialog_can_be_cancelled_and_a_partial_failure_is_reported(tmp_path, monkeypatch):
    items = make_items(tmp_path, 5)
    calls = []

    def flaky(item, text):
        if item.name == "Char 003":
            calls.append((item.name, text))
            return None                                              # this one can't be moved
        return mover(calls)(item, text)

    def open_dialog(root, cv, names):
        click(cv, texts(cv, names[0])[0])
        for name in names[1:]:
            _ctrl_click(cv, texts(cv, name)[0])
        _press(cv, "Return")
        pump(root, 0.5)
        dlg = _series_dialog(root)
        entry = next(w for w in walk(dlg) if w.__class__.__name__ == "CTkEntry")
        entry._entry.focus_force()
        pump(root, 0.1)
        return dlg, entry

    def script(root):
        cv = gallery_canvas(root)
        dlg, entry = open_dialog(root, cv, ["Char 004", "Char 003"])
        entry.insert(0, "Nope")
        entry._entry.event_generate("<Escape>")
        pump(root, 0.3)
        assert _series_dialog(root) is None and calls == [] and root.focus_get() is cv

        dlg, entry = open_dialog(root, cv, ["Char 004", "Char 003"])
        entry._entry.event_generate("<Return>")                       # empty text: nothing happens, stays open
        pump(root, 0.2)
        assert _series_dialog(root) is not None and calls == []
        entry.insert(0, "Fate")
        entry._entry.event_generate("<Return>")
        pump(root, 0.5)
        assert [c[0] for c in calls] == ["Char 004", "Char 003"]
        assert label_text(root, "Moved") == ["Moved 1 characters → Fate · 1 could not be moved"]

    assert run_dialog(monkeypatch, items, lambda t: [], flaky, script) == 1


def test_sort_menu_options_order_default_and_effect(tmp_path, monkeypatch):
    items = make_items(tmp_path, 4)
    for i, item in enumerate(items):                 # created order is the reverse of updated order
        item.ctime = 1_600_000_000 + (10 - i)
        item.mtime = 1_700_000_000 + i

    def first_name(cv):
        return min((i for i in cv.find_all() if cv.type(i) == "text" and cv.itemcget(i, "text").startswith("Char ")),
                   key=lambda i: (cv.bbox(i)[1], cv.bbox(i)[0]))

    def script(root):
        cv = gallery_canvas(root)
        menu = next(w for w in walk(root) if w.__class__.__name__ == "CTkOptionMenu")
        assert list(menu.cget("values")) == ["Created date", "Updated date", "Name", "File name", "File size"]
        assert menu.get() == "Updated date"
        assert cv.itemcget(first_name(cv), "text") == "Char 003"          # newest updated first
        menu.set("Created date")
        menu.cget("command")("Created date")
        pump(root, 0.4)
        assert cv.itemcget(first_name(cv), "text") == "Char 000"          # newest created first

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_series_rows_highlight_under_the_pointer(tmp_path, monkeypatch):
    items = make_items(tmp_path, 4, tagged={0: "Fate", 1: "Fate", 2: "SAO"})
    hover = {mgd._LIGHT["hover"], mgd._DARK["hover"]}
    plain = {mgd._LIGHT["side"], mgd._DARK["side"]}

    def script(root):
        box = next(w for w in walk(root) if isinstance(w, tk.Listbox) and w.master.master.__class__.__name__ == "CTkFrame")
        assert list(box.get(0, "end")) == ["Fate   (2)", "SAO   (1)"]
        x, y, w, h = box.bbox(1)
        box.event_generate("<Motion>", x=x + 5, y=y + h // 2)
        pump(root, 0.1)
        assert box.itemcget(1, "background") in hover and box.itemcget(0, "background") not in hover
        x, y, w, h = box.bbox(0)
        box.event_generate("<Motion>", x=x + 5, y=y + h // 2)         # moving on moves the highlight
        pump(root, 0.1)
        assert box.itemcget(0, "background") in hover and box.itemcget(1, "background") in plain
        box.event_generate("<Motion>", x=5, y=box.winfo_height() - 2)   # empty space below the rows
        pump(root, 0.1)
        assert box.itemcget(0, "background") in plain
        box.event_generate("<Motion>", x=x + 5, y=y + h // 2)
        box.event_generate("<Leave>")
        pump(root, 0.1)
        assert box.itemcget(0, "background") in plain and box.itemcget(1, "background") in plain

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)
