import os
import sys
from pathlib import Path


def _get_config_dir() -> Path:
    """Return the KKAFIO config directory, matching Rust get_app_data_dir()."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA environment variable is not set")
        return Path(appdata) / "KKAFIO"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "KKAFIO"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        return (Path(xdg) if xdg else Path.home() / ".config") / "KKAFIO"


CONFIG_DIR = _get_config_dir()
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# MXU saves config as  <CONFIG_DIR>/config/mxu-KKAFIO.json
CONFIG_PATH    = CONFIG_DIR / "config" / "mxu-KKAFIO.json"
SEVEN_ZIP_PATH = CONFIG_DIR / "7zip.json"
