"""
subprocess_utils.py — subprocess wrappers that decode text output safely.

`subprocess.run(..., text=True)` decodes the child's stdout/stderr using
Python's default text encoding (UTF-8). On Windows, console tools like 7-Zip
and PowerShell actually write using the system's legacy codepage (e.g.
cp932/Shift-JIS), not UTF-8. The moment a filename or pasted value contains a
non-ASCII byte that doesn't happen to be valid UTF-8, decoding raises
UnicodeDecodeError and takes down the whole task.

`run_text` / `popen_text` decode with the system's actual preferred encoding
and replace anything that still doesn't decode, instead of crashing.
"""

import locale
import subprocess


def _safe_text_kwargs() -> dict:
    return {
        "text": True,
        "encoding": locale.getpreferredencoding(False),
        "errors": "replace",
    }


def run_text(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run() with safe text decoding. Any text=/encoding=/errors=
    kwarg the caller passes explicitly takes precedence."""
    return subprocess.run(cmd, **{**_safe_text_kwargs(), **kwargs})


def popen_text(cmd: list[str], **kwargs) -> subprocess.Popen:
    """subprocess.Popen() with safe text decoding, same override rules as run_text()."""
    return subprocess.Popen(cmd, **{**_safe_text_kwargs(), **kwargs})
