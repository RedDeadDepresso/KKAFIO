"""
build_modpack_index.py - Build (or incrementally update) the Sideloader Modpack
GUID indexes straight from the BetterRepack download server.

How it works:
  1. Crawl the server's directory listings (Apache + Apaxy theme). Every row gives
     the file's properly encoded URL, its "last modified" date and its size.
  2. For every .zipmod that is new or changed since the last run, read its GUID and
     version from the server with HTTP Range requests (the zip's tail, then only
     manifest.xml - a few KB per mod, never the whole file).
  3. Write one index per game: {guid: [url, ...]}, best URL first.

Incremental updates: the listing's date and size strings are the change signature.
Files whose signature is unchanged keep their cached GUID and are not probed; a file
that only moved or was renamed (same filename, date and size) keeps it too. The cache
is shared by both games and is NOT shipped (default: tools/modpack_index_cache.json).

Not indexed: "Sideloader Modpack - Bleeding Edge" (not part of the BetterRepack game
release), Studio, and the EmotionCreators categories. Edit KK_CATEGORIES / KKS_CATEGORIES
below to change what a game's index contains.

Output (assets/data/kk_modpack_index.json, assets/data/kks_modpack_index.json):
  {
    "generated": "2025-01-01T12:00:00",
    "game_type": "kk",
    "count":     4300,
    "guids":     {"com.example.mod": ["https://.../Sideloader%20Modpack/a/mod.zipmod", ...], ...}
  }
The URLs are percent-encoded exactly as the server lists them; the path inside the
modpack is derived from a URL (see kkafio.indices.modpack.url_to_rel_path).

URLs of one GUID are ordered best first: main "Sideloader Modpack" folder before the
specialised categories, then newest manifest version, then newest file date. Identical
copies (same filename, date and size) in several folders are listed once, and at most
--max-urls URLs are kept per GUID.

Usage:
  python tools/build_modpack_index.py                    # both games
  python tools/build_modpack_index.py --game-type kks
  python tools/build_modpack_index.py --full             # ignore the cache, probe everything
  python tools/build_modpack_index.py --retry-failed     # probe files that failed permanently before
"""

import argparse
import http.client
import json
import os
import re
import struct
import sys
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BASE_URL = "https://sideload.betterrepack.com/download/KKEC/"
USER_AGENT = "KKAFIO-modpack-index-builder/2.0"

MAIN_CATEGORY = "Sideloader Modpack"

# Server folders (first level under the base URL) that go into each game's index, in
# ranking order: the main folder always first. Anything else on the server (Bleeding Edge,
# Studio, EC_*) is neither crawled nor indexed.
KK_CATEGORIES = [
    MAIN_CATEGORY,
    "Sideloader Modpack - Exclusive KK KKS",
    "Sideloader Modpack - Exclusive KK",
    "Sideloader Modpack - Animations",
    "Sideloader Modpack - Fixes",
    "Sideloader Modpack - KK_MaterialEditor",
    "Sideloader Modpack - KK_UncensorSelector",
    "Sideloader Modpack - Maps",
]
KKS_CATEGORIES = [
    MAIN_CATEGORY,
    "Sideloader Modpack - Exclusive KK KKS",
    "Sideloader Modpack - Exclusive KKS",
    "Sideloader Modpack - Animations",
    "Sideloader Modpack - KK_MaterialEditor",
    "Sideloader Modpack - KK_UncensorSelector",
    "Sideloader Modpack - KKS_MaterialEditor",
    "Sideloader Modpack - KKS_UncensorSelector",
    "Sideloader Modpack - KKS_Maps",
]
GAME_CATEGORIES = {"kk": KK_CATEGORIES, "kks": KKS_CATEGORIES}

DEFAULT_MAX_URLS = 4
CACHE_FORMAT = 1

# urllib raises OSError (incl. HTTP errors and timeouts); a body cut short raises HTTPException.
NETWORK_ERRORS = (OSError, http.client.HTTPException)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _canonical(url: str) -> str:
    """The percent-encoded form used for requests, the cache and the index. Idempotent."""
    return quote(url, safe=":/%?=&")


def _request(url: str, headers: dict[str, str] | None = None, timeout: int = 30):
    request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if headers:
        request_headers.update(headers)
    return urlopen(Request(_canonical(url), headers=request_headers), timeout=timeout)


def _with_retries(func, attempts: int = 3):
    """Run func(); retry network errors with a short pause."""
    for attempt in range(attempts):
        try:
            return func()
        except NETWORK_ERRORS:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5 * (attempt + 1))


def _range_get(url: str, start: int, end: int) -> bytes:
    def go() -> bytes:
        with _request(url, {"Range": f"bytes={start}-{end}"}) as response:
            if response.status == 206:
                return response.read()
            if response.status == 200 and start == 0:
                return response.read(end + 1)[: end + 1]
            raise OSError("Server ignored the Range request; stopped to avoid downloading a whole file")
    return _with_retries(go)


def _suffix_get(url: str, length: int) -> tuple[bytes, int, int]:
    """(last `length` bytes, total file size, offset the bytes start at) from one Range request."""
    def go() -> tuple[bytes, int, int]:
        with _request(url, {"Range": f"bytes=-{length}"}) as response:
            if response.status != 206:
                raise OSError("Server ignored the tail Range request; stopped to avoid downloading a whole file")
            match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "").strip())
            if not match:
                raise OSError("Server returned an invalid Content-Range")
            start, _end, total = (int(v) for v in match.groups())
            return response.read(), total, start
    return _with_retries(go)


class PermanentProbeError(ValueError):
    """The file itself can't be indexed (not a usable zipmod). Retrying won't help."""


def read_remote_manifest(url: str) -> dict[str, str]:
    """GUID and version of a remote zipmod, read via Range requests (tail, then manifest.xml).

    Raises PermanentProbeError for files that can't be read (ZIP64, encrypted, no root
    manifest.xml, no GUID, ...) and a NETWORK_ERRORS exception for network trouble (worth
    retrying later)."""
    tail, size, tail_start = _suffix_get(url, 256 * 1024)
    try:
        eocd_at = tail.rfind(b"PK\x05\x06")
        if eocd_at < 0 or eocd_at + 22 > len(tail):
            raise PermanentProbeError("ZIP end-of-central-directory not found")
        eocd = struct.unpack_from("<4s4H2LH", tail, eocd_at)
        central_size, central_offset = eocd[5], eocd[6]
        if central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
            raise PermanentProbeError("ZIP64 is not supported")

        if central_offset >= tail_start and central_offset + central_size <= size:
            begin = central_offset - tail_start
            central = tail[begin: begin + central_size]
        else:
            central = _range_get(url, central_offset, central_offset + central_size - 1)

        position = 0
        selected = None
        while position + 46 <= len(central) and central[position: position + 4] == b"PK\x01\x02":
            values = struct.unpack_from("<4s6H3I5H2I", central, position)
            flags, method = values[3], values[4]
            compressed_size = values[8]
            name_length, extra_length, comment_length = values[10], values[11], values[12]
            local_offset = values[16]
            name_bytes = central[position + 46: position + 46 + name_length]
            name = name_bytes.decode("utf-8" if flags & 0x800 else "cp437", errors="replace").replace("\\", "/")
            if name.casefold() == "manifest.xml":
                selected = (flags, method, compressed_size, local_offset)
                break
            position += 46 + name_length + extra_length + comment_length
        if selected is None:
            raise PermanentProbeError("no manifest.xml in the zip root")
        flags, method, compressed_size, local_offset = selected
        if flags & 1:
            raise PermanentProbeError("encrypted zipmod")

        prefix = b""
        if local_offset < 64 * 1024:
            prefix = _range_get(url, 0, min(size - 1, 64 * 1024 - 1))
            local_header = prefix[local_offset:]
        else:
            local_header = _range_get(url, local_offset, min(size - 1, local_offset + 4095))
        if local_header[:4] != b"PK\x03\x04":
            raise PermanentProbeError("invalid zip local file header")
        local_values = struct.unpack_from("<4s5H3I2H", local_header, 0)
        data_offset = local_offset + 30 + local_values[9] + local_values[10]
        if prefix and data_offset + compressed_size <= len(prefix):
            compressed = prefix[data_offset: data_offset + compressed_size]
        else:
            compressed = _range_get(url, data_offset, data_offset + compressed_size - 1)
        if method == 0:
            raw = compressed
        elif method == 8:
            raw = zlib.decompress(compressed, -zlib.MAX_WBITS)
        else:
            raise PermanentProbeError(f"manifest.xml uses unsupported compression method {method}")

        root = ElementTree.fromstring(raw.decode("utf-8-sig", errors="replace"))
    except PermanentProbeError:
        raise
    except (struct.error, zlib.error, ElementTree.ParseError, IndexError) as exc:
        raise PermanentProbeError(f"unreadable zip or manifest ({exc})") from exc

    def text(name: str) -> str:
        element = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1].casefold() == name), None)
        return (element.text or "").strip() if element is not None else ""

    guid = text("guid")
    if not guid:
        raise PermanentProbeError("manifest.xml has no GUID")
    return {"guid": guid, "version": text("version")}


# ---------------------------------------------------------------------------
# Directory listing crawl (Apache mod_autoindex + Apaxy)
# ---------------------------------------------------------------------------

class _ListingParser(HTMLParser):
    """Rows of an Apaxy listing: <tr><td class="indexcolname"><a href>, indexcollastmod, indexcolsize."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, str] | None = None
        self._cell: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "tr":
            self._row = {"parent": "1" if "parent" in classes else "", "href": "", "mtime": "", "size": ""}
        elif tag == "td" and self._row is not None:
            self._cell = next(
                (key for key, css in (("name", "indexcolname"), ("mtime", "indexcollastmod"),
                                      ("size", "indexcolsize")) if css in classes), None)
        elif tag == "a" and self._row is not None and self._cell == "name" and not self._row["href"]:
            self._row["href"] = attributes.get("href") or ""

    def handle_data(self, data: str) -> None:
        if self._row is not None and self._cell in ("mtime", "size"):
            self._row[self._cell] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def parse_listing(html: str, current_url: str, base_url: str) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """(subdirectory URLs, {file URL: (mtime, size)}) of one listing page.

    Only links that stay under base_url are kept, and only .zipmod files."""
    parser = _ListingParser()
    parser.feed(html)
    base_host = urlparse(base_url).hostname
    base_path = urlparse(base_url).path
    directories: list[str] = []
    files: dict[str, tuple[str, str]] = {}
    for row in parser.rows:
        href = row["href"]
        if not href or row["parent"] or href.startswith(("?", "#")):
            continue
        absolute = _canonical(urljoin(current_url, href))
        parsed = urlparse(absolute)
        if parsed.hostname != base_host or not unquote(parsed.path).startswith(unquote(base_path)):
            continue
        if parsed.path.endswith("/"):
            directories.append(absolute)
        elif parsed.path.casefold().endswith(".zipmod"):
            files[absolute] = (" ".join(row["mtime"].split()), row["size"].strip())
    return directories, files


def crawl(base_url: str, categories: set[str], workers: int) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Crawl the listed categories. Returns ({file URL: (mtime, size)}, [directory URLs that failed])."""
    def fetch(directory: str) -> tuple[list[str], dict[str, tuple[str, str]]]:
        def go() -> str:
            with _request(directory) as response:
                return response.read().decode("utf-8", errors="replace")
        return parse_listing(_with_retries(go), directory, base_url)

    root = _canonical(base_url)
    root_dirs, _ = fetch(root)
    wanted = {unquote(urlparse(d).path).rstrip("/").rsplit("/", 1)[-1]: d for d in root_dirs}
    missing = categories - set(wanted)
    if missing:
        print(f"WARN: categories not found on the server: {', '.join(sorted(missing))}")
    pending = [wanted[c] for c in sorted(categories) if c in wanted]
    seen = set(pending)
    files: dict[str, tuple[str, str]] = {}
    failed: list[str] = []
    read = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, d): d for d in pending}
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                directory = futures.pop(future)
                read += 1
                try:
                    subdirs, found = future.result()
                except NETWORK_ERRORS as exc:
                    failed.append(directory)
                    print(f"WARN: could not read {unquote(urlparse(directory).path)}: {exc}")
                    continue
                files.update(found)
                for sub in subdirs:
                    if sub not in seen:
                        seen.add(sub)
                        futures[pool.submit(fetch, sub)] = sub
                if read % 50 == 0:
                    print(f"  Crawled {read} directories, {len(files)} zipmods so far...", end="\r")
    print(f"  Crawled {read} directories, {len(files)} zipmods.        ")
    return files, failed


# ---------------------------------------------------------------------------
# Cache (shared by both games; {url: {mtime, size, guid, version, error}})
# ---------------------------------------------------------------------------

def load_cache(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != CACHE_FORMAT:
            return {}
        return {u: e for u, e in data["files"].items() if isinstance(e, dict)}
    except (OSError, ValueError, KeyError, AttributeError):
        return {}


def save_cache(path: Path, entries: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"format": CACHE_FORMAT, "files": dict(sorted(entries.items()))},
                              ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, path)


def _filename(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1]).casefold()


def category_of(url: str, base_url: str) -> str:
    rel = unquote(urlparse(url).path)[len(unquote(urlparse(base_url).path)):]
    return rel.split("/", 1)[0]


def refresh_cache(listing: dict[str, tuple[str, str]], cache: dict[str, dict], failed_dirs: list[str],
                  full: bool, retry_failed: bool, workers: int, cache_path: Path
                  ) -> tuple[dict[str, dict], int]:
    """Bring the cache in line with the listing, probing only new/changed files.

    Returns (new cache, number of files that couldn't be probed this time)."""
    old = {} if full else cache
    # Entries below a directory that couldn't be read are kept as they are rather than dropped.
    kept = {u: e for u, e in old.items() if u not in listing and any(u.startswith(d) for d in failed_dirs)}

    # Files that vanished from the listing, by (filename, mtime, size), to recognise moves/renames.
    gone: dict[tuple[str, str, str], list[dict]] = {}
    for u, e in old.items():
        if u not in listing and u not in kept and e.get("guid"):
            gone.setdefault((_filename(u), e.get("mtime", ""), e.get("size", "")), []).append(e)

    result: dict[str, dict] = dict(kept)
    to_probe: list[str] = []
    reused = moved = changed = 0
    for url, (mtime, size) in listing.items():
        e = old.get(url)
        if e and e.get("mtime") == mtime and e.get("size") == size and (e.get("guid") or (e.get("error") and not retry_failed)):
            result[url] = e
            reused += 1
            continue
        if e:
            changed += 1
        twin = gone.get((_filename(url), mtime, size))
        if not e and twin and len(twin) == 1:
            result[url] = {**twin[0], "mtime": mtime, "size": size}
            moved += 1
            continue
        to_probe.append(url)

    print(f"  Reused: {reused} | moved/renamed: {moved} | to probe: {len(to_probe)} "
          f"({changed} changed, {len(to_probe) - changed} new)")
    if old and len(to_probe) > max(50, len(listing) // 2):
        print("  WARN: most files look changed - the server may have reset its dates. Probing them all.")

    unreachable = permanent = done = 0

    def probe(url: str) -> tuple[str, dict | None, str | None, bool]:
        try:
            info = read_remote_manifest(url)
            return url, info, None, False
        except PermanentProbeError as exc:
            return url, None, str(exc), True
        except NETWORK_ERRORS as exc:
            return url, None, str(exc), False

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(probe, u) for u in to_probe]):
                url, info, error, is_permanent = future.result()
                mtime, size = listing[url]
                done += 1
                if info:
                    result[url] = {"mtime": mtime, "size": size, "guid": info["guid"], "version": info["version"]}
                elif is_permanent:
                    permanent += 1
                    result[url] = {"mtime": mtime, "size": size, "error": error}
                    print(f"\n  SKIP {unquote(urlparse(url).path)}: {error}")
                else:
                    unreachable += 1
                    print(f"\n  FAIL {unquote(urlparse(url).path)}: {error}")
                if done % 100 == 0 or done == len(to_probe):
                    print(f"  Probed {done}/{len(to_probe)}...", end="\r")
                if done % 500 == 0:
                    save_cache(cache_path, result)
    finally:
        save_cache(cache_path, result)
    if to_probe:
        print()
    if permanent:
        print(f"  {permanent} zipmod(s) can't be indexed (recorded; use --retry-failed to try again)")
    return result, unreachable


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def build_game_index(entries: dict[str, dict], categories: list[str], base_url: str,
                     max_urls: int) -> dict[str, list[str]]:
    """{guid: [url, ...]} for one game, best URL first."""
    rank = {c: i for i, c in enumerate(categories)}
    by_guid: dict[str, list[tuple[str, dict]]] = {}
    for url, e in entries.items():
        if e.get("guid") and category_of(url, base_url) in rank:
            by_guid.setdefault(e["guid"], []).append((url, e))

    index: dict[str, list[str]] = {}
    for guid, items in by_guid.items():
        # Chained stable sorts, least significant key first.
        items.sort(key=lambda it: it[0])
        items.sort(key=lambda it: it[1].get("mtime", ""), reverse=True)
        items.sort(key=lambda it: _version_key(it[1].get("version", "")), reverse=True)
        items.sort(key=lambda it: rank[category_of(it[0], base_url)])
        urls: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for url, e in items:
            identity = (_filename(url), e.get("mtime", ""), e.get("size", ""))
            if identity in seen:
                continue                                    # an identical copy in another folder
            seen.add(identity)
            urls.append(url)
        index[guid] = urls[:max_urls]
    return dict(sorted(index.items()))


def write_index(path: Path, game: str, index: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "game_type": game,
        "count": len(index),
        "guids": index,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-type", "-g", default="all", choices=["kk", "kks", "all"],
                    help="Which index to build (default: both)")
    ap.add_argument("--output", "-o", default=None, metavar="PATH",
                    help="Output JSON path; only with a single --game-type "
                         "(default: assets/data/<game>_modpack_index.json)")
    ap.add_argument("--cache", default=str(REPO_ROOT / "tools" / "modpack_index_cache.json"), metavar="PATH",
                    help="Incremental-update cache (default: tools/modpack_index_cache.json)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help=argparse.SUPPRESS)
    ap.add_argument("--full", action="store_true", help="Ignore the cache and probe every file")
    ap.add_argument("--retry-failed", action="store_true",
                    help="Probe again the files that were recorded as not indexable")
    ap.add_argument("--max-urls", type=int, default=DEFAULT_MAX_URLS, metavar="N",
                    help=f"Most URLs kept per GUID (default: {DEFAULT_MAX_URLS})")
    ap.add_argument("--workers", type=int, default=8, metavar="N", help="Parallel requests (default: 8, max 16)")
    args = ap.parse_args(argv)

    games = ["kk", "kks"] if args.game_type == "all" else [args.game_type]
    if args.output and len(games) != 1:
        ap.error("--output needs a single --game-type")
    workers = max(1, min(args.workers, 16))
    base_url = _canonical(args.base_url if args.base_url.endswith("/") else args.base_url + "/")
    cache_path = Path(args.cache)
    categories = {c for g in games for c in GAME_CATEGORIES[g]}

    print(f"Server : {base_url}")
    print(f"Games  : {', '.join(games)}")
    print("Crawling the server listings...")
    listing, failed_dirs = crawl(base_url, categories, workers)

    cache = {} if args.full else load_cache(cache_path)
    print("Full rescan requested (--full)" if args.full else f"Cache: {len(cache)} known file(s)")
    entries, unreachable = refresh_cache(listing, cache, failed_dirs, args.full, args.retry_failed,
                                         workers, cache_path)

    for game in games:
        index = build_game_index(entries, GAME_CATEGORIES[game], base_url, args.max_urls)
        out = Path(args.output) if args.output else REPO_ROOT / "assets" / "data" / f"{game}_modpack_index.json"
        write_index(out, game, index)
        print(f"{game}: {len(index)} GUIDs, {sum(len(v) for v in index.values())} URLs -> {out}")

    if failed_dirs or unreachable:
        print(f"\nWARNING: {len(failed_dirs)} listing(s) unreadable and {unreachable} file(s) unreachable. "
              "The index is missing those mods; run the script again before shipping it.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
