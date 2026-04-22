# coding:utf-8
from typing import List, Union

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileDialog

from qfluentwidgets import (
    ConfigItem, ExpandSettingCard, FluentIcon as FIF,
    FluentIconBase, PushButton, qconfig,
)
from qfluentwidgets.components.settings.folder_list_setting_card import FolderItem


class FileItem(FolderItem):
    def __init__(self, file, parent=None):
        super().__init__(file, parent)
        self.file = file


class FileListSettingCard(ExpandSettingCard):
    """Setting card with an expandable list of file paths."""

    fileChanged = Signal(list)

    def __init__(self, configItem: ConfigItem, icon: Union[str, QIcon, FluentIconBase],
                 title: str, content: str = None,
                 file_filter: str = "PNG files (*.png)", parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem  = configItem
        self.file_filter = file_filter

        self.addFileButton    = PushButton(self.tr("Add"),       self, FIF.ADD)
        self.clearAllFileButton = PushButton(self.tr("Clear All"), self, FIF.CLOSE)

        self.files     : List[str] = qconfig.get(configItem).copy()
        self.fileItems : set       = set()

        self.__initWidget()

    def __initWidget(self):
        self.addWidget(self.addFileButton)
        self.addWidget(self.clearAllFileButton)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setAlignment(Qt.AlignTop)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)

        for file in self.files:
            self.__addFileItem(file)

        self.clearAllFileButton.clicked.connect(self.__removeAllFiles)
        self.addFileButton.clicked.connect(self.__showFileDialog)

    def showEvent(self, e):
        """Expand after the first layout pass so _adjustViewSize has correct geometry."""
        super().showEvent(e)
        if not self.isExpand:
            # Defer by one event loop tick so the parent layout has finished
            # calculating sizes before we set the fixed height.
            QTimer.singleShot(0, lambda: self.setExpand(True))

    def __showFileDialog(self):
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
        item = FileItem(file, self.view)
        item.removed.connect(lambda: self.__removeFile(item))
        self.viewLayout.addWidget(item)
        item.show()
        self._adjustViewSize()
        self.fileItems.add(item)

    def __removeAllFiles(self):
        for item in self.fileItems:
            self.viewLayout.removeWidget(item)
            item.deleteLater()

        self._adjustViewSize()
        self.files.clear()
        self.fileItems.clear()
        self.fileChanged.emit(self.files)
        qconfig.set(self.configItem, self.files)

    def __removeFile(self, item: FileItem):
        if item.file not in self.files:
            return
        self.files.remove(item.file)
        self.viewLayout.removeWidget(item)
        item.deleteLater()
        self._adjustViewSize()
        self.fileItems.discard(item)
        self.fileChanged.emit(self.files)
        qconfig.set(self.configItem, self.files)
