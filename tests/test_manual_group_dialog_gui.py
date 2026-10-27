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
        assert label_text(root, "12 characters") == ["12 characters · 11 untagged"]

    assert run_dialog(monkeypatch, items, lambda t: [], mover([]), script) == 0


def test_click_type_pick_a_suggestion_and_enter_moves_the_card(tmp_path, monkeypatch):
    items = make_items(tmp_path, 4)
    calls = []

    def script(root):
        cv = gallery_canvas(root)
        click(cv, texts(cv, "Unknown series")[0])
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
        assert button(root, "Untagged").cget("text").endswith("3")

    moved = run_dialog(monkeypatch, items, lambda t: ["Fate", "Fate Grand Order"] if t else [], mover(calls), script)
    assert moved == 1
    assert (tmp_path / "Fate Grand Order" / "card_003.png").exists()


def test_enter_without_choosing_uses_the_typed_text_and_tab_completes(tmp_path, monkeypatch):
    items = make_items(tmp_path, 3)
    calls = []

    def script(root):
        cv = gallery_canvas(root)
        click(cv, texts(cv, "Unknown series")[0])
        pump(root, 0.2)
        entry = editor(cv)
        type_into(entry, "My Own Series")
        entry.event_generate("<Return>")
        pump(root, 0.3)
        click(cv, texts(cv, "Unknown series")[0])
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
        click(cv, texts(cv, "Unknown series")[0])
        pump(root, 0.2)
        type_into(editor(cv), "Fate")
        editor(cv).event_generate("<Escape>")
        pump(root, 0.2)
        assert editor(cv) is None and calls == []                      # Esc only closes the input

        click(cv, texts(cv, "Unknown series")[0])
        pump(root, 0.2)
        type_into(editor(cv), "   ")
        editor(cv).event_generate("<Return>")
        pump(root, 0.2)
        assert calls == []                                             # blank: nothing to assign

        click(cv, texts(cv, "Unknown series")[0])
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
        assert button(root, "Untagged").cget("text").endswith("3")
        series_box = next(w for w in walk(root) if isinstance(w, tk.Listbox) and w.master.master.__class__.__name__ == "CTkFrame")
        assert list(series_box.get(0, "end")) == ["Fate   (2)", "SAO   (1)"]

        button(root, "Untagged").invoke()
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
        assert label_text(root, "showing") == ["showing 1 of 6 characters · 3 untagged"]

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
