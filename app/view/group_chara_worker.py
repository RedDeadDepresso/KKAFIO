# coding:utf-8
import json
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal


class _CopySignalBus(QObject):
    # Emits (clipboard_text, error_message) — error is empty string on success
    finishSignal = Signal(str, str)


class GroupCharaCopyWorker(QRunnable):
    """Scan folder for chara PNGs and build the LLM prompt string off the main thread."""

    def __init__(self, folder: str, prompt: str, include_subfolders: bool = False) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.folder = folder
        self.prompt = prompt
        self.include_subfolders = include_subfolders
        self._bus = _CopySignalBus()
        self.finishSignal = self._bus.finishSignal

    def run(self):
        try:
            from modules.group_chara import export
            result = export(Path(self.folder), include_subfolders=self.include_subfolders)
            if not result:
                self.finishSignal.emit("", "No readable character cards found in the selected folder.")
                return

            # result contains the built-in prompt + JSON; we replace the prompt
            # with whatever the user has typed in the settings text area.
            json_start = result.find('{')
            json_only  = result[json_start:] if json_start != -1 else result
            full_text  = self.prompt.rstrip() + "\n" + json_only
            self.finishSignal.emit(full_text, "")
        except Exception:
            self.finishSignal.emit("", traceback.format_exc())


class _PasteSignalBus(QObject):
    # Emits error_message — empty string on success
    finishSignal = Signal(str)


class GroupCharaPasteWorker(QRunnable):
    """Validate the clipboard JSON off the main thread."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.text = text
        self._bus = _PasteSignalBus()
        self.finishSignal = self._bus.finishSignal

    def run(self):
        try:
            clean = self.text.strip()
            # Strip markdown fences the LLM sometimes adds
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
