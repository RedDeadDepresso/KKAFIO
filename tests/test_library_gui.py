"""The Manual-mode library window, driven with synthetic events. Needs a display (xvfb-run is
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

from kkafio.dialogs.library import window as mgd
from kkafio.dialogs.common import show_and_wait as real_show_and_wait


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
    """assign(): records the call and really moves the card, like library.characters.manual_callbacks."""
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


def library_canvas(root):
    return max((w for w in walk(root) if isinstance(w, tk.Canvas)), key=lambda w: w.winfo_width() * w.winfo_height())


def texts(cv, text):
    return [i for i in cv.find_all() if cv.type(i) == "text" and cv.itemcget(i, "text") == text]


def first_unknown(cv):
    """The "Not in a folder" text of the top-left tile (item order on the canvas isn't tile order)."""
    return min(texts(cv, "Not in a folder"), key=lambda i: (cv.bbox(i)[1], cv.bbox(i)[0]))


def click(cv, item_id):
    x1, y1, x2, y2 = cv.bbox(item_id)
    cv.event_generate("<Button-1>", x=int((x1 + x2) / 2 - cv.canvasx(0)), y=int((y1 + y2) / 2 - cv.canvasy(0)))


def click_cover(cv, name_id):
    """Click the cover of the tile whose file name is `name_id` (the name sits right under it)."""
    x1, y1, x2, y2 = cv.bbox(name_id)
    cv.event_generate("<Button-1>", x=int((x1 + x2) / 2 - cv.canvasx(0)), y=int(y1 - 40 - cv.canvasy(0)))


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


def show_all(root, label="All"):
    """The library opens on the "All" entry; click it anyway (a no-op there, and it waits for the redraw)."""
    button(root, label).invoke()
    pump(root, 0.4)


def run_dialog(monkeypatch, items, suggest, assign, script):
    """Open the library; `script(root)` runs once it is up (and must not close it)."""
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
        cv = library_canvas(root)
        show_all(root)
        pump(root, 1.5)
        assert len(texts(cv, "Not in a folder")) == 11 and texts(cv, "Fate")
        assert texts(cv, "Char 011")                                  # newest first by default
        images = [i for i in cv.find_all() if cv.type(i) == "image"]
        assert len(images) == 12 and all(cv.itemcget(i, "image") for i in images)    # every cover arrived
        assert label_text(root, "12 characters") == ["12 characters · 11 not in a folder"]

    assert run_dialog(monkeypatch, items, lambda t: [], mover([]), script) == 0


def test_click_type_pick_a_suggestion_and_enter_moves_the_card(tmp_path, monkeypatch):
    items = make_items(tmp_path, 4)
    calls = []

    def script(root):
        cv = library_canvas(root)
        show_all(root)
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
        assert button(root, "Not in a folder").cget("text").endswith("3")

    moved = run_dialog(monkeypatch, items, lambda t: ["Fate", "Fate Grand Order"] if t else [], mover(calls), script)
    assert moved == 1
    assert (tmp_path / "Fate Grand Order" / "card_003.png").exists()


def test_enter_without_choosing_uses_the_typed_text_and_tab_completes(tmp_path, monkeypatch):
    items = make_items(tmp_path, 3)
    calls = []

    def script(root):
        cv = library_canvas(root)
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
        cv = library_canvas(root)
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
        assert len(texts(cv, "Not in a folder")) == 3                   # still untagged
        assert label_text(root, "Could not move") == ["Could not move Char 002"]

    assert run_dialog(monkeypatch, items, lambda t: [], mover(calls), script) == 0
    assert all(i.series is None for i in items)


def test_sidebar_views_search_and_sort(tmp_path, monkeypatch):
    items = make_items(tmp_path, 6, tagged={0: "Fate", 1: "Fate", 2: "SAO"})

    def script(root):
        cv = library_canvas(root)
        assert button(root, "All characters").cget("text").endswith("6")
        assert button(root, "Not in a folder").cget("text").endswith("3")
        series_box = next(w for w in walk(root) if isinstance(w, mgd.SideList))
        assert list(series_box.get(0, "end")) == ["Fate   (2)", "SAO   (1)"]

        button(root, "Not in a folder").invoke()
        pump(root, 0.3)
        assert len(texts(cv, "Not in a folder")) == 3 and not texts(cv, "Fate")

        series_box.selection_set(0)
        series_box.event_generate("<<ListboxSelect>>")
        pump(root, 0.3)
        assert texts(cv, "Fate") and not texts(cv, "Not in a folder") and not texts(cv, "SAO")

        button(root, "All characters").invoke()
        search = next(w for w in walk(root) if w.__class__.__name__ == "CTkEntry")
        search._entry.focus_force()
        search.update()
        search.insert(0, "char 004")
        search._entry.event_generate("<KeyRelease>", keysym="4")
        pump(root, 0.6)
        assert texts(cv, "Char 004") and not texts(cv, "Char 003")
        assert label_text(root, "showing") == ["showing 1 of 6 characters · 3 not in a folder"]

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
        cv = library_canvas(root)
        pump(root, 1.0)
        assert 0 < len(texts(cv, "Not in a folder")) < 40               # not 300 tiles' worth of items
        cv.yview_moveto(0.6)
        pump(root, 1.5)
        shown = texts(cv, "Not in a folder")
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
        cv = library_canvas(root)
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
        cv = library_canvas(root)
        show_all(root)
        assert _highlighted(cv) is None
        name_id = texts(cv, "Char 013")[0]                           # newest = first tile
        click_cover(cv, name_id)                                     # the cover selects the card, opens nothing
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

        _press(cv, "Return")                                         # focus is back on the library
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
        cv = library_canvas(root)
        for _ in range(3):
            _press(cv, "Down")                                       # first press selects, the rest move
        assert cv.canvasy(0) > 0 or _highlighted(cv) is not None
        for _ in range(12):
            _press(cv, "s")
        name = _highlighted(cv)
        assert name is not None                                      # still visible: the view followed it
        assert cv.canvasy(0) > 0

        button(root, "Not in a folder").invoke()
        pump(root, 0.3)
        _press(cv, "Return")
        pump(root, 0.2)
        before = _highlighted(cv)
        type_into(editor(cv), "Fate")
        editor(cv).event_generate("<Return>")
        pump(root, 0.5)
        after = _highlighted(cv)
        assert after is not None and after != before                 # the assigned card left Not in a folder; next selected

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
        cv = library_canvas(root)
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
        cv = library_canvas(root)
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
        cv = library_canvas(root)
        show_all(root)
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
        cv = library_canvas(root)
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
        cv = library_canvas(root)
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

    def script(root):
        box = next(w for w in walk(root) if isinstance(w, mgd.SideList))
        assert list(box.get(0, "end")) == ["Fate   (2)", "SAO   (1)"]
        pump(root, 0.2)
        x, y, w, h = box.row_box(1)
        box.event_generate("<Motion>", x=x + 5, y=y + h // 2)
        pump(root, 0.1)
        assert box._hover == 1
        x, y, w, h = box.row_box(0)
        box.event_generate("<Motion>", x=x + 5, y=y + h // 2)         # moving on moves the highlight
        pump(root, 0.1)
        assert box._hover == 0
        gap_y = box.row_box(0)[1] + box.row_box(0)[3] + 1               # between two rows
        box.event_generate("<Motion>", x=5, y=gap_y)
        pump(root, 0.1)
        assert box._hover is None
        box.event_generate("<Motion>", x=5, y=box.winfo_height() - 2)   # empty space below the rows
        pump(root, 0.1)
        assert box._hover is None
        box.event_generate("<Motion>", x=x + 5, y=y + h // 2)
        box.event_generate("<Leave>")
        pump(root, 0.1)
        assert box._hover is None

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_the_series_rows_are_as_big_and_as_far_apart_as_the_sidebar_buttons(tmp_path, monkeypatch):
    items = make_items(tmp_path, 4, tagged={0: "Fate", 1: "Fate", 2: "SAO"})

    def script(root):
        box = next(w for w in walk(root) if isinstance(w, mgd.SideList))
        pump(root, 0.3)
        all_btn, untagged_btn = button(root, "All"), button(root, "Not in a folder")
        btn_h = all_btn.winfo_height()
        btn_gap = untagged_btn.winfo_y() - (all_btn.winfo_y() + btn_h)
        (_, y0, w0, h0), (_, y1, _w, h1) = box.row_box(0), box.row_box(1)
        assert h0 == h1 == btn_h and y1 - (y0 + h0) == btn_gap
        assert w0 == box.winfo_width() > 0

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_a_long_series_list_scrolls_with_wheel_and_scrollbar_and_picks_by_click(tmp_path, monkeypatch):
    items = make_items(tmp_path, 40, tagged={i: f"Series {i:02d}" for i in range(40)})

    def script(root):
        cv = library_canvas(root)
        box = next(w for w in walk(root) if isinstance(w, mgd.SideList))
        pump(root, 0.5)
        assert box.size() == 40 and box.canvasy(0) == 0
        box.event_generate("<Button-5>", x=10, y=10)                    # wheel down
        pump(root, 0.1)
        assert box.canvasy(0) > 0
        box.yview("scroll", 1, "units")                                  # what the scrollbar sends
        pump(root, 0.1)
        box.yview_moveto(0)
        pump(root, 0.1)
        x, y, w, h = box.row_box(2)
        box.event_generate("<Button-1>", x=x + 5, y=y + h // 2)
        pump(root, 0.4)
        assert box.curselection() == (2,) and texts(cv, "Series 02") and not texts(cv, "Series 03")

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_the_library_takes_its_wording_from_a_dialog_text(tmp_path, monkeypatch):
    """Group Coordinates reuses the library with its own sentences: nothing says "series" or "characters"."""
    from kkafio.dialogs.library.common import dialog_text
    items = make_items(tmp_path, 6, tagged={0: "Rin"})
    text = dialog_text("dialog.manual_coord")
    calls = []

    def script(root):
        cv = library_canvas(root)
        show_all(root, text.all)
        pump(root, 0.8)
        assert len(texts(cv, text.unknown_series)) == 5 and texts(cv, "Rin")
        assert not texts(cv, "Not in a folder")
        assert button(root, text.all).cget("text") == f"{text.all}   6"
        assert button(root, text.untagged).cget("text") == f"{text.untagged}   5"
        assert label_text(root, text.series_header) == [text.series_header]
        assert label_text(root, "6 coordinates") == [text.status.format(total=6, untagged=5)]
        assert label_text(root, "Select a card")[0] == text.hint
        click(cv, min(texts(cv, text.unknown_series), key=lambda i: (cv.bbox(i)[1], cv.bbox(i)[0])))
        type_into(editor(cv), "Saber")
        editor(cv).event_generate("<Return>")
        pump(root, 0.5)
        assert label_text(root, "Moved") == [text.moved.format(name="Char 005", series="Saber")]
        assert calls == [("Char 005", "Saber")]
        assert button(root, text.untagged).cget("text") == f"{text.untagged}   4"

    errors = []

    def fake_show(root, focus_widget=None, on_shown=None):
        def go():
            try:
                pump(root, 0.6)
                script(root)
            except BaseException as e:
                errors.append(e)
            finally:
                root.quit()
        real_show_and_wait(root, focus_widget, on_shown=lambda: root.after(150, go))

    monkeypatch.setattr(mgd, "show_and_wait", fake_show)
    assert mgd.manual_dialog("test", items, lambda t: ["Saber"], mover(calls), text) == 1
    if errors:
        raise errors[0]


def test_cover_ratio_makes_landscape_tiles(tmp_path, monkeypatch):
    """Scenes are landscape: the cover is as wide as the tile and 9/16 as tall (portrait by default)."""
    items = make_items(tmp_path, 3)
    heights = {}

    def script(root):
        cv = library_canvas(root)
        pump(root, 0.8)
        for i in cv.find_all():
            if cv.type(i) == "image":
                x1, y1, x2, y2 = cv.bbox(i)
                heights[round((y2 - y1) / (x2 - x1), 2)] = True

    assert run_dialog_ratio(monkeypatch, items, script, 180 / 320) == 0
    assert list(heights) == [0.56]


def run_dialog_ratio(monkeypatch, items, script, ratio):
    errors = []

    def fake_show(root, focus_widget=None, on_shown=None):
        def go():
            try:
                pump(root, 0.6)
                script(root)
            except BaseException as e:
                errors.append(e)
            finally:
                root.quit()
        real_show_and_wait(root, focus_widget, on_shown=lambda: root.after(150, go))

    monkeypatch.setattr(mgd, "show_and_wait", fake_show)
    moved = mgd.manual_dialog("test", items, lambda t: [], mover([]), cover_ratio=ratio)
    if errors:
        raise errors[0]
    return moved


def test_the_library_always_opens_on_the_all_entry(tmp_path, monkeypatch):
    items = make_items(tmp_path, 6, tagged={0: "Rin", 1: "Rin"})

    def script(root):
        cv = library_canvas(root)
        pump(root, 0.8)
        assert len(texts(cv, "Not in a folder")) == 4 and texts(cv, "Rin")     # every card is shown
        button(root, "Not in a folder").invoke()                    # the other entry is one click away
        pump(root, 0.4)
        assert len(texts(cv, "Not in a folder")) == 4 and not texts(cv, "Rin")

    assert run_dialog(monkeypatch, items, lambda t: [], mover([]), script) == 0


def test_the_series_list_uses_the_font_of_the_sidebar_buttons(tmp_path, monkeypatch):
    from tkinter import font as tkfont
    items = make_items(tmp_path, 3, tagged={0: "Rin"})

    def script(root):
        box = next(w for w in walk(root) if isinstance(w, mgd.SideList))
        btn = button(root, "All").cget("font")
        want = tkfont.Font(root=root, font=button(root, "All")._apply_font_scaling(btn))
        got = tkfont.Font(root=root, font=box._font)
        assert got.actual("family") == want.actual("family") and got.actual("size") == want.actual("size")

    assert run_dialog(monkeypatch, items, lambda t: [], mover([]), script) == 0


def test_clicking_a_series_row_never_draws_an_empty_list_and_keeps_the_scroll_position(tmp_path, monkeypatch):
    items = make_items(tmp_path, 40, tagged={i: f"Series {i:02d}" for i in range(40)})

    def script(root):
        box = next(w for w in walk(root) if isinstance(w, mgd.SideList))
        pump(root, 0.5)
        box.yview_moveto(0.5)
        pump(root, 0.2)
        top = box.canvasy(0)
        assert top > 0
        drawn = []
        real = box._redraw
        box._redraw = lambda: (drawn.append(len(box._rows)), real())      # what was in the list at each draw
        x, y, w, h = box.row_box(int(top // box._step) + 2)
        box.event_generate("<Button-1>", x=x + 5, y=y + h // 2)
        pump(root, 0.5)
        assert drawn and all(n == 40 for n in drawn), drawn
        assert abs(box.canvasy(0) - top) < 1
        assert len(box.curselection()) == 1

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


# ---------------------------------------------------------------------------
# renaming a card's file by clicking its name
# ---------------------------------------------------------------------------

def test_clean_file_stem():
    clean = mgd.clean_file_stem
    assert clean("  Alice  ", ".png") == "Alice"
    assert clean("Alice.png", ".png") == "Alice" and clean("Alice.PNG", ".png") == "Alice"
    assert clean("a.b", ".png") == "a.b"
    assert clean("Alice.", ".png") == "Alice"                        # Windows drops a trailing dot
    assert clean("日本語 カード", ".png") == "日本語 カード"
    for bad in ("", "   ", "...", ".png", "a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a|b", "a\tb",
                "CON", "nul.txt", "com1"):
        assert clean(bad, ".png") == "", bad


def test_rename_card_never_overwrites(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"A")
    b.write_bytes(b"B")
    new = mgd.rename_card(a, "alice")
    assert new == tmp_path / "alice.png" and new.read_bytes() == b"A" and not a.exists()
    assert mgd.rename_card(new, "alice") == new                      # same name: nothing to do
    with pytest.raises(FileExistsError):
        mgd.rename_card(new, "b")
    assert b.read_bytes() == b"B" and new.read_bytes() == b"A"       # both untouched
    assert mgd.rename_card(new, "Alice").name == "Alice.png"         # changing only the case is fine
    with pytest.raises(OSError):
        mgd.rename_card(tmp_path / "gone.png", "x")


def test_model_renamed_follows_the_file(tmp_path):
    item = mgd.CharaItem(tmp_path / "old.png", tmp_path, "old", None, 1, 0.0)
    mgd.LibraryModel([item]).renamed(item, tmp_path / "new name.png")
    assert item.path.name == "new name.png" and item.name == "new name"


def test_clicking_the_file_name_renames_the_file(tmp_path, monkeypatch):
    items = make_items(tmp_path, 3)
    calls = []

    def script(root):
        cv = library_canvas(root)
        click(cv, texts(cv, "Char 002")[0])                          # the bold file name of the first tile
        pump(root, 0.2)
        entry = editor(cv)
        assert entry is not None and entry.get() == "card_002"       # the stem, without ".png"
        assert suggestions(cv) == []                                 # a file name has no suggestions
        type_into(entry, "Renamed.png")                              # typing the extension is fine
        entry.event_generate("<Return>")
        pump(root, 0.4)
        assert (tmp_path / "Renamed.png").exists() and not (tmp_path / "card_002.png").exists()
        assert items[2].path.name == "Renamed.png" and items[2].name == "Renamed"
        assert len(texts(cv, "Renamed")) == 1 and not texts(cv, "Char 002")
        assert editor(cv) is None and _highlighted_or_none(cv, "Renamed")
        assert label_text(root, "Renamed card_002.png")

    run_dialog(monkeypatch, items, lambda t: [], mover(calls), script)
    assert calls == []                                               # renaming is not a series assignment


def _highlighted_or_none(cv, name):
    """True if the tile showing `name` has the selection border."""
    accents = {mgd._LIGHT["accent"], mgd._DARK["accent"]}
    boxes = [cv.bbox(i) for i in cv.find_all()
             if cv.type(i) == "polygon" and cv.itemcget(i, "outline") in accents and not cv.itemcget(i, "fill")]
    x1, y1, x2, y2 = cv.bbox(texts(cv, name)[0])
    return any(bx1 <= x1 <= bx2 and by1 <= y1 <= by2 for bx1, by1, bx2, by2 in boxes)


def test_rename_refuses_taken_and_invalid_names_and_esc_cancels(tmp_path, monkeypatch):
    items = make_items(tmp_path, 3)

    def script(root):
        cv = library_canvas(root)

        def rename_to(text, key="<Return>"):
            click(cv, texts(cv, "Char 002")[0])
            pump(root, 0.2)
            entry = editor(cv)
            assert entry is not None
            type_into(entry, text)
            entry.event_generate(key)
            pump(root, 0.3)

        rename_to("card_000")                                        # taken by another card
        assert (tmp_path / "card_002.png").exists() and (tmp_path / "card_000.png").exists()
        assert label_text(root, "card_000.png already exists")
        assert editor(cv) is None

        rename_to("a/b")                                             # not a file name
        assert (tmp_path / "card_002.png").exists()
        assert label_text(root, '"a/b" can')

        rename_to("zzz", key="<Escape>")                             # cancelled
        assert (tmp_path / "card_002.png").exists() and not (tmp_path / "zzz.png").exists()
        assert editor(cv) is None

        rename_to("card_002")                                        # unchanged: nothing happens
        assert (tmp_path / "card_002.png").exists() and editor(cv) is None
        assert items[2].path.name == "card_002.png"

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)


def test_clicking_the_series_line_still_opens_the_series_input(tmp_path, monkeypatch):
    items = make_items(tmp_path, 2)

    def script(root):
        cv = library_canvas(root)
        click(cv, first_unknown(cv))
        pump(root, 0.2)
        entry = editor(cv)
        assert entry is not None and entry.get() == ""               # the series input, not the file name

    run_dialog(monkeypatch, items, lambda t: [], mover([]), script)



def test_the_right_click_menu_is_a_customtkinter_menu_that_runs_and_closes(tmp_path, monkeypatch):
    import tkinter as tk
    items = make_items(tmp_path, 3)

    def script(root):
        cv = library_canvas(root)
        show_all(root)
        x1, y1, x2, y2 = cv.bbox(first_unknown(cv))
        cv.event_generate("<Button-3>", x=(x1 + x2) // 2, y=y1 - 10)
        pump(root, 0.3)
        menus = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(menus) == 1 and any(w.__class__.__name__ == "CTkLabel" for w in walk(menus[0]))
        assert not any(isinstance(w, tk.Menu) for w in walk(root))        # not a native menu
        cv.event_generate("<Button-1>", x=5, y=5)                         # a click elsewhere closes it
        pump(root, 0.3)
        assert not [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]

    assert run_dialog(monkeypatch, items, lambda t: [], mover([]), script) == 0


def test_the_settings_dialog_has_both_tabs_and_saves_the_folder_suggestions(tmp_path, monkeypatch):
    import json
    from kkafio.core.paths import CONFIG_DIR
    items = make_items(tmp_path, 3)

    def script(root):
        library_canvas(root)
        pump(root, 0.3)
        button(root, "Settings").invoke()
        pump(root, 0.5)
        dialogs = [w for w in walk(root) if w.__class__.__name__ == "CTkToplevel"]
        assert len(dialogs) == 1
        tabview = next(w for w in walk(dialogs[0]) if w.__class__.__name__ == "CTkTabview")
        assert list(tabview._name_list) == ["Shortcuts", "Folder suggestions"]
        menus = [w for w in walk(dialogs[0]) if w.__class__.__name__ == "CTkOptionMenu"]
        assert len(menus) == 4 and menus[2].get() == "Existing folders + Authors"      # one per kind of card
        assert menus[3].get() == "English"                                          # and the series language
        menus[3]._dropdown_callback("日本語")
        menus[0]._dropdown_callback("No")                                         # the Character tab
        saved = json.loads((CONFIG_DIR / "config" / "library-suggestions.json").read_text("utf-8"))
        assert saved["suggestions"] == {"chara": "none", "outfit": "all", "scene": "all"}
        assert saved["series_language"] == "ja_jp"

    assert run_dialog(monkeypatch, items, lambda t: [], mover([]), script) == 0


def run_library(monkeypatch, items, script, **callbacks):
    """Like `run_dialog`, but through `library_dialog` so the close callbacks (`on_archive`,
    `on_trash`) can be given."""
    errors = []

    def fake_show(root, focus_widget=None, on_shown=None):
        def go():
            try:
                pump(root, 0.6)
                script(root)
            except BaseException as e:
                errors.append(e)
            finally:
                root.quit()
        real_show_and_wait(root, focus_widget, on_shown=lambda: root.after(150, go))

    monkeypatch.setattr(mgd, "show_and_wait", fake_show)
    tab = mgd.LibraryTab("", items, lambda t: [], mover([]), None, mgd.COVER_RATIO)
    result = mgd.library_dialog("test", [tab], **callbacks)
    if errors:
        raise errors[0]
    return result


def menu_labels(root):
    import tkinter as tk
    menus = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    assert len(menus) == 1
    return menus[0], [w.cget("text") for w in walk(menus[0]) if w.__class__.__name__ == "CTkLabel"]


def pick_menu_entry(menu, label):
    entry = next(w for w in walk(menu) if w.__class__.__name__ == "CTkLabel" and w.cget("text").startswith(label))
    entry._label.event_generate("<Button-1>", x=2, y=2)


def right_click_first_card(root, cv):
    x1, y1, x2, y2 = cv.bbox(first_unknown(cv))
    cv.event_generate("<Button-3>", x=int((x1 + x2) / 2 - cv.canvasx(0)), y=int(y1 - 10 - cv.canvasy(0)))
    pump(root, 0.3)


def test_archive_queue_menu_shortcut_restore_and_the_handoff_on_close(tmp_path, monkeypatch):
    items = make_items(tmp_path, 4)
    seen = {}

    def script(root):
        cv = library_canvas(root)
        show_all(root)
        assert button(root, "Archive queue").cget("text").endswith("0")
        rows = [w for w in walk(root) if w.__class__.__name__ == "CTkButton"]
        names = [str(w.cget("text")).split("   ")[0] for w in rows]
        assert names.index("Archive queue") + 1 == names.index("Trash")           # right above the trash

        # right click: Move to trash and Archive; Archive queues the card (and it leaves "All")
        right_click_first_card(root, cv)
        menu, labels = menu_labels(root)
        assert "Move to trash" in labels and "Archive" in labels and "F11" in labels
        pick_menu_entry(menu, "Archive")
        pump(root, 0.4)
        assert button(root, "Archive queue").cget("text").endswith("1")
        assert button(root, "All").cget("text").endswith("3")

        # F11 archives the selected card
        right_click_first_card(root, cv)                                          # selects the card
        cv.event_generate("<Button-1>", x=5, y=5)                                 # and closes the menu again
        pump(root, 0.3)
        cv.focus_force()
        pump(root, 0.2)
        cv.event_generate("<KeyPress>", keysym="F11")
        pump(root, 0.4)
        assert button(root, "Archive queue").cget("text").endswith("2")

        # the queue shows its cards; right click there offers Restore only
        button(root, "Archive queue").invoke()
        pump(root, 0.4)
        right_click_first_card(root, cv)
        menu, labels = menu_labels(root)
        assert "Restore" in labels and "Archive" not in labels and "Move to trash" not in labels
        pick_menu_entry(menu, "Restore")
        pump(root, 0.4)
        assert button(root, "Archive queue").cget("text").endswith("1")
        assert button(root, "All").cget("text").endswith("3")

    result = run_library(monkeypatch, items, script, on_archive=lambda paths: seen.setdefault("paths", paths))
    assert result == [0]
    assert len(seen["paths"]) == 1 and seen["paths"][0] in [i.path for i in items]      # what is left in the queue
