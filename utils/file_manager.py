import shutil
import subprocess
import tempfile
import time

from datetime import datetime
from pathlib import Path
from utils.classifier import is_mod_archive
from utils.logger import logger
from typing import Union, Literal

from utils.subprocess_utils import popen_text, run_text


FileEntry = tuple[Path, int, str]


class FileManager:
    _path_to_7zip = None

    def __init__(self, config):
        self.config = config

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

                if file_extension.lower() == ".zip" and is_mod_archive(file_path):
                    # A plain .zip containing a manifest.xml is a mod, not a
                    # generic archive to extract — route it alongside
                    # .zipmod files instead of into archive_list.
                    file_list.append(file_entry)
                elif file_extension in archive_extensions:
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
                logger.error(file_type, f"Could not remove {base_name}: {e}")

    @staticmethod
    def _get_nt_7z_dirs() -> list[str]:
        """Return 7-Zip install directories from the registry (HKLM, then
        HKCU for per-user installs), or an empty list."""
        try:
            import winreg  # noqa: PLC0415
        except ImportError:
            return []  # not Windows
        import platform  # noqa: PLC0415

        keyname = r"SOFTWARE\7-Zip"
        wow64 = 0
        if platform.architecture()[0] == "32bit" and platform.machine().endswith("64"):
            wow64 = winreg.KEY_WOW64_64KEY  # read the 64-bit key from 32-bit Python

        dirs: list[str] = []
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in ({wow64, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY}
                         if wow64 == 0 else {wow64}):
                try:
                    key = winreg.OpenKey(hive, keyname, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                try:
                    for value_name in ("Path64", "Path"):
                        try:
                            value = winreg.QueryValueEx(key, value_name)[0]
                        except OSError:
                            continue
                        if value and value not in dirs:
                            dirs.append(value)
                finally:
                    winreg.CloseKey(key)
        return dirs

    @classmethod
    def find_7zip(cls) -> str | None:
        """Return the path to the 7-Zip executable, or None if not found.

        Search order: registry install dirs (HKLM, HKCU), then PATH (7z,
        7zz, 7za), then the usual Program Files locations.
        """
        if cls._path_to_7zip is not None:
            return cls._path_to_7zip

        import os  # noqa: PLC0415

        found: str | None = None
        for d in cls._get_nt_7z_dirs():
            found = shutil.which("7z", path=d)
            if found:
                break

        if not found:
            for name in ("7z", "7zz", "7za"):
                found = shutil.which(name)  # default PATH lookup
                if found:
                    break

        if not found:
            candidates = [
                os.path.join(os.environ.get(var, ""), "7-Zip")
                for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
                if os.environ.get(var)
            ]
            for d in candidates:
                found = shutil.which("7z", path=d)
                if found:
                    break

        cls._path_to_7zip = found
        return found

    def create_archive(self, files: list[Path], output_path: Path, fmt: str) -> None:
        path_to_7zip = self.find_7zip()
        if not path_to_7zip:
            raise RuntimeError("7-Zip not found. Install 7-Zip and ensure '7z' is on PATH.")

        output_path = Path(output_path)
        if output_path.exists():
            # `7z a` (add) UPDATES an existing archive rather than replacing
            # it: entries not in `files` this time (e.g. a mod that's no
            # longer needed, or last run's README) are left behind instead
            # of being dropped, and if the same content_path bundle name is
            # reused (single-card mode always reuses "<name>_bundle.7z")
            # every re-run would silently accumulate stale entries forever.
            # An explicit archive is meant to be a clean, reproducible
            # snapshot of exactly `files` — start from nothing.
            output_path.unlink()

        flag = "-t7z" if fmt == "7z" else "-tzip"
        # -sccUTF-8 makes 7-Zip write its console output as UTF-8, so decode it
        # as UTF-8 explicitly instead of relying on the process-wide locale
        # (which is UTF-8 under MXU because kkafio.rs sets PYTHONUTF8=1, but
        # would be the legacy codepage when run from a terminal).
        #
        # File list is passed via a 7-Zip @listfile instead of directly on
        # the command line: cmd.exe / CreateProcess have a ~32K character
        # command-line limit, and a combined archive built from many cards
        # (each with its own zipmods and matched coordinates) can easily
        # exceed that with long absolute paths, causing 7-Zip to simply
        # never be invoked (or invoked with a silently truncated file list).
        import tempfile
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", prefix="kkafio_7z_list_",
            delete=False, encoding="utf-8",
        ) as listfile:
            listfile.write("\n".join(str(f) for f in files))
            listfile_path = listfile.name

        try:
            # -scsUTF-8: the listfile itself was written as UTF-8 — needed so
            # 7-Zip parses non-ASCII paths in it correctly (Koikatsu mod and
            # character names are very often Japanese/Chinese).
            cmd = [path_to_7zip, "a", flag, "-sccUTF-8", "-scsUTF-8",
                   str(output_path), f"@{listfile_path}"]
            result = run_text(cmd, capture_output=True, encoding="utf-8")
            if result.returncode not in (0, 1):
                raise RuntimeError(f"7-Zip failed:\n{result.stderr}")
        finally:
            try:
                Path(listfile_path).unlink()
            except OSError:
                pass
    
    def copy_files_flat(self, files: list[Path], output_dir: Path) -> None:
        """Copy every file in `files` directly into `output_dir`, flattened
        to a single level exactly the way create_archive's 7-Zip call lays
        them out (each file added by its own absolute path ends up at the
        archive root, named just by its filename — there's no per-file
        destination sub-folder to replicate).

        If `output_dir` already exists (as a folder or, defensively, a
        stray file) it's removed first and rebuilt from nothing, matching
        create_archive's "start a clean, reproducible snapshot" behaviour
        for a reused output name rather than layering onto whatever was
        left over from a previous run.
        """
        output_dir = Path(output_dir)
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
        elif output_dir.exists():
            output_dir.unlink()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Same collision 7-Zip itself would hit ("Duplicate filename on
        # disk") when two source files share a basename — fail loudly
        # instead of silently overwriting one with the other.
        seen: dict[str, Path] = {}
        for f in files:
            f = Path(f)
            name = f.name
            prior = seen.get(name)
            if prior is not None and prior != f:
                raise RuntimeError(
                    f"Duplicate filename among files to copy: {name!r} "
                    f"({prior} and {f})")
            seen[name] = f
            shutil.copy2(f, output_dir / name)

    def stage_bundle_files(self, card_files: dict[str, list[Path]],
                           loose_files: list[Path]) -> Path:
        """Build a fresh staging folder for a per-card bundle layout: one
        subfolder per key of `card_files` holding that card's own files,
        plus `loose_files` (e.g. the bundle README) copied straight into
        the staging root. Used so a combined archive/copy of several cards
        can be laid out as `<Character Name>/<their files>` instead of
        everything dumped flat at the top level.

        Raises the same way 7-Zip itself would ("Duplicate filename on
        disk") if two files that would land in the same folder share a
        basename, instead of silently overwriting one with the other.

        The caller owns the returned folder: remove it once an archive has
        been built from it, or move it straight into place for a plain
        folder-copy bundle.
        """
        staging = Path(tempfile.mkdtemp(prefix="kkafio_stage_"))

        def _copy_into(dest_dir: Path, files: list[Path], scope: str) -> None:
            dest_dir.mkdir(parents=True, exist_ok=True)
            seen: dict[str, Path] = {}
            for f in files:
                f = Path(f)
                name = f.name
                prior = seen.get(name)
                if prior is not None and prior != f:
                    raise RuntimeError(
                        f"Duplicate filename in {scope}: {name!r} "
                        f"({prior} and {f})")
                seen[name] = f
                shutil.copy2(f, dest_dir / name)

        for folder_name, files in card_files.items():
            _copy_into(staging / folder_name, files, repr(folder_name))
        _copy_into(staging, loose_files, "bundle root")

        return staging

    def create_archive_from_dir(self, source_dir: Path, output_path: Path, fmt: str) -> None:
        """Archive the *contents* of `source_dir` (not `source_dir` itself)
        into `output_path`, preserving whatever subfolder structure it has.
        Unlike create_archive — which adds each file by its own absolute
        path and so always collapses to a flat archive root — this is for
        a combined bundle built with stage_bundle_files, where the
        per-card subfolder layout needs to survive into the archive.
        """
        path_to_7zip = self.find_7zip()
        if not path_to_7zip:
            raise RuntimeError("7-Zip not found. Install 7-Zip and ensure '7z' is on PATH.")

        output_path = Path(output_path).resolve()
        if output_path.exists():
            output_path.unlink()

        flag = "-t7z" if fmt == "7z" else "-tzip"
        # cwd=source_dir + "." (rather than an absolute path) is what makes
        # 7-Zip store paths relative to source_dir, keeping the per-card
        # subfolders intact instead of flattening everything to basenames.
        cmd = [path_to_7zip, "a", flag, "-sccUTF-8", "-scsUTF-8", str(output_path), "."]
        result = run_text(cmd, capture_output=True, encoding="utf-8", cwd=str(source_dir))
        if result.returncode not in (0, 1):
            raise RuntimeError(f"7-Zip failed:\n{result.stderr}")

    def move_dir_into_place(self, source_dir: Path, output_dir: Path) -> None:
        """Move `source_dir` so it becomes `output_dir`, deleting whatever
        already exists at `output_dir` first (folder or, defensively, a
        stray file) — the folder-copy equivalent of create_archive's
        "start a clean, reproducible snapshot" behaviour for a reused
        output name.
        """
        output_dir = Path(output_dir)
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
        elif output_dir.exists():
            output_dir.unlink()
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(output_dir))

    def create_game_archive(self, folders: list[Literal["mods", "UserData", "BepInEx"]], archive_path: Union[str, Path]):
        """Create an archive of the given folders using 7zip."""
        path_to_7zip = self.find_7zip()
        if not path_to_7zip:
            logger.error("SCRIPT", "7zip not found. Unable to create backup")
            raise Exception()
        
        if not folders:
            logger.error("SCRIPT", "No folders selected for the backup")
            raise Exception("No folders selected for the backup")

        archive_path = Path(archive_path)
        # Append rather than with_suffix(): a filename such as
        # "backup_2026.09.24" would otherwise lose its ".24" part.
        if archive_path.suffix.lower() != ".7z":
            archive_path = archive_path.with_name(archive_path.name + ".7z")

        if archive_path.exists():
            archive_path.unlink()

        # Wildcard so every Sideloader Modpack folder ("Sideloader Modpack",
        # "Sideloader Modpack - Studio", and any added in future) is skipped.
        exclude_patterns = ["Sideloader Modpack*"]

        cmd = [path_to_7zip, "a", "-t7z", "-bsp1", "-sccUTF-8", str(archive_path)]
        cmd += [str(f) for f in folders]
        cmd += [f"-xr!{pattern}" for pattern in exclude_patterns]

        # -sccUTF-8 (above) makes 7-Zip emit UTF-8, so decode it as UTF-8
        # regardless of the process locale.
        process = popen_text(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=self.config.game_path['base'], encoding="utf-8",
        )

        while True:
            line = process.stdout.readline()
            if not line:
                break
            if line.strip():
                logger.info("7-Zip", line.strip())

        process.wait()

        # Check the return code
        if process.returncode not in [0, 1]:
            logger.error("7-Zip", f"Exited with return code: {process.returncode}")
            raise Exception(f"7-zip exited with return code: {process.returncode}")
        
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

        result = run_text(cmd, capture_output=True)
        if result.returncode == 0:
            return True

        # 7-Zip can still create the output folder and write partial/0-byte
        # copies of encrypted entries before it hits the password error, so
        # a failed attempt (wrong/missing password, corrupted archive, etc.)
        # can otherwise leave junk files behind even though nothing usable
        # was actually extracted. Clean up before returning so a retry (with
        # a different password, or none) starts from a clean folder, and so
        # nothing is left over if extraction is abandoned entirely.
        if extract_path.exists():
            try:
                shutil.rmtree(extract_path)
            except OSError as e:
                logger.error("ARCHIVE", f"Could not remove partial extraction folder {extract_path}: {e}")

        return False

    def extract_archive(self, archive_path: Union[Path, str], task_config: dict = None):
        """Extract the archive using 7-Zip into a folder named after it.

        Returns the extraction folder, or None if the archive was skipped
        (folder already exists) or could not be extracted.
        """
        if task_config is None:
            task_config = self.config.install_contents

        archive_path = Path(archive_path)
        archive_name = archive_path.name

        # Extract next to the archive into a folder named after it. If that
        # folder is already there the archive was extracted before, so leave
        # it alone instead of extracting it again.
        extract_path = archive_path.with_name(archive_path.stem)
        if extract_path.exists():
            logger.skipped("ARCHIVE", f"{archive_name} (folder '{extract_path.name}' already exists)")
            return None

        logger.info("ARCHIVE", f"Extracting {archive_name}")

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