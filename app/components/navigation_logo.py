# coding:utf-8
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout
from qfluentwidgets import AvatarWidget
from qfluentwidgets.components.navigation import NavigationWidget


class NavigationLogoWidget(NavigationWidget):
    """ Navigation widget with a logo """

    def __init__(self, logoPath: str, radius: int = 100, parent=None):
        super().__init__(isSelectable=False, parent=parent)
        self.logoPath = logoPath
        self.radius = radius
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.avatar = AvatarWidget(self.logoPath)
        self.avatar.setRadius(self.radius)
        layout.addWidget(self.avatar, 0, Qt.AlignCenter)
        self.setLayout(layout)

    def setCompacted(self, isCompacted: bool):
        """ set whether the widget is compacted """
        if isCompacted == self.isCompacted:
            return

        self.isCompacted = isCompacted
        if isCompacted:
            self.setFixedSize(40, 36)
        else:
            self.setFixedSize(self.avatar.size())

        self.update()