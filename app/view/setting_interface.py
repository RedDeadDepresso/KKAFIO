# coding:utf-8
from qfluentwidgets import (SettingCardGroup, SwitchSettingCard,
                            OptionsSettingCard, PushSettingCard,
                            HyperlinkCard, PrimaryPushSettingCard, ScrollArea,
                            ComboBoxSettingCard, ExpandLayout, Theme, CustomColorSettingCard,
                            setTheme, setThemeColor, RangeSettingCard, isDarkTheme)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar, DisplayLabel
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QFileDialog

from ..common.config import cfg, HELP_URL, FEEDBACK_URL, AUTHOR, VERSION, YEAR, isWin11
from ..components.line_edit_card import LineEditSettingCard
from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet


class SettingInterface(ScrollArea):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        # setting label
        self.settingLabel = DisplayLabel(self.tr("Settings"), self)

        # core
        self.coreGroup = SettingCardGroup(
            self.tr("Core"), self.scrollWidget)
        self.gamePathCard = PushSettingCard(
            self.tr('Choose folder'),
            FIF.GAME,
            self.tr("Koikatsu directory"),
            cfg.get(cfg.gamePath),
            self.coreGroup
        )
        
        # createBackup
        self.backupGroup = SettingCardGroup(
            self.tr("Create Backup"), self.scrollWidget)
        self.backupPathCard = PushSettingCard(
            self.tr('Choose folder'),
            FIF.ZIP_FOLDER,
            self.tr("Backup directory"),
            cfg.get(cfg.backupPath),
            self.backupGroup
        )
        self.filenameCard = LineEditSettingCard(
            cfg.filename,
            FIF.ZIP_FOLDER, 
            self.tr('Name for the backup zip file'),
            None,
            parent=self.backupGroup
        )
        self.modsCard = SwitchSettingCard(
            FIF.FOLDER,
            self.tr('mods'),
            None,
            configItem=cfg.mods,
            parent=self.backupGroup
        )
        self.userDataCard = SwitchSettingCard(
            FIF.FOLDER,
            self.tr('UserData'),
            None,
            configItem=cfg.userData,
            parent=self.backupGroup
        )
        self.bepInExCard = SwitchSettingCard(
            FIF.FOLDER,
            self.tr('BepInEx'),
            None,
            configItem=cfg.bepInEx,
            parent=self.backupGroup
        )

        # fckks
        self.fckksGroup = SettingCardGroup(
            self.tr("Filter & Convert"), self.scrollWidget)
        self.fckksPathCard = PushSettingCard(
            self.tr('Choose folder'),
            FIF.DOWNLOAD,
            self.tr("Input directory"),
            cfg.get(cfg.fccksPath),
            self.fckksGroup
        )
        self.convertCard = SwitchSettingCard(
            FIF.UPDATE,
            self.tr('Convert'),
            self.tr('Convert filtered KKS cards to KK card and store them in the KKS_to_KK directory'),
            configItem=cfg.convert,
            parent=self.fckksGroup
        )

        # installChara
        self.installGroup = SettingCardGroup(
            self.tr("Install Chara"), self.scrollWidget)
        self.installPathCard = PushSettingCard(
            self.tr('Choose folder'),
            FIF.DOWNLOAD,
            self.tr("Input directory"),
            cfg.get(cfg.installPath),
            self.installGroup,
        )
        self.fileConflictsCard = ComboBoxSettingCard(
            cfg.fileConflicts,
            FIF.CANCEL_MEDIUM,
            self.tr('If file conflicts:'),
            None,
            texts=["Skip", "Replace", "Rename"],
            parent=self.installGroup
        )
        self.archivePasswordCard = ComboBoxSettingCard(
            cfg.archivePassword,
            FIF.QUESTION,
            self.tr('If password is required for archives:'),
            texts=["Skip", "Request Password"],
            parent=self.installGroup
        )
    
        # removeChara
        self.removeGroup = SettingCardGroup(
            self.tr("Remove Chara"), self.scrollWidget)
        self.removePathCard = PushSettingCard(
            self.tr('Choose folder'),
            FIF.DOWNLOAD,
            self.tr("Input directory"),
            cfg.get(cfg.removePath),
            self.removeGroup,
        )
    
        # personalization
        self.personalGroup = SettingCardGroup(
            self.tr('Personalization'), self.scrollWidget)
        self.micaCard = SwitchSettingCard(
            FIF.TRANSPARENT,
            self.tr('Mica effect'),
            self.tr('Apply semi transparent to windows and surfaces'),
            cfg.micaEnabled,
            self.personalGroup
        )
        self.themeCard = OptionsSettingCard(
            cfg.themeMode,
            FIF.BRUSH,
            self.tr('Application theme'),
            self.tr("Change the appearance of your application"),
            texts=[
                self.tr('Light'), self.tr('Dark'),
                self.tr('Use system setting')
            ],
            parent=self.personalGroup
        )
        self.themeColorCard = CustomColorSettingCard(
            cfg.themeColor,
            FIF.PALETTE,
            self.tr('Theme color'),
            self.tr('Change the theme color of you application'),
            self.personalGroup
        )
        self.zoomCard = OptionsSettingCard(
            cfg.dpiScale,
            FIF.ZOOM,
            self.tr("Interface zoom"),
            self.tr("Change the size of widgets and fonts"),
            texts=[
                "100%", "125%", "150%", "175%", "200%",
                self.tr("Use system setting")
            ],
            parent=self.personalGroup
        )
        self.languageCard = ComboBoxSettingCard(
            cfg.language,
            FIF.LANGUAGE,
            self.tr('Language'),
            self.tr('Set your preferred language for UI'),
            texts=['简体中文', '繁體中文', 'English', self.tr('Use system setting')],
            parent=self.personalGroup
        )

        # material
        self.materialGroup = SettingCardGroup(
            self.tr('Material'), self.scrollWidget)
        self.blurRadiusCard = RangeSettingCard(
            cfg.blurRadius,
            FIF.ALBUM,
            self.tr('Acrylic blur radius'),
            self.tr('The greater the radius, the more blurred the image'),
            self.materialGroup
        )

        # update software
        self.updateSoftwareGroup = SettingCardGroup(
            self.tr("Software update"), self.scrollWidget)
        self.updateOnStartUpCard = SwitchSettingCard(
            FIF.UPDATE,
            self.tr('Check for updates when the application starts'),
            self.tr('The new version will be more stable and have more features'),
            configItem=cfg.checkUpdateAtStartUp,
            parent=self.updateSoftwareGroup
        )

        # application
        self.aboutGroup = SettingCardGroup(self.tr('About'), self.scrollWidget)
        self.helpCard = HyperlinkCard(
            HELP_URL,
            self.tr('Open help page'),
            FIF.HELP,
            self.tr('Help'),
            self.tr(
                'Discover new features and learn useful tips about PyQt-Fluent-Widgets'),
            self.aboutGroup
        )
        self.feedbackCard = PrimaryPushSettingCard(
            self.tr('Provide feedback'),
            FIF.FEEDBACK,
            self.tr('Provide feedback'),
            self.tr('Help us improve PyQt-Fluent-Widgets by providing feedback'),
            self.aboutGroup
        )
        self.aboutCard = PrimaryPushSettingCard(
            self.tr('Check update'),
            FIF.INFO,
            self.tr('About'),
            '© ' + self.tr('Copyright') + f" {YEAR}, {AUTHOR}. " +
            self.tr('Version') + " " + VERSION,
            self.aboutGroup
        )

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName('settingInterface')

        # initialize style sheet
        self.scrollWidget.setObjectName('scrollWidget')
        self.settingLabel.setObjectName('settingLabel')
        StyleSheet.SETTING_INTERFACE.apply(self)

        self.micaCard.setEnabled(isWin11())

        # initialize layout
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.settingLabel.move(36, 30)

        # add cards to group
        self.coreGroup.addSettingCard(self.gamePathCard)

        self.backupGroup.addSettingCard(self.backupPathCard)
        self.backupGroup.addSettingCard(self.filenameCard)
        self.backupGroup.addSettingCard(self.modsCard)
        self.backupGroup.addSettingCard(self.userDataCard)
        self.backupGroup.addSettingCard(self.bepInExCard)

        self.fckksGroup.addSettingCard(self.fckksPathCard)
        self.fckksGroup.addSettingCard(self.convertCard)

        self.installGroup.addSettingCard(self.installPathCard)
        self.installGroup.addSettingCard(self.fileConflictsCard)
        self.installGroup.addSettingCard(self.archivePasswordCard)

        self.removeGroup.addSettingCard(self.removePathCard)

        self.personalGroup.addSettingCard(self.micaCard)
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.zoomCard)
        self.personalGroup.addSettingCard(self.languageCard)

        self.materialGroup.addSettingCard(self.blurRadiusCard)

        self.updateSoftwareGroup.addSettingCard(self.updateOnStartUpCard)

        self.aboutGroup.addSettingCard(self.helpCard)
        self.aboutGroup.addSettingCard(self.feedbackCard)
        self.aboutGroup.addSettingCard(self.aboutCard)

        # add setting card group to layout
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.expandLayout.addWidget(self.coreGroup)
        self.expandLayout.addWidget(self.backupGroup)
        self.expandLayout.addWidget(self.fckksGroup)
        self.expandLayout.addWidget(self.installGroup)
        self.expandLayout.addWidget(self.removeGroup)
        self.expandLayout.addWidget(self.personalGroup)
        self.expandLayout.addWidget(self.materialGroup)
        self.expandLayout.addWidget(self.updateSoftwareGroup)
        self.expandLayout.addWidget(self.aboutGroup)

    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            self.tr('Updated successfully'),
            self.tr('Configuration takes effect after restart'),
            duration=1500,
            parent=self
        )

    def __onFolderCardClicked(self, item, card):
        """ download folder card clicked slot """
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Choose folder"), "./")
        if not folder or cfg.get(item) == folder:
            return

        cfg.set(item, folder)
        card.setContent(folder)

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.appRestartSig.connect(self.__showRestartTooltip)
        # gamePath
        self.gamePathCard.clicked.connect(lambda: self.__onFolderCardClicked(cfg.gamePath, self.gamePathCard))
        # backup
        self.backupPathCard.clicked.connect(lambda: self.__onFolderCardClicked(cfg.backupPath, self.backupPathCard))
        # fckks 
        self.fckksPathCard.clicked.connect(lambda: self.__onFolderCardClicked(cfg.fccksPath, self.fckksPathCard))
        # install
        self.installPathCard.clicked.connect(lambda: self.__onFolderCardClicked(cfg.installPath, self.installPathCard))
        # remove                                     
        self.removePathCard.clicked.connect(lambda: self.__onFolderCardClicked(cfg.removePath, self.removePathCard))

        # personalization
        self.themeCard.optionChanged.connect(lambda ci: setTheme(cfg.get(ci)))
        self.themeColorCard.colorChanged.connect(lambda c: setThemeColor(c))
        self.micaCard.checkedChanged.connect(signalBus.micaEnableChanged)

        # about
        self.feedbackCard.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(FEEDBACK_URL)))
        
    def scrollToGroup(self, group):
        self.verticalScrollBar().setValue(group.y())
