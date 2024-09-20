from PySide6.QtCore import QThread, QProcess, Signal, QObject
import os

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
                
    def readOutput(self):
        if self._process is not None:
            while self._process.canReadLine():
                line = str(self._process.readLine(), encoding='utf-8').strip()
                if line == self._line:
                    self.logger.line()
                else:
                    self.logger.colorize(line)

    def readError(self):
        if self._process is not None:
            while self._process.canReadLine():
                error_line = str(self._process.readLine(), encoding='utf-8').strip()
                self.logger.colorize(f"Error: {error_line}", color='red')

    def processFinished(self):
        """Slot called when the process finishes."""
        self.signalBus.stopSignal.emit()

    def stop(self):
        if self._process is not None:
            self._process.terminate()
            self._process.waitForFinished()
            self._process = None
