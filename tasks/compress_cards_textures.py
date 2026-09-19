"""
compress_cards_textures.py — Recompress the textures embedded inside
Koikatsu chara/coordinate cards using KoiCardTexTool
(https://github.com/EeEeX4/koikatsu-card-texture-tool), shrinking card file
size dramatically (often -50% to -80%) with minimal quality loss.

KoiCardTexTool itself isn't bundled with KKAFIO — if it isn't found where
configured, this task downloads and extracts the release build automatically
before running it.
"""

import io
import subprocess
import zipfile
from pathlib import Path

from send2trash import send2trash

from tasks.base_task import BaseTask, validate_input_path
from utils.logger import logger
from utils.subprocess_utils import popen_text

KOICARDTEXTOOL_URL = (
    "https://github.com/EeEeX4/koikatsu-card-texture-tool/"
    "releases/download/v1.0/KoiCardTexTool-1.0.zip"
)
KOICARDTEXTOOL_EXE = "KoiCardTexTool.exe"


class CompressCardsTextures(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.compress_cards_textures
        self.input_path_str  : str  = cfg.get("InputPath", "")
        self.tool_path_str   : str  = cfg.get("KoiCardTexToolPath", "")
        self.delete_original : bool = cfg.get("DeleteOriginalCards", False)

    def _find_exe(self, tool_dir: Path) -> Path | None:
        """Look for the exe directly inside tool_dir, then recursively (the
        release zip may nest everything in a subfolder)."""
        direct = tool_dir / KOICARDTEXTOOL_EXE
        if direct.exists():
            return direct
        if tool_dir.exists():
            for candidate in tool_dir.rglob(KOICARDTEXTOOL_EXE):
                return candidate
        return None

    def _ensure_tool(self, tool_dir: Path) -> Path | None:
        """Make sure KoiCardTexTool.exe exists in tool_dir, downloading and
        extracting the release zip there if it's missing. Returns the exe
        path, or None on failure."""
        existing = self._find_exe(tool_dir)
        if existing is not None:
            return existing

        logger.info("KOITEX", f"KoiCardTexTool not found in {tool_dir} — downloading...")
        tool_dir.mkdir(parents=True, exist_ok=True)

        try:
            import httpx
            with httpx.Client(follow_redirects=True, timeout=120) as client:
                resp = client.get(KOICARDTEXTOOL_URL)
                resp.raise_for_status()
                zip_bytes = resp.content
        except Exception as e:
            logger.error("KOITEX", f"Could not download KoiCardTexTool: {e}")
            return None

        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                zf.extractall(tool_dir)
        except Exception as e:
            logger.error("KOITEX", f"Could not extract KoiCardTexTool: {e}")
            return None

        found = self._find_exe(tool_dir)
        if found is not None:
            logger.success("KOITEX", f"KoiCardTexTool ready: {found}")
            return found

        logger.error("KOITEX",
            f"{KOICARDTEXTOOL_EXE} not found after extracting the release zip.")
        return None

    def _delete_originals(self, input_path: Path) -> None:
        logger.line()
        logger.info("KOITEX", "Deleting original cards with a compressed [zip] version...")
        deleted = 0
        checked = 0
        for compressed in input_path.rglob("*.png"):
            if "[zip]" not in compressed.stem:
                continue
            checked += 1
            original_path = compressed.with_name(compressed.name.replace("[zip]", "", 1))
            if not original_path.exists():
                continue
            try:
                send2trash(str(original_path))
                logger.removed("KOITEX", original_path.name)
                deleted += 1
            except Exception as e:
                logger.error("KOITEX", f"Could not delete {original_path.name}: {e}")
        logger.info("KOITEX", f"Compressed cards found: {checked}  Originals deleted: {deleted}")
        logger.line()

    def run(self) -> None:
        if not self.input_path_str:
            logger.error("KOITEX", "No input folder specified.")
            return
        input_path = Path(self.input_path_str)
        validate_input_path("KOITEX", input_path)

        # Default the tool's own folder to the input folder itself when not
        # explicitly configured, so "download it if missing" naturally means
        # "download it right into the cards folder" in the common case.
        tool_dir = Path(self.tool_path_str) if self.tool_path_str else input_path

        exe_path = self._ensure_tool(tool_dir)
        if exe_path is None:
            return

        logger.line()
        logger.info("KOITEX", f"Input folder    : {input_path}")
        logger.info("KOITEX", f"KoiCardTexTool  : {exe_path}")
        logger.info("KOITEX", f"Delete originals: {self.delete_original}")
        logger.line()

        cmd = [str(exe_path), "batch", str(input_path), str(input_path)]

        try:
            process = popen_text(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
        except Exception as e:
            logger.error("KOITEX", f"Could not run KoiCardTexTool: {e}")
            return

        while True:
            line = process.stdout.readline()
            if not line:
                break
            if line.strip():
                logger.info("KoiCardTexTool", line.strip())

        process.wait()

        if process.returncode != 0:
            logger.error("KOITEX", f"KoiCardTexTool exited with code {process.returncode}")
        else:
            logger.success("KOITEX", "Compression complete")

        if self.delete_original:
            self._delete_originals(input_path)
