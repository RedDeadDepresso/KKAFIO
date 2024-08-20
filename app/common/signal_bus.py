# coding: utf-8
from PySide6.QtCore import QObject, Signal
from qfluentwidgets import SettingCardGroup


class SignalBus(QObject):
    """ Signal bus """

    switchToSettingGroup = Signal(SettingCardGroup)
    micaEnableChanged = Signal(bool)
    supportSignal = Signal()

    selectAllClicked = Signal()
    clearAllClicked = Signal()
    startSignal = Signal()
    stopSignal = Signal()
    loggerSignal = Signal(str)


signalBus = SignalBus()