"""cache.py — Shared on-disk cache helpers for the downloaded indices.

Every index lives in CONFIG_DIR/indices/<module name>.json (agv.json, gacha.json,
kkc_mod.json, password.json, scene.json) next to a <module name>.meta.json file,
{"etag": "...", "last_checked": "<ISO 8601 datetime>"}: the ETag of the copy on disk and when
KKAFIO last confirmed it is current. The server is only asked again once CHECK_INTERVAL has
passed since last_checked; the request is then conditional (If-None-Match), so an unchanged
index costs a 304 and no download.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

CHECK_INTERVAL = timedelta(hours=1)


def default_dir() -> Path:
    from kkafio.core.paths import CONFIG_DIR
    return CONFIG_DIR / "indices"


def meta_path(path: Path) -> Path:
    return path.with_name(path.stem + ".meta.json")


def _read_meta(path: Path) -> dict:
    try:
        meta = json.loads(meta_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _write_meta(path: Path, etag: str | None) -> None:
    meta = {"etag": etag or "", "last_checked": datetime.now().astimezone().isoformat(timespec="seconds")}
    tmp = meta_path(path).with_name(meta_path(path).name + ".part")
    tmp.write_text(json.dumps(meta), encoding="utf-8")
    os.replace(tmp, meta_path(path))


def is_fresh(path: Path) -> bool:
    """True if the index at `path` was checked against the server less than CHECK_INTERVAL ago.
    Callers only use it when the cached copy is known to load."""
    try:
        last_checked = datetime.fromisoformat(_read_meta(path)["last_checked"])
        if last_checked.tzinfo is None:
            last_checked = last_checked.astimezone()
    except (KeyError, TypeError, ValueError):
        return False
    age = datetime.now().astimezone() - last_checked
    return timedelta(0) <= age < CHECK_INTERVAL


def conditional_headers(path: Path) -> dict[str, str]:
    """If-None-Match header for the cached index at `path`, or {} if there is no saved ETag.
    Only send it when the cached copy is known to load, or a 304 would leave nothing to use."""
    etag = _read_meta(path).get("etag")
    return {"If-None-Match": etag} if isinstance(etag, str) and etag else {}


def touch(path: Path) -> None:
    """The server confirmed (304) that the cached index is current: restart the interval.
    Best effort — if the meta file can't be written the next run just asks again."""
    try:
        _write_meta(path, _read_meta(path).get("etag") or None)
    except OSError:
        pass


def save(path: Path, data, etag: str | None) -> None:
    """Atomically write `data` as the cached index and record its ETag and the check time.
    Raises OSError if the files can't be written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    _write_meta(path, etag)
