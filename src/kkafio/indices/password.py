"""kk-password-scraper index: {archive filename: [passwords]}.

The index is published by https://github.com/RedDeadDepresso/kk-password-scraper.
A copy is kept in CONFIG_DIR/indices/password.json and only re-downloaded when its ETag
changes (see kkafio.indices.cache).
"""

import json
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.indices import cache

PASSWORD_INDEX_URL = "https://reddeaddepresso.github.io/kk-password-scraper/kk_password_index.json"

INDEX_FILENAME = "password.json"

TAG = "PWDIDX"

# Loaded at most once per process: extracting many archives must not hit the
# network once per archive.
_session_index: "PasswordIndex | None" = None


class PasswordIndex:
    """Filename -> passwords lookup. Exact match first, then case-insensitive."""

    def __init__(self, data: dict[str, list[str]] | None = None):
        self._exact: dict[str, list[str]] = data or {}
        self._folded: dict[str, list[str]] = {}
        for name, passwords in self._exact.items():
            self._folded.setdefault(name.strip().casefold(), passwords)

    def __len__(self) -> int:
        return len(self._exact)

    def get(self, filename: str) -> list[str]:
        """Known passwords for `filename` (empty list if it isn't indexed)."""
        if filename in self._exact:
            return self._exact[filename]
        return self._folded.get(filename.strip().casefold(), [])


def _load_cached_index(index_path: Path) -> dict | None:
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        logger.warning(TAG, f"Cached {index_path.name} is not a JSON object.")
    except Exception as e:
        logger.warning(TAG, f"Could not load cached {index_path.name}: {e}")
    return None


def normalize_index(raw: dict) -> dict[str, list[str]]:
    """{filename: [password, ...]} with junk dropped.

    A bare string is accepted as a single password. Empty or non-string
    passwords are dropped, duplicates removed (order kept), and filenames left
    with no passwords omitted.
    """
    index: dict[str, list[str]] = {}
    for name, value in raw.items():
        if not isinstance(name, str):
            continue
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            continue
        passwords = list(dict.fromkeys(p for p in value if isinstance(p, str) and p))
        if passwords:
            index[name] = passwords
    return index


def load_password_index(client=None, index_dir: Path | None = None) -> PasswordIndex:
    """Return the password index.

    Uses a conditional request (ETag) against the cached copy, so an unchanged index
    costs a 304 and no download. If the download fails (e.g. offline), the cached copy
    is used. Returns an empty index if none is available.
    """
    index_path = (index_dir or cache.default_dir()) / INDEX_FILENAME
    cached = _load_cached_index(index_path)
    headers = cache.conditional_headers(index_path) if cached is not None else {}

    own_client = client is None
    if own_client:
        import httpx
        client = httpx.Client(follow_redirects=True, timeout=60)
    try:
        try:
            r = client.get(PASSWORD_INDEX_URL, headers=headers)
            if r.status_code == 304 and cached is not None:
                index = normalize_index(cached)
                logger.info(TAG, f"Password index up to date: {len(index)} archive(s) (cached)")
                return PasswordIndex(index)
            r.raise_for_status()
            raw = r.json()
            if not isinstance(raw, dict):
                raise ValueError("index is not a JSON object")
        except Exception as e:
            if cached is not None:
                logger.warning(TAG, f"Could not check for password index updates ({e}) — using cached copy.")
                return PasswordIndex(normalize_index(cached))
            logger.error(TAG, f"Failed to download password index: {e}")
            return PasswordIndex()

        try:
            cache.save(index_path, raw, r.headers.get("etag"))
        except OSError as e:
            logger.warning(TAG, f"Could not save password index cache: {e}")

        index = normalize_index(raw)
        logger.info(TAG, f"Password index downloaded: {len(index)} archive(s)")
        return PasswordIndex(index)
    finally:
        if own_client:
            client.close()


def get_known_passwords(filename: str) -> list[str]:
    """Known passwords for an archive filename, loading the index on first use."""
    global _session_index
    if _session_index is None:
        try:
            _session_index = load_password_index()
        except Exception as e:
            # Never let the index break extraction; fall back to the dialog.
            logger.warning(TAG, f"Password index unavailable: {e}")
            return []
    return _session_index.get(filename)
