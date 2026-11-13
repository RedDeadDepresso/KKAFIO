"""The download history: ``{source url: path of the file it produced}``.

It is what makes "Skip already downloaded" work, and is checkpointed after
every URL / page so an interrupted run keeps everything fetched so far.
"""

from __future__ import annotations

import json
import os

from kkafio.core.logger import logger
from kkafio.core.paths import CONFIG_DIR

# Use the same per-platform config directory as everything else (config.json,
# telegram.json) instead of a separate, Windows-only APPDATA
# lookup — the old fallback (`Path.home() / "AppData" / "Roaming"`) doesn't
# make sense on Linux/macOS at all, so history silently ended up somewhere
# that isn't even where the rest of KKAFIO's own data lives.
HISTORY_FILE = CONFIG_DIR / "download_history.json"


def load_history() -> dict[str, str]:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error("DLOAD", f"Could not load download history: {e}")
        return {}


def save_history(history: dict[str, str]) -> None:
    """Write the history atomically (temp file + rename) so a crash or Stop
    in the middle of a save can't leave a truncated/corrupt history file.

    Written compactly (no indent) rather than indent=2: this is now
    checkpointed after every single URL/page (see the callers), and
    it only grows over time as more downloads happen, so keeping it small
    and skipping the pretty-printing overhead actually matters here.
    """
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_name(HISTORY_FILE.name + ".tmp")
    tmp.write_text(
        json.dumps(history, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(tmp, HISTORY_FILE)
