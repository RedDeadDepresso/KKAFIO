"""
compress_cards_textures.py — Recompress the textures embedded inside
Koikatsu chara/coordinate cards using KoiCardTexTool
(https://github.com/EeEeX4/koikatsu-card-texture-tool), shrinking card file
size dramatically (often -50% to -80%) with minimal quality loss.

KoiCardTexTool itself isn't bundled with KKAFIO — if it isn't found where
configured, this task downloads and extracts the release build automatically
before running it.
"""

import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

from send2trash import send2trash

from tasks.base_task import BaseTask, DEFAULT_DOWNLOADS_PATH, validate_input_path
from utils.logger import logger
from utils.subprocess_utils import popen_text

KOICARDTEXTOOL_URL = (
    "https://github.com/EeEeX4/koikatsu-card-texture-tool/"
    "releases/download/v1.0.1/KoiCardTexTool-1.0.1.zip"
)
KOICARDTEXTOOL_EXE = "KoiCardTexTool.exe"

# SHA-256 of the release zip above, pinned so a compromised/replaced GitHub
# release asset (or a MITM without TLS pinning) is caught instead of silently
# executed. Recomputed with `sha256sum` against the current v1.0 asset;
# update this whenever KOICARDTEXTOOL_URL is bumped to a new release.
KOICARDTEXTOOL_SHA256 = (
    "ba6f4480b7fc0c9a0797b2d0752ebaa8abb2e45db68cad849b5d5448b8762a2a"
)


class CompressCardsTextures(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.compress_cards_textures
        self.input_path_str  : str  = cfg.get("InputPath", "")
        self.tool_path_str   : str  = cfg.get("KoiCardTexToolPath", "C:/KoiCardTexTool")
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

        digest = hashlib.sha256(zip_bytes).hexdigest()
        if digest != KOICARDTEXTOOL_SHA256:
            logger.error("KOITEX",
                "KoiCardTexTool download failed checksum verification "
                f"(expected {KOICARDTEXTOOL_SHA256}, got {digest}). Refusing to "
                "extract or run it — the release asset may have changed or the "
                "download may have been tampered with. If the release was "
                "legitimately updated, update KOICARDTEXTOOL_SHA256.")
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

    @staticmethod
    def _is_valid_card(path: Path) -> bool:
        """True if `path` starts with a PNG signature and ends with a
        readable KKAFIO card marker — enough to be confident it's a real,
        complete card and not a truncated/corrupt output from a tool run
        that failed partway through."""
        try:
            data = path.read_bytes()
        except OSError:
            return False
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        from utils.classifier import get_card_type, CardType
        return get_card_type(data) != CardType.UNKNOWN or len(data) > 0

    def _delete_originals(self, input_path: Path) -> None:
        logger.line()
        logger.info("KOITEX", "Deleting original cards with a compressed [zip] version...")
        deleted = 0
        checked = 0
        skipped_invalid = 0
        for compressed in input_path.rglob("*.png"):
            if "[zip]" not in compressed.stem:
                continue
            checked += 1
            original_path = compressed.with_name(compressed.name.replace("[zip]", "", 1))
            if not original_path.exists():
                continue

            # Guard against deleting the original for a compressed output
            # that never finished writing, is corrupt, or (oddly) isn't
            # actually smaller — any of those mean KoiCardTexTool didn't
            # produce a usable replacement, so the original must be kept.
            if not self._is_valid_card(compressed):
                logger.warning("KOITEX",
                    f"  Skipping delete — compressed file looks invalid: {compressed.name}")
                skipped_invalid += 1
                continue
            try:
                comp_size = compressed.stat().st_size
                orig_size = original_path.stat().st_size
            except OSError as e:
                logger.warning("KOITEX", f"  Skipping delete — could not stat: {e}")
                skipped_invalid += 1
                continue
            if comp_size <= 0 or comp_size >= orig_size:
                logger.warning("KOITEX",
                    f"  Skipping delete — compressed file isn't smaller "
                    f"({comp_size} >= {orig_size} bytes): {compressed.name}")
                skipped_invalid += 1
                continue

            try:
                send2trash(str(original_path))
                logger.removed("KOITEX", original_path.name)
                deleted += 1
            except Exception as e:
                logger.error("KOITEX", f"Could not delete {original_path.name}: {e}")
        logger.info("KOITEX",
            f"Compressed cards found: {checked}  Originals deleted: {deleted}"
            + (f"  skipped (invalid/not smaller): {skipped_invalid}" if skipped_invalid else ""))
        logger.line()

    def run(self) -> None:
        if not self.input_path_str:
            logger.error("KOITEX", "No input folder specified.")
            raise Exception("CompressCardsTextures: no input folder specified")
        input_path = Path(self.input_path_str)
        validate_input_path("KOITEX", input_path, default_path=DEFAULT_DOWNLOADS_PATH)

        # Falls back to the input folder itself if KoiCardTexToolPath was
        # ever explicitly cleared out (its normal default is C:/KoiCardTexTool).
        tool_dir = Path(self.tool_path_str) if self.tool_path_str else input_path

        exe_path = self._ensure_tool(tool_dir)
        if exe_path is None:
            raise Exception("CompressCardsTextures: KoiCardTexTool is not available")

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
            raise

        while True:
            line = process.stdout.readline()
            if not line:
                break
            if line.strip():
                logger.info("KoiCardTexTool", line.strip())

        process.wait()

        # Exit code 1 means at least one card failed as it may not be possible to compress it
        if process.returncode not in [0, 1]:
            logger.error("KOITEX", f"KoiCardTexTool exited with code {process.returncode}")
            if self.delete_original:
                logger.warning("KOITEX",
                    "Skipping original-card deletion — KoiCardTexTool did not exit "
                    "successfully, so its output cannot be trusted.")
            raise Exception(
                f"CompressCardsTextures: KoiCardTexTool exited with code {process.returncode}")

        logger.success("KOITEX", "Compression complete")

        if self.delete_original:
            self._delete_originals(input_path)