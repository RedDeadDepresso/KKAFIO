import os
import shutil
import datetime
import patoolib
import customtkinter
import subprocess
import time

from app.common.logger import logger


class FileManager:

    def __init__(self, config):
        self.config = config

    def findAllFiles(self, directory):
        fileList = []
        archiveList = []
        archiveExtensions = [".rar", ".zip", ".7z"]

        for root, dirs, files in os.walk(directory):
            for file in files:

                filePath = os.path.join(root, file)
                fileSize = os.path.getsize(filePath)
                _, fileExtension = os.path.splitext(filePath)

                if fileExtension in archiveExtensions:
                    archiveList.append((filePath, fileSize, fileExtension))
                else:
                    fileList.append((filePath, fileSize, fileExtension))

        fileList.sort(key=lambda x: x[1])
        archiveList.sort(key=lambda x: x[1])
        return fileList, archiveList
    
    def copyAndPaste(self, type, sourcePath, destinationFolder):
        sourcePath = sourcePath[0]
        baseName = os.path.basename(sourcePath)
        destinationPath = os.path.join(destinationFolder, baseName)
        conflicts = self.config.install_chara["FileConflicts"]
        alreadyExists = os.path.exists(destinationPath)

        if alreadyExists and conflicts == "Skip":
            logger.skipped(type, baseName)
            return
        
        elif alreadyExists and conflicts == "Replace":
            logger.replaced(type, baseName)
        
        elif alreadyExists and conflicts == "Rename":
            maxRetries = 3
            for attempt in range(maxRetries):
                try:
                    filename, fileExtension = os.path.splitext(sourcePath)
                    newName = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
                    newSourcePath = f"{filename}_{newName}{fileExtension}"
                    os.rename(sourcePath, newSourcePath)
                    sourcePath = newSourcePath
                    logger.renamed(type, baseName)

                    filename, fileExtension = os.path.splitext(destinationPath)
                    destinationPath = f"{filename}_{newName}{fileExtension}"
                    break  # Exit the loop if renaming is successful
                except PermissionError:
                    if attempt < maxRetries - 1:
                        time.sleep(1)  # Wait for 1 second before retrying
                    else:
                        logger.error(type, f"Failed to rename {baseName} after {maxRetries} attempts.")
                        return

        try:
            shutil.copy(sourcePath, destinationPath)
            print(f"File copied successfully from {sourcePath} to {destinationPath}")
            if not alreadyExists:
                logger.success(type, baseName)
        except FileNotFoundError:
            logger.error(type, f"{baseName} does not exist.")
        except PermissionError:
            logger.error(type, f"Permission denied for {baseName}")
        except Exception as e:
            logger.error(type, f"An error occurred: {e}")

    def findAndRemove(self, type, sourcePath, destinationFolder):
        sourcePath = sourcePath[0]
        baseName = os.path.basename(sourcePath)
        destinationPath = os.path.join(destinationFolder, baseName)  
        if os.path.exists(destinationPath):
            try:
                os.remove(destinationPath)
                logger.removed(type, baseName)
            except OSError as e:
                logger.error(type, baseName)

    def createArchive(self, folders, archivePath):
        # Specify the full path to the 7zip executable
        pathTo7zip = patoolib.util.find_program("7z")
        if not pathTo7zip:
            logger.error("SCRIPT", "7zip not found. Unable to create backup")
            raise Exception()
        
        if os.path.exists(archivePath+".7z"):
            os.remove(archivePath+".7z")
        
        pathTo7zip = f'"{pathTo7zip}"'
        archivePath = f'"{archivePath}"'
        excludeFolders = [
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
        excludeString = ''
        for folder in excludeFolders:
            excludeString += f'-xr!{folder} '

        # Create a string of folder names to include
        includeString = ''
        for folder in folders:
            includeString += f'"{folder}" '

        # Construct the 7zip command
        command = f'{pathTo7zip} a -t7z {archivePath} {includeString} {excludeString}'
        # Call the command
        process = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Print the output
        for line in process.stdout.decode().split('\n'):
            if line.strip() != "":
                logger.info("7-Zip", line)
        # Check the return code
        if process.returncode not in [0, 1]:
            raise Exception()

    def extractArchive(self, archivePath):
        try:
            archiveName = os.path.basename(archivePath)
            logger.info("ARCHIVE", f"Extracting {archiveName}")
            extractPath = os.path.join(f"{os.path.splitext(archivePath)[0]}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}")
            patoolib.extract_archive(archivePath, outdir=extractPath)
            return extractPath
        
        except patoolib.util.PatoolError as e:
            text = f"There is an error with the archive {archiveName} but it is impossible to detect the cause. Maybe it requires a password?"
            while self.config.install_chara["Password"] == "Request Password":
                try:
                    dialog = customtkinter.CTkInputDialog(text=text, title="Enter Password")
                    password = dialog.get_input() 

                    if password is not None or "":
                        patoolib.extract_archive(archivePath, outdir=extractPath, password=password)
                        return extractPath                
                    else:
                        break
                except:
                    text = f"Wrong password or {archiveName} is corrupted. Please enter password again or click Cancel"

            logger.skipped("ARCHIVE", archiveName)


fileManager = FileManager()