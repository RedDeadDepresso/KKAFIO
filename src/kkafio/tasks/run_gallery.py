"""
run_gallery.py — One gallery window for the three kinds of card: scenes, characters, outfits.

It is where scenes, characters and outfits are sorted by hand (Group Scenes, Group Characters and
Group Coordinates only run automatically / with the LLM), side by side in a single dialog. Tabs on
the left of the search input (Scene, Character, Outfit) switch between the three galleries — each
with its own sidebar, sorting, selection and series/author/character input — and a card is moved
into its folder the moment you confirm it
(see dialogs/manual_group.py). Every tab keeps its own search text, sort and scroll position.

The folders default to the game's (Studio scene folder, female + male chara folders, coordinate
folder); the Scene folder is left out of the window when the game has no Studio and no folder was
given. The caches the Group tasks use are used here too (the PNG hash cache for scenes, the
character key cache, the coordinate caches) and brought along with every card that is moved.
"""

from __future__ import annotations

from pathlib import Path

from kkafio.cards.chara import hint_language
from kkafio.core.errors import InputError
from kkafio.core.i18n import t
from kkafio.core.logger import logger
from kkafio.tasks.base_task import BaseTask, resolve_chara_dirs
from kkafio.tasks.group_chara import chara_gallery_tab
from kkafio.tasks.group_coordinates import coordinate_gallery_tab
from kkafio.tasks.group_scenes import scene_gallery_tab

TAG = "GALLERY"


class RunGallery(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.run_gallery
        self.chara_dir_str : str  = cfg.get("CharaDir", "")
        self.scene_dir_str : str  = cfg.get("SceneDir", "")
        self.coord_dir_str : str  = cfg.get("CoordDir", "")
        self.use_cache     : bool = cfg.get("UseCache", True)

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

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        chara_dirs = resolve_chara_dirs(self.config.game_path, self.chara_dir_str, TAG)
        scene_dir = self._scene_dir()
        coord_dir = self._coord_dir()

        self.log_start(TAG, f"characters: {', '.join(str(d) for d in chara_dirs)}")
        logger.info(TAG, f"Scenes     : {scene_dir if scene_dir else '(none)'}")
        logger.info(TAG, f"Coordinates: {coord_dir}")
        logger.info(TAG, f"Use cache  : {self.use_cache}")
        logger.line()

        # The series suggestions of the Character tab come from the character index's English
        # titles (Group Characters reads the language from the prompt, which this task has none of).
        hint_lang = hint_language.DEFAULT_LANGUAGE

        tabs = []
        try:
            if scene_dir is not None:
                tabs.append(scene_gallery_tab(scene_dir, self.use_cache, t("dialog.gallery.tab_scene")))
                logger.line()
            tabs.append(chara_gallery_tab(chara_dirs, hint_lang, self.use_cache, t("dialog.gallery.tab_chara")))
            logger.line()
            tabs.append(coordinate_gallery_tab(chara_dirs, coord_dir, self.use_cache, t("dialog.gallery.tab_outfit")))
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

        from kkafio.dialogs.manual_group import gallery_dialog

        # Opens on the first tab that has cards; gallery_dialog() runs every tab's finish() (cache
        # saving) when the window is gone, also if it failed.
        initial = next(i for i, tab in enumerate(tabs) if tab.items)
        logger.info(TAG, "Opening the gallery: " + ", ".join(f"{tab.label} {len(tab.items)}" for tab in tabs))
        moved = gallery_dialog(t("dialog.gallery.title"), tabs, initial)
        logger.line()
        logger.success(TAG, "Done — moved: " + ", ".join(f"{tab.label} {n}" for tab, n in zip(tabs, moved)))
