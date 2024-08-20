# coding:utf-8
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout

from qfluentwidgets.components.navigation import NavigationWidget


class NavigationLogoWidget(NavigationWidget):
    """ Navigation widget with a logo """

    def __init__(self, logo_path: str, logo_size: QSize = None, parent=None):
        super().__init__(isSelectable=False, parent=parent)
        self.logo_path = logo_path
        self.logo_size = logo_size if logo_size else QSize(parent.width(), 36)
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)  # Remove margins to fully center the QLabel

        self.logoLabel = QLabel(self)
        self.logoLabel.setAlignment(Qt.AlignCenter)  # Center the QLabel itself
        self.setLogo(self.logo_path)

        layout.addWidget(self.logoLabel)
        layout.setAlignment(Qt.AlignCenter)  # Center the QLabel within the layout

        self.setLayout(layout)
        self.setFixedSize(self.logo_size)

    def setLogo(self, logo_path: str):
        self.logo_path = logo_path
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            if self.logo_size:
                pixmap = pixmap.scaled(self.logo_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.logoLabel.setPixmap(pixmap)

    def setLogoSize(self, logo_size: QSize):
        self.logo_size = logo_size
        self.setFixedSize(logo_size)
        self.setLogo(self.logo_path)

    def setCompacted(self, isCompacted: bool):
        """ set whether the widget is compacted """
        if isCompacted == self.isCompacted:
            return

        self.isCompacted = isCompacted
        if isCompacted:
            self.setFixedSize(40, 36)
        else:
            self.setFixedSize(self.logo_size if self.logo_size else QSize(self.parent().width(), 36))

        self.update()