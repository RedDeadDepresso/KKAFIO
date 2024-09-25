import sys
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout
from qfluentwidgets import Dialog, FluentTitleBar, LineEdit, setTheme, Theme


app = QApplication(sys.argv)


class PasswordDialog(Dialog):
    yesSignal = Signal()
    cancelSignal = Signal()

    def __init__(self, title: str, content: str, parent=None):
        super().__init__(title, content, parent)
        self.passwordLineEdit = LineEdit(self)
        self.password = ''
        self.yesButtonPressed = False
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        passwordLayout = QHBoxLayout()
        passwordLayout.setContentsMargins(12, 12, 12, 12)
        passwordLayout.addWidget(self.passwordLineEdit, 1, Qt.AlignTop)
        self.vBoxLayout.insertLayout(2, passwordLayout, 1)
        self.setTitleBar(FluentTitleBar(self))

    def __connectSignalToSlot(self):
        self.passwordLineEdit.textChanged.connect(self.onTextChanged)
        self.yesSignal.connect(self.onYesSignal)

    def onTextChanged(self, text: str):
        self.password = text
                               
    def onYesSignal(self):
        self.yesButtonPressed = True

    def getPassword(self) -> str:
        if self.yesButtonPressed:
            print(self.password)
            return self.password
        return ''
    
def password_dialog(title: str, content: str) -> str:
    setTheme(Theme.AUTO)
    dialog = PasswordDialog(title, content)
    dialog.exec()
    return dialog.getPassword()


if __name__ == "__main__":
    password = password_dialog('test', 'test')
    print(password)