# coding: utf-8
import os
import sys
import ssl
import json
import shutil
import zipfile
import tempfile

import certifi
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from packaging.version import Version
from PySide6.QtCore import QRunnable, QObject, Signal, Slot
from qfluentwidgets import MessageBox

from app.common.config import VERSION, REPO_URL, cfg


def _ssl_context():
    return ssl.create_default_context(cafile=certifi.where())


GITHUB_API = REPO_URL.replace("https://github.com/", "https://api.github.com/repos/") + "/releases/latest"


class UpdaterSignals(QObject):
    updateAvailable = Signal(str, str)  # (latest_version, download_url)
    noUpdate = Signal()
    error = Signal(str)


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
                zip_path.write_bytes(response.read())

            # Extract to a staging folder inside tmp — do NOT extract directly
            # over the install dir since the running exe locks files in _internal
            staging_dir = tmp_dir / "staging"
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(staging_dir)

            install_dir = Path(sys.executable).parent
            helper_path = install_dir / "_update_helper.py"

            # Write a helper script that waits for this process to exit,
            # copies staged files over, then restarts KKAFIO.exe
            helper_script = f"""\
import os
import sys
import time
import shutil
import psutil

pid        = {os.getpid()}
staging    = r"{staging_dir}"
target     = r"{install_dir}"
executable = r"{sys.executable}"

# Wait for KKAFIO.exe to exit
try:
    proc = psutil.Process(pid)
    proc.wait(timeout=30)
except Exception:
    pass

# Give the OS a moment to release file handles
time.sleep(1)

# Copy staged files over the install dir
for item in os.listdir(staging):
    src = os.path.join(staging, item)
    dst = os.path.join(target, item)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)

# Clean up staging and this helper script
shutil.rmtree(staging, ignore_errors=True)
try:
    os.remove(os.path.join(target, "_update_helper.py"))
except Exception:
    pass

# Restart KKAFIO
os.execv(executable, [executable])
"""
            helper_path.write_text(helper_script, encoding="utf-8")

            self.signals.updateAvailable.emit("done", str(helper_path))

        except Exception as e:
            self.signals.error.emit(str(e))


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
        installer = UpdateInstaller(download_url)
        installer.signals.updateAvailable.connect(self._onInstallDone)
        installer.signals.error.connect(self._onInstallError)
        self.signal_bus.threadPool.start(installer)

    @Slot(str, str)
    def _onInstallDone(self, _, helper_path: str):
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
        # Launch helper with pythonw (no console window).
        # It waits for this process to exit before copying files over.
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        if not pythonw.exists():
            pythonw = Path(sys.executable)
        subprocess.Popen([str(pythonw), helper_path])
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