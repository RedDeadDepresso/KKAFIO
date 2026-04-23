# coding:utf-8
"""
Workers for the Group Chara copy/paste buttons in SettingInterface.

Copy worker: shells out to  kkafio_cli  group-chara --export
  so no heavy modules (kkloader, msgpack, …) are imported by the GUI process.
  stdout is the prompt + JSON ready to be placed on the clipboard.
  Exit code 0 = success, anything else = failure.

Paste worker: validates the clipboard text is legal JSON (pure Python, no
  external modules needed).
"""

import json
import sys
import subprocess
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal


def _cli_path() -> str:
    """Return the absolute path to kkafio_cli (exe when frozen, .py from source)."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent / "kkafio_cli.exe")
    return str(Path(__file__).resolve().parents[2] / "kkafio_cli.py")


def _cli_cmd(args: list[str]) -> list[str]:
    """Build a command list for subprocess, prepending the Python interpreter when running from source."""
    cli = _cli_path()
    if getattr(sys, "frozen", False):
        return [cli] + args
    return [sys.executable, "-u", cli] + args


# ---------------------------------------------------------------------------
# Copy worker
# ---------------------------------------------------------------------------

class _CopySignalBus(QObject):
    # (clipboard_text, error_message) — error is empty string on success
    finishSignal = Signal(str, str)


class GroupCharaCopyWorker(QRunnable):
    """Run  kkafio_cli group-chara --export  off the main thread.

    Captures stdout as the clipboard text.  Treats a non-zero exit code or
    any stderr output as an error.
    """

    def __init__(self, folder: str, prompt: str,
                 include_subfolders: bool = False) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.folder            = folder
        self.prompt            = prompt
        self.include_subfolders = include_subfolders
        self._bus              = _CopySignalBus()
        self.finishSignal      = self._bus.finishSignal

    def run(self) -> None:
        try:
            args = ["group-chara", "--export", "--input", self.folder]
            if self.include_subfolders:
                args.append("--include-subfolders")

            result = subprocess.run(
                _cli_cmd(args),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            if result.returncode != 0:
                error = result.stderr.strip() or result.stdout.strip() or \
                        f"CLI exited with code {result.returncode}"
                self.finishSignal.emit("", error)
                return

            stdout = result.stdout.strip()
            if not stdout:
                self.finishSignal.emit(
                    "", "No character cards found in the selected folder.")
                return

            # The CLI prints the built-in prompt + JSON.
            # Replace the built-in prompt with the user's custom one from settings.
            json_start = stdout.find("{")
            json_only  = stdout[json_start:] if json_start != -1 else stdout
            full_text  = self.prompt.rstrip() + "\n" + json_only
            self.finishSignal.emit(full_text, "")

        except Exception:
            self.finishSignal.emit("", traceback.format_exc())


# ---------------------------------------------------------------------------
# Paste worker — validate JSON only, no subprocess needed
# ---------------------------------------------------------------------------

class _PasteSignalBus(QObject):
    # error_message — empty string on success
    finishSignal = Signal(str)


class GroupCharaPasteWorker(QRunnable):
    """Validate that the clipboard text is legal JSON off the main thread."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.text     = text
        self._bus     = _PasteSignalBus()
        self.finishSignal = self._bus.finishSignal

    def run(self) -> None:
        try:
            clean = self.text.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.splitlines()[1:])
            if clean.endswith("```"):
                clean = "\n".join(clean.splitlines()[:-1])
            json.loads(clean.strip())
            self.finishSignal.emit("")
        except json.JSONDecodeError:
            self.finishSignal.emit(
                "Clipboard does not contain valid JSON. "
                "Make sure you copied the LLM's full response and try again."
            )
        except Exception:
            self.finishSignal.emit(traceback.format_exc())
