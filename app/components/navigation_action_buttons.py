# coding:utf-8
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import PushButton, isDarkTheme

from app.common.signal_bus import signalBus
from app.common.config import cfg


class _BadgePushButton(PushButton):
    """PushButton that optionally draws a numeric badge on its top-right corner."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._badge: int = 0

    def setBadge(self, count: int) -> None:
        self._badge = max(0, count)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._badge <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Badge circle
        r      = 9
        margin = 4
        cx     = self.width()  - margin - r
        cy     = margin + r
        painter.setBrush(QColor(cfg.themeColor.value))   # red badge
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Badge text
        font = QFont()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(cx - r, cy - r, r * 2, r * 2,
                         Qt.AlignCenter, str(self._badge))
        painter.end()


class NavigationActionButtons(QWidget):
    """Select All / Clear All + Start button with disabled state and task count badge."""

    def __init__(self):
        super().__init__()
        self.mainLayout  = QVBoxLayout()
        self.upperLayout = QHBoxLayout()
        self.lowerLayout = QHBoxLayout()

        self.selectAllButton = PushButton("Select All")
        self.clearAllButton  = PushButton("Clear All")
        startButton     = _BadgePushButton("Start")
        self.startButton     = startButton
        # Count how many tasks are currently enabled from config at startup
        self._checkedCount = self._initialCount()
        self._updateStart()

        self.__initLayout()
        self.__connectSignalToSlot()

    def _initialCount(self) -> int:
        """Read config to find how many tasks are currently ticked."""
        attrs = [
            "backupEnable", "fckksEnable", "filterDuplicatesEnable",
            "installEnable", "removeEnable", "groupCharaEnable",
            "ungroupCharaEnable", "archiveCharaEnable", "deleteCharaEnable",
        ]
        count = 0
        for attr in attrs:
            item = getattr(cfg, attr, None)
            if item is not None and cfg.get(item):
                count += 1
        return count

    def _updateStart(self) -> None:
        """Sync start button enabled state and badge with current count."""
        self.startButton.setEnabled(self._checkedCount > 0)
        self.startButton.setBadge(self._checkedCount)

    def __initLayout(self):
        self.upperLayout.addWidget(self.selectAllButton, alignment=Qt.AlignVCenter)
        self.upperLayout.addWidget(self.clearAllButton,  alignment=Qt.AlignVCenter)
        self.lowerLayout.addWidget(self.startButton,     alignment=Qt.AlignVCenter)

        self.mainLayout.addLayout(self.upperLayout)
        self.mainLayout.addSpacing(20)
        self.mainLayout.addLayout(self.lowerLayout)
        self.setLayout(self.mainLayout)

    def __connectSignalToSlot(self):
        self.selectAllButton.clicked.connect(self.onSelectAllClicked)
        self.clearAllButton.clicked.connect(self.onClearAllClicked)
        self.startButton.clicked.connect(self.onStartClicked)

        signalBus.startSignal.connect(lambda: self.startButton.setText("Stop"))
        signalBus.stopSignal.connect(lambda: self.startButton.setText("Start"))
        signalBus.disableStartSignal.connect(lambda state: self.startButton.setDisabled(state))

        # Update badge + enabled state whenever any checkbox changes
        signalBus.checkCountChanged.connect(self._onCheckCountChanged)

        # Select all / clear all also change the count
        signalBus.selectAllClicked.connect(self._onSelectAll)
        signalBus.clearAllClicked.connect(self._onClearAll)

    def _onCheckCountChanged(self, checked: bool) -> None:
        self._checkedCount += 1 if checked else -1
        self._checkedCount = max(0, self._checkedCount)
        self._updateStart()

    def _onSelectAll(self) -> None:
        self._checkedCount = self._initialCount()
        self._updateStart()

    def _onClearAll(self) -> None:
        self._checkedCount = 0
        self._updateStart()

    def onSelectAllClicked(self):
        signalBus.selectAllClicked.emit()

    def onClearAllClicked(self):
        signalBus.clearAllClicked.emit()

    def onStartClicked(self):
        if self.startButton.text() == "Start":
            signalBus.startSignal.emit()
        else:
            signalBus.stopSignal.emit()
