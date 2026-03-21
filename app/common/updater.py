# coding: utf-8
import os
import sys
import ssl
import json
import zipfile
import tempfile

import certifi
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from packaging.version import Version
from PySide6.QtCore import QRunnable, QObject, Signal, Slot
from qfluentwidgets import MessageBox, ProgressBar, CaptionLabel

from app.common.config import VERSION, REPO_URL, cfg


def _ssl_context():
    return ssl.create_default_context(cafile=certifi.where())


GITHUB_API = REPO_URL.replace("https://github.com/", "https://api.github.com/repos/") + "/releases/latest"


class UpdaterSignals(QObject):
    updateAvailable = Signal(str, str)  # (latest_version, download_url)
    noUpdate = Signal()
    error = Signal(str)
    progress = Signal(int, int)          # (bytes_downloaded, total_bytes)
    extracting = Signal()                # download done, extraction started


class UpdateChecker(QRunnable):
    """Checks GitHub releases API for a newer version. Runs in a thread."""

    def __init__(self):
        super().__init__()
        self.setAutoDelete(False)
        self.signals = UpdaterSignals()

    def run(self):
        try:
            with urlopen(GITHUB_API, timeout=5, context=_ssl_context()) as response:
                data = json.loads(response.read().decode())

            latest = data.get("tag_name", "").lstrip("v")
            if not latest:
                self.signals.error.emit("Could not parse release version.")
                return

            assets = data.get("assets", [])
            zip_url = next(
                (a["browser_download_url"] for a in assets if a["name"].endswith(".zip")),
                None
            )

            if not zip_url:
                self.signals.error.emit("No zip asset found in latest release.")
                return

            if Version(latest) > Version(VERSION):
                self.signals.updateAvailable.emit(latest, zip_url)
            else:
                self.signals.noUpdate.emit()

        except URLError as e:
            self.signals.error.emit(f"Network error: {e.reason}")
        except Exception as e:
            self.signals.error.emit(str(e))


class UpdateInstaller(QRunnable):
    """Downloads and extracts the update zip over the current directory."""

    def __init__(self, download_url: str):
        super().__init__()
        self.setAutoDelete(False)
        self.download_url = download_url
        self.signals = UpdaterSignals()

    def run(self):
        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="kkafio_update_"))
            zip_path = tmp_dir / "update.zip"

            with urlopen(self.download_url, context=_ssl_context()) as response:
                total = int(response.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 65536  # 64KB chunks
                with open(zip_path, "wb") as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        self.signals.progress.emit(downloaded, total)

            self.signals.extracting.emit()

            # Extract to staging — do NOT extract directly over the install dir
            # since the running exe locks files in _internal
            staging_dir = tmp_dir / "staging"
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(staging_dir)

            install_dir  = Path(sys.executable).parent
            helper_path  = tmp_dir / "_update_helper.ps1"
            executable   = install_dir / "KKAFIO.exe"
            log_path     = tmp_dir / "update.log"

            # Use raw Windows paths with backslashes to avoid PowerShell path issues
            staging_win  = str(staging_dir).replace("/", "\\")
            target_win   = str(install_dir).replace("/", "\\")
            exe_win      = str(executable).replace("/", "\\")
            log_win      = str(log_path).replace("/", "\\")

            helper_script = f"""
$pid_to_wait = {os.getpid()}
$staging     = '{staging_win}'
$target      = '{target_win}'
$executable  = '{exe_win}'
$log         = '{log_win}'

function Log($msg) {{
    $ts = Get-Date -Format 'HH:mm:ss'
    Add-Content -Path $log -Value "[$ts] $msg"
}}

Log "Helper started. Waiting for PID $pid_to_wait to exit."

try {{
    $proc = Get-Process -Id $pid_to_wait -ErrorAction SilentlyContinue
    if ($proc) {{
        Log "Process found, waiting..."
        $proc.WaitForExit(30000)
        Log "Process exited."
    }} else {{
        Log "Process already gone."
    }}
}} catch {{
    Log "Wait error: $_"
}}

Start-Sleep -Seconds 2

Log "Running robocopy from $staging to $target"
$result = robocopy $staging $target /MIR /IS /IT /IM /NFL /NDL /XD "$target\app\config" 2>&1
Log "Robocopy output: $result"
Log "Robocopy exit code: $LASTEXITCODE"

if ($LASTEXITCODE -le 7) {{
    Log "Copy successful. Launching $executable"
    Start-Process $executable
    Log "Launched."
}} else {{
    Log "ERROR: robocopy failed with exit code $LASTEXITCODE"
}}
"""
            helper_path.write_text(helper_script, encoding="utf-8")

            self.signals.updateAvailable.emit("done", str(helper_path))

        except Exception as e:
            self.signals.error.emit(str(e))


class DownloadDialog(MessageBox):
    """Modal dialog that shows a progress bar while the update is downloading."""

    def __init__(self, parent=None):
        super().__init__(
            "Downloading update",
            "Please wait while the update is being downloaded...",
            parent
        )
        # Hide the buttons — the dialog closes automatically when done
        self.yesButton.hide()
        self.cancelButton.hide()

        self.statusLabel = CaptionLabel("Starting download...", self)
        self.progressBar = ProgressBar(self)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)

        self.textLayout.addWidget(self.statusLabel)
        self.textLayout.addWidget(self.progressBar)

    @Slot(int, int)
    def onProgress(self, downloaded: int, total: int):
        if total > 0:
            percent = int(downloaded / total * 100)
            self.progressBar.setValue(percent)
            self.statusLabel.setText(
                f"Downloading... {downloaded // (1024*1024):.1f} MB / {total // (1024*1024):.1f} MB"
            )
        else:
            # Content-Length not available — show indeterminate progress
            self.progressBar.setRange(0, 0)
            self.statusLabel.setText(
                f"Downloading... {downloaded // (1024*1024):.1f} MB"
            )

    @Slot()
    def onExtracting(self):
        self.progressBar.setRange(0, 0)
        self.statusLabel.setText("Extracting...")


class UpdateManager:
    """
    Coordinates the check → prompt → install → restart flow.
    Call check() after the main window is shown.
    """

    def __init__(self, signal_bus, main_window):
        self.signal_bus = signal_bus
        self.main_window = main_window
        signal_bus.checkUpdateSignal.connect(self._onManualCheck)

    def check(self):
        """Automatic check on startup — respects checkUpdateAtStartUp setting."""
        if not cfg.get(cfg.checkUpdateAtStartUp):
            return
        self._run_checker(manual=False)

    def _onManualCheck(self):
        """Manual check triggered by the About card — always runs."""
        self._run_checker(manual=True)

    def _run_checker(self, manual: bool):
        checker = UpdateChecker()
        checker.signals.updateAvailable.connect(self._onUpdateAvailable)
        if manual:
            checker.signals.noUpdate.connect(self._onNoUpdate)
            checker.signals.error.connect(self._onCheckError)
        self.signal_bus.threadPool.start(checker)

    @Slot(str, str)
    def _onUpdateAvailable(self, latest_version: str, download_url: str):
        dialog = MessageBox(
            f"Update available — v{latest_version}",
            f"A new version of KKAFIO is available (you have v{VERSION}).\n\n"
            "Do you want to download and install it now?\n"
            "The application will restart automatically.",
            self.main_window
        )
        dialog.yesButton.setText("Update")
        dialog.cancelButton.setText("Later")

        if dialog.exec():
            self._install(download_url)

    def _install(self, download_url: str):
        self._download_dialog = DownloadDialog(self.main_window)

        installer = UpdateInstaller(download_url)
        installer.signals.progress.connect(self._download_dialog.onProgress)
        installer.signals.extracting.connect(self._download_dialog.onExtracting)
        installer.signals.updateAvailable.connect(self._onInstallDone)
        installer.signals.error.connect(self._onInstallError)
        installer.signals.error.connect(lambda _: self._download_dialog.close())

        self.signal_bus.threadPool.start(installer)
        self._download_dialog.exec()

    @Slot(str, str)
    def _onInstallDone(self, _, helper_path: str):
        self._download_dialog.close()
        dialog = MessageBox(
            "Update ready",
            "The update has been downloaded. KKAFIO will now close and apply the update, then restart automatically.",
            self.main_window
        )
        dialog.cancelButton.hide()
        dialog.exec()
        self._restart(helper_path)

    @staticmethod
    def _restart(helper_path: str):
        import subprocess
        import time

        # Use cmd.exe to launch PowerShell so it's fully detached from this process tree.
        # start /b launches it in the background with no window.
        cmd = (
            f'powershell.exe -ExecutionPolicy Bypass -File "{helper_path}"'
        )
        subprocess.Popen(
            f'cmd.exe /c start "" /b {cmd}',
            shell=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        # Brief pause to ensure the process is spawned before we exit
        time.sleep(0.5)
        sys.exit(0)

    @Slot(str)
    def _onInstallError(self, error: str):
        dialog = MessageBox(
            "Update failed",
            f"An error occurred while installing the update:\n\n{error}\n\n"
            "You can download the update manually from the releases page.",
            self.main_window
        )
        dialog.cancelButton.hide()
        dialog.exec()

    @Slot()
    def _onNoUpdate(self):
        dialog = MessageBox(
            "No updates available",
            f"You are already on the latest version (v{VERSION}).",
            self.main_window
        )
        dialog.cancelButton.hide()
        dialog.exec()

    @Slot(str)
    def _onCheckError(self, error: str):
        dialog = MessageBox(
            "Update check failed",
            f"Could not check for updates:\n\n{error}",
            self.main_window
        )
        dialog.cancelButton.hide()
        dialog.exec()