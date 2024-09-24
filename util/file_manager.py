import shutil
import datetime
import patoolib
import customtkinter
import subprocess
import time
from pathlib import Path
from util.logger import logger
from typing import Union, Literal


class FileManager:

    def __init__(self, config):
        self.config = config

    def find_all_files(self, directory: Union[Path, str]) -> tuple[Path, int, str]:
        """Find all files in the given directory."""
        directory = Path(directory)
        file_list = []
        archive_list = []
        archive_extensions = {".rar", ".zip", ".7z"}

        for file_path in directory.rglob('*'):
            file_size = file_path.stat().st_size
            file_extension = file_path.suffix

            if file_extension in archive_extensions:
                archive_list.append((file_path, file_size, file_extension))
            else:
                file_list.append((file_path, file_size, file_extension))

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
                    new_name = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
                    destination_path = destination_path.with_stem(f"{destination_path.stem}_{new_name}")
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
        archive_file = archive_path.with_suffix(".7z")

        if archive_file.exists():
            archive_file.unlink()

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
        command = f'"{path_to_7zip}" a -t7z "{archive_file}" {include_string} {exclude_string}'

        # Call the command
        process = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Print the output
        for line in process.stdout.decode().split('\n'):
            if line.strip():
                logger.info("7-Zip", line)

        # Check the return code
        if process.returncode not in [0, 1]:
            raise Exception()

    def extract_archive(self, archive_path: Union[Path, str]):
        """Extract the archive."""
        archive_path = Path(archive_path)
        archive_name = archive_path.name
        logger.info("ARCHIVE", f"Extracting {archive_name}")

        extract_path = archive_path.with_stem(f"{archive_path.stem}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}")

        try:
            patoolib.extract_archive(str(archive_path), outdir=str(extract_path))
            return extract_path
        
        except patoolib.util.PatoolError as e:
            text = f"There is an error with the archive {archive_name} but it is impossible to detect the cause. Maybe it requires a password?"
            while self.config.install_chara["Password"] == "Request Password":
                try:
                    dialog = customtkinter.CTkInputDialog(text=text, title="Enter Password")
                    password = dialog.get_input() 

                    if password:
                        patoolib.extract_archive(str(archive_path), outdir=str(extract_path), password=password)
                        return extract_path                
                    else:
                        break
                except:
                    text = f"Wrong password or {archive_name} is corrupted. Please enter password again or click Cancel"

            logger.skipped("ARCHIVE", archive_name)
