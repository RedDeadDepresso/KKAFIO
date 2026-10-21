"""
compress_cards_textures.py — Recompress the textures embedded inside
Koikatsu chara/coordinate cards using KoiCardTexTool
(https://github.com/EeEeX4/koikatsu-card-texture-tool), shrinking card file
size dramatically (often -50% to -80%) with minimal quality loss.

KoiCardTexTool writes its compressed copies to a sibling folder named after the
input folder plus "[zip]" (C:/KKAFIO/Downloads -> C:/KKAFIO/Downloads[zip]),
keeping the input's subfolder layout, each file named "<original name>[zip].png".
Once it finishes, everything is moved back into the input folder (same relative
layout), the "[zip]" folder is removed, and — if enabled — the originals that
now have a compressed "[zip]" copy next to them are sent to the bin.

Cards already named "<name>[zip].png" (left by an earlier run) are renamed to
"<name>[zip].png.bak" while the tool runs, so it doesn't compress them again, and
renamed back afterwards, whether or not the run succeeded.

KoiCardTexTool itself isn't bundled with KKAFIO — if it isn't found where
configured, this task downloads and extracts the release build automatically
before running it.
"""

import hashlib
import io
import shutil
import subprocess
import zipfile
from pathlib import Path

from send2trash import send2trash

from kkafio.tasks.base_task import BaseTask, DEFAULT_DOWNLOADS_PATH, validate_input_path
from kkafio.core.errors import InputError, TaskFailedError, ToolNotFoundError
from kkafio.core.logger import logger
from kkafio.system.subprocess_utils import popen_text

KOICARDTEXTOOL_URL = (
    "https://github.com/EeEeX4/koikatsu-card-texture-tool/"
    "releases/download/v1.0.1/KoiCardTexTool-1.0.1.zip"
)
KOICARDTEXTOOL_EXE = "KoiCardTexTool.exe"

# Appended to the input folder's path to name the output folder, and passed to
# the tool as `suffix=` so every compressed card is named "<name>[zip].png".
ZIP_SUFFIX = "[zip]"

# Marker KoiCardTexTool drops in its output folder; it must NOT be moved into
# the input folder, because folders carrying it are never used as tool input.
OUT_MARKER = ".koicardtex-outdir"

# Extra extension put on an existing "<name>[zip].png" while the tool runs.
BAK_SUFFIX = ".bak"

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
        """True only if `path` is a complete PNG (signature + IEND chunk) that
        carries a recognisable KK payload after IEND: a chara card, scene, or
        coordinate card. A truncated or corrupt output from a failed tool run
        fails at least one of these checks."""
        from kkafio.cards.parsing import _find_iend_end
        from kkafio.cards.classifier import CardType, get_card_type, is_coordinate

        try:
            data = path.read_bytes()
        except OSError:
            return False
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        png_end = _find_iend_end(data)
        if png_end < 0 or png_end >= len(data):
            return False  # no IEND (truncated) or no card payload after it
        payload = data[png_end:]
        return get_card_type(payload) != CardType.UNKNOWN or is_coordinate(payload)

    @staticmethod
    def _backup_zip_cards(input_path: Path) -> list[Path]:
        """Rename every existing "<name>[zip].png" under `input_path` to "<name>[zip].png.bak"
        so KoiCardTexTool leaves it alone. Returns the .bak paths that were made."""
        backed_up: list[Path] = []
        for png in sorted(input_path.rglob("*.png")):
            if not png.is_file() or not png.stem.endswith(ZIP_SUFFIX):
                continue
            bak = png.with_name(png.name + BAK_SUFFIX)
            try:
                png.replace(bak)      # a .bak left behind by an interrupted run is overwritten
                backed_up.append(bak)
            except OSError as e:
                logger.warning("KOITEX", f"Could not rename {png.name} to {bak.name}: {e}")
        if backed_up:
            logger.info("KOITEX", f"Set aside {len(backed_up)} existing {ZIP_SUFFIX} card(s) "
                                  f"as {BAK_SUFFIX} while the tool runs")
        return backed_up

    @staticmethod
    def _restore_zip_cards(backed_up: list[Path]) -> None:
        """Undo _backup_zip_cards(). If a card with the original name now exists (the tool just
        produced a fresh one), that fresh copy wins and the old .bak is dropped, as the merge
        into the input folder always overwrote an older copy."""
        restored = replaced = 0
        for bak in backed_up:
            if not bak.exists():
                continue
            png = bak.with_name(bak.name[:-len(BAK_SUFFIX)])
            try:
                if png.exists():
                    bak.unlink()
                    replaced += 1
                else:
                    bak.replace(png)
                    restored += 1
            except OSError as e:
                logger.error("KOITEX", f"Could not restore {png.name} from {bak.name}: {e}")
        if backed_up:
            logger.info("KOITEX", f"Restored {restored} {ZIP_SUFFIX} card(s)"
                                  + (f"; {replaced} replaced by a fresh compressed copy" if replaced else ""))

    def _merge_output_into_input(self, input_path: Path, output_path: Path) -> bool:
        """Move every file from the "[zip]" output folder into the input folder
        (same relative layout; an existing file is overwritten), then delete
        the output folder. Returns True if the output folder is gone.

        If any file can't be moved the output folder is left in place so
        nothing is lost.
        """
        logger.line()
        if not output_path.exists():
            logger.info("KOITEX", f"Output folder {output_path} not found — nothing to move")
            return False

        logger.info("KOITEX", f"Moving compressed cards into {input_path} ...")
        moved = 0
        failed = 0
        for src in sorted(output_path.rglob("*")):
            if not src.is_file() or src.name == OUT_MARKER:
                continue
            dst = input_path / src.relative_to(output_path)
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    dst.unlink()
                shutil.move(str(src), str(dst))
                moved += 1
            except Exception as e:
                failed += 1
                logger.error("KOITEX", f"Could not move {src.name}: {e}")
        logger.info("KOITEX", f"Moved {moved} file(s)" + (f", {failed} failed" if failed else ""))

        if failed:
            logger.warning("KOITEX",
                f"Keeping {output_path} because some files could not be moved")
            return False
        try:
            shutil.rmtree(output_path)
            logger.removed("KOITEX", output_path.name)
            return True
        except Exception as e:
            logger.error("KOITEX", f"Could not delete {output_path}: {e}")
            return False

    def _delete_originals(self, input_path: Path) -> None:
        logger.line()
        logger.info("KOITEX", f"Deleting original cards that have a compressed {ZIP_SUFFIX} version...")
        deleted = 0
        checked = 0
        skipped_invalid = 0
        for compressed in input_path.rglob("*.png"):
            if not compressed.stem.endswith(ZIP_SUFFIX):
                continue
            checked += 1
            original_path = compressed.with_name(compressed.stem[:-len(ZIP_SUFFIX)] + ".png")
            if not original_path.exists():
                continue

            # Guard against deleting the original for a compressed output
            # that never finished writing or is corrupt — KoiCardTexTool
            # didn't produce a usable replacement, so the original is kept.
            if not self._is_valid_card(compressed):
                logger.warning("KOITEX",
                    f"  Skipping delete — compressed file looks invalid: {compressed.name}")
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
            + (f"  skipped (invalid): {skipped_invalid}" if skipped_invalid else ""))
        logger.line()

    def run(self) -> None:
        if not self.input_path_str:
            raise InputError("No input folder specified.", tag="KOITEX")
        input_path = Path(self.input_path_str)
        validate_input_path("KOITEX", input_path, default_path=DEFAULT_DOWNLOADS_PATH)

        # Falls back to the input folder itself if KoiCardTexToolPath was
        # ever explicitly cleared out (its normal default is C:/KoiCardTexTool).
        tool_dir = Path(self.tool_path_str) if self.tool_path_str else input_path

        exe_path = self._ensure_tool(tool_dir)
        if exe_path is None:
            raise ToolNotFoundError("CompressCardsTextures: KoiCardTexTool is not available", tag="KOITEX")

        # Compressed copies go to "<input folder>[zip]" (a sibling folder).
        output_path = Path(f"{input_path}{ZIP_SUFFIX}")

        logger.line()
        logger.info("KOITEX", f"Input folder    : {input_path}")
        logger.info("KOITEX", f"Output folder   : {output_path}")
        logger.info("KOITEX", f"KoiCardTexTool  : {exe_path}")
        logger.info("KOITEX", f"Delete originals: {self.delete_original}")
        logger.line()

        # Existing "<name>[zip].png" cards are set aside so the tool skips them; they come
        # back afterwards even if the run fails.
        backed_up = self._backup_zip_cards(input_path)
        try:
            cmd = [str(exe_path), "batch", str(input_path), str(output_path), f"suffix={ZIP_SUFFIX}"]

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
                raise TaskFailedError(
                    f"CompressCardsTextures: KoiCardTexTool exited with code {process.returncode}")

            logger.success("KOITEX", "Compression complete")

            # Bring the compressed cards back next to the originals and drop the
            # temporary "[zip]" folder, then (optionally) bin the originals.
            self._merge_output_into_input(input_path, output_path)
        finally:
            self._restore_zip_cards(backed_up)

        if self.delete_original:
            self._delete_originals(input_path)