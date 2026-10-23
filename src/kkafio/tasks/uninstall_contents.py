from pathlib import Path

from kkafio.cards.mods import build_mods_cache
from kkafio.cards.shared_mods import find_guids_in_use, keep_unshared
from kkafio.tasks.base_task import DEFAULT_DOWNLOADS_PATH, validate_input_path
from kkafio.tasks.content_resolver import ContentTypeResolver
from kkafio.core.config import Config, GameType
from kkafio.core.file_manager import FileManager
from kkafio.core.logger import logger


class UninstallContents(ContentTypeResolver):
    def __init__(self, config: Config, file_manager: FileManager):
        self.config       = config
        self.file_manager = file_manager
        self.game_path    = self.config.game_path
        self.input_path   = Path(self.config.uninstall_contents["InputPath"])
        self.game_type    = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        self.is_sunshine  = self.game_type == GameType.KOIKATSU_SUNSHINE.value

        cfg = self.config.uninstall_contents
        self.do_chara    : bool = cfg.get("Chara",    True)
        self.do_mods     : bool = cfg.get("Mods",     True)
        self.do_coords   : bool = cfg.get("Coords",   True)
        self.do_scenes   : bool = cfg.get("Scenes",   True)
        self.do_overlays : bool = cfg.get("Overlays", True)
        self.check_shared_mods: bool = cfg.get("CheckSharedMods", True)
        self.use_cache        : bool = cfg.get("UseCache", True)

    def _file_action(self, label: str, image_path: Path, dest_folder) -> None:
        self.file_manager.find_and_remove(label, image_path, dest_folder)

    def _unsupported_chara_reason(self, unsupported_game: str) -> str:
        return f"not in {unsupported_game} install"

    def _zipmod_guids(self, mods: list[Path], input_dir: Path) -> dict[Path, str]:
        """{zipmod: GUID} for the zipmods about to be uninstalled, read from the mods caches of the
        input folder and of the game's mods folder (the installed copy is what gets deleted, so its
        GUID wins). A zipmod with no readable GUID is left out."""
        by_path: dict[str, str] = {}
        for d in (input_dir, Path(self.game_path["mods"])):
            if d.exists():
                by_path.update({p: g for g, p in build_mods_cache(
                    d, include_modpack=True, use_cache=self.use_cache).items()})
        game_mods = Path(self.game_path["mods"])
        out: dict[Path, str] = {}
        for path in mods:
            guid = by_path.get(str(game_mods / path.name)) or by_path.get(str(path))
            if guid:
                out[path] = guid
        return out

    def _uninstall_mods(self, mods: list[Path], input_dir: Path) -> None:
        """Remove the installed copies of `mods`, keeping any that a remaining character, scene or
        coordinate card still uses (if Check for Shared Mods is on)."""
        protected: set[Path] = set()
        if self.check_shared_mods:
            guid_by_path = self._zipmod_guids(mods, input_dir)
            # Cards were uninstalled before this runs, so what is left in the game folders is
            # exactly what must keep working.
            in_use = find_guids_in_use("UNINST", self.game_path, self.use_cache)
            protected = set(guid_by_path) - set(keep_unshared(guid_by_path, in_use, "UNINST"))
        for path in mods:
            if path not in protected:
                self.file_manager.find_and_remove("MODS", path, self.game_path["mods"])

    def run(self):
        folder_path = self.input_path
        validate_input_path("UNINST", folder_path, default_path=DEFAULT_DOWNLOADS_PATH)

        foldername = folder_path.name
        logger.line()
        logger.info("FOLDER", foldername)

        file_list, _ = self.file_manager.find_supported_files(folder_path)

        mods: list[Path] = []
        for path in file_list:
            match path.suffix:
                case ".zipmod" | ".zip":
                    # A plain .zip only reaches file_list (as opposed to
                    # being extracted as an archive) when find_supported_files has
                    # already confirmed it contains a manifest.xml, so it's
                    # safe to treat it exactly like a .zipmod here.
                    if self.do_mods:
                        mods.append(path)
                case ".png":
                    self.resolve_png(path)
                case _:
                    pass

        # Zipmods last: the shared-mod check looks at the cards still installed, so the cards
        # being uninstalled must already be gone and must not count as users.
        if mods:
            self._uninstall_mods(mods, folder_path)
        logger.line()
