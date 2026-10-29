"""cache.py — Shared on-disk cache helpers for the downloaded indices.

Every index lives in CONFIG_DIR/indices/<module name>.json (agv.json, gacha.json,
kkc_mod.json, password.json, scene.json) next to a <name>.json.etag file holding the ETag of
the copy on disk. Indices are re-downloaded with a conditional request (If-None-Match), so
an unchanged index costs a 304 and no download.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def default_dir() -> Path:
    from kkafio.core.paths import CONFIG_DIR
    return CONFIG_DIR / "indices"


def etag_path(path: Path) -> Path:
    return path.with_name(path.name + ".etag")


def conditional_headers(path: Path) -> dict[str, str]:
    """If-None-Match header for the cached index at `path`, or {} if there is no saved ETag.
    Only send it when the cached copy is known to load, or a 304 would leave nothing to use."""
    try:
        etag = etag_path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    return {"If-None-Match": etag} if etag else {}


def save(path: Path, data, etag: str | None) -> None:
    """Atomically write `data` as the cached index and record its ETag (or drop a stale one).
    Raises OSError if the files can't be written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    if etag:
        etag_path(path).write_text(etag, encoding="utf-8")
    else:
        etag_path(path).unlink(missing_ok=True)
