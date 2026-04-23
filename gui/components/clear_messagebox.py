from PySide6.QtCore import Qt, Signal
from qfluentwidgets import MessageBox, CheckBox


class ClearMessageBox(MessageBox):
    mainWindow = None
    yesSignal = Signal()
    cancelSignal = Signal()

    def __init__(self, title: str, content: str, parent=None):
        parent = parent if parent is not None else self.mainWindow
        if parent is None:
            raise Exception("Please, assign mainWindow")
                
        super().__init__(title, content, parent)
        self.yesButtonPressed = False
        self.deleteFolder = False

        self.checkBox = CheckBox('Delete folder')
        self.textLayout.insertWidget(2, self.checkBox, 0, alignment=Qt.AlignLeft)

        self.__connectSignalToSlot()

    def __connectSignalToSlot(self):
        self.checkBox.checkStateChanged.connect(self.onChecked)
        self.yesSignal.connect(self.onYesSignal)
                               
    def onYesSignal(self):
        self.yesButtonPressed = True

    def onChecked(self, value: bool):
        self.deleteFolder = value

    def getResponse(self) -> tuple[bool, bool]:
        return self.yesButtonPressed, self.deleteFolder