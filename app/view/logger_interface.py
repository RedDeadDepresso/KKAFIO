# coding:utf-8
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QFrame
from qfluentwidgets import PushButton, TextEdit, DisplayLabel

from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet


class LoggerInterface(QFrame):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        # setting label
        self.mainLayout = QVBoxLayout()
        self.topLayout = QHBoxLayout()
        self.loggerLabel = DisplayLabel(self.tr("Log"), self)
        self.clearButton = PushButton(self.tr('Clear'))
        self.loggerBox = TextEdit()
        self.loggerBox.setReadOnly(True)

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setObjectName('loggerInterface')

        # initialize style sheet
        self.loggerLabel.setObjectName('settingLabel')
        StyleSheet.SETTING_INTERFACE.apply(self)

        # initialize layout
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.topLayout.addWidget(self.loggerLabel, alignment=Qt.AlignLeft)
        self.topLayout.addWidget(self.clearButton, alignment=Qt.AlignRight)

        self.mainLayout.addLayout(self.topLayout)
        self.mainLayout.setSpacing(28)
        self.mainLayout.addWidget(self.loggerBox)

        self.setLayout(self.mainLayout)
        self.setContentsMargins(36, 10, 36, 28)

    def __connectSignalToSlot(self):
        signalBus.loggerSignal.connect(self.loggerBox.append)
        self.clearButton.clicked.connect(self.loggerBox.clear)
