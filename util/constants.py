import os
from pathlib import Path


CONFIG_DIR = Path(os.environ["APPDATA"]) / "KKAFIO"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "config.json"
SEVEN_ZIP_PATH = CONFIG_DIR / "7zip.json"