import os, signal, sys, threading, time
sys.path.insert(0, "/home/claude/k/KKAFIO-refactor/src")
import tkinter as tk
from kkafio.system import dialog_common, password_dialog as pd, llm_dialog as ld

def drive(module, script):
    """Patch module.show_and_wait so `script(root, info)` runs once the window is up."""
    real = dialog_common.show_and_wait
    info = {}
    def fake(root, focus_widget=None):
        info["root"] = root
        def run():
            try: script(root, info)
            except BaseException as e:
                info["err"] = e; root.quit()
        root.after(300, run)
        real(root, focus_widget)
        if "err" in info: raise info["err"]
    module.show_and_wait = fake
    return info

def walk(w):
    yield w
    for c in w.winfo_children(): yield from walk(c)

def find(root, cls):
    return [w for w in walk(root) if w.__class__.__name__ == cls]

def press(root, seq):
    root.focus_force(); root.update(); root.event_generate(seq)

ok = 0
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok += cond
    assert cond, name

# ---- password: masked, Enter returns text exactly (no strip, unicode, typographic quote)
def s(root, info):
    e = find(root, "CTkEntry")[0]
    info["show"] = e._entry.cget("show"); info["topmost"] = bool(root.attributes("-topmost"))
    info["resizable"] = root.resizable()
    info["geom"] = (root.winfo_width(), root.winfo_height(), root.winfo_x(), root.winfo_y(), root.winfo_screenwidth(), root.winfo_screenheight())
    info["focus_is_entry"] = root.focus_get() is e._entry
    e.insert(0, " héllo 'x’s 日本 ")
    press(root, "<Return>")
info = drive(pd, s)
r = pd._ctk_dialog("T", "Enter pw", True)
check("password returns typed text verbatim", r == " héllo 'x’s 日本 ")
check("mask uses *", info["show"] == "*")
check("topmost", info["topmost"])
check("not resizable", info["resizable"] in ((False, False), (0, 0)))
check("entry focused", info["focus_is_entry"])
w, h, x, y, sw, sh = info["geom"]
check(f"centered (win {w}x{h}+{x}+{y} on {sw}x{sh})", abs(x - (sw - w)//2) <= 2 and abs(y - (sh - h)//2) <= 2)

# ---- unmasked + Escape cancels
def s(root, info):
    e = find(root, "CTkEntry")[0]; info["show"] = e._entry.cget("show"); e.insert(0, "abc"); press(root, "<Escape>")
info = drive(pd, s)
check("Esc cancels -> ''", pd._ctk_dialog("T", "x", False) == "")
check("unmasked shows text", info["show"] == "")

# ---- empty OK returns ''
def s(root, info): press(root, "<Return>")
drive(pd, s); check("empty OK -> ''", pd._ctk_dialog("T", "x") == "")

# ---- window height follows wrapped label
sizes = []
def s(root, info): sizes.append(root.winfo_height()); press(root, "<Escape>")
drive(pd, s)
pd._ctk_dialog("T", "short")
pd._ctk_dialog("T", "Log in to koikatsucards.com, then:\n1. Open DevTools (F12)\n2. Go to Application → Cookies → https://koikatsucards.com\n3. Find 'kkd_session' and copy its Value\n\nPaste the value below:" + " more words" * 20)
check(f"height grows with text {sizes}", sizes[1] > sizes[0] + 40)

# ---- OK / Cancel buttons by click
def s(root, info):
    e = find(root, "CTkEntry")[0]; e.insert(0, "clicked")
    [b for b in find(root, "CTkButton") if b.cget("text") == "OK"][0].invoke()
drive(pd, s); check("OK button click", pd._ctk_dialog("T", "x") == "clicked")
def s(root, info):
    find(root, "CTkEntry")[0].insert(0, "nope")
    [b for b in find(root, "CTkButton") if b.cget("text") == "Cancel"][0].invoke()
drive(pd, s); check("Cancel button click -> ''", pd._ctk_dialog("T", "x") == "")

# ---- window X (WM_DELETE_WINDOW) -> ''
def s(root, info): root.event_generate("<<x>>"); root.protocol("WM_DELETE_WINDOW")  # exists
def s(root, info): root.tk.call(root.protocol("WM_DELETE_WINDOW"))
drive(pd, s); check("window close -> ''", pd._ctk_dialog("T", "x") == "")

# ---- llm
PROMPT = "Please translate:\n{\n  \"k\": \"日本語\"\n}\n" + "x" * 300
def s(root, info):
    box = find(root, "CTkTextbox")[0]
    info["text"] = box.get("1.0", "end-1c"); info["state"] = str(box._textbox.cget("state"))
    info["wrap"] = str(box._textbox.cget("wrap")); info["minsize"] = root.wm_minsize()
    info["resizable"] = root.resizable(); info["topmost"] = bool(root.attributes("-topmost"))
    [b for b in find(root, "CTkButton") if b.cget("text") == "Copy"][0].invoke()
    info["clip_after_copy"] = root.clipboard_get()
    # simulate user copying the LLM reply elsewhere
    root.clipboard_clear(); root.clipboard_append("\n  {\"reply\": \"日本\"}  \n"); root.update()
    [b for b in find(root, "CTkButton") if b.cget("text") == "Paste"][0].invoke()
info = drive(ld, s)
r = ld._ctk_dialog("T", PROMPT)
check("textbox shows prompt", info["text"] == PROMPT)
check("textbox read-only", info["state"] == "disabled")
check("no word wrap", info["wrap"] == "none")
check("Copy puts prompt on clipboard", info["clip_after_copy"] == PROMPT)
check("Paste returns stripped clipboard", r == '{"reply": "日本"}')
check("resizable + topmost", info["resizable"] in ((True, True), (1, 1)) and info["topmost"])
check(f"minsize set {info['minsize']}", info["minsize"][0] > 400)

def s(root, info): press(root, "<Escape>")
drive(ld, s); check("llm Esc -> ''", ld._ctk_dialog("T", "p") == "")
def s(root, info):
    root.clipboard_clear(); root.update(); press(root, "<Return>")
drive(ld, s); check("llm Enter w/ empty clipboard -> '' (no crash)", ld._ctk_dialog("T", "p") == "")
def s(root, info): [b for b in find(root, "CTkButton") if b.cget("text") == "Cancel"][0].invoke()
drive(ld, s); check("llm Cancel -> ''", ld._ctk_dialog("T", "p") == "")

# ---- selectable read-only: click focuses the text widget
def s(root, info):
    box = find(root, "CTkTextbox")[0]; box._textbox.event_generate("<Button-1>", x=5, y=5); root.update()
    info["focus_text"] = root.focus_get() is box._textbox
    box._textbox.tag_add("sel", "1.0", "end"); info["sel"] = bool(box._textbox.tag_ranges("sel"))
    press(root, "<Escape>")
info = drive(ld, s); ld._ctk_dialog("T", "p")
check("click focuses read-only box", info["focus_text"] and info["sel"])

# ---- KeyboardInterrupt while idle propagates and window is destroyed
old = signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
def s(root, info):
    threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM)).start()
info = drive(pd, s)
t0 = time.time(); got = None
try: pd._ctk_dialog("T", "x")
except KeyboardInterrupt: got = time.time() - t0
check(f"SIGTERM interrupts idle dialog (after {got and round(got,2)}s)", got is not None and got < 1.5)
try: destroyed = not info["root"].winfo_exists()
except tk.TclError: destroyed = True
check("window destroyed after interrupt", destroyed)
signal.signal(signal.SIGTERM, old)

# dialog still works after an interrupted one (host root survived)
def s(root, info):
    find(root, "CTkEntry")[0].insert(0, "after-interrupt"); press(root, "<Return>")
drive(pd, s); check("dialog works after KeyboardInterrupt", pd._ctk_dialog("T", "x") == "after-interrupt")

# many sequential dialogs (Telegram: phone, code x3, 2FA)
def s(root, info):
    find(root, "CTkEntry")[0].insert(0, "v"); press(root, "<Return>")
drive(pd, s); drive_ok = all(pd._ctk_dialog("T", "x", i % 2 == 0) == "v" for i in range(8))
check("8 sequential dialogs", drive_ok)
print(f"\nall {ok} checks passed")