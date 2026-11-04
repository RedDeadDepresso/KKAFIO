"""Helpers for tests of the downloaded-index cache (kkafio.indices.cache)."""

import json
from datetime import datetime, timedelta
from pathlib import Path


def read_meta(index_path: Path) -> dict:
    """The <name>.meta.json beside the cached index at `index_path`."""
    return json.loads(index_path.with_name(index_path.stem + ".meta.json").read_text(encoding="utf-8"))


def age(index_path: Path, hours: float) -> None:
    """Make the cached index look as if it was last checked `hours` ago."""
    meta_file = index_path.with_name(index_path.stem + ".meta.json")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["last_checked"] = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
