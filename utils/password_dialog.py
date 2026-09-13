"""
password_dialog.py — Show a native password prompt.

On Windows uses a PowerShell WinForms dialog (same approach as
llm_dialog.py) so no extra runtime or Tcl/Tk dependency is needed. The
dialog process is tied to this process's lifetime via a Windows Job Object
(see utils/job_object.py), so it's automatically closed if this process is
killed while the dialog is still open.
On macOS/Linux falls back to a terminal prompt.
"""

import os
import subprocess
import sys
import tempfile

from utils.job_object import die_with_parent


def password_dialog(title: str, content: str) -> str:
    if sys.platform == "win32":
        return _powershell_dialog(title, content)
    return _terminal_prompt(title, content)


def _powershell_dialog(title: str, content: str) -> str:
    """
    Show a WinForms window with a masked password TextBox plus OK / Cancel
    buttons. Returns the entered password, or '' if cancelled.
    """
    t = title.replace("'", "''")
    c = content.replace("'", "''")

    # Route the result through a UTF-8 temp file instead of stdout — Windows
    # PowerShell's console pipe encoding isn't guaranteed to be UTF-8, so
    # decoding arbitrary typed text (which can contain non-ASCII characters)
    # on the Python side can raise UnicodeDecodeError. A file written with an
    # explicit UTF-8 encoding sidesteps that entirely.
    fd, response_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        r = response_path.replace("'", "''")

        ps = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class KKAFIOWin32 {{
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}}
"@

$form = New-Object System.Windows.Forms.Form
$form.Text = '{t}'
$form.Width = 420
$form.Height = 200
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.StartPosition = 'CenterScreen'
$form.Topmost = $true

$label = New-Object System.Windows.Forms.Label
$label.Text = '{c}'
$label.AutoSize = $false
$label.Left = 10
$label.Top = 15
$label.Width = 384
$label.Height = 40
$form.Controls.Add($label)

$box = New-Object System.Windows.Forms.TextBox
$box.PasswordChar = '*'
$box.Left = 10
$box.Top = 60
$box.Width = 384
$form.Controls.Add($box)

$okBtn = New-Object System.Windows.Forms.Button
$okBtn.Text = 'OK'
$okBtn.Left = 214
$okBtn.Top = 100
$okBtn.Width = 90
$okBtn.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($okBtn)

$cancelBtn = New-Object System.Windows.Forms.Button
$cancelBtn.Text = 'Cancel'
$cancelBtn.Left = 314
$cancelBtn.Top = 100
$cancelBtn.Width = 90
$cancelBtn.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancelBtn)

$form.AcceptButton = $okBtn
$form.CancelButton = $cancelBtn

# Force the window to the foreground even though it's launched from a
# background/hidden process — Topmost alone isn't always enough, since
# Windows can otherwise refuse to grant a newly created window focus
# ("foreground lock").
$form.Add_Shown({{
    $form.Activate()
    [KKAFIOWin32]::SetForegroundWindow($form.Handle) | Out-Null
    $box.Focus()
}})

$result = $form.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText('{r}', $box.Text, $utf8NoBom)
}}
"""

        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x0800_0000,  # CREATE_NO_WINDOW (for the powershell.exe console)
            )
            die_with_parent(proc)
            proc.wait()
            if os.path.isfile(response_path) and os.path.getsize(response_path) > 0:
                with open(response_path, "r", encoding="utf-8") as f:
                    return f.read()
            return ""  # dialog was cancelled — no response file was written
        except Exception:
            return _terminal_prompt(title, content)
    finally:
        try:
            os.unlink(response_path)
        except OSError:
            pass


def _terminal_prompt(title: str, content: str) -> str:
    import getpass
    print(f"\n{title}\n{content}")
    return getpass.getpass("Password: ")


if __name__ == "__main__":
    pwd = password_dialog("Enter Password", "Please enter the archive password:")
    print(f"Password: {pwd}")