"""The tabbed gallery window (Run Gallery). Needs a display (xvfb-run); skipped without one."""

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")
try:
    _probe = tk.Tk()
    _probe.destroy()
except tk.TclError:
    pytest.skip("no display available for Tk", allow_module_level=True)

import test_manual_group_dialog_gui as g
from kkafio.dialogs import manual_group as mgd


def _run(monkeypatch, tabs, script, initial=0):
    errors = []

    def fake_show(root, focus_widget=None, on_shown=None):
        def go():
            try:
                g.pump(root, 0.6)
                script(root)
            except BaseException as e:
                errors.append(e)
            finally:
                root.quit()
        g.real_show_and_wait(root, focus_widget, on_shown=lambda: root.after(150, go))

    monkeypatch.setattr(mgd, "show_and_wait", fake_show)
    out = mgd.gallery_dialog("test", tabs, initial)
    if errors:
        raise errors[0]
    return out


def _tabs(tmp_path, calls):
    tabs = []
    for name in ("Scene", "Character", "Outfit"):
        items = g.make_items(tmp_path / name, 3)
        for i in items:
            i.name = f"{name} {i.name}"
        tabs.append(mgd.GalleryTab(name, items, lambda t: [], g.mover(calls[name]), None, mgd.COVER_RATIO,
                                   lambda n=name: calls["finish"].append(n)))
    return tabs


def _segmented(root):
    return next(w for w in g.walk(root) if w.__class__.__name__ == "CTkSegmentedButton")


def test_tabs_switch_galleries_and_keep_their_own_search(tmp_path, monkeypatch):
    calls = {"Scene": [], "Character": [], "Outfit": [], "finish": []}

    def script(root):
        bar = _segmented(root)
        search = next(w for w in g.walk(root) if w.__class__.__name__ == "CTkEntry"
                      and w.cget("placeholder_text").startswith("Search"))
        g.type_into(search._entry, "Scene card_001")
        g.pump(root, 0.5)
        bar._buttons_dict["Outfit"].invoke()
        g.pump(root, 0.6)
        assert search.get() == ""                                   # Outfit has its own (empty) search
        g.show_all(root)
        cv = max((w for w in g.walk(root) if isinstance(w, tk.Canvas) and w.winfo_ismapped()),
                 key=lambda w: w.winfo_width() * w.winfo_height())      # the open tab's tile canvas
        assert g.texts(cv, "Outfit Char 002")
        bar._buttons_dict["Scene"].invoke()
        g.pump(root, 0.6)
        assert search.get() == "Scene card_001"                     # restored when its tab reopens

    assert _run(monkeypatch, _tabs(tmp_path, calls), script) == [0, 0, 0]
    assert sorted(calls["finish"]) == ["Character", "Outfit", "Scene"]
