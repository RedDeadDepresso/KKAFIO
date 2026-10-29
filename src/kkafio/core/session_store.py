"""
session_store.py — Read and write one JSON file of a service's saved login state.

Every service keeps its state in ``<SESSIONS_DIR>/<service>.json`` (see
core/paths.py). A `SessionStore` owns one such file:

    store = SessionStore(SESSIONS_DIR / "pixiv.json", "PXVSES")
    store.save({"session": cookie})
    store.load().get("session", "")

Both operations are best effort: a missing, unreadable or malformed file loads
as ``{}`` and a failed write is logged and reported through the return value,
never raised — a lost session only means the user is asked to log in again.
Writes are atomic (temp file + replace), so a crash can't leave a half-written
file behind.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from kkafio.core.logger import logger


class SessionStore:
    def __init__(self, path: Path, tag: str):
        self.path = path
        self.tag  = tag          # logger category for this store's warnings

    def load(self) -> dict:
        """The stored data, or ``{}`` if there is none (or it is unreadable)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning(self.tag, f"Could not read {self.path.name}: {e}")
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict) -> bool:
        """Replace the stored data. Returns False (after logging) if it failed."""
        tmp = self.path.with_name(self.path.name + ".part")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            logger.warning(self.tag, f"Could not save {self.path.name}: {e}")
            return False

    def update(self, **fields) -> bool:
        """Merge `fields` into the stored data (other keys are kept)."""
        data = self.load()
        data.update(fields)
        return self.save(data)
