# coding:utf-8
"""
core/paths.py — Where KKAFIO's shipped files live.

`APP_DIR` is the folder that contains ``assets/`` (and, in a release, the
executable itself):

  - frozen (PyInstaller) build: the folder holding the executable
  - running from source:        the repository root

Anything that needs a shipped file should build on `ASSETS_DIR` rather than
walking up from its own ``__file__`` — that way moving a module between
packages can never silently break asset lookup.

(`core/constants.py` is different: it holds the *per-user* config directory
under %APPDATA%, not files shipped with the app.)
"""

import sys
from pathlib import Path

# src/kkafio/core/paths.py -> parents: core, kkafio, src, <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]

APP_DIR: Path = (
    Path(sys.executable).parent if getattr(sys, "frozen", False) else _REPO_ROOT
)
ASSETS_DIR: Path = APP_DIR / "assets"
ASSETS_DATA_DIR: Path = ASSETS_DIR / "data"
