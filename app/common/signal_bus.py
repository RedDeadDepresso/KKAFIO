# coding: utf-8
from PySide6.QtCore import QObject, Signal, QThreadPool
from qfluentwidgets import SettingCardGroup
from app.common.logger import Logger
from app.common.script_manager import ScriptManager


class SignalBus(QObject):
    """ Signal bus """

    switchToSettingGroup = Signal(SettingCardGroup)
    micaEnableChanged = Signal(bool)
    supportSignal = Signal()

    selectAllClicked = Signal()
    clearAllClicked = Signal()
    disableStartSignal = Signal(bool)
    startSignal = Signal()
    stopSignal = Signal()
    loggerSignal = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.logger = Logger(self)
        self.scriptManager = ScriptManager(self)
        self.threadPool = QThreadPool(self)

    def scriptRunning(self) -> bool:
        return self.scriptManager.scriptRunning()

signalBus = SignalBus()