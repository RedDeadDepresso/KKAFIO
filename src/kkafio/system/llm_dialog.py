"""
llm_dialog.py — Show a "Copy prompt / Paste LLM response" dialog.

Used by tasks that need a human to relay text through an external LLM
(GroupChara, RenameChara): the task builds a prompt + JSON blob, shows it in
this dialog, the user clicks Copy, pastes it into their LLM of choice, copies
the reply, and clicks Paste — which reads the clipboard and returns it to the
caller so the task can immediately process it (move/rename files) in the same
run.

On Windows uses a CustomTkinter window (see dialog_common.py for the shared
always-on-top / foreground / centering behaviour). It runs in-process and blocks
the calling thread until the user answers, so it disappears automatically if
this process is killed — no child process or Job Object is involved.
On macOS/Linux falls back to a terminal prompt.
"""

import sys

from kkafio.system.dialog_common import new_window, show_and_wait

_COPIED_FEEDBACK_MS = 1500  # how long the Copy button reads "Copied!"


def llm_dialog(title: str, prompt_text: str) -> str:
    """Show the prompt+JSON to the user and return whatever they paste back.

    Returns an empty string if the user cancels the dialog.
    """
    if sys.platform == "win32":
        try:
            return _ctk_dialog(title, prompt_text)
        except Exception:
            return _terminal_dialog(title, prompt_text)
    return _terminal_dialog(title, prompt_text)


def _ctk_dialog(title: str, prompt_text: str) -> str:
    """
    Show a resizable window with a read-only, selectable textbox containing
    prompt_text, plus Copy / Paste / Cancel buttons.

    - Copy  → puts the prompt on the clipboard. This also happens automatically
              when the dialog opens; the button briefly reads "Copied!" each time.
    - Paste → reads the clipboard and closes the dialog, returning that text.
    - Cancel / Esc / closing the window → returns an empty string.
    Enter = Paste. No timeout: the user may take a while with their LLM.
    """
    import tkinter as tk
    import customtkinter as ctk

    root = new_window(title)
    root.minsize(480, 360)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(1, weight=1)  # the textbox takes all extra space

    result = {"value": ""}
    copy_feedback = {"id": None}

    def on_copy() -> None:
        root.clipboard_clear()
        root.clipboard_append(prompt_text)
        # Tk on Windows renders clipboard data lazily; processing pending
        # events right away makes it stick even if the dialog closes soon.
        root.update()
        # Brief "Copied!" feedback; clicking again restarts the timer.
        if copy_feedback["id"] is not None:
            root.after_cancel(copy_feedback["id"])
        copy_btn.configure(text="Copied!")
        copy_feedback["id"] = root.after(_COPIED_FEEDBACK_MS, reset_copy_label)

    def reset_copy_label() -> None:
        copy_feedback["id"] = None
        try:
            copy_btn.configure(text="Copy")
        except tk.TclError:  # dialog already closed
            pass

    def on_paste(_event=None) -> None:
        try:
            clip = root.clipboard_get()
        except tk.TclError:  # empty clipboard or non-text content
            clip = ""
        result["value"] = clip.strip()
        root.quit()

    def on_cancel(_event=None) -> None:
        root.quit()

    label = ctk.CTkLabel(
        root,
        text=(
            "The prompt is on your clipboard (click Copy to copy it again). "
            "Paste it into your LLM, then copy its reply and click Paste."
        ),
        wraplength=680,
        justify="left",
        anchor="w",
    )
    label.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")

    box = ctk.CTkTextbox(
        root,
        width=680,
        height=460,
        wrap="none",
        font=ctk.CTkFont(family="Consolas", size=12),
    )
    box.grid(row=1, column=0, padx=10, pady=(4, 0), sticky="nsew")
    box.insert("1.0", prompt_text)
    box.configure(state="disabled")  # read-only…
    # …but still selectable/copyable: a disabled Text widget doesn't take
    # focus on click by itself, so Ctrl+C would otherwise go nowhere.
    box.bind("<Button-1>", lambda _e: box.focus_set())

    bar = ctk.CTkFrame(root, fg_color="transparent")
    bar.grid(row=2, column=0, padx=10, pady=10, sticky="ew")
    bar.grid_columnconfigure(1, weight=1)
    ctk.CTkButton(bar, text="Cancel", width=100, command=on_cancel).grid(row=0, column=0, sticky="w")
    copy_btn = ctk.CTkButton(bar, text="Copy", width=100, command=on_copy)
    copy_btn.grid(row=0, column=2, padx=(0, 10))
    ctk.CTkButton(bar, text="Paste", width=120, command=on_paste).grid(row=0, column=3)

    root.bind("<Return>", on_paste)
    root.bind("<Escape>", on_cancel)
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    # Copy automatically on open so the user can paste straight into their LLM.
    try:
        show_and_wait(root, on_shown=on_copy)
    finally:
        if copy_feedback["id"] is not None:  # don't leave the revert timer behind
            try:
                root.after_cancel(copy_feedback["id"])
            except tk.TclError:
                pass
    return result["value"]


def _terminal_dialog(title: str, prompt_text: str) -> str:
    print(f"\n{title}\n{'=' * len(title)}\n")
    print(prompt_text)
    print(
        "\nCopy the text above into your LLM, then paste its response below.\n"
        "Press Enter on an empty line when done (or leave empty to cancel):"
    )
    lines: list[str] = []
    try:
        while True:
            line = input()
            if not line and (not lines or not lines[-1]):
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


if __name__ == "__main__":
    reply = llm_dialog("KKAFIO — Example", "Please translate this JSON:\n{\n    \"key\": \"\"\n}")
    print(f"Got: {reply!r}")