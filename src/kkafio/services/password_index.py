"""kk-password-scraper index: {archive filename: [passwords]}.

The index is published by https://github.com/RedDeadDepresso/kk-password-scraper.
A copy is kept in CONFIG_DIR/config and only re-downloaded when the repo's
latest commit differs from the one recorded in
kk_password_index_last_commit.txt (same scheme as the scene index).
"""

import json
import os
from pathlib import Path

from kkafio.core.logger import logger

PASSWORD_INDEX_URL = "https://reddeaddepresso.github.io/kk-password-scraper/kk_password_index.json"
PASSWORD_INDEX_COMMITS_API = "https://api.github.com/repos/RedDeadDepresso/kk-password-scraper/commits"

INDEX_FILENAME = "kk_password_index.json"
COMMIT_FILENAME = "kk_password_index_last_commit.txt"

TAG = "PWDIDX"

# Loaded at most once per process: extracting many archives must not hit the
# GitHub API once per archive.
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


def _fetch_latest_commit(client) -> str | None:
    """SHA of the repo's latest commit, or None if it couldn't be determined."""
    try:
        r = client.get(
            PASSWORD_INDEX_COMMITS_API,
            params={"per_page": 1},
            headers={"Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        data = r.json()
        return data[0]["sha"].strip() if data else None
    except Exception as e:
        logger.warning(TAG, f"Could not fetch latest kk-password-scraper commit: {e}")
        return None


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


def load_password_index(client=None, config_dir: Path | None = None) -> PasswordIndex:
    """Return the password index.

    The cached copy is used only if the commit saved next to it matches the
    repo's latest commit and it loads; otherwise the index is downloaded again
    and the saved commit updated. If the latest commit can't be checked (e.g.
    offline), a cached copy is used rather than failing. Returns an empty
    index if none is available.
    """
    if config_dir is None:
        from kkafio.core.paths import CONFIG_DIR
        config_dir = CONFIG_DIR / "config"
    index_path = config_dir / INDEX_FILENAME
    commit_path = config_dir / COMMIT_FILENAME

    own_client = client is None
    if own_client:
        import httpx
        client = httpx.Client(follow_redirects=True, timeout=60)
    try:
        latest = _fetch_latest_commit(client)

        saved = ""
        try:
            if commit_path.exists():
                saved = commit_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass

        if latest and saved == latest:
            cached = _load_cached_index(index_path)
            if cached is not None:
                index = normalize_index(cached)
                logger.info(TAG, f"Password index up to date: {len(index)} archive(s) (cached)")
                return PasswordIndex(index)
            logger.info(TAG, "Password index cache missing or unreadable — downloading...")
        elif latest is None:
            cached = _load_cached_index(index_path)
            if cached is not None:
                logger.warning(TAG, "Could not check for password index updates — using cached copy.")
                return PasswordIndex(normalize_index(cached))
        else:
            logger.info(TAG, "Password index is new or outdated — downloading...")

        try:
            r = client.get(PASSWORD_INDEX_URL)
            r.raise_for_status()
            raw = r.json()
            if not isinstance(raw, dict):
                raise ValueError("index is not a JSON object")
        except Exception as e:
            logger.error(TAG, f"Failed to download password index: {e}")
            return PasswordIndex()

        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            tmp = index_path.with_name(index_path.name + ".part")
            tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, index_path)
            if latest:
                commit_path.write_text(latest, encoding="utf-8")
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
