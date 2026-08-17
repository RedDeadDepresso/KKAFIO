"""Shared pytest fixtures and path setup."""
import sys
from pathlib import Path

# Add the KKAFIO root to sys.path so all imports work
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
