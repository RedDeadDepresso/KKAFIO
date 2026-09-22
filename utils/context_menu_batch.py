"""
context_menu_batch.py — Coalesce multiple simultaneous Explorer context-menu
invocations (one per selected file) into a single batched run.

The problem: Explorer's basic registry-based "shell command" context menu
entries don't support passing every selected file to one process invocation.
Selecting N files and choosing a KKAFIO command spawns N separate processes,
each given exactly one of the N paths via %1 — which is why Archive Cards /
Delete Cards previously opened one terminal window per selected card instead
of doing one combined operation.

The fix here doesn't need to recover the full selection from Explorer at all
(e.g. via clipboard tricks) — each of the N processes already has its own
correct path. They just need to coordinate:

  1. Every invocation locks a small on-disk "batch" file (keyed by task name
     + the shared parent folder — a single Explorer multi-select is always
     within one folder) and appends its own path to it.
  2. The first process to create that batch file is the "leader": after
     appending its own path, it waits a short debounce window for sibling
     invocations to register theirs, then reads back the final combined
     list and does the real work for all of them.
  3. Every other process ("follower") appends its path and returns
     immediately without doing anything else — no terminal window lingers,
     no duplicate/racy work happens.

Windows-only (msvcrt-based file locking); safe to import elsewhere since the
only public function no-ops with a single-item batch on other platforms.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import xxhash

_DEBOUNCE_SECONDS = 0.6   # quiet period: how long with no new sibling before the leader gives up waiting
_POLL_INTERVAL    = 0.05  # how often the leader re-checks the batch file while waiting
_MAX_WAIT_SECONDS = 5.0   # hard cap on total wait, in case siblings keep trickling in forever
_STALE_SECONDS     = 5.0  # a leftover batch file older than this is ignored


def _batch_file_paths(task_name: str, parent_dir: Path) -> tuple[Path, Path]:
    # Must be identical across the separate Explorer-spawned processes, so use a
    # deterministic hash (not Python's per-process-randomised built-in hash()).
    # xxh3_64 hexdigest is exactly 16 chars.
    key = xxhash.xxh3_64_hexdigest(f"{task_name}|{parent_dir}".encode("utf-8"))
    tmp = Path(tempfile.gettempdir())
    return tmp / f"kkafio_ctxmenu_{key}.lock", tmp / f"kkafio_ctxmenu_{key}.json"


def coordinate_batch(task_name: str, own_path: Path,
                     debounce_seconds: float = _DEBOUNCE_SECONDS,
                     stale_seconds: float = _STALE_SECONDS) -> list[Path] | None:
    """Coordinate this invocation with any Explorer siblings.

    Returns the full list of paths for this batch if this process is the
    leader (it should do the real work), or None if it's a follower (it
    should exit immediately without doing anything).
    """
    if sys.platform != "win32":
        # No context-menu integration outside Windows — always act alone.
        return [own_path]

    import msvcrt

    own_path = own_path.resolve()
    lock_path, batch_path = _batch_file_paths(task_name, own_path.parent)

    is_leader = False
    try:
        with open(lock_path, "a+") as lock_file:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                now = time.time()
                entries: list[str] = []
                started_at = now

                if batch_path.exists():
                    try:
                        data = json.loads(batch_path.read_text(encoding="utf-8"))
                        if now - data.get("started_at", 0) <= stale_seconds:
                            entries    = data.get("paths", [])
                            started_at = data.get("started_at", now)
                        # else: stale leftover from an earlier run — start fresh
                    except Exception:
                        pass

                is_leader = not entries

                if str(own_path) not in entries:
                    entries.append(str(own_path))

                batch_path.write_text(
                    json.dumps({"started_at": started_at, "paths": entries}),
                    encoding="utf-8",
                )
            finally:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        # Locking unavailable for some reason — fail safe by acting alone
        # on just this one path rather than silently doing nothing.
        return [own_path]

    if not is_leader:
        return None

    # Leader: wait for sibling invocations to register their own path.
    #
    # A single fixed sleep here doesn't scale with the size of the
    # selection: Explorer spawns one process per selected file, and for a
    # large multi-select (hundreds of cards) the OS can take much longer
    # than a fixed ~0.6s to fork/launch all of them and have each one reach
    # this point. A leader that stops waiting too early reads back an
    # incomplete batch, unlinks the batch file, and moves on — every
    # straggler that registers after that finds no batch file, becomes a
    # leader in its own right, and processes/logs as its own separate
    # (much smaller) batch instead of being folded into the first one.
    #
    # Poll instead: keep waiting as long as new siblings are still showing
    # up (a sliding "quiet period" of debounce_seconds with no growth), up
    # to a hard cap of _MAX_WAIT_SECONDS so one huge or stalled selection
    # can't block the leader indefinitely.
    wait_start = time.time()
    last_count = 1  # this leader's own path was already written above
    last_growth_at = wait_start
    while True:
        time.sleep(_POLL_INTERVAL)
        now = time.time()
        try:
            data = json.loads(batch_path.read_text(encoding="utf-8"))
            count = len(data.get("paths", []))
        except Exception:
            count = last_count
        if count > last_count:
            last_count = count
            last_growth_at = now
        if now - last_growth_at >= debounce_seconds:
            break
        if now - wait_start >= _MAX_WAIT_SECONDS:
            break

    final_paths = [own_path]
    try:
        with open(lock_path, "a+") as lock_file:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                try:
                    data = json.loads(batch_path.read_text(encoding="utf-8"))
                    final_paths = [Path(p) for p in data.get("paths", [own_path])]
                except Exception:
                    pass
                try:
                    batch_path.unlink()
                except OSError:
                    pass
            finally:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass

    try:
        lock_path.unlink()
    except OSError:
        pass

    return final_paths