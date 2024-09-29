from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar, InfoBarPosition


class Notification:

    mainWindow = None

    def success(self, title: str, content: str = ''):
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
            parent=self.mainWindow
        )

    def error(self, title: str, content: str = ''):
        InfoBar.error(
            title=title,
            content=content,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
            parent=self.mainWindow
        )


notification = Notification()