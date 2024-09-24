import os
import pathlib
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileDialog
from qfluentwidgets import SettingCard, FluentIconBase, FluentIcon, CommandBar, Action, LineEdit, LineEditButton
from typing import Union
from ..common.config import cfg
    

class FolderLineEdit(LineEdit):
    """ Search line edit """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.validButton = LineEditButton(FluentIcon.ACCEPT, self)
        self.hBoxLayout.addWidget(self.validButton, 0, Qt.AlignRight)
        self.setTextMargins(0, 0, 59, 0)

    def setValid(self, value: bool):
        if value:
            self.validButton._icon = FluentIcon.ACCEPT
        else:
            self.validButton._icon = FluentIcon.CLOSE


class FolderSettingCard(SettingCard):
    def __init__(self, configItem, icon: Union[str, QIcon, FluentIconBase], title, content=None, parent=None):
        """
        Parameters
        ----------
        configItem: TimeConfigItem
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
        self.lineEdit = FolderLineEdit()
        self.lineEdit.setText(configItem.value)
        
        self.__initCommandBar()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initCommandBar(self):
        self.commandBar = CommandBar()
        explorerAction = Action(FluentIcon.FOLDER, 'Show in Explorer')
        explorerAction.triggered.connect(self.openExplorer)
        self.commandBar.addHiddenAction(explorerAction)

        browseAction = Action(FluentIcon.EDIT, 'Browse')
        browseAction.triggered.connect(self.browse)
        self.commandBar.addHiddenAction(browseAction)

    def __initLayout(self):
        self.setFixedHeight(70)
        self.vBoxLayout.addWidget(self.lineEdit)
        self.hBoxLayout.setStretch(2, 10)
        self.hBoxLayout.addSpacing(8)
        self.hBoxLayout.addWidget(self.commandBar, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def __connectSignalToSlot(self):
        self.lineEdit.textChanged.connect(self.pathValid)

    def pathValid(self, text):
        if not text:
            cfg.set(self.configItem, "")
            self.lineEdit.setValid(True)
            return
        
        path = pathlib.Path(text)
        if path.is_absolute() and path.is_dir() and path.exists():
            cfg.set(self.configItem, text)
            self.lineEdit.setValid(True)
        else:
            self.lineEdit.setValid(False)

    def openExplorer(self):
        if self.configItem.value:
            file_path = os.path.normpath(self.configItem.value)        
            subprocess.Popen(f'explorer /select,"{file_path}"')

    def browse(self):
        """ download folder card clicked slot """
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Choose folder"), "./")
        if not folder or cfg.get(self.configItem) == folder:
            return
        self.lineEdit.setText(folder)