"""
manage_library.py — One library window for the three kinds of card: scenes, characters, outfits.

It is where scenes, characters and outfits are sorted by hand (Group Scenes, Group Characters and
Group Coordinates only run automatically / with the LLM), side by side in a single dialog. Tabs on
the left of the search input (Scene, Character, Outfit) switch between the three libraries — each
with its own sidebar, sorting, selection and series/author/character input — and a card is moved
into its folder the moment you confirm it
(see dialogs/library/: the window, and what each tab scans, suggests and moves). Every tab keeps its own search text, sort and scroll position.

The folders default to the game's (Studio scene folder, female + male chara folders, coordinate
folder); the Scene folder is left out of the window when the game has no Studio and no folder was
given. The caches the Group tasks use are used here too (the PNG hash cache for scenes, the
character key cache, the coordinate caches) and brought along with every card that is moved.

A collapsible sidebar on the right of the window lists the mods (GUIDs) the card under the cursor
uses, marking each installed or missing in the installed and downloaded mods folders (the game's, or the
Installed / Downloaded Mods Directory — a mod in either counts as installed):
a missing one has a search icon that looks its GUID up on Google (see dialogs/library/mods_panel.py).

Cards sent to a library's Archive queue (right-click > Archive, F11) are left alone until the
window closes; then they are handed to Archive Cards, which bundles them with their zipmods (and,
for characters, their matching coordinates) into the Archive Output Directory, in the Archive
Format and with the Archive Options (combined archive, Sideloader Modpack, coordinates). The
originals stay where they are; Restore in the queue takes a card out of it again.

Cards sent to a library's trash are not sent to the Recycle Bin directly: when the window closes
they are handed to Delete Cards, which also deletes the character's matching coordinates (Include
Coordinates) and the mods nothing else uses (Check Shared Mods), looking for mods in the downloaded
mods directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from kkafio.cards.chara import hint_language
from kkafio.core.config import GameType
from kkafio.core.errors import InputError
from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.dialogs.library.characters import chara_library_tab
from kkafio.dialogs.library.coordinates import coordinate_library_tab
from kkafio.dialogs.library.mods_panel import CardMods, ModsLookup, mods_dir_for
from kkafio.dialogs.library.scenes import scene_library_tab
from kkafio.tasks.base_task import BaseTask, resolve_chara_dirs

TAG = "LIBRARY"


class ManageLibrary(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.manage_library
        self.chara_dir_str : str  = cfg.get("CharaDir", "")
        self.scene_dir_str : str  = cfg.get("SceneDir", "")
        self.coord_dir_str : str  = cfg.get("CoordDir", "")
        self.installed_mods_dir_str  : str = cfg.get("InstalledModsDir", "")
        self.downloaded_mods_dir_str : str = cfg.get("DownloadedModsDir", "")
        self.use_cache     : bool = cfg.get("UseCache", True)
        self.check_shared_mods  : bool = cfg.get("CheckSharedMods", True)
        self.include_coordinates: bool = cfg.get("IncludeCoordinates", True)
        self.archive_output_dir_str : str  = cfg.get("ArchiveOutputPath", "")
        self.archive_format         : str  = cfg.get("ArchiveFormat", "7z")
        self.archive_combined       : bool = cfg.get("ArchiveCombined", True)
        self.archive_include_modpack: bool = cfg.get("ArchiveIncludeModpack", False)
        self.archive_include_coords : bool = cfg.get("ArchiveIncludeCoordinates", True)
        self._installed_mods_dir   : Path | None = None     # set by _card_mods(): where Archive Cards looks for mods
        self._downloaded_mods_dir  : Path | None = None     # set by _card_mods(): where Delete Cards looks for mods

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    def _scene_dir(self) -> Path | None:
        """The scene folder, or None when there is none to show (no folder given and the game
        has no Studio). A folder that was given but doesn't exist is an error."""
        if self.scene_dir_str.strip():
            folder = Path(self.scene_dir_str)
            if not folder.exists():
                raise InputError(f"Scene directory does not exist: {folder}", tag=TAG)
            return folder
        folder = self.config.game_path.get("scene")
        if folder is None:
            logger.warning(TAG, "No scene folder: the game has no Studio and no Scene Directory is set — "
                                "the Scene tab is left out.")
            return None
        return Path(folder)

    def _coord_dir(self) -> Path:
        if self.coord_dir_str.strip():
            folder = Path(self.coord_dir_str)
        else:
            folder = self.config.game_path.get("coordinate")
            if folder is None:
                raise InputError("Coordinate folder not set and not resolvable from game path.", tag=TAG)
            folder = Path(folder)
        if not folder.exists():
            raise InputError(f"Coordinate directory does not exist: {folder}", tag=TAG)
        return folder

    def _mods_dirs(self) -> tuple[Path | None, Path | None]:
        """(installed, downloaded) mods folders the sidebar checks mods against; a mod installed in either
        counts as installed. Each is the one given, else the game's. A given installed folder that
        doesn't exist is an error; a given downloaded folder that doesn't exist is left out (Download
        Missing Mods creates it, so it may simply not be there yet). With none at all the sidebar
        still lists a card's mods, it just can't say which are installed."""
        game_mods = self.config.game_path.get("mods")
        installed_dir = mods_dir_for(self.installed_mods_dir_str, game_mods)
        downloaded_dir = mods_dir_for(self.downloaded_mods_dir_str, game_mods)

        if installed_dir is not None and not installed_dir.exists():
            if self.installed_mods_dir_str.strip():
                raise InputError(f"Installed mods directory does not exist: {installed_dir}", tag=TAG)
            logger.warning(TAG, f"Mods folder not found: {installed_dir}")
            installed_dir = None
        if downloaded_dir is not None and not downloaded_dir.exists():
            if self.downloaded_mods_dir_str.strip():
                logger.warning(TAG, f"Downloaded mods directory does not exist (yet): {downloaded_dir} — ignored.")
            else:
                logger.warning(TAG, f"Mods folder not found: {downloaded_dir}")
            downloaded_dir = None
        if installed_dir is None and downloaded_dir is None:
            logger.warning(TAG, "No mods folder: no Installed/Downloaded Mods Directory is set and none could be "
                                "resolved from the game path — the mod dependencies sidebar can't tell "
                                "which mods are installed.")
        return installed_dir, downloaded_dir

    def _card_mods(self) -> CardMods:
        game_type = getattr(self.config, "config_data", {}).get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        installed_dir, downloaded_dir = self._mods_dirs()
        self._installed_mods_dir = installed_dir
        self._downloaded_mods_dir = downloaded_dir
        return CardMods(ModsLookup(installed_dir, game_type, self.use_cache, extra_dirs=(downloaded_dir,)))

    # ------------------------------------------------------------------
    # Deleting the trashed cards
    # ------------------------------------------------------------------

    def _delete_cards_settings(self, paths: list[Path], chara_dirs: list[Path], scene_dir: Path | None,
                               coord_dir: Path, downloaded_mods_dir: Path | None) -> dict:
        """The Delete Cards options for the cards left in the library's trash. The mods folder is
        the downloaded mods directory (explicit, so AutoResolve is off): that is where the downloaded
        mods live, and the only folder whose zipmods are deleted. The shared-mod check sees every
        folder the library shows plus the game's own, so a mod another card still uses is kept."""
        game = self.config.game_path
        dirs: list[str] = []
        seen: set[str] = set()
        for d in [*chara_dirs, game.get("charaFemale"), game.get("charaMale")]:
            if d and (key := os.path.normcase(os.path.realpath(str(d)))) not in seen:
                seen.add(key)
                dirs.append(str(d))
        return {
            "Enable": True,
            "ContentPaths": [str(p) for p in paths],
            "CheckSharedMods": self.check_shared_mods,
            "IncludeCoordinates": self.include_coordinates,
            "UseCache": self.use_cache,
            "AutoResolve": False,
            "ModsDir": str(downloaded_mods_dir) if downloaded_mods_dir is not None else "",
            "CharaDirs": dirs,
            "SceneDir": str(scene_dir) if scene_dir is not None else "",
            "CoordDir": str(coord_dir),
        }

    def _delete_trashed(self, paths: list[Path], chara_dirs: list[Path], scene_dir: Path | None,
                        coord_dir: Path, downloaded_mods_dir: Path | None) -> None:
        from kkafio.tasks.delete_cards import DeleteCards
        logger.line()
        logger.info(TAG, f"{len(paths)} trashed card(s) - handing them to Delete Cards")
        DeleteCards(self.config, self.file_manager,
                    settings=self._delete_cards_settings(paths, chara_dirs, scene_dir, coord_dir,
                                                         downloaded_mods_dir)).run()

    # ------------------------------------------------------------------
    # Archiving the queued cards
    # ------------------------------------------------------------------

    def _archive_output_dir(self) -> Path | None:
        """The Archive Output Directory, or None when none is set (Archive Cards then puts each
        archive next to its card). The shipped Windows default is rooted under the home folder on
        other platforms, like the other tasks' default folders; the folder is created by Archive
        Cards when something is archived."""
        text = self.archive_output_dir_str.strip()
        if not text:
            return None
        if text == "C:/KKAFIO/Archived Cards":
            from kkafio.core.config import Config
            return Config._platform_default(text)
        return Path(text)

    def _archive_cards_settings(self, paths: list[Path], coord_dir: Path,
                                installed_mods_dir: Path | None, downloaded_mods_dir: Path | None) -> dict:
        """The Archive Cards options for the cards left in the library's archive queue. Mods and
        coordinates come from the folders the library shows (explicit, so AutoResolve is off): the
        installed mods folder, else the downloaded one."""
        mods_dir = installed_mods_dir or downloaded_mods_dir
        output_dir = self._archive_output_dir()
        return {
            "Enable": True,
            "ContentPaths": [str(p) for p in paths],
            "OutputPath": str(output_dir) if output_dir is not None else "",
            "Format": self.archive_format,
            "CombinedArchive": self.archive_combined,
            "IncludeModpack": self.archive_include_modpack,
            "IncludeCoordinates": self.archive_include_coords,
            "UseCache": self.use_cache,
            "AutoResolve": False,
            "ModsDir": str(mods_dir) if mods_dir is not None else "",
            "CoordDir": str(coord_dir),
        }

    def _archive_queued(self, paths: list[Path], coord_dir: Path, installed_mods_dir: Path | None,
                        downloaded_mods_dir: Path | None) -> None:
        from kkafio.tasks.archive_cards import ArchiveCards
        logger.line()
        logger.info(TAG, f"{len(paths)} queued card(s) - handing them to Archive Cards")
        ArchiveCards(self.config, self.file_manager,
                     settings=self._archive_cards_settings(paths, coord_dir, installed_mods_dir,
                                                           downloaded_mods_dir)).run()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        chara_dirs = resolve_chara_dirs(self.config.game_path, self.chara_dir_str, TAG)
        scene_dir = self._scene_dir()
        coord_dir = self._coord_dir()
        card_mods = self._card_mods()

        self.log_start(TAG, f"characters: {', '.join(str(d) for d in chara_dirs)}")
        logger.info(TAG, f"Scenes     : {scene_dir if scene_dir else '(none)'}")
        logger.info(TAG, f"Coordinates: {coord_dir}")
        mods_dirs = [d for d in card_mods.lookup.mods_dirs if d.is_dir()]
        logger.info(TAG, f"Mods       : {', '.join(str(d) for d in mods_dirs) if mods_dirs else '(none)'}")
        logger.info(TAG, f"Use cache  : {self.use_cache}")
        logger.line()

        # The Character tab suggests series from the character index. The language of the titles is
        # the library's Settings (English until the user picks another); this only turns the index on
        # (Group Characters reads the language from the prompt, which this task has none of).
        hint_lang = hint_language.DEFAULT_LANGUAGE

        tabs = []
        try:
            if scene_dir is not None:
                tabs.append(scene_library_tab(scene_dir, self.use_cache, t("dialog.library.tab_scene")))
                logger.line()
            tabs.append(chara_library_tab(chara_dirs, hint_lang, self.use_cache, t("dialog.library.tab_chara")))
            logger.line()
            tabs.append(coordinate_library_tab(chara_dirs, coord_dir, self.use_cache, t("dialog.library.tab_outfit")))
        except BaseException:
            for tab in tabs:                                # the caches of the tabs that were read
                if tab.finish is not None:
                    tab.finish()
            raise

        if not any(tab.items for tab in tabs):
            logger.warning(TAG, "No readable cards found")
            for tab in tabs:
                if tab.finish is not None:
                    tab.finish()
            return

        from kkafio.dialogs.library import library_dialog

        # Opens on the first tab that has cards; library_dialog() runs every tab's finish() (cache
        # saving) when the window is gone, also if it failed.
        initial = next(i for i, tab in enumerate(tabs) if tab.items)
        logger.info(TAG, "Opening the library: " + ", ".join(f"{tab.label} {len(tab.items)}" for tab in tabs))
        # The sidebar's lookup keeps the downloaded folder only when it exists; that is the one whose
        # mods Delete Cards may delete.
        downloaded_mods_dir = self._downloaded_mods_dir
        installed_mods_dir = self._installed_mods_dir
        moved = library_dialog(
            t("dialog.library.title"), tabs, initial, card_mods,
            on_trash=lambda paths: self._delete_trashed(paths, chara_dirs, scene_dir, coord_dir, downloaded_mods_dir),
            on_archive=lambda paths: self._archive_queued(paths, coord_dir, installed_mods_dir, downloaded_mods_dir))
        logger.line()
        logger.success(TAG, "Done — moved: " + ", ".join(f"{tab.label} {n}" for tab, n in zip(tabs, moved)))
