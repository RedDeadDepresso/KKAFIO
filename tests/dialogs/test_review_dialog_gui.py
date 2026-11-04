"""The Review Similar Characters window: one canvas that only draws the rows in view, no pages.
Needs a display (xvfb-run is enough); skipped where Tk can't open one."""

import time

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

from kkafio.dialogs import similar_chara as sc


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _run(monkeypatch, tmp_path, group_count, script):
    groups = [sc.ReviewGroup(label=f"Char {g}", description=f"group {g}",
                             paths=tuple(tmp_path / f"g{g}" / f"c{c}.png" for c in range(3)))
              for g in range(group_count)]
    monkeypatch.setattr(sc, "load_thumbnail",
                        lambda path, box=sc.THUMB_MAX: Image.new("RGBA", (60, 80), (200, 80, 80, 255)))

    def driver(root, *args, **kwargs):
        root.geometry("1000x760+0+0")
        root.deiconify()
        root.update()
        root.update()
        # the list's canvas is the biggest one (every CustomTkinter widget has a small canvas of its own)
        canvas = max((w for w in _walk(root) if isinstance(w, tk.Canvas)),
                     key=lambda w: w.winfo_width() * w.winfo_height())
        script(root, canvas)
        root.quit()

    monkeypatch.setattr(sc, "show_and_wait", driver)
    return sc.review_dialog("Review", groups, [("Keep first", "first")], lambda paths, key: paths[0])


def _trash_text(root):
    return next(w.cget("text") for w in _walk(root) if hasattr(w, "_text") and "Trash" in str(w.cget("text")))


def test_a_huge_list_keeps_only_the_rows_in_view_on_the_canvas(monkeypatch, tmp_path):
    seen = {}

    def script(root, canvas):
        seen["top"] = len(canvas.find_all())
        for fraction in (0.25, 0.5, 0.99):
            canvas.yview_moveto(fraction)
            root.update()
            seen[fraction] = len(canvas.find_all())

    _run(monkeypatch, tmp_path, 5000, script)                    # 15 000 cards, one list
    assert 0 < seen["top"] < 200
    assert all(0 < n < 200 for n in seen.values())


def test_clicking_a_checkbox_ticks_the_card_and_locks_the_group(monkeypatch, tmp_path):
    seen = {}

    def script(root, canvas):
        canvas.event_generate("<Button-1>", x=40, y=80)           # first card's checkbox
        root.update()
        seen["ticked"] = _trash_text(root)
        canvas.event_generate("<Button-1>", x=40, y=80)           # and off again
        root.update()
        seen["unticked"] = _trash_text(root)

    _run(monkeypatch, tmp_path, 20, script)
    assert "(1)" in seen["ticked"] and "(0)" in seen["unticked"]


def test_search_filters_the_list_and_scrolls_back_to_the_top(monkeypatch, tmp_path):
    seen = {}

    def script(root, canvas):
        canvas.yview_moveto(0.5)
        root.update()
        entry = next(w for w in _walk(root) if w.__class__.__name__ == "CTkEntry")
        entry._entry.focus_force()                                # key events go to the focused widget
        root.update()
        entry.insert(0, "g7/")
        entry._entry.event_generate("<KeyRelease>")
        deadline = time.time() + 1.0                              # the search waits a moment while you type
        while time.time() < deadline:
            root.update()
            time.sleep(0.02)
        seen["view"] = canvas.yview()
        summary = [str(w.cget("text")) for w in _walk(root) if hasattr(w, "_text")]
        seen["summary"] = [s for s in summary if "groups" in s]

    _run(monkeypatch, tmp_path, 40, script)
    assert seen["view"][0] == 0.0
    assert any("1" in s and "40" in s for s in seen["summary"]), seen["summary"]
