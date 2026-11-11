"""
filehosts.py — Download one mod from a Google Drive, Mega, Gofile or Catbox link of the mod index (indices/mod.py).

Reuses the downloaders of the content sources (services/content_sources/gdrive.py, mega.py), with
three differences for a mod:

* The file is downloaded into a scratch folder first and only moved to its place once its manifest
  says it is the mod that was asked for (a dead link, a quota page or a pack of other mods must not
  end up in the mods folder, and a wrong file must not stop the next source from being tried).
* The content downloaders count every file in the progress window; here the caller counts per mod,
  so their counting is muted.
* They checkpoint a download history for "Skip already downloaded"; mods are verified on disk
  instead, so that history is left alone.
"""

import asyncio
import contextlib
import shutil
import tempfile
from pathlib import Path

from kkafio.cards.mods.zipmods import guid_from_zipmod
from kkafio.core import progress
from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.catbox import download_catbox, is_catbox_url
from kkafio.services.content_sources.gdrive import download_gdrive, is_gdrive_url
from kkafio.services.content_sources.gofile import download_gofile, is_gofile_url
from kkafio.services.content_sources.mega import download_mega, is_mega_url

_DOWNLOADERS = {
    "drive": (is_gdrive_url, download_gdrive),
    "mega":  (is_mega_url,   download_mega),
    "gofile": (is_gofile_url, download_gofile),
    "catbox": (is_catbox_url, download_catbox),
}


@contextlib.contextmanager
def _history_not_saved():
    """The content downloaders save their history file when done: don't let them (their dict is a
    scratch one, and saving it would replace the real history)."""
    original = history_store.save_history
    history_store.save_history = lambda history: None
    try:
        yield
    finally:
        history_store.save_history = original


def _find_mod(folder: Path, guid: str) -> Path | None:
    """The file below `folder` that is the mod `guid`, else None."""
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if path.suffix.lower() in (".zipmod", ".zip") and guid_from_zipmod(path) == guid:
            return path
    return None


async def download_via_host(
    source: str,
    url: str,
    guid: str,
    mods_dir: Path,
    client,
    rel_path: str | None = None,
) -> bool | str:
    """Download the mod `guid` from `url`, a link of `source` ("drive", "mega", "gofile" or "catbox").

    The file is saved as mods_dir / <folder of rel_path> / <its name> (like the Telegram downloads
    do; directly in mods_dir without a rel_path). Returns True once it is saved, "skipped" if a file
    of that name is already there and is that mod, False otherwise (the reason is logged).
    """
    handler = _DOWNLOADERS.get(source)
    if handler is None:
        logger.error("DLMOD", f"Unknown download source {source!r}")
        return False
    matches, download = handler
    if not matches(url):
        logger.error("DLMOD", f"Not a {source} link: {url}")
        return False

    target_dir = (mods_dir / Path(rel_path).parent) if rel_path else mods_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".kkafio-dl-", dir=target_dir))
    try:
        try:
            with progress.quiet_counts(), _history_not_saved():
                ok, _skipped, failed = await download(client, url, scratch, {}, False)
        except Exception as e:              # (CancelledError is not an Exception: it goes through)
            logger.error("DLMOD", f"  {source} download failed [{guid}]: {e}")
            return False
        if not ok:
            logger.error("DLMOD", f"  {source} download failed [{guid}]: nothing was downloaded"
                                  f"{f' ({failed} failed)' if failed else ''}")
            return False

        mod = await asyncio.to_thread(_find_mod, scratch, guid)
        if mod is None:
            logger.error("DLMOD", f"  {source} link did not hold the mod {guid} — ignoring it")
            return False

        dest = target_dir / mod.name
        if dest.exists() and await asyncio.to_thread(guid_from_zipmod, dest) == guid:
            logger.info("DLMOD", f"  Already downloaded: {dest.name}")
            return "skipped"
        await asyncio.to_thread(shutil.move, str(mod), str(dest))
        logger.info("DLMOD", f"  Saved {dest.name} ({source})")
        return True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
