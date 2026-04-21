# coding:utf-8
from typing import List, Union
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPainter, QIcon
from PySide6.QtWidgets import (QPushButton, QFileDialog, QWidget, QLabel,
                               QHBoxLayout, QToolButton, QSizePolicy)

from qfluentwidgets import FluentIconBase, ToolButton, PushButton,  ConfigItem, qconfig, FluentIcon as FIF, Dialog, ExpandSettingCard
from qfluentwidgets.components.settings.folder_list_setting_card import FolderItem


class FileItem(FolderItem):
    def __init__(self, file, parent=None):
        super().__init__(file, parent)
        self.file = file


class FileListSettingCard(ExpandSettingCard):
    """ Folder list setting card """

    fileChanged = Signal(list)

    def __init__(self, configItem: ConfigItem, icon: Union[str, QIcon, FluentIconBase], title: str, content: str = None, file_filter: str = "PNG files (*.png)", parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem = configItem
        self.addFileButton = PushButton(self.tr('Add chara'), self, FIF.ADD)
        self.files = qconfig.get(configItem).copy()   # type:List[str]
        self.file_filter = file_filter
        self.__initWidget()

    def __initWidget(self):
        self.addWidget(self.addFileButton)

        # initialize layout
        self.viewLayout.setSpacing(0)
        self.viewLayout.setAlignment(Qt.AlignTop)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        for file in self.files:
            self.__addFileItem(file)

        self.addFileButton.clicked.connect(self.__showFildDialog)

    def __showFildDialog(self):
        """ show folder dialog """
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select character cards", "", self.file_filter
        )
        
        for file in files:
            if file in self.files:
                continue
            self.__addFileItem(file)
            self.files.append(file)
        
        qconfig.set(self.configItem, self.files)
        self.fileChanged.emit(self.files)

    def __addFileItem(self, file: str):
        """ add folder item """
        item = FileItem(file, self.view)
        item.removed.connect(lambda: self.__removeFile(item))
        self.viewLayout.addWidget(item)
        item.show()
        self._adjustViewSize()

    def __removeFile(self, item: FileItem):
        """ remove folder """
        if item.file not in self.files:
            return

        self.files.remove(item.file)
        self.viewLayout.removeWidget(item)
        item.deleteLater()
        self._adjustViewSize()

        self.fileChanged.emit(self.files)
        qconfig.set(self.configItem, self.files)
