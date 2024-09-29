import os

from pathlib import Path
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileDialog
from qfluentwidgets import SettingCard, FluentIconBase, FluentIcon, CommandBar, Action, LineEdit, LineEditButton
from typing import Union
from app.common.notification import notification
from app.common.config import cfg
from app.common.signal_bus import signalBus
from app.common.clear_worker import ClearWorker
from app.components.clear_messagebox import ClearMessageBox


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
    clearQueue = set()

    def __init__(self, configItem, icon: Union[str, QIcon, FluentIconBase], titleGroup, title, content=None, parent=None, clearable=True):
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
        self.titleGroup = titleGroup
        self.configItem = configItem
        self.lineEdit = FolderLineEdit()
        self.lineEdit.setText(configItem.value)
        self.clearable = clearable

        self.__initCommandBar()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initCommandBar(self):
        self.commandBar = CommandBar()
        self.explorerAction = Action(FluentIcon.FOLDER, 'Show in Explorer')
        self.explorerAction.triggered.connect(self.openExplorer)
        self.commandBar.addHiddenAction(self.explorerAction)

        self.browseAction = Action(FluentIcon.EDIT, 'Browse')
        self.browseAction.triggered.connect(self.browse)
        self.commandBar.addHiddenAction(self.browseAction)

        if self.clearable:
            self.clearAction = Action(FluentIcon.DELETE, 'Clear')
            self.clearAction.triggered.connect(self.showClearDialog)
            self.commandBar.addHiddenAction(self.clearAction)

    def __initLayout(self):
        self.setFixedHeight(70)
        self.vBoxLayout.addWidget(self.lineEdit)
        self.hBoxLayout.setStretch(2, 70)
        self.hBoxLayout.addWidget(self.commandBar, 0, Qt.AlignBottom | Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def __connectSignalToSlot(self):
        self.lineEdit.textChanged.connect(self.validatePath)
        if self.clearable:
            signalBus.startSignal.connect(lambda: self.setDisabledClear(True))
            signalBus.stopSignal.connect(lambda: self.setDisabledClear(False))

    @Slot(str)
    def validatePath(self, text: str):
        if not text:
            cfg.set(self.configItem, "")
            self.lineEdit.setValid(True)
            self.explorerAction.setDisabled(True)
            self.setDisabledClear(True)
            return
        
        path = Path(text)
        if path.is_absolute() and path.is_dir() and path.exists():
            cfg.set(self.configItem, text)
            self.lineEdit.setValid(True)
            self.setDisabledClear(False)
            self.explorerAction.setDisabled(False)
        else:
            self.lineEdit.setValid(False)
            self.setDisabledClear(True)
            self.explorerAction.setDisabled(True)

    @Slot()
    def openExplorer(self):
        if self.configItem.value:
            path = os.path.realpath(self.configItem.value)        
            os.startfile(path)

    @Slot()
    def browse(self):
        """ download folder card clicked slot """
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Choose folder"), "./")
        if not folder or cfg.get(self.configItem) == folder:
            return
        self.lineEdit.setText(folder)

    @Slot()
    def showClearDialog(self):
        path = Path(self.lineEdit.text())
        if path in self.clearQueue:
            notification.error(f"{self.titleGroup}'s directory is already being cleared!")
            return
                        
        if not (path.is_absolute() and path.is_dir() and path.exists()):
            notification.error(f"{self.titleGroup} directory path is not valid!")
            return
        
        w = ClearMessageBox("Delete all files", f"Are you sure you want to delete all files in {self.titleGroup}'s directory:\n\n{path}?")
        w.exec()
        yesPressed, deleteFolder = w.getResponse()
        if yesPressed:
            self.clear(path, deleteFolder)

    @Slot()
    def clear(self, path: Path, deleteFolder: bool):
        signalBus.disableStartSignal.emit(True)

        self.clearQueue.add(path)
        self.lineEdit.setDisabled(True)
        self.clearAction.setDisabled(True)

        worker = ClearWorker(path, deleteFolder)
        worker.finishSignal.connect(lambda successful: self.afterClear(path, deleteFolder, successful))
        signalBus.threadPool.start(worker)

    @Slot()
    def afterClear(self, path: Path, deleteFolder: bool, successful: bool):
        if successful:
            if deleteFolder:
                self.lineEdit.clear()
                notification.success(f"Successfully deleted {self.titleGroup}'s directory")
            else:
                notification.success(f"Successfully cleared {self.titleGroup}'s directory")
        else:
            notification.error(f"Error clearing {self.titleGroup}'s directory")

        self.lineEdit.setDisabled(False)
        self.clearAction.setDisabled(False)
        self.clearQueue.remove(path)
        if not self.clearQueue:
            signalBus.disableStartSignal.emit(False)

    def setDisabledClear(self, value: bool):
        if not self.clearable:
            return 
        if not value and signalBus.scriptRunning():
            value = True

        self.clearAction.setDisabled(value)

