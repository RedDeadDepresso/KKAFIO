# coding:utf-8
from typing import Union

from PySide6.QtGui import QIcon

from qfluentwidgets import ConfigItem, ExpandSettingCard, FluentIconBase, PlainTextEdit, SettingCard

from ..common.config import qconfig


class TextAreaSettingCard(ExpandSettingCard):
    def __init__(self, configItem: ConfigItem, icon: Union[str, QIcon, FluentIconBase],
                 title: str, content: str = None, placeholder: str = None, parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem = configItem
        self.configName = configItem.name

        self.textEdit = PlainTextEdit(self)
        self.textEdit.setPlaceholderText(placeholder or "")
        self.textEdit.setPlainText(qconfig.get(configItem))
        self.textEdit.setMinimumHeight(200)
        self.viewLayout.addWidget(self.textEdit)

        configItem.valueChanged.connect(self._onValueChanged)
        self.textEdit.textChanged.connect(self._onTextChanged)

    def _onTextChanged(self):
        qconfig.set(self.configItem, self.textEdit.toPlainText())

    def _onValueChanged(self, value: str):
        if self.textEdit.toPlainText() != value:
            self.textEdit.setPlainText(value)

    def text(self) -> str:
        return self.textEdit.toPlainText()

    def setText(self, text: str):
        self.textEdit.setPlainText(text)