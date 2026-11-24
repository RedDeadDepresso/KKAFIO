"""A per-run memo for card scans, so the several passes of one task read and parse each file once.

Group Coordinates matches in passes (hair, outfit, head mod, ...). Each pass asks for a different
field of the same cards, and each field used to come from its own read + parse of every file: with
the cache off, or on a first run with an empty cache, that is the same file parsed 3-4 times.

Inside `with scan_session():` the compact per-file RESULTS (strings and small lists, never the
loaded card) are remembered by (kind, path, fingerprint), so later passes get them without touching
the file. Outside a session nothing is remembered, so "cache off" still means a fresh read on every
call, and nothing outlives the run that created it.
"""

from collections.abc import Callable
from contextlib import contextmanager

_MISSING = object()
_memo: dict | None = None


def active() -> bool:
    return _memo is not None


@contextmanager
def scan_session():
    """Remember scan results for the duration of the with-block (nestable: an inner block reuses
    the outer one's memo and leaves it in place)."""
    global _memo
    outer = _memo
    if outer is None:
        _memo = {}
    try:
        yield
    finally:
        if outer is None:
            _memo = None


def memoized(key, compute: Callable[[], object]):
    """compute() once per `key` while a session is active; every time otherwise. A None result is
    remembered too (it means "not this kind of file"). An exception is not."""
    memo = _memo
    if memo is None:
        return compute()
    value = memo.get(key, _MISSING)
    if value is _MISSING:
        value = memo[key] = compute()
    return value
