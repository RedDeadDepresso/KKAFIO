"""
kkafio_cli.py — thin launcher for the KKAFIO CLI.

The implementation lives in the `kkafio` package under src/ (see
src/kkafio/cli.py for the command reference). This file exists so that

  - the PyInstaller build has a plain script to use as its entry point
    (the frozen exe is what interface.json / the context menu call), and
  - `python kkafio_cli.py <command>` keeps working from a source checkout
    without installing the package first.

Equivalent ways to run from source:  `python -m kkafio ...` (with src/ on
the path, e.g. after `uv sync`) or the `kkafio` console script.
"""

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    _src = Path(__file__).resolve().parent / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from kkafio.cli import main

if __name__ == "__main__":
    main()
