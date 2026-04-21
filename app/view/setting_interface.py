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
from PySide6.QtWidgets import QWidget, QApplication

from ..common.config import cfg, HELP_URL, FEEDBACK_URL, AUTHOR, VERSION, YEAR, isWin11
from ..components.line_edit_card import LineEditSettingCard
from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet
from ..components.folder_setting_card import FolderSettingCard
from ..components.text_area_card import TextAreaSettingCard
from ..components.file_list_setting_card import FileListSettingCard


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
        self.gamePathCard = FolderSettingCard(
            cfg.gamePath,
            FIF.GAME,
            'Core',
            self.tr("Koikatsu directory"),
            parent=self.coreGroup,
            clearable=False
        )

        # createBackup
        self.backupGroup = SettingCardGroup(
            self.tr("Create Backup"), self.scrollWidget)
        self.backupPathCard = FolderSettingCard(
            cfg.backupPath,
            FIF.ZIP_FOLDER,
            'Create Backup',
            self.tr("Backup directory"),
            parent=self.backupGroup
        )
        self.filenameCard = LineEditSettingCard(
            cfg.filename,
            FIF.ZIP_FOLDER, 
            self.tr('Name for the backup 7-zip file'),
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
        self.fckksPathCard = FolderSettingCard(
            cfg.fccksPath,
            FIF.DOWNLOAD,
            'Filter & Convert',
            self.tr("Input directory"),
            parent=self.fckksGroup
        )
        self.convertCard = SwitchSettingCard(
            FIF.UPDATE,
            self.tr('Convert'),
            self.tr('Convert filtered KKS cards to KK card and store them in the KKS_to_KK directory'),
            configItem=cfg.convert,
            parent=self.fckksGroup
        )
        self.fckksExtractArchiveCard = SwitchSettingCard(
            FIF.ZIP_FOLDER,
            self.tr('Extract archives'),
            self.tr('Extract ZIP, RAR and 7z archives found in the input directory before filtering'),
            configItem=cfg.fckksExtractArchive,
            parent=self.fckksGroup
        )
        self.fckksArchivePasswordCard = ComboBoxSettingCard(
            cfg.fckksArchivePassword,
            FIF.QUESTION,
            self.tr('If password is required for archives:'),
            texts=["Skip", "Request Password"],
            parent=self.fckksGroup
        )

        # filterDuplicates
        self.filterDuplicatesGroup = SettingCardGroup(
            self.tr("Filter Duplicates"), self.scrollWidget)
        self.filterDuplicatesPathCard = FolderSettingCard(
            cfg.filterDuplicatesPath,
            FIF.SEARCH,
            'Filter Duplicates',
            self.tr("Input directory"),
            parent=self.filterDuplicatesGroup
        )
        self.filterDuplicatesFuzzyCard = SwitchSettingCard(
            FIF.SEARCH,
            self.tr('Fuzzy matching for character cards'),
            self.tr('Use perceptual image hashing to catch updated cards with the same preview pose. May produce false positives.'),
            configItem=cfg.filterDuplicatesFuzzy,
            parent=self.filterDuplicatesGroup
        )
        self.filterDuplicatesKeepCard = ComboBoxSettingCard(
            cfg.filterDuplicatesKeep,
            FIF.PIN,
            self.tr('Which copy to keep as the original'),
            self.tr('When duplicates are found, this determines which file is kept in place. All others are moved or deleted.'),
            texts=["None — move all copies", "Newest", "Oldest",
                   "Biggest file size", "Smallest file size",
                   "Last alphabetically", "First alphabetically"],
            parent=self.filterDuplicatesGroup
        )
        self.filterDuplicatesDeleteCard = SwitchSettingCard(
            FIF.DELETE,
            self.tr('Send duplicates to recycle bin'),
            self.tr('Delete duplicates immediately via the recycle bin instead of moving them to a _duplicates_ folder'),
            configItem=cfg.filterDuplicatesDelete,
            parent=self.filterDuplicatesGroup
        )

        # installChara
        self.installGroup = SettingCardGroup(
            self.tr("Install Chara"), self.scrollWidget)
        self.installPathCard = FolderSettingCard(
            cfg.installPath,
            FIF.DOWNLOAD,
            'Install Chara',
            self.tr("Input directory"),
            parent=self.installGroup
        )
        self.fileConflictsCard = ComboBoxSettingCard(
            cfg.fileConflicts,
            FIF.CANCEL_MEDIUM,
            self.tr('If file conflicts:'),
            None,
            texts=["Skip", "Replace", "Rename"],
            parent=self.installGroup
        )
        self.installExtractArchiveCard = SwitchSettingCard(
            FIF.ZIP_FOLDER,
            self.tr('Extract archives'),
            self.tr('Extract ZIP, RAR and 7z archives found in the input directory before installing'),
            configItem=cfg.installExtractArchive,
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
        self.removePathCard = FolderSettingCard(
            cfg.removePath,
            FIF.DOWNLOAD,
            'Remove Chara',
            self.tr("Input directory"),
            parent=self.removeGroup
        )

        # groupChara
        self.groupCharaGroup = SettingCardGroup(
            self.tr("Group Chara"), self.scrollWidget)
        self.groupCharaPathCard = FolderSettingCard(
            cfg.groupCharaPath,
            FIF.FOLDER,
            'Group Chara',
            self.tr("Input directory"),
            parent=self.groupCharaGroup
        )
        self.groupCharaPromptCard = TextAreaSettingCard(
            cfg.groupCharaPrompt,
            FIF.CHAT,
            self.tr("LLM prompt"),
            self.tr("Prompt sent to the LLM alongside the character JSON. Edit to customise."),
            parent=self.groupCharaGroup
        )
        self.groupCharaCopyCard = PrimaryPushSettingCard(
            self.tr("Copy"),
            FIF.COPY,
            self.tr("Step 1: Copy"),
            self.tr("Scan the input folder, build character JSON and copy prompt + JSON to clipboard. Paste it into your LLM."),
            parent=self.groupCharaGroup
        )
        self.groupCharaPasteCard = PrimaryPushSettingCard(
            self.tr("Paste"),
            FIF.PASTE,
            self.tr("Step 2: Paste"),
            self.tr("After the LLM replies with the filled-in JSON, copy that response and click Paste to save it."),
            parent=self.groupCharaGroup
        )
        self.groupCharaCopyCard.clicked.connect(self.__onGroupCharaCopy)
        self.groupCharaPasteCard.clicked.connect(self.__onGroupCharaPaste)
        self.groupCharaIncludeSubfoldersCard = SwitchSettingCard(
            FIF.FOLDER,
            self.tr('Include subfolders'),
            self.tr('Also export character cards from subfolders. Disable to skip already-sorted cards.'),
            configItem=cfg.groupCharaIncludeSubfolders,
            parent=self.groupCharaGroup
        )

        # ungroupChara
        self.ungroupCharaGroup = SettingCardGroup(
            self.tr("Ungroup Chara"), self.scrollWidget)
        self.ungroupCharaPathCard = FolderSettingCard(
            cfg.ungroupCharaPath,
            FIF.FOLDER,
            'Ungroup Chara',
            self.tr("Input directory"),
            parent=self.ungroupCharaGroup
        )
        self.ungroupCharaDeleteEmptyCard = SwitchSettingCard(
            FIF.DELETE,
            self.tr('Delete empty folders'),
            self.tr('Remove subdirectories that are left empty after moving files to the top level.'),
            configItem=cfg.ungroupCharaDeleteEmpty,
            parent=self.ungroupCharaGroup
        )

        # archiveChara
        self.archiveCharaGroup = SettingCardGroup(
            self.tr("Archive Chara"), self.scrollWidget)
        self.archiveCharaOutputDirCard = FolderSettingCard(
            cfg.archiveCharaOutputDir,
            FIF.FOLDER,
            "Archive Chara",
            self.tr("Output directory"),
            parent=self.archiveCharaGroup
        )
        self.archiveCharaFilesCard = FileListSettingCard(
            cfg.archiveCharaPaths,
            FIF.FOLDER,
            self.tr("Character cards"),
            self.tr("PNG card files to archive"),
            parent=self.archiveCharaGroup
        )
        self.archiveCharaFormatCard = ComboBoxSettingCard(
            cfg.archiveCharaFormat,
            FIF.SAVE,
            self.tr("Archive format"),
            self.tr("Output archive format"),
            texts=["7z", "zip"],
            parent=self.archiveCharaGroup
        )
        self.archiveCharaCombinedCard = SwitchSettingCard(
            FIF.ZIP_FOLDER,
            self.tr('Combined archive'),
            self.tr('Put all cards into one archive file. Disable to create one archive per character card.'),
            configItem=cfg.archiveCharaCombined,
            parent=self.archiveCharaGroup
        )
        self.archiveCharaAutoResolveCard = SwitchSettingCard(
            FIF.SEARCH,
            self.tr("Auto-resolve mods and coordinate paths"),
            self.tr("Infer mods and coordinate directories from the game path or card location"),
            configItem=cfg.archiveCharaAutoResolve,
            parent=self.archiveCharaGroup
        )
        self.archiveCharaModsDirCard = FolderSettingCard(
            cfg.archiveCharaModsDir,
            FIF.FOLDER,
            "Archive Chara",
            self.tr("Mods directory"),
            parent=self.archiveCharaGroup
        )
        self.archiveCharaCoordDirCard = FolderSettingCard(
            cfg.archiveCharaCoordDir,
            FIF.FOLDER,
            "Archive Chara",
            self.tr("Coordinate directory"),
            parent=self.archiveCharaGroup
        )
        self.archiveCharaIncludeModpackCard = SwitchSettingCard(
            FIF.FOLDER,
            self.tr('Include Sideloader Modpack mods'),
            self.tr('Also bundle zipmods from Sideloader Modpack subfolders (excluded by default)'),
            configItem=cfg.archiveCharaIncludeModpack,
            parent=self.archiveCharaGroup
        )
        self.archiveCharaAutoResolveCard.checkedChanged.connect(self.__onArchiveAutoResolveChanged)
        self.__onArchiveAutoResolveChanged(cfg.get(cfg.archiveCharaAutoResolve))
    
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
        # self.materialGroup = SettingCardGroup(
        #     self.tr('Material'), self.scrollWidget)
        # self.blurRadiusCard = RangeSettingCard(
        #     cfg.blurRadius,
        #     FIF.ALBUM,
        #     self.tr('Acrylic blur radius'),
        #     self.tr('The greater the radius, the more blurred the image'),
        #     self.materialGroup
        # )

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
            self.tr('Discover new features and learn useful tips about KKAFIO'),
            self.aboutGroup
        )
        self.feedbackCard = PrimaryPushSettingCard(
            self.tr('Provide feedback'),
            FIF.FEEDBACK,
            self.tr('Provide feedback'),
            self.tr('Help us improve KKAFIO by providing feedback'),
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
        self.fckksGroup.addSettingCard(self.fckksExtractArchiveCard)
        self.fckksGroup.addSettingCard(self.fckksArchivePasswordCard)

        self.filterDuplicatesGroup.addSettingCard(self.filterDuplicatesPathCard)
        self.filterDuplicatesGroup.addSettingCard(self.filterDuplicatesFuzzyCard)
        self.filterDuplicatesGroup.addSettingCard(self.filterDuplicatesKeepCard)
        self.filterDuplicatesGroup.addSettingCard(self.filterDuplicatesDeleteCard)

        self.installGroup.addSettingCard(self.installPathCard)
        self.installGroup.addSettingCard(self.fileConflictsCard)
        self.installGroup.addSettingCard(self.installExtractArchiveCard)
        self.installGroup.addSettingCard(self.archivePasswordCard)

        self.removeGroup.addSettingCard(self.removePathCard)

        self.groupCharaGroup.addSettingCard(self.groupCharaPathCard)
        self.groupCharaGroup.addSettingCard(self.groupCharaIncludeSubfoldersCard)
        self.groupCharaGroup.addSettingCard(self.groupCharaPromptCard)
        self.groupCharaGroup.addSettingCard(self.groupCharaCopyCard)
        self.groupCharaGroup.addSettingCard(self.groupCharaPasteCard)

        self.ungroupCharaGroup.addSettingCard(self.ungroupCharaPathCard)
        self.ungroupCharaGroup.addSettingCard(self.ungroupCharaDeleteEmptyCard)

        self.archiveCharaGroup.addSettingCard(self.archiveCharaOutputDirCard)
        self.archiveCharaGroup.addSettingCard(self.archiveCharaFilesCard)
        self.archiveCharaGroup.addSettingCard(self.archiveCharaFormatCard)
        self.archiveCharaGroup.addSettingCard(self.archiveCharaCombinedCard)
        self.archiveCharaGroup.addSettingCard(self.archiveCharaIncludeModpackCard)
        self.archiveCharaGroup.addSettingCard(self.archiveCharaAutoResolveCard)
        self.archiveCharaGroup.addSettingCard(self.archiveCharaModsDirCard)
        self.archiveCharaGroup.addSettingCard(self.archiveCharaCoordDirCard)

        self.personalGroup.addSettingCard(self.micaCard)
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.zoomCard)
        self.personalGroup.addSettingCard(self.languageCard)

        # self.materialGroup.addSettingCard(self.blurRadiusCard)

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
        self.expandLayout.addWidget(self.filterDuplicatesGroup)
        self.expandLayout.addWidget(self.installGroup)
        self.expandLayout.addWidget(self.removeGroup)
        self.expandLayout.addWidget(self.groupCharaGroup)
        self.expandLayout.addWidget(self.ungroupCharaGroup)
        self.expandLayout.addWidget(self.archiveCharaGroup)
        self.expandLayout.addWidget(self.personalGroup)
        # self.expandLayout.addWidget(self.materialGroup)
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

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.appRestartSig.connect(self.__showRestartTooltip)

        # personalization
        self.themeCard.optionChanged.connect(lambda ci: setTheme(cfg.get(ci)))
        self.themeColorCard.colorChanged.connect(lambda c: setThemeColor(c))
        self.micaCard.checkedChanged.connect(signalBus.micaEnableChanged)

        # about
        self.feedbackCard.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(FEEDBACK_URL)))
        self.aboutCard.clicked.connect(signalBus.checkUpdateSignal.emit)

    def __onGroupCharaCopy(self):
        from .group_chara_worker import GroupCharaCopyWorker
        folder              = cfg.get(cfg.groupCharaPath)
        prompt              = cfg.get(cfg.groupCharaPrompt)
        include_subfolders  = cfg.get(cfg.groupCharaIncludeSubfolders)
        self.groupCharaCopyCard.button.setEnabled(False)
        self.groupCharaCopyCard.button.setText("Copying...")
        self._copyWorker = GroupCharaCopyWorker(folder, prompt, include_subfolders)
        self._copyWorker.finishSignal.connect(self.__onCopyFinished)
        signalBus.threadPool.start(self._copyWorker)

    def __onCopyFinished(self, text: str, error: str):
        self.groupCharaCopyCard.button.setEnabled(True)
        self.groupCharaCopyCard.button.setText("Copy")
        if error:
            InfoBar.error(self.tr("Error"), error, parent=self)
        else:
            QApplication.clipboard().setText(text)
            InfoBar.success(self.tr("Copied"),
                self.tr("Prompt and character JSON copied to clipboard. Paste into your LLM."),
                parent=self)

    def __onGroupCharaPaste(self):
        from .group_chara_worker import GroupCharaPasteWorker
        text = QApplication.clipboard().text().strip()
        if not text:
            InfoBar.error(self.tr("Error"), self.tr("Clipboard is empty."), parent=self)
            return
        self.groupCharaPasteCard.button.setEnabled(False)
        self._pasteWorker = GroupCharaPasteWorker(text)
        self._pasteWorker.finishSignal.connect(self.__onPasteFinished)
        signalBus.threadPool.start(self._pasteWorker)

    def __onPasteFinished(self, error: str):
        self.groupCharaPasteCard.button.setEnabled(True)
        if error:
            InfoBar.error(self.tr("Error"), error, parent=self)
        else:
            cfg.set(cfg.groupCharaResponse, QApplication.clipboard().text().strip())
            InfoBar.success(self.tr("Saved"),
                self.tr("the LLM response saved. Enable Group Chara and click Start to move files."),
                parent=self)

    def __onArchiveAutoResolveChanged(self, enabled: bool) -> None:
        self.archiveCharaModsDirCard.setDisabled(enabled)
        self.archiveCharaCoordDirCard.setDisabled(enabled)

    def scrollToGroup(self, group):
        self.verticalScrollBar().setValue(group.y())