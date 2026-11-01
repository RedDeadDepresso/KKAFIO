"""
popup_menu.py — A right-click menu drawn with CustomTkinter widgets, so it matches the rest of the
window (rounded highlights, the window's colours in light and dark) where `tk.Menu` is a native
menu that ignores the theme.

`open_popup_menu()` shows a small borderless window next to the pointer with one row per entry
(label on the left, shortcut on the right, highlighted under the pointer). It closes when a row is
clicked, on a click anywhere else in the window, on Esc, when the window moves, is resized or
minimised, or when the application loses the focus. Keyboard focus stays where it was.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

# label, shortcut text ("" for none), what clicking the row does
MenuEntry = tuple[str, str, Callable[[], None]]

_CLOSING_EVENTS = ("<Button-1>", "<Button-2>", "<Button-3>", "<Escape>", "<Unmap>", "<Configure>", "<FocusOut>")


def open_popup_menu(anchor, x: int, y: int, entries: Sequence[MenuEntry],
                    colors: Mapping[str, tuple[str, str]]) -> Callable[[], None]:
    """Show the menu with its top-left corner at the screen position (x, y), kept on the screen.

    `anchor` is any widget of the window the menu belongs to. `colors` holds (light, dark) pairs
    for "bg", "border", "hover", "text" and "muted". Returns the function that closes the menu
    (closing twice is fine)."""
    import tkinter as tk
    import customtkinter as ctk

    root = anchor.winfo_toplevel()
    top = tk.Toplevel(root)
    top.withdraw()
    top.overrideredirect(True)
    try:
        top.attributes("-topmost", True)
    except tk.TclError:
        pass
    state: dict = {"closed": False, "bound": []}

    def close(_event=None) -> None:
        if state["closed"]:
            return
        state["closed"] = True
        for sequence, func_id in state["bound"]:
            try:
                root.unbind(sequence, func_id)
            except tk.TclError:
                pass
        try:
            top.destroy()
        except tk.TclError:
            pass

    def on_root_event(event) -> None:
        if event.type == tk.EventType.Configure and event.widget is not root:
            return                                          # a child's layout changed, not the window
        if event.type == tk.EventType.FocusOut:
            # Only when another application took the focus; the menu itself is part of this one.
            root.after(60, lambda: None if state["closed"] or root.focus_displayof() is not None else close())
            return
        close()

    frame = ctk.CTkFrame(top, corner_radius=0, border_width=1, fg_color=colors["bg"],
                         border_color=colors["border"])
    frame.pack()
    last = len(entries) - 1
    for n, (label, shortcut, command) in enumerate(entries):
        row = ctk.CTkFrame(frame, corner_radius=6, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(4 if n == 0 else 0, 4 if n == last else 0))
        parts = [row]
        name = ctk.CTkLabel(row, text=label, anchor="w", text_color=colors["text"])
        name.pack(side="left", padx=(10, 24), pady=4)
        parts.append(name)
        if shortcut:
            hint = ctk.CTkLabel(row, text=shortcut, anchor="e", text_color=colors["muted"])
            hint.pack(side="right", padx=(0, 10))
            parts.append(hint)

        def run(_event=None, command=command) -> None:
            close()
            command()

        for part in parts:
            part.bind("<Enter>", lambda _e, row=row: row.configure(fg_color=colors["hover"]), add="+")
            part.bind("<Leave>", lambda _e, row=row: row.configure(fg_color="transparent"), add="+")
            part.bind("<Button-1>", run, add="+")

    def watch_window() -> None:
        # Not before the click that opened the menu has been handled by every binding: the
        # window's own <Button-3> binding runs after the canvas's and would close it at once.
        if state["closed"]:
            return
        for sequence in _CLOSING_EVENTS:
            state["bound"].append((sequence, root.bind(sequence, on_root_event, add="+")))

    root.after_idle(watch_window)

    top.update_idletasks()
    width, height = top.winfo_reqwidth(), top.winfo_reqheight()
    x = max(0, min(x, top.winfo_screenwidth() - width))
    y = max(0, min(y, top.winfo_screenheight() - height))
    top.geometry(f"+{x}+{y}")
    top.deiconify()
    top.lift()
    return close
