import os 
import subprocess
import threading

from app.common.signal_bus import signalBus
from app.common.logger import logger


class ThreadManager:    
    def __init__(self):
        super().__init__()
        self._script = None

    def start(self):
        if self._script is not None:
            self.stop()
        else:
            args = []
            if os.path.exists("script.exe"):
                args = ["script.exe"]
            elif os.path.exists("script.py"):
                args = ["python", "script.py"]
            self._script = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            threading.Thread(target=self.readOutput).start()

    def readOutput(self):
        while self._script is not None:
            line = self._script.stdout.readline().decode('utf-8')
            if not line:
                signalBus.stopSignal.emit()
            else:
                logger.colorize(line)

    def stop(self):
        if self._script is not None:
            self._script.terminate()
            self._script = None


threadManager = ThreadManager()
signalBus.startSignal.connect(threadManager.start)
signalBus.stopSignal.connect(threadManager.stop)