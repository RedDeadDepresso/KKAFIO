# coding:utf-8
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QWidget

from qfluentwidgets import ToolButton, CheckBox
from qfluentwidgets.common.icon import FluentIcon as FIF

from app.common.signal_bus import signalBus
from app.common.config import cfg


class NavigationCheckBox(QWidget):
    def __init__(self, text, configItem, settingGroup) -> None:
        super().__init__()
        self.layout = QHBoxLayout()
        self.checkBox = CheckBox(text)
        self.configItem = configItem
        self.loadCheckState()
        self.toolButton = ToolButton(FIF.SETTING)
        self.settingGroup = settingGroup

        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.layout.addWidget(self.checkBox, alignment=Qt.AlignmentFlag.AlignLeft)
        self.layout.addWidget(self.toolButton, alignment=Qt.AlignmentFlag.AlignRight)
        self.setLayout(self.layout)

    def __connectSignalToSlot(self):
        self.checkBox.stateChanged.connect(self.saveCheckState)
        self.toolButton.clicked.connect(lambda: signalBus.switchToSettingGroup.emit(self.settingGroup))
        signalBus.selectAllClicked.connect(lambda: self.checkBox.setChecked(True))
        signalBus.clearAllClicked.connect(lambda: self.checkBox.setChecked(False))

    def loadCheckState(self):
        value = cfg.get(self.configItem)
        if value:
            self.checkBox.setCheckState(Qt.CheckState.Checked)
        else:
            self.checkBox.setCheckState(Qt.CheckState.Unchecked)

    def saveCheckState(self, state):
        isChecked = state > 0
        cfg.set(self.configItem, isChecked)





    