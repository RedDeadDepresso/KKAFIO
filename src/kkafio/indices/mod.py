"""kk-mod-scraper index: {mod GUID: [{"version": "1.2", "mega": [...], "drive": [...], "telegram": [...], "gofile": [...], "catbox": [...], "mediafire": [...], "pixeldrain": [...], "dropbox": [...]}, ...]}.

The index is published by https://github.com/RedDeadDepresso/kk-mod-scraper and tells
Download Missing Mods where a given mod can be downloaded: each mod has one entry per known
version, newest first and an entry of unknown version ("version": "unknown") last, and each
list of an entry holds links to the file on that host (Mega, Google Drive, a t.me message,
Gofile, Catbox, MediaFire, Pixeldrain, Dropbox). The version is normalized by the scraper
("1", "v1.0" and "1.0.0" are all "1.0") and is only used for logging. A copy is kept in
CONFIG_DIR/indices/mod.json; the server is asked at most once an hour, and the copy is only
re-downloaded when its ETag changes (see kkafio.indices.cache).
"""

import json
from pathlib import Path
from urllib.parse import urlparse

from kkafio.core.logger import logger
from kkafio.indices import cache

SOURCES = ("mega", "drive", "telegram", "gofile", "catbox", "mediafire", "pixeldrain", "dropbox")   # the keys of an entry of the index

# The default order to try the sources of a mod in. Download Missing Mods itself uses the order
# (and the selection) of its "Mod Sources" option, which is the user's to change; the trade-offs:
#   telegram  fastest (parallel MTProto download through the user's own account) and the most
#             reliable: files stay in the archive channel and there is no per-IP quota. It needs a
#             Telegram account, the other two don't.
#   drive     fast and needs no account, but popular files hit Drive's download quota
#             ("too many users have viewed or downloaded this file").
#   mega      transfers are metered per IP address and stop when the quota is used up, the
#             data is decrypted on the fly (slower), and its links die more often.
#   gofile    no account needed (a guest token is created on the fly); files expire when nobody
#             downloads them for a while, so its links die too.
#   catbox    plain HTTP download without any account or quota; the least likely to be rate
#             limited, but only has the mods somebody uploaded there.
#   mediafire no account needed; the download link is read from the file page, and the
#             files are only kept while somebody downloads them.
#   pixeldrain no account needed; popular files can be refused for anonymous downloads
#             (rate limit / captcha), and files expire when nobody downloads them.
#   dropbox   no account needed; a shared link is a plain HTTP download (a folder is fetched as a
#             zip), but links die when their owner removes the share, and heavily shared files
#             can hit Dropbox's traffic limit.
SOURCE_ORDER = ("telegram", "drive", "mega", "gofile", "catbox", "mediafire", "pixeldrain", "dropbox")

UNKNOWN_VERSION = "unknown"

# One version of a mod: {"version": "1.2", "drive": [link, ...], ...}; only sources with a link.
ModVersion = dict[str, "str | list[str]"]
# {guid: [version entry, ...]}, newest version first, "unknown" last (the order of the file).
ModIndex = dict[str, list[ModVersion]]

# Hosts that are always the same source, whatever list of the entry a link was put in.
_HOST_SOURCES = {"gofile.io": "gofile", "catbox.moe": "catbox", "mediafire.com": "mediafire",
                 "pixeldrain.com": "pixeldrain", "pixeldrain.net": "pixeldrain", "pixeldrain.dev": "pixeldrain",
                 "dropbox.com": "dropbox", "dropboxusercontent.com": "dropbox"}


def _source_of_host(url: str) -> str | None:
    """"gofile" / "catbox" / "mediafire" / "pixeldrain" / "dropbox" if `url` is a link of that host, else None."""
    host = (urlparse(url).hostname or "").lower()
    for domain, source in _HOST_SOURCES.items():
        if host == domain or host.endswith("." + domain):
            return source
    return None

INDEX_FILENAME = "mod.json"
MOD_INDEX_URL = f"https://reddeaddepresso.github.io/kk-mod-scraper/indices/{INDEX_FILENAME}"


def _normalize_version(entry) -> ModVersion | None:
    """One version entry of a mod: {"version": ..., "<source>": [link, ...], ...}. Sources that
    aren't lists of links and anything that isn't a link are dropped. None if it is not an object
    or is left without a link."""
    if not isinstance(entry, dict):
        return None
    links: dict[str, list[str]] = {}
    for source in SOURCES:
        urls = entry.get(source)
        if isinstance(urls, list):
            urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
            if urls:
                links[source] = urls
    # A Gofile / Catbox link is used as such even if the index listed it under another source.
    for source in list(links):
        for url in list(links[source]):
            host_source = _source_of_host(url)
            if host_source and host_source != source:
                links[source].remove(url)
                if url not in links.setdefault(host_source, []):
                    links[host_source].append(url)
        if not links[source]:
            del links[source]
    if not links:
        return None
    version = entry.get("version")
    version = version.strip() if isinstance(version, str) else ""
    return {"version": version or UNKNOWN_VERSION, **links}


def _normalize_index(raw) -> ModIndex:
    """The {guid: [version entry, ...]} mapping of a mod.json. Raises ValueError if `raw` isn't a
    JSON object. Mods that aren't lists of version entries (e.g. the older formats), versions
    that aren't objects and everything dropped by _normalize_version are left out, as are mods
    left without a link. The order of the versions (newest first) is the file's."""
    if not isinstance(raw, dict):
        raise ValueError("index is not a JSON object")
    index: ModIndex = {}
    for guid, versions in raw.items():
        if not isinstance(versions, list):
            continue
        entries = [v for v in map(_normalize_version, versions) if v is not None]
        if entries:
            index[guid] = entries
    return index


def normalize_sources(names) -> tuple[str, ...]:
    """The sources named by `names` (the case names of the "Mod Sources" option, e.g.
    ["Drive", "Telegram"]), as the keys of the index, in the order given. Case does not matter;
    unknown names and repeats are dropped."""
    out: list[str] = []
    for name in names or ():
        key = str(name).strip().lower()
        if key in SOURCES and key not in out:
            out.append(key)
    return tuple(out)


def ordered_links(entry: list[ModVersion] | None,
                  sources: tuple[str, ...] = SOURCE_ORDER) -> list[tuple[str, str, str]]:
    """[(source, link, version), ...] of one mod in the order to try them: newest version first
    (the order of the index), within a version by `sources` (the only sources used, best first;
    SOURCE_ORDER by default), then in the order of the index within a source."""
    if not entry:
        return []
    return [(source, url, version["version"])
            for version in entry
            for source in sources
            for url in version.get(source, [])]


def version_label(version: str | None) -> str:
    """How a version is written in the log: "v1.2", or "unknown version"."""
    return "unknown version" if not version or version == UNKNOWN_VERSION else f"v{version}"


def _load_cached_mod_index(index_path: Path) -> ModIndex | None:
    """Load the cached mod.json. None if missing or unreadable."""
    if not index_path.exists():
        return None
    try:
        index = _normalize_index(json.loads(index_path.read_text(encoding="utf-8")))
    except Exception as e:
        logger.warning("DLMOD", f"Could not load cached {index_path.name}: {e}")
        return None
    # A copy with no usable entry (e.g. one in an older format) is as good as none: download it again.
    return index or None


async def load_mod_index(client, index_dir: Path | None = None) -> ModIndex:
    """Return the {guid: [version entry, ...]} index from kk-mod-scraper.

    The server is only asked if the cached copy was last checked an hour or more ago, with a
    conditional request (ETag), so an unchanged index costs a 304 and no download. If the
    download fails, the cached copy is used.

    Returns an empty dict if the index is unavailable.
    """
    index_path = (index_dir or cache.default_dir()) / INDEX_FILENAME
    cached = _load_cached_mod_index(index_path)
    if cached is not None and cache.is_fresh(index_path):
        logger.info("DLMOD", f"mod index checked recently: {len(cached)} GUIDs (cached)")
        return cached
    headers = cache.conditional_headers(index_path) if cached is not None else {}

    try:
        r = await client.get(MOD_INDEX_URL, headers=headers)
        if r.status_code == 304 and cached is not None:
            cache.touch(index_path)
            logger.info("DLMOD", f"mod index up to date: {len(cached)} GUIDs (cached)")
            return cached
        r.raise_for_status()
        raw = r.json()
        index = _normalize_index(raw)
    except Exception as e:
        if cached is not None:
            logger.warning("DLMOD", f"Could not check for mod index updates ({e}) — using cached copy.")
            return cached
        logger.error("DLMOD", f"Failed to download mod index: {e}")
        return {}

    try:
        cache.save(index_path, raw, r.headers.get("etag"))
    except OSError as e:
        logger.warning("DLMOD", f"Could not save mod index cache: {e}")

    logger.info("DLMOD", f"mod index downloaded: {len(index)} GUIDs")
    return index
