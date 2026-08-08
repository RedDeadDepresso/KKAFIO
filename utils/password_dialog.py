"""
password_dialog.py — Show a native password prompt.

On Windows uses a PowerShell SecureString input box which works on every
Windows version with no extra runtime or Tcl/Tk needed.
On macOS/Linux falls back to a terminal prompt.
"""

import sys


def password_dialog(title: str, content: str) -> str:
    if sys.platform == "win32":
        return _powershell_dialog(title, content)
    return _terminal_prompt(title, content)


def _powershell_dialog(title: str, content: str) -> str:
    """
    Show a Windows Forms InputBox via PowerShell.
    Returns the entered password or '' if cancelled.
    """
    import subprocess

    # Escape single quotes in the strings
    t = title.replace("'", "''")
    c = content.replace("'", "''")

    ps = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        f"[Microsoft.VisualBasic.Interaction]::InputBox('{c}', '{t}', '')"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=0x0800_0000,  # CREATE_NO_WINDOW
        )
        return result.stdout.strip()
    except Exception:
        return _terminal_prompt(title, content)


def _terminal_prompt(title: str, content: str) -> str:
    import getpass
    print(f"\n{title}\n{content}")
    return getpass.getpass("Password: ")


if __name__ == "__main__":
    pwd = password_dialog("Enter Password", "Please enter the archive password:")
    print(f"Password: {pwd}")