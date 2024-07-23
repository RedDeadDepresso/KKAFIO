# coding:utf-8
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import PushButton

from app.common.signal_bus import signalBus


class NavigationActionButtons(QWidget):
    """ Navigation widget with select all and clear all buttons """

    def __init__(self):
        super().__init__()
        self.mainLayout = QVBoxLayout() 
        self.upperLayout = QHBoxLayout()
        self.lowerLayout = QHBoxLayout()

        self.selectAllButton = PushButton("Select All")
        self.clearAllButton = PushButton("Clear All")
        self.startButton = PushButton("Start")

        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.upperLayout.addWidget(self.selectAllButton, alignment=Qt.AlignVCenter)
        self.upperLayout.addWidget(self.clearAllButton, alignment=Qt.AlignVCenter)
        self.lowerLayout.addWidget(self.startButton, alignment=Qt.AlignVCenter)

        self.mainLayout.addLayout(self.upperLayout)  
        self.mainLayout.addSpacing(20)
        self.mainLayout.addLayout(self.lowerLayout)  

        self.setLayout(self.mainLayout) 

    def __connectSignalToSlot(self):
        self.selectAllButton.clicked.connect(self.onSelectAllClicked)
        self.clearAllButton.clicked.connect(self.onClearAllClicked)

        signalBus.startSignal.connect(lambda: self.startButton.setText("Stop"))
        signalBus.stopSignal.connect(lambda: self.startButton.setText("Start"))

    def onSelectAllClicked(self):
        signalBus.selectAllClicked.emit()

    def onClearAllClicked(self):
        signalBus.clearAllClicked.emit()

    def onStartClicked(self):
        text = self.startButton.text()
        if text == "Start":
            signalBus.startSignal.emit()
        elif text == "Stop":
            signalBus.stopSignal.emit()
