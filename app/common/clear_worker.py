from send2trash import send2trash
import traceback

from pathlib import Path
from PySide6.QtCore import QRunnable, Signal, QObject


class ClearSignalBus(QObject):
    finishSignal = Signal(bool)


class ClearWorker(QRunnable):
    def __init__(self, path: Path, deleteFolder=False) -> None:
        super().__init__()
        self.path = path
        self.deleteFolder = deleteFolder
        self.clearSignalBus = ClearSignalBus()
        self.finishSignal = self.clearSignalBus.finishSignal

    def run(self):
        try:
            if self.deleteFolder:
                send2trash(self.path)
            else:
                self.deleteContents()
            self.finishSignal.emit(True)
        except Exception as e:
            traceback.print_exc()
            self.finishSignal.emit(False)
    
    def deleteContents(self):
        for item in self.path.iterdir():
            send2trash(item)

