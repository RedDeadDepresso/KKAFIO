import os
from PySide6.QtCore import QProcess, Signal, QObject, Slot


class ScriptManager(QObject):
    def __init__(self, signalBus):
        super().__init__()
        self.signalBus = signalBus
        self.logger = signalBus.logger

        self._process = None
        self._line = '--------------------------------------------------------------------'

        self.__connectSignalToSlot()

    def __connectSignalToSlot(self):
        self.signalBus.startSignal.connect(self.start)
        self.signalBus.stopSignal.connect(self.stop)

    @Slot()
    def start(self):
        if self._process is not None:
            self.stop()
        else:
            if os.path.exists("script.exe"):
                args = ["script.exe"]
            elif os.path.exists("script.py"):
                args = ["python", "script.py"]
            else:
                self.logger.error("No valid script found to execute.")
                return

            self._process = QProcess(self)
            self._process.readyReadStandardOutput.connect(self.readOutput)
            self._process.readyReadStandardError.connect(self.readError)
            self._process.finished.connect(self.processFinished)

            if len(args) > 1:
                self._process.start(args[0], args[1:])
            else:
                self._process.start(args[0])

    @Slot()
    def readOutput(self):
        if self._process is not None:
            while self._process.canReadLine():
                line = str(self._process.readLine(), encoding='utf-8').strip()
                if line == self._line:
                    self.logger.line()
                else:
                    self.logger.colorize(line)

    @Slot()
    def readError(self):
        if self._process is not None:
            while self._process.canReadLine():
                error_line = str(self._process.readLine(), encoding='utf-8').strip()
                self.logger.error(f"Error: {error_line}", color='red')

    @Slot()
    def stop(self):
        if self._process is not None:
            self._process.kill()

    @Slot()
    def processFinished(self):
        """Slot called when the process finishes."""
        self._process = None
        self.signalBus.stopSignal.emit()

