# coding:utf-8
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QFrame, QFileDialog
from qfluentwidgets import FluentIcon as FIF, PushButton, TextEdit, DisplayLabel, InfoBar

from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet


class LoggerInterface(QFrame):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.mainLayout  = QVBoxLayout()
        self.topLayout   = QHBoxLayout()
        self.loggerLabel = DisplayLabel(self.tr("Log"), self)
        self.exportButton = PushButton(self.tr("Export"), self, FIF.SAVE)
        self.clearButton  = PushButton(self.tr("Clear"),  self, FIF.DELETE)
        self.loggerBox    = TextEdit()
        self.loggerBox.setReadOnly(True)

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setObjectName('loggerInterface')
        self.loggerLabel.setObjectName('settingLabel')
        StyleSheet.SETTING_INTERFACE.apply(self)
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.topLayout.addWidget(self.loggerLabel, alignment=Qt.AlignLeft)
        self.topLayout.addStretch(1)
        self.topLayout.addWidget(self.exportButton, alignment=Qt.AlignRight)
        self.topLayout.addWidget(self.clearButton,  alignment=Qt.AlignRight)

        self.mainLayout.addLayout(self.topLayout)
        self.mainLayout.setSpacing(28)
        self.mainLayout.addWidget(self.loggerBox)

        self.setLayout(self.mainLayout)
        self.setContentsMargins(36, 10, 36, 28)

    def __connectSignalToSlot(self):
        signalBus.loggerSignal.connect(self.loggerBox.append)
        self.clearButton.clicked.connect(self.loggerBox.clear)
        self.exportButton.clicked.connect(self.__onExportClicked)

    def __onExportClicked(self):
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save log"), "kkafio_log.txt",
            self.tr("Text files (*.txt);;All files (*)")
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.loggerBox.toPlainText())
        except Exception as e:
            InfoBar.error(self.tr("Error"), str(e), parent=self)
