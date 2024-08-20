# coding:utf-8
from typing import Union

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from qfluentwidgets import ConfigItem, FluentIconBase, LineEdit, SettingCard

from ..common.config import qconfig


class LineEditSettingCard(SettingCard):
    """ Setting card with a line edit """

    def __init__(self, configItem: ConfigItem, icon: Union[str, QIcon, FluentIconBase], title, content=None, parent=None):
        """
        Parameters
        ----------
        configItem: OptionsConfigItem
            configuration item operated by the card

        icon: str | QIcon | FluentIconBase
            the icon to be drawn

        title: str
            the title of card

        content: str
            the content of card

        parent: QWidget
            parent widget
        """
        super().__init__(icon, title, content, parent)
        self.configItem = configItem
        self.lineEdit = LineEdit(self)
        self.hBoxLayout.addWidget(self.lineEdit, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

        self.lineEdit.setText(qconfig.get(configItem))
        configItem.valueChanged.connect(self.setValue)
        self.lineEdit.textChanged.connect(self.setValue)

    def setValue(self, value):
        qconfig.set(self.configItem, value)