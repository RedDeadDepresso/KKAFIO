from pathlib import Path
from typing import Optional

from tasks.base_task import validate_input_path
from utils.content_resolver import ContentTypeResolver
from utils.config import Config, GameType
from utils.file_manager import FileManager
from utils.logger import logger


class InstallContents(ContentTypeResolver):
    def __init__(self, config: Config, file_manager: FileManager):
        self.config          = config
        self.file_manager    = file_manager
        self.game_path       = self.config.game_path
        self.input_path      = Path(self.config.install_contents["InputPath"])
        self.extract_archive = self.config.install_contents.get("ExtractArchive", True)
        self.game_type       = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        self.is_sunshine     = self.game_type == GameType.KOIKATSU_SUNSHINE.value

        cfg = self.config.install_contents
        self.do_chara    : bool = cfg.get("Chara",    True)
        self.do_mods     : bool = cfg.get("Mods",     True)
        self.do_coords   : bool = cfg.get("Coords",   True)
        self.do_scenes   : bool = cfg.get("Scenes",   True)
        self.do_overlays : bool = cfg.get("Overlays", True)

    def _file_action(self, label: str, image_path: Path, dest_folder) -> None:
        self.file_manager.copy_and_paste(label, image_path, dest_folder)

    def _unsupported_chara_reason(self, unsupported_game: str) -> str:
        return f"not supported by {unsupported_game}"

    def run(self, folder_path: Optional[Path] = None, skip_extract: bool = False):
        if folder_path is None:
            folder_path = self.input_path
        folder_path = Path(folder_path)

        validate_input_path("INSTALL", folder_path)

        foldername = folder_path.name
        logger.line()
        logger.info("FOLDER", foldername)

        file_list, archive_list = self.file_manager.find_all_files(folder_path)

        for file in file_list:
            path, size, extension = file
            match extension:
                case ".zipmod":
                    if self.do_mods:
                        self.file_manager.copy_and_paste("MODS", path, self.game_path["mods"])
                case ".png":
                    self.resolve_png(path)
                case _:
                    basename = Path(path).name
                    logger.error("UNKNOWN", f"Cannot classify {basename}")
        logger.line()

        should_extract = self.extract_archive and not skip_extract
        if should_extract:
            for archive in archive_list:
                extract_path = self.file_manager.extract_archive(archive[0], self.config.install_contents)
                if extract_path is not None:
                    self.run(extract_path)
        elif archive_list:
            names = ", ".join(Path(a[0]).name for a in archive_list)
            logger.info("SKIP", f"Archive extraction skipped: {names}")
