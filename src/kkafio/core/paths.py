# coding:utf-8
"""
core/paths.py — Where KKAFIO's files live: shipped files and per-user config.

Shipped files
-------------
`APP_DIR` is the folder that contains ``assets/`` (and, in a release, the
executable itself):

  - frozen (PyInstaller) build: the folder holding the executable
  - running from source:        the repository root

Anything that needs a shipped file should build on `ASSETS_DIR` rather than
walking up from its own ``__file__`` — that way moving a module between
packages can never silently break asset lookup.

Per-user config
---------------
`CONFIG_DIR` is the *per-user* config directory (e.g. %APPDATA%/KKAFIO). `CONFIG_PATH` and `TELEGRAM_CONFIG` live inside it.
"""

import functools
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Per-user config directory
# ---------------------------------------------------------------------------

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


@functools.cache
def _config_dir() -> Path:
    """Resolve and create the config directory (once, on first use)."""
    d = _get_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_DIR = _config_dir()
# MXU saves config as  <CONFIG_DIR>/config/mxu-KKAFIO.json
CONFIG_PATH      = CONFIG_DIR / "config" / "mxu-KKAFIO.json"
TELEGRAM_CONFIG  = CONFIG_DIR / "config" / "telegram.json"


# src/kkafio/core/paths.py -> parents: core, kkafio, src, <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]

APP_DIR: Path = (
    Path(sys.executable).parent if getattr(sys, "frozen", False) else _REPO_ROOT
)
ASSETS_DIR: Path = APP_DIR / "assets"
ASSETS_DATA_DIR: Path = ASSETS_DIR / "data"