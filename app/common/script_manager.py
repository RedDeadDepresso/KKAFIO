import json
import sys
import psutil

from pathlib import Path
from PySide6.QtCore import QProcess, QObject, Slot, QRunnable


class ScriptCleaner(QRunnable):
    def __init__(self, backupInfoPath: Path) -> None:
        super().__init__()
        self.backupInfoPath = backupInfoPath

    def run(self):
        if self.backupInfoPath.exists():
            try:
                with open(self.backupInfoPath, "r") as f:
                    backupInfo = json.load(f)
                proc7zip = psutil.Process(backupInfo['PID'])
                proc7zip.terminate()
                proc7zip.wait()
                Path(backupInfo['ArchivePath']).unlink(missing_ok=True)
                self.backupInfoPath.unlink(missing_ok=True)
            except Exception as e:
                print(e)


class ScriptManager(QObject):
    def __init__(self, signalBus):
        super().__init__()
        self.signalBus = signalBus
        self.logger = signalBus.logger
        self.procScript = None

        self.__connectSignalToSlot()

    def __connectSignalToSlot(self):
        self.signalBus.startSignal.connect(self.start)
        self.signalBus.stopSignal.connect(self.stop)

    @Slot()
    def start(self):
        if self.procScript is not None:
            self.stop()
        else:
            if Path("script.exe").exists():
                args = ["script.exe"]
            elif Path("script.py").exists():
                args = [sys.executable, "-u", "script.py"]  # -u disables stdout/stderr buffering
            else:
                self.logger.error("No valid script found to execute.")
                return

            self.procScript = QProcess(self)
            self.procScript.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            self.procScript.readyRead.connect(self.readOutput)
            self.procScript.finished.connect(self.processFinished)

            if len(args) > 1:
                self.procScript.start(args[0], args[1:])
            else:
                self.procScript.start(args[0])

    @Slot()
    def readOutput(self):
        if self.procScript is not None:
            while self.procScript.canReadLine():
                line = str(self.procScript.readLine(), encoding='utf-8').strip()
                if line:
                    self.logger.colorize(line)
            # Flush any remaining partial line (output not ending in newline)
            remaining = self.procScript.read(self.procScript.bytesAvailable())
            if remaining:
                for line in str(remaining, encoding='utf-8').splitlines():
                    line = line.strip()
                    if line:
                        self.logger.colorize(line)

    @Slot()
    def stop(self):
        if self.procScript is not None:
            self.procScript.kill()
            scriptCleaner = ScriptCleaner(Path('app/config/7zip.json'))
            self.signalBus.threadPool.start(scriptCleaner)

    @Slot()
    def processFinished(self):
        """Slot called when the process finishes naturally."""
        self.procScript = None
        scriptCleaner = ScriptCleaner(Path('app/config/7zip.json'))
        self.signalBus.threadPool.start(scriptCleaner)
        self.signalBus.stopSignal.emit()

    def scriptRunning(self) -> bool:
        return self.procScript is not None

