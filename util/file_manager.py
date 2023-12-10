import os
import shutil
import datetime
import patoolib
import customtkinter
import subprocess
import time
from util.logger import Logger

class FileManager:

    def __init__(self, config):
        self.config = config

    def find_all_files(self, directory):
        file_list = []
        compressed_file_list = []
        compressed_extensions = [".rar", ".zip", ".7z"]

        for root, dirs, files in os.walk(directory):
            for file in files:

                file_path = os.path.join(root, file)
                file_size = os.path.getsize(file_path)
                _, file_extension = os.path.splitext(file_path)

                if file_extension in compressed_extensions:
                    compressed_file_list.append((file_path, file_size, file_extension))
                else:
                    file_list.append((file_path, file_size, file_extension))

        file_list.sort(key=lambda x: x[1])
        compressed_file_list.sort(key=lambda x: x[1])
        return file_list, compressed_file_list
    
    def copy_and_paste(self, type, source_path, destination_folder):
        source_path = source_path[0]
        base_name = os.path.basename(source_path)
        destination_path = os.path.join(destination_folder, base_name)
        conflicts = self.config.install_chara["FileConflicts"]
        already_exists =  os.path.exists(destination_path)

        if already_exists and conflicts == "Skip":
            Logger.log_skipped(type, base_name)
            return
        
        elif already_exists and conflicts == "Replace":
            Logger.log_replaced(type, base_name)
        
        elif already_exists and conflicts == "Rename":
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    filename, file_extension = os.path.splitext(source_path)
                    new_name = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
                    new_source_path = f"{filename}_{new_name}{file_extension}"
                    os.rename(source_path, new_source_path)
                    source_path = new_source_path
                    Logger.log_renamed(type, base_name)

                    filename, file_extension = os.path.splitext(destination_path)
                    destination_path = f"{filename}_{new_name}{file_extension}"
                    break  # Exit the loop if renaming is successful
                except PermissionError:
                    if attempt < max_retries - 1:
                        time.sleep(1)  # Wait for 1 second before retrying
                    else:
                        Logger.log_error(type, f"Failed to rename {base_name} after {max_retries} attempts.")
                        return

        try:
            shutil.copy(source_path, destination_path)
            print(f"File copied successfully from {source_path} to {destination_path}")
            if not already_exists:
                Logger.log_success(type, base_name)
        except FileNotFoundError:
            Logger.log_error(type, f"{base_name} does not exist.")
        except PermissionError:
            Logger.log_error(type, f"Permission denied for {base_name}")
        except Exception as e:
            Logger.log_error(type, f"An error occurred: {e}")

    def find_and_remove(self, type, source_path, destination_folder):
        source_path = source_path[0]
        base_name = os.path.basename(source_path)
        destination_path = os.path.join(destination_folder, base_name)  
        if os.path.exists(destination_path):
            try:
                os.remove(destination_path)
                Logger.log_removed(type, base_name)
            except OSError as e:
                Logger.log_error(type, base_name)

    def create_archive(self, folders, archive_path):
        # Specify the full path to the 7zip executable
        path_to_7zip = patoolib.util.find_program("7z")
        if not path_to_7zip:
            Logger.log_error("SCRIPT", "7zip not found. Unable to create backup")
            raise Exception()
        
        if os.path.exists(archive_path+".7z"):
            os.remove(archive_path+".7z")
        
        path_to_7zip = f'"{path_to_7zip}"'
        archive_path = f'"{archive_path}"'
        exclude_folders = [
            '"Sideloader Modpack"', 
            '"Sideloader Modpack - Studio"',
            '"Sideloader Modpack - KK_UncensorSelector"', 
            '"Sideloader Modpack - Maps"', 
            '"Sideloader Modpack - KK_MaterialEditor"',
            '"Sideloader Modpack - Fixes"',
            '"Sideloader Modpack - Exclusive KK KKS"',
            '"Sideloader Modpack - Exclusive KK"',
            '"Sideloader Modpack - Animations"'
        ] 

        # Create a string of folder names to exclude
        exclude_string = ''
        for folder in exclude_folders:
            exclude_string += f'-xr!{folder} '

        # Create a string of folder names to include
        include_string = ''
        for folder in folders:
            include_string += f'"{folder}" '

        # Construct the 7zip command
        command = f'{path_to_7zip} a -t7z {archive_path} {include_string} {exclude_string}'
        # Call the command
        process = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Print the output
        for line in process.stdout.decode().split('\n'):
            if line.strip() != "":
                Logger.log_info("7-Zip", line)
        # Check the return code
        if process.returncode not in [0, 1]:
            raise Exception()

    def extract_archive(self, archive_path):
        try:
            archive_name = os.path.basename(archive_path)
            Logger.log_info("ARCHIVE", f"Extracting {archive_name}")
            extract_path = os.path.join(f"{os.path.splitext(archive_path)[0]}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}")
            patoolib.extract_archive(archive_path, outdir=extract_path)
            return extract_path
        
        except patoolib.util.PatoolError as e:
            text = f"There is an error with the archive {archive_name} but it is impossible to detect the cause. Maybe it requires a password?"
            while self.config.install_chara["Password"] == "Request Password":
                try:
                    dialog = customtkinter.CTkInputDialog(text=text, title="Enter Password")
                    password = dialog.get_input() 

                    if password is not None or "":
                        patoolib.extract_archive(archive_path, outdir=extract_path, password=password)
                        return extract_path                
                    else:
                        break
                except:
                    text = f"Wrong password or {archive_name} is corrupted. Please enter password again or click Cancel"

            Logger.log_skipped("ARCHIVE", archive_name)

