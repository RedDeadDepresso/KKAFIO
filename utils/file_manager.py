import shutil
import subprocess
import time
import json

from datetime import datetime
from pathlib import Path
from utils.logger import logger
from typing import Union, Literal

from utils.constants import SEVEN_ZIP_PATH


FileEntry = tuple[Path, int, str]


class FileManager:
    _path_to_7zip = None

    def __init__(self, config):
        self.config = config
        self.backup_info_path = SEVEN_ZIP_PATH

    def find_all_files(self, directory: Path | str) -> tuple[list[FileEntry], list[FileEntry]]:
        """Find all files and archive files in the given directory.

        Returns:
            Tuple containing:
            - A list of regular files (path, size, extension)
            - A list of archive files (path, size, extension)
        """
        directory = Path(directory)
        file_list: list[FileEntry] = []
        archive_list: list[FileEntry] = []
        archive_extensions = {".rar", ".zip", ".7z"}

        for file_path in directory.glob('**/*'):
            if file_path.is_file():
                file_size = file_path.stat().st_size
                file_extension = file_path.suffix

                file_entry: FileEntry = (file_path, file_size, file_extension)

                if file_extension in archive_extensions:
                    archive_list.append(file_entry)
                else:
                    file_list.append(file_entry)
                    
        file_list.sort(key=lambda x: x[1])
        archive_list.sort(key=lambda x: x[1])

        return file_list, archive_list
    
    def copy_and_paste(self, type: str, source_path: Path | str, destination_folder: str | Path):
        """Copy file from source to destination, handling file conflicts."""
        source_path = Path(source_path)
        destination_folder = Path(destination_folder)

        base_name = source_path.name
        destination_path = destination_folder / base_name
        conflicts = self.config.install_contents["FileConflicts"]
        already_exists = destination_path.exists()

        if already_exists and conflicts == "Skip":
            logger.skipped(type, base_name)
            return
        
        elif already_exists and conflicts == "Replace":
            logger.replaced(type, base_name)
        
        elif already_exists and conflicts == "Rename":
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.renamed(type, base_name)
                    new_stem = f"{source_path.stem}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')}"
                    source_path = source_path.rename(source_path.with_stem(new_stem))
                    destination_path = destination_path.with_stem(new_stem)
                    break
                except PermissionError:
                    if attempt < max_retries - 1:
                        time.sleep(1)
                    else:
                        logger.error(type, f"Failed to rename {base_name} after {max_retries} attempts.")
                        return

        try:
            shutil.copy(source_path, destination_path)
            if not already_exists:
                logger.success(type, base_name)
        except FileNotFoundError:
            logger.error(type, f"{base_name} does not exist.")
        except PermissionError:
            logger.error(type, f"Permission denied for {base_name}")
        except Exception as e:
            logger.error(type, f"An error occurred: {e}")

    def find_and_remove(self, file_type: str, source_path: str | Path, destination_folder: str | Path):
        """Remove file if it exists at the destination."""
        source_path = Path(source_path)
        destination_folder = Path(destination_folder)

        base_name = source_path.name
        destination_path = destination_folder / base_name

        if destination_path.exists():
            try:
                destination_path.unlink()
                logger.removed(file_type, base_name)
            except OSError as e:
                logger.error(file_type, base_name)

    @staticmethod
    def _get_nt_7z_dir() -> str:
        """Return 7-Zip directory from registry, or an empty string."""
        import winreg  # noqa: PLC0415
        import platform  # noqa: PLC0415

        python_bits = platform.architecture()[0]
        keyname = r"SOFTWARE\7-Zip"
        try:
            if python_bits == '32bit' and platform.machine().endswith('64'):
                # get 64-bit registry key from 32-bit Python
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    keyname,
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                )
            else:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, keyname)
            try:
                return winreg.QueryValueEx(key, "Path")[0]
            finally:
                winreg.CloseKey(key)
        except OSError:
            return ""

    @classmethod
    def find_7zip(cls) -> str | None:
        """Return the path to 7z.exe, or None if not found."""
        if cls._path_to_7zip is None:
            cls._path_to_7zip = shutil.which("7z", path=cls._get_nt_7z_dir())
        return cls._path_to_7zip

    def create_archive(self, files: list[Path], output_path: Path, fmt: str) -> None:
        path_to_7zip = self.find_7zip()
        if not path_to_7zip:
            raise RuntimeError("7-Zip not found. Install 7-Zip and ensure '7z' is on PATH.")
        flag = "-t7z" if fmt == "7z" else "-tzip"
        cmd = [path_to_7zip, "a", flag, str(output_path)] + [str(f) for f in files]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode not in (0, 1):
            raise RuntimeError(f"7-Zip failed:\n{result.stderr}")
    
    def create_game_archive(self, folders: list[Literal["mods", "UserData", "BepInEx"]], archive_path: Union[str, Path]):
        """Create an archive of the given folders using 7zip."""
        path_to_7zip = self._find_7zip()
        if not path_to_7zip:
            logger.error("SCRIPT", "7zip not found. Unable to create backup")
            raise Exception()
        
        archive_path = Path(archive_path)
        archive_path = archive_path.with_suffix(".7z")

        if archive_path.exists():
            archive_path.unlink()

        exclude_folders = [
            "Sideloader Modpack",
            "Sideloader Modpack - Studio",
            "Sideloader Modpack - KK_UncensorSelector",
            "Sideloader Modpack - Maps",
            "Sideloader Modpack - KK_MaterialEditor",
            "Sideloader Modpack - Fixes",
            "Sideloader Modpack - Exclusive KK KKS",
            "Sideloader Modpack - Exclusive KK",
            "Sideloader Modpack - Animations",
        ]

        cmd = [path_to_7zip, "a", "-t7z", "-bsp1", str(archive_path)]
        cmd += [str(f) for f in folders]
        cmd += [f"-xr!{folder}" for folder in exclude_folders]

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=self.config.game_path['base'])

        self.write_backup_info(archive_path, process.pid)
        while True:
            line = process.stdout.readline()
            if not line:
                break
            if line.strip():
                logger.info("7-Zip", line.strip())

        process.wait()

        self.backup_info_path.unlink(missing_ok=True)

        # Check the return code
        if process.returncode not in [0, 1]:
            logger.error("7-Zip", f"Exited with return code: {process.returncode}")
            raise Exception(f"7-zip exited with return code: {process.returncode}")
        
    def write_backup_info(self, archive_path: Path, pid: int):
        with open(self.backup_info_path, "w") as f:
            data = {"ArchivePath": str(archive_path), "PID": pid}
            json.dump(data, f)

    def _run_7zip_extract(self, archive_path: Path, extract_path: Path,
                          password: str | None = None) -> bool:
        """Run 7-Zip to extract archive_path into extract_path.

        Args:
            password: password string to use, empty string to attempt
                      no-password extraction, or None to skip -p flag entirely.
        Returns True on success, False on failure.
        """
        path_to_7zip = self.find_7zip()
        if not path_to_7zip:
            logger.error("ARCHIVE", "7-Zip not found. Cannot extract archive.")
            return False

        cmd = [path_to_7zip, "x", str(archive_path),
               f"-o{extract_path}", "-y"]
        if password:
            cmd.append(f"-p{password}")
        else:
            cmd.append("-p")          # prompt-less no-password attempt

        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0

    def extract_archive(self, archive_path: Union[Path, str], task_config: dict = None):
        """Extract the archive using 7-Zip."""
        if task_config is None:
            task_config = self.config.install_contents

        archive_path = Path(archive_path)
        archive_name = archive_path.name
        logger.info("ARCHIVE", f"Extracting {archive_name}")

        extract_path = archive_path.with_name(
            f"{archive_path.stem}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")

        # First attempt — no password
        if self._run_7zip_extract(archive_path, extract_path):
            return extract_path
        
        if task_config.get("Password") != "Request":
            logger.error("ARCHIVE", archive_name)
            return None

        # Failed — may need a password
        text = (f"There is an error with the archive {archive_name}. "
                f"Maybe it requires a password?")

        from utils.password_dialog import password_dialog

        while True:
            password = password_dialog("Enter Password", text)
            if not password:
                break

            if self._run_7zip_extract(archive_path, extract_path, password=password):
                return extract_path

            text = (f"Wrong password or {archive_name} is corrupted. "
                    f"Please enter the password again or click Cancel.")

        logger.error("ARCHIVE", archive_name)
        return None