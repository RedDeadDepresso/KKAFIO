"""Shared low-level helpers for KKAFIO's on-disk JSON caches."""

import json as _json
import os
from pathlib import Path


def atomic_write_json(path: Path, data) -> None:
    """Write `data` as JSON to `path` atomically and compactly.

    Atomic: written to a temp file next to `path` first, then moved into
    place with os.replace — a crash, power loss, or Stop mid-write can
    never leave a half-written cache file behind (previously a plain
    write_text() could, and a truncated cache is unreadable JSON, forcing
    a full rescan next run instead of the incremental one the cache exists
    to avoid).

    Compact: no indent and no spaces after separators. These cache files
    (guids-by-file, mods, coordinates) can run to many thousands of
    entries — indent=2 alone roughly doubles the on-disk size for data
    that's never meant to be hand-edited, and pretty-printing is pure
    overhead for something read back by json.loads() alone.
    """
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(_json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# File fingerprint helpers
# ---------------------------------------------------------------------------

def file_fp(p: Path) -> list[int]:
    """Return [mtime_int, size] for a file — used as a change fingerprint.

    A list (not a tuple) so it compares equal to values loaded from the JSON
    caches and can be stored back without conversion."""
    try:
        st = p.stat()
        return [int(st.st_mtime), st.st_size]
    except OSError:
        return [0, 0]
