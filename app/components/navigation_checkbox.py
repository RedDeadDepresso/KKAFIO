# coding:utf-8
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QWidget

from qfluentwidgets import ToolButton, CheckBox
from qfluentwidgets.common.icon import FluentIcon as FIF

from app.common.signal_bus import signalBus


class NavigationCheckBox(QWidget):
    def __init__(self, text, settingGroup) -> None:
        super().__init__()
        self.layout = QHBoxLayout()
        self.checkBox = CheckBox(text)
        self.toolButton = ToolButton(FIF.SETTING)
        self.settingGroup = settingGroup

        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.layout.addWidget(self.checkBox, alignment=Qt.AlignmentFlag.AlignLeft)
        self.layout.addWidget(self.toolButton, alignment=Qt.AlignmentFlag.AlignRight)
        self.setLayout(self.layout)

    def __connectSignalToSlot(self):
        self.toolButton.clicked.connect(lambda: signalBus.switchToSettingGroup.emit(self.settingGroup))
        signalBus.selectAllClicked.connect(lambda: self.checkBox.setChecked(True))
        signalBus.clearAllClicked.connect(lambda: self.checkBox.setChecked(False))





    