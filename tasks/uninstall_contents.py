from pathlib import Path

from tasks.base_task import validate_input_path
from utils.content_resolver import ContentTypeResolver
from utils.config import Config, GameType
from utils.file_manager import FileManager
from utils.logger import logger


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

    def _file_action(self, label: str, image_path: Path, dest_folder) -> None:
        self.file_manager.find_and_remove(label, image_path, dest_folder)

    def _unsupported_chara_reason(self, unsupported_game: str) -> str:
        return f"not in {unsupported_game} install"

    def run(self):
        folder_path = self.input_path
        validate_input_path("UNINST", folder_path)

        foldername = folder_path.name
        logger.line()
        logger.info("FOLDER", foldername)

        file_list, _ = self.file_manager.find_all_files(folder_path)

        for file in file_list:
            path, size, extension = file
            match extension:
                case ".zipmod":
                    if self.do_mods:
                        self.file_manager.find_and_remove("MODS", path, self.game_path["mods"])
                case ".png":
                    self.resolve_png(path)
                case _:
                    pass
        logger.line()
