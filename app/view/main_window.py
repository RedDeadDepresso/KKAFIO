# coding: utf-8
from typing import List
from PySide6.QtCore import Qt, Signal, QEasingCurve, QUrl, QSize
from PySide6.QtGui import QIcon, QDesktopServices, QColor
from PySide6.QtWidgets import QApplication, QHBoxLayout, QFrame, QWidget

from qfluentwidgets import (NavigationAvatarWidget, NavigationItemPosition, MessageBox, FluentWindow,
                            SplashScreen, PushButton)
from qfluentwidgets import FluentIcon as FIF

from .logger_interface import LoggerInterface
from .setting_interface import SettingInterface
from ..common.config import ZH_SUPPORT_URL, EN_SUPPORT_URL, cfg
from ..common.signal_bus import signalBus
from ..common import resource
from ..components.navigation_checkbox import NavigationCheckBox
from ..components.navigation_action_buttons import NavigationActionButtons
from ..components.navigation_logo import NavigationLogoWidget


class MainWindow(FluentWindow):

    def __init__(self):
        super().__init__()
        self.initWindow()

        # create sub interface
        self.loggerInterface = LoggerInterface(self)
        self.settingInterface = SettingInterface(self)

        # enable acrylic effect
        self.navigationInterface.setAcrylicEnabled(True)
        self.navigationInterface.setMenuButtonVisible(False)
        self.navigationInterface.setCollapsible(False)

        self.connectSignalToSlot()

        # add items to navigation interface
        self.initNavigation()
        self.splashScreen.finish()

    def connectSignalToSlot(self):
        signalBus.micaEnableChanged.connect(self.setMicaEffectEnabled)
        signalBus.switchToSettingGroup.connect(self.switchToSetting)
        signalBus.supportSignal.connect(self.onSupport)

    def initNavigation(self):
        # add navigation items
        self.navigationInterface.addWidget(
            'Logo',
            NavigationLogoWidget('gui/icons/karin.png', QSize(160, 160)),
            position=NavigationItemPosition.TOP
        )
        self.navigationInterface.panel.topLayout.setAlignment(Qt.AlignCenter)
        scrollLayout = self.navigationInterface.panel.scrollLayout
        scrollLayout.addWidget(NavigationCheckBox('Create Backup', cfg.backupEnable, self.settingInterface.backupGroup))
        scrollLayout.addWidget(NavigationCheckBox('Filter and Convert KKS', cfg.fckksEnable, self.settingInterface.fckksGroup))
        scrollLayout.addWidget(NavigationCheckBox('Install Chara', cfg.installEnable, self.settingInterface.installGroup))
        scrollLayout.addWidget(NavigationCheckBox('Remove Chara', cfg.removeEnable, self.settingInterface.removeGroup))
        scrollLayout.addWidget(NavigationActionButtons())

        # add custom widget to bottom
        self.addSubInterface(
            self.loggerInterface, FIF.CALENDAR, self.tr('Log'), NavigationItemPosition.BOTTOM)
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, self.tr('Settings'), NavigationItemPosition.BOTTOM)

    def initWindow(self):
        self.resize(960, 780)
        self.setMinimumWidth(760)
        self.setWindowIcon(QIcon(':/gallery/images/logo.png'))
        self.setWindowTitle('KKAFIO')

        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        # create splash screen
        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        desktop = QApplication.screens()[0].availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w//2 - self.width()//2, h//2 - self.height()//2)
        self.show()
        QApplication.processEvents()

    def onSupport(self):
        language = cfg.get(cfg.language).value
        if language.name() == "zh_CN":
            QDesktopServices.openUrl(QUrl(ZH_SUPPORT_URL))
        else:
            QDesktopServices.openUrl(QUrl(EN_SUPPORT_URL))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'splashScreen'):
            self.splashScreen.resize(self.size())

    def switchToSetting(self, settingGroup):
        """ switch to sample """
        self.stackedWidget.setCurrentWidget(self.settingInterface, False)
        self.settingInterface.scrollToGroup(settingGroup)
