import shutil
import patoolib
import subprocess
import time
import json

from datetime import datetime
from pathlib import Path
from util.logger import logger
from typing import Union, Literal


FileEntry = tuple[Path, int, str]


class FileManager:
    def __init__(self, config):
        self.config = config
        self.backup_info_path = Path('app/config/7zip.json')

    def find_all_files(self, directory: Union[Path, str]) -> tuple[list[FileEntry], list[FileEntry]]:
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
    
    def copy_and_paste(self, type: str, source_path: Union[Path, str], destination_folder: Union[str, Path]):
        """Copy file from source to destination, handling file conflicts."""
        source_path = Path(source_path)
        destination_folder = Path(destination_folder)

        base_name = source_path.name
        destination_path = destination_folder / base_name
        conflicts = self.config.install_chara["FileConflicts"]
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

    def find_and_remove(self, file_type: str, source_path: Union[str, Path], destination_folder: Union[str, Path]):
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

    def create_archive(self, folders: list[Literal["mods", "UserData", "BepInEx"]], archive_path: Union[str, Path]):
        """Create an archive of the given folders using 7zip."""
        # Specify the full path to the 7zip executable
        path_to_7zip = patoolib.util.find_program("7z")
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
            "Sideloader Modpack - Animations"
        ] 

        # Create a string of folder names to exclude
        exclude_string = ' '.join([f'-xr!"{folder}"' for folder in exclude_folders])

        # Create a string of folder names to include
        include_string = ' '.join([f'"{folder}"' for folder in folders])

        # Construct the 7zip command
        command = f'"{path_to_7zip}" a -t7z "{archive_path}" {include_string} {exclude_string}'

        # Call the command
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.config.game_path['base'])

        self.write_backup_info(archive_path, process.pid)
        # Print the output
        while process.poll() is None:
            for line in process.stdout:
                if line.strip():
                    logger.info("7-Zip", line)

        self.backup_info_path.unlink(missing_ok=True)

        # Check the return code
        if process.returncode not in [0, 1]:
            logger.error("7-Zip", f"Exited with return code: {process.returncode}")
            raise Exception(f"7-zip exited with return code: {process.returncode}")
        
    def write_backup_info(self, archive_path: Path, pid: int):
        with open(self.backup_info_path, "w") as f:
            data = {"ArchivePath": str(archive_path), "PID": pid}
            json.dump(data, f)

    def extract_archive(self, archive_path: Union[Path, str]):
        from app.components.password_dialog import password_dialog
        """Extract the archive."""
        archive_path = Path(archive_path)
        archive_name = archive_path.name
        logger.info("ARCHIVE", f"Extracting {archive_name}")

        extract_path = archive_path.with_name(f"{archive_path.stem}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")

        try:
            patoolib.extract_archive(str(archive_path), outdir=str(extract_path))
            return extract_path
        except:
            
            text = f"There is an error with the archive {archive_name}, but it is impossible to detect the cause. Maybe it requires a password?"
            while self.config.install_chara["Password"] == "Request Password":
                try:
                    password = password_dialog('Enter Password', text)
                    
                    if not password:
                        break
                    
                    patoolib.extract_archive(str(archive_path), outdir=str(extract_path), password=password)
                    return extract_path
                
                except patoolib.util.PatoolError as e:
                    text = f"Wrong password or {archive_name} is corrupted. Please enter password again or click Cancel."
                    print(f"Error: {str(e)}")
                
                except Exception as e:
                    print(f"An unexpected error occurred: {str(e)}")
                    break
                
            logger.error("ARCHIVE", archive_name)
