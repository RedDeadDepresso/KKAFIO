"""
llm_dialog.py — Show a native "Copy prompt / Paste LLM response" dialog.

Used by tasks that need a human to relay text through an external LLM
(GroupChara, RenameChara): the task builds a prompt + JSON blob, shows it in
this dialog, the user clicks Copy, pastes it into their LLM of choice, copies
the reply, and clicks Paste — which reads the clipboard and returns it to the
caller so the task can immediately process it (move/rename files) in the same
run.

On Windows uses a PowerShell WinForms dialog (same approach as
password_dialog.py) so no extra runtime or Tcl/Tk dependency is needed.
On macOS/Linux falls back to a terminal prompt.
"""

import os
import subprocess
import sys
import tempfile


def llm_dialog(title: str, prompt_text: str) -> str:
    """Show the prompt+JSON to the user and return whatever they paste back.

    Returns an empty string if the user cancels the dialog.
    """
    if sys.platform == "win32":
        return _powershell_dialog(title, prompt_text)
    return _terminal_dialog(title, prompt_text)


def _powershell_dialog(title: str, prompt_text: str) -> str:
    """
    Show a WinForms window with a read-only textbox containing prompt_text,
    plus Copy / Paste / Cancel buttons.

    - Copy  → puts the textbox content on the clipboard.
    - Paste → reads the clipboard and closes the dialog, returning that text.
    - Cancel / closing the window → returns an empty string.
    """
    t = title.replace("'", "''")

    # Route prompt in / response out through UTF-8 temp files instead of
    # stdin/stdout. Windows PowerShell's console pipe encoding is NOT
    # guaranteed to be UTF-8 (it's typically the system's legacy codepage),
    # so Write-Output-ing arbitrary pasted text (which can contain CJK
    # characters, etc.) and decoding it as UTF-8 on the Python side can
    # raise UnicodeDecodeError. Files written/read with an explicit UTF-8
    # encoding sidestep that entirely.
    fd, prompt_path = tempfile.mkstemp(suffix=".txt")
    response_path = prompt_path + ".response.txt"
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt_text)

        r = response_path.replace("'", "''")
        p = prompt_path.replace("'", "''")

        ps = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$promptText = [System.IO.File]::ReadAllText('{p}', [System.Text.Encoding]::UTF8)

$form = New-Object System.Windows.Forms.Form
$form.Text = '{t}'
$form.Width = 720
$form.Height = 600
$form.StartPosition = 'CenterScreen'
$form.Topmost = $true
$form.MinimumSize = New-Object System.Drawing.Size(480, 360)

$label = New-Object System.Windows.Forms.Label
$label.Text = 'Click Copy, paste into your LLM, then paste its reply here and click Paste.'
$label.AutoSize = $false
$label.Anchor = 'Top,Left,Right'
$label.Left = 10
$label.Top = 10
$label.Width = 680
$label.Height = 36
$form.Controls.Add($label)

$box = New-Object System.Windows.Forms.TextBox
$box.Multiline = $true
$box.ScrollBars = 'Vertical'
$box.ReadOnly = $true
$box.WordWrap = $false
$box.Font = New-Object System.Drawing.Font('Consolas', 9)
$box.Anchor = 'Top,Bottom,Left,Right'
$box.Left = 10
$box.Top = 50
$box.Width = 680
$box.Height = 460
$form.Controls.Add($box)
$box.Text = $promptText

$cancelBtn = New-Object System.Windows.Forms.Button
$cancelBtn.Text = 'Cancel'
$cancelBtn.Anchor = 'Bottom,Left'
$cancelBtn.Left = 10
$cancelBtn.Top = 520
$cancelBtn.Width = 100
$cancelBtn.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancelBtn)

$copyBtn = New-Object System.Windows.Forms.Button
$copyBtn.Text = 'Copy'
$copyBtn.Anchor = 'Bottom,Right'
$copyBtn.Left = 460
$copyBtn.Top = 520
$copyBtn.Width = 100
$copyBtn.Add_Click({{ [System.Windows.Forms.Clipboard]::SetText($box.Text) }})
$form.Controls.Add($copyBtn)

$pasteBtn = New-Object System.Windows.Forms.Button
$pasteBtn.Text = 'Paste'
$pasteBtn.Anchor = 'Bottom,Right'
$pasteBtn.Left = 570
$pasteBtn.Top = 520
$pasteBtn.Width = 120
$pasteBtn.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($pasteBtn)

$form.AcceptButton = $pasteBtn
$form.CancelButton = $cancelBtn

$result = $form.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    try {{
        $clip = [System.Windows.Forms.Clipboard]::GetText()
    }} catch {{
        $clip = ''
    }}
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText('{r}', $clip, $utf8NoBom)
}}
"""

        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True,
                creationflags=0x0800_0000,  # CREATE_NO_WINDOW (for the powershell.exe console)
            )
            if os.path.isfile(response_path):
                with open(response_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            return ""  # dialog was cancelled — no response file was written
        except Exception:
            return _terminal_dialog(title, prompt_text)
    finally:
        for p in (prompt_path, response_path):
            try:
                os.unlink(p)
            except OSError:
                pass


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