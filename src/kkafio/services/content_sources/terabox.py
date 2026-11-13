"""terabox.com — shared files and folders (and its many mirror domains).

  https://www.terabox.com/s/1<surl>                      a share: every file in it is downloaded
  https://www.terabox.com/sharing/link?surl=<surl>       the same, in the "sharing/link" shape
  https://www.terabox.com/wap/share/filelist?surl=<surl> the same, in the mobile shape
  https://1024terabox.com/s/1<surl>                      any of the TeraBox mirrors (terabox.app, 1024tera.com,
                                                         teraboxapp.com, nephobox.com, 4funbox.com, ...)
  ...?surl=<surl>&path=/Folder/Sub                       only that folder of the share (also ``dir=``)
  ...?surl=<surl>&path=/Folder/Sub/file.zip              only that one file (TeraBox has no link of its own
                                                         for a file: it is the share plus the file's path)

The resolver follows the one of Teralace (https://github.com/LoLyeah/teralace), without its
user-configured "API pool" / Cloudflare Worker resolvers:

  listing   the mobile share page (``/wap/share/filelist``) carries the file list, the share's
            ``shareid`` / ``uk`` / ``sign`` / ``timestamp`` and, per file, a ready ``dlink``; it only
            renders the first 20 items, so the rest (and the content of subfolders) is read from the
            ``/share/list`` API, page by page. If the page cannot be read, ``/share/list`` alone is used.
  download  the share's metadata is posted to the ``sharedownload`` API (on one TeraBox host after the
            other) to get a fresh download link; a link TeraBox refuses with 403 is dropped. Then, in
            this order: the ``indexsubtitle`` resolver (it works for many files without a login), and
            the ``dlink`` that came with the listing.

Folders (and any listing the share page truncates) are read the way Terabox-DL
(https://github.com/Dragonblocks-dev/Terabox-DL) does it, which does not suffer from the lazy loading of
the share page: ``/api/shorturlinfo`` gives the share's ``shareid`` / ``uk`` / ``sign`` / ``timestamp`` /
``randsk``, then every directory is read page by page from the *signed* ``/share/list`` call (those
parameters, ``dir=<path>``, ``order=name`` and, when TeraBox asks for it, a ``jsToken`` scraped from the
share page). The ``randsk`` also goes into the ``sharedownload`` request as its ``sekey``. The older
anonymous calls stay as fallbacks.

Most original files need a TeraBox login: the ``ndus`` cookie is asked for (and stored) like the pixiv
PHPSESSID -- see ``terabox_session``. Without one, what the anonymous resolvers cannot reach fails.
A folder's subfolders become subfolders of the output directory, like Gofile, MediaFire and Mega.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from kkafio.core import progress
from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.net import MAX_CONNECTIONS, safe_filename
from kkafio.services.content_sources.transfer import download_file

APP_ID = "250528"
# TeraBox's own desktop client: its download CDN is happiest with it, and it sends no Referer.
CLIENT_USER_AGENT = "netdisk;P2SP;2.2.60.2"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)
WAP_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
INDEXSUBTITLE_BASE = "https://tbx.indexsubtitle.cc"
INDEXSUBTITLE_KEY = "116486c5e3a2ea45fa2c4cfc3fdc66caf3ae134598d3aedb"

LIST_HOSTS = ("www.terabox.com", "www.terabox.app", "1024tera.com")
META_HOSTS = ("www.terabox.app", "www.terabox.com", "1024tera.com")
DOWNLOAD_HOSTS = ("dm.terabox.com", "www.terabox.app", "www.terabox.com", "www.1024tera.com", "www.nephobox.com")

PAGE_SIZE = 100
MAX_PAGES = 500                 # a safety net against a listing that never ends
WAP_FIRST_PAGE = 20             # the share page renders at most this many items
MAX_DEPTH = 50                  # nested folders followed (visited folders are never read twice)
PROBE_TIMEOUT = 5.0
JS_TOKEN_ERRNOS = (400210, 4000020, 4000023)     # TeraBox: jsToken missing / not accepted / expired

# -- URLs ----------------------------------------------------------------------------------------

# The host names of TeraBox and its mirrors (matched against the host, never the whole URL).
_HOST_PATTERNS = [re.compile(p, re.I) for p in (
    r"(^|\.)terabox\.(com|app|fun|best|ap|club|click|me|site|pro|xyz)$",
    r"(^|\.)terabox(app|link|linke|share|sharefile|url|free|mod)\.(com|app)$",
    r"(^|\.)terashare(link|file|us|drive)\.com$",
    r"(^|\.)terafileshare\.com$",
    r"(^|\.)(tera1024box|1024tera|1024terabox|1024-terabox|1024box)\.(com|co)$",
    r"(^|\.)4funbox\.(com|co|in)$",
    r"(^|\.)(mirrobox|nephobox|freeterabox|momerybox|tibibox|tibabox|gibibox|pebibox|fancybox|dubox|bestclouddrive)\.(com|in)$",
    r"(^|\.)(playduo\.link|theteraboxmod\.app|teraboxdownloader\.com|teradownloader\.com)$",
)]

SURL_LENGTH = 22
_SURL = r"[A-Za-z0-9_-]{6,64}"
_PATH_SURL_RE = re.compile(rf"^/s/({_SURL})/?$")
_QUERY_SURL_RE = re.compile(rf"^({_SURL})$")


def is_terabox_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(p.search(host) for p in _HOST_PATTERNS)


def is_terabox_url(url: str) -> bool:
    return is_terabox_host(urlparse(url).hostname or "")


@dataclass(frozen=True)
class TeraboxLink:
    surl: str                   # the share id without its leading "1"
    path: str | None = None     # a folder of the share to take, from ?path= / ?dir=

    @property
    def share_url(self) -> str:
        return f"https://www.terabox.app/sharing/link?surl={self.surl}"


def parse_terabox_url(url: str) -> TeraboxLink | None:
    """The share a TeraBox link points to; None for anything else (home page, login, ...)."""
    if not is_terabox_url(url):
        return None
    parts = urlparse(url)
    query = parse_qs(parts.query)
    path = unquote(parts.path)
    surl = ""
    m = _PATH_SURL_RE.match(path)
    if m:
        # /s/1<surl>: the web puts a "1" in front of the id; the APIs take it without.
        surl = m.group(1)
        if surl.startswith("1") and len(surl) > 1:
            surl = surl[1:]
    elif path.rstrip("/").endswith(("/sharing/link", "/wap/share/filelist", "/share/link", "/share/filelist")):
        value = (query.get("surl") or [""])[0].strip()
        if _QUERY_SURL_RE.match(value):
            surl = value
            # Normally already without the "1", but some links keep it. An id is 22 characters, so a
            # 23-character one starting with "1" has the prefix (a real id may itself start with "1").
            if len(surl) == SURL_LENGTH + 1 and surl.startswith("1"):
                surl = surl[1:]
    if not surl:
        return None
    folder = None
    for key in ("path", "dir"):
        value = (query.get(key) or [""])[0].strip()
        if value and value != "/":
            folder = value if value.startswith("/") else "/" + value
            break
    return TeraboxLink(surl, folder)


# -- the ndus cookie -----------------------------------------------------------------------------

def cookie_header(session: str | None) -> str | None:
    """``ndus=<value>`` for a stored value (a pasted ``name=value`` pair / Cookie header is kept as is)."""
    text = (session or "").strip().strip("\"'").strip()
    if not text:
        return None
    return text if "=" in text else f"ndus={text}"


# -- data ----------------------------------------------------------------------------------------

@dataclass
class ShareMeta:
    shareid: int = 0
    uk: int = 0
    sign: str = ""
    timestamp: int = 0
    randsk: str = ""            # the share's session key; goes into sharedownload as its "sekey"

    @property
    def complete(self) -> bool:
        return bool(self.shareid and self.uk and self.sign and self.timestamp)

    def override(self, other: "ShareMeta") -> None:
        """Take every value `other` has, replacing what is here (shorturlinfo is the authority)."""
        for name in ("shareid", "uk", "sign", "timestamp", "randsk"):
            if getattr(other, name):
                setattr(self, name, getattr(other, name))

    def merge(self, other: "ShareMeta") -> None:
        """Fill what is missing here from `other`."""
        for name in ("shareid", "uk", "sign", "timestamp", "randsk"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))


@dataclass
class ShareFile:
    fs_id: str
    name: str
    is_dir: bool = False
    size: int | None = None
    path: str | None = None
    dlink: str | None = None


@dataclass
class Listing:
    files: list[ShareFile] = field(default_factory=list)
    meta: ShareMeta = field(default_factory=ShareMeta)
    total: int = 0


class TeraboxError(Exception):
    pass


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true")
    return False


def _file_from_item(item: dict) -> ShareFile:
    name = str(item.get("server_filename") or item.get("filename") or "file")
    return ShareFile(
        fs_id=str(item.get("fs_id") if item.get("fs_id") is not None else ""),
        name=name,
        is_dir=_as_bool(item.get("isdir", item.get("is_dir"))),
        size=_as_int(item.get("size")),
        path=item.get("path") if isinstance(item.get("path"), str) else None,
        dlink=(item.get("dlink") or None) if isinstance(item.get("dlink"), str) else None,
    )


def _meta_from(data: dict) -> ShareMeta:
    return ShareMeta(
        shareid=_as_int(data.get("shareid", data.get("share_id"))) or 0,
        uk=_as_int(data.get("uk")) or 0,
        sign=str(data.get("sign") or ""),
        timestamp=_as_int(data.get("timestamp")) or 0,
        randsk=str(data.get("randsk") or ""),
    )


_JS_TOKEN_PATTERNS = (
    re.compile(r"function%20fn%28a%29%7Bwindow\.jsToken%20%3D%20a%7D%3Bfn%28%22(.+?)%22%29"),
    re.compile(r"""window\.jsToken\s*=\s*["']([A-Za-z0-9_.-]+)["']"""),
    re.compile(r"""["']jsToken["']\s*:\s*["']([A-Za-z0-9_.-]+)["']"""),
    re.compile(r"""fn\(\s*["']([A-Za-z0-9_.-]{16,})["']\s*\)"""),
)


def extract_js_token(html: str) -> str | None:
    """The ``jsToken`` a TeraBox page hands to its scripts (the formats Terabox-DL knows)."""
    for pattern in _JS_TOKEN_PATTERNS:
        m = pattern.search(html or "")
        if m and m.group(1):
            return m.group(1)
    return None


def extract_initial_state(html: str) -> dict:
    """The ``window.__INITIAL_STATE__={...}`` object of a TeraBox share page."""
    marker = "window.__INITIAL_STATE__="
    start = html.find(marker)
    if start < 0:
        raise TeraboxError("no __INITIAL_STATE__ in the share page")
    body = html[start + len(marker):]
    depth, in_string, escape, end = 0, False, False, None
    for i, ch in enumerate(body):
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
    if end is None:
        raise TeraboxError("unterminated __INITIAL_STATE__ in the share page")
    try:
        state = json.loads(body[:end])
    except ValueError as exc:
        raise TeraboxError(f"unreadable __INITIAL_STATE__: {exc}") from exc
    if not isinstance(state, dict):
        raise TeraboxError("unexpected __INITIAL_STATE__")
    return state


def _state_value(state: dict, keys: tuple[str, ...]):
    """The first of `keys` found in the share state, in share.shareInfo, share, shareInfo, then the top level."""
    share = state.get("share") if isinstance(state.get("share"), dict) else {}
    info = share.get("shareInfo") if isinstance(share.get("shareInfo"), dict) else {}
    top_info = state.get("shareInfo") if isinstance(state.get("shareInfo"), dict) else {}
    for key in keys:
        for scope in (info, share, top_info, state):
            if scope.get(key) not in (None, ""):
                return scope[key]
    return None


def parse_wap_state(state: dict) -> Listing:
    share = state.get("share") if isinstance(state.get("share"), dict) else {}
    raw = share.get("fileList") or share.get("list")
    if not isinstance(raw, list):
        raise TeraboxError("no fileList in the share page")
    if not raw:
        raise TeraboxError("the share page's fileList is empty")
    files = [_file_from_item(i) for i in raw if isinstance(i, dict)]
    meta = ShareMeta(
        shareid=_as_int(_state_value(state, ("shareid", "share_id", "primaryid"))) or 0,
        uk=_as_int(_state_value(state, ("uk", "share_uk"))) or 0,
        sign=str(_state_value(state, ("sign", "share_sign")) or ""),
        timestamp=_as_int(_state_value(state, ("timestamp", "ctime"))) or 0,
    )
    total = _as_int(_state_value(state, ("filesTotal", "total", "count"))) or len(files)
    return Listing(files, meta, total)


# -- the resolver --------------------------------------------------------------------------------

class TeraboxResolver:
    """Reads a share's listing and turns files into download links. One per client / cookie."""

    def __init__(self, client, session: str | None = None):
        self.client = client
        self.cookie = cookie_header(session)
        self._js_token: str | None = None
        self._info_tried = False        # shorturlinfo is asked once per resolver
        self._token_tried = False       # so is the first jsToken scrape

    # headers ----------------------------------------------------------------
    def _headers(self, user_agent: str, referer: str | None = None, extra: dict | None = None) -> dict:
        headers = {"User-Agent": user_agent}
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = referer.rstrip("/")
        if self.cookie:
            headers["Cookie"] = self.cookie
        if extra:
            headers.update(extra)
        return headers

    def download_headers(self, url: str) -> dict:
        """The headers of the file transfer itself: the cookie goes to TeraBox hosts only."""
        headers = {"User-Agent": CLIENT_USER_AGENT}
        if self.cookie and is_terabox_host(urlparse(url).hostname or ""):
            headers["Cookie"] = self.cookie
        return headers

    # listing ----------------------------------------------------------------
    async def _wap(self, surl: str, path: str | None) -> Listing:
        url = f"https://www.terabox.com/wap/share/filelist?surl={surl}"
        if path:
            url += f"&path={quote(path, safe='')}"
        resp = await self.client.get(url, headers=self._headers(WAP_USER_AGENT, extra={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}))
        if resp.status_code != 200:
            raise TeraboxError(f"share page HTTP {resp.status_code}")
        return parse_wap_state(extract_initial_state(resp.text))

    async def _share_list(self, surl: str, page: int, num: int, directory: str | None,
                          hosts: tuple[str, ...] = LIST_HOSTS) -> Listing:
        where = f"&dir={quote(directory, safe='')}" if directory else "&root=1"
        last = "no host answered"
        for host in hosts:
            url = (f"https://{host}/share/list?app_id={APP_ID}&shorturl={surl}{where}"
                   f"&page={page}&num={num}")
            try:
                resp = await self.client.get(url, headers=self._headers(
                    CLIENT_USER_AGENT, referer=f"https://{host}/"))
            except Exception as exc:                      # network trouble: try the next host
                last = str(exc) or type(exc).__name__
                continue
            if resp.status_code != 200:
                last = f"HTTP {resp.status_code}"
                continue
            try:
                data = resp.json()
            except ValueError:
                last = "not JSON"
                continue
            if not isinstance(data, dict) or data.get("errno") != 0 or not isinstance(data.get("list"), list):
                last = f"errno {data.get('errno') if isinstance(data, dict) else '?'}"
                continue
            return Listing([_file_from_item(i) for i in data["list"] if isinstance(i, dict)],
                           _meta_from(data), _as_int(data.get("total")) or 0)
        raise TeraboxError(f"share/list failed: {last}")

    # -- the signed listing (Terabox-DL's method) -----------------------------
    def _seed_cookies(self) -> None:
        """Put ``ndus`` (and ``lang=en``) in the client's cookie jar for the TeraBox domains, like
        Terabox-DL's requests.Session: the share-page visit that yields the jsToken also sets cookies
        (browserid, ...) and the API calls after it must send those too, which a hand-written
        ``Cookie`` header would replace with ``ndus`` alone."""
        ndus = ""
        for pair in (self.cookie or "").split(";"):
            name, _, value = pair.strip().partition("=")
            if name.strip().lower() == "ndus":
                ndus = value.strip()
        jar = getattr(self.client, "cookies", None)
        if jar is None:
            return
        for domain in (".terabox.com", ".terabox.app", ".1024tera.com"):
            try:
                if ndus:
                    jar.set("ndus", ndus, domain=domain)
                jar.set("lang", "en", domain=domain)
            except Exception:
                pass

    def _jar_headers(self, *args, **kwargs) -> dict:
        headers = self._headers(*args, **kwargs)
        headers.pop("Cookie", None)             # the cookie jar sends them (see _seed_cookies)
        return headers

    async def _refresh_js_token(self, surl: str) -> bool:
        """Scrape a fresh ``jsToken`` from the share page (then the home page). True if one was found."""
        self._seed_cookies()
        for url in (f"https://www.terabox.com/s/1{surl}", "https://www.terabox.com/"):
            try:
                resp = await self.client.get(url, headers=self._jar_headers(
                    BROWSER_USER_AGENT, extra={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}))
            except Exception:
                continue
            token = extract_js_token(resp.text) if resp.status_code == 200 else None
            if token:
                self._js_token = token
                return True
        return False

    async def _signed_get(self, surl: str, path: str, params: dict) -> dict:
        """GET a TeraBox web API (browser headers, the ndus cookie, the jsToken when there is one);
        a jsToken TeraBox calls missing / expired is refreshed once. Returns the JSON when errno is 0."""
        last = "no host answered"
        self._seed_cookies()
        if not self._js_token and not self._token_tried:
            self._token_tried = True              # like Terabox-DL: have a token before the first call
            await self._refresh_js_token(surl)
        for host in LIST_HOSTS:
            for attempt in (0, 1):
                query = dict(params)
                if self._js_token:
                    query["jsToken"] = self._js_token
                try:
                    resp = await self.client.get(f"https://{host}{path}", params=query, headers=self._jar_headers(
                        BROWSER_USER_AGENT, referer=f"https://{host}/", extra={
                            "Accept": "application/json, text/plain, */*", "X-Requested-With": "XMLHttpRequest"}))
                    if resp.status_code != 200:
                        last = f"HTTP {resp.status_code}"
                        break
                    data = resp.json()
                except Exception as exc:                  # network trouble / not JSON: next host
                    last = str(exc) or type(exc).__name__
                    break
                if not isinstance(data, dict):
                    last = "unexpected answer"
                    break
                errno = data.get("errno")
                if errno in JS_TOKEN_ERRNOS and attempt == 0 and await self._refresh_js_token(surl):
                    continue
                if errno != 0:
                    note = str(data.get("errmsg") or data.get("show_msg") or data.get("msg") or "").strip()
                    last = f"errno {errno}" + (f" ({note[:120]})" if note else "")
                    break
                return data
        raise TeraboxError(f"{path} failed: {last}")

    async def share_info(self, surl: str) -> Listing:
        """``/api/shorturlinfo``: the share's metadata (with the ``randsk``) and its first level."""
        data = await self._signed_get(surl, "/api/shorturlinfo", {
            "shorturl": f"1{surl}", "root": "1", "app_id": APP_ID, "web": "1",
            "channel": "dubox", "clienttype": "0"})
        meta = _meta_from(data)
        if not meta.shareid or not meta.sign:
            raise TeraboxError("shorturlinfo carried no share metadata")
        raw = data.get("list")
        files = [_file_from_item(i) for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
        return Listing(files, meta, len(files))

    async def list_dir_signed(self, surl: str, meta: ShareMeta, directory: str | None) -> list[ShareFile]:
        """Every item of one directory (``None`` / ``/`` = the share's root) from the signed
        ``/share/list``, page by page until a short page. An empty folder is an empty list."""
        root = not directory or directory == "/"
        files: list[ShareFile] = []
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            data = await self._signed_get(surl, "/share/list", {
                "shareid": meta.shareid, "uk": meta.uk, "sign": meta.sign, "timestamp": meta.timestamp,
                "dir": "/" if root else directory, "root": "1" if root else "0",
                "app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0",
                "page": page, "num": PAGE_SIZE, "order": "name"})
            items = data.get("list")
            if not isinstance(items, list) or not items:
                break
            new = [f for f in (_file_from_item(i) for i in items if isinstance(i, dict)) if f.fs_id not in seen]
            if not new:
                break
            seen.update(f.fs_id for f in new)
            files.extend(new)
            if len(items) < PAGE_SIZE:
                break
        return files

    async def share_meta(self, surl: str) -> ShareMeta:
        """The share's shareid / uk / sign / timestamp, from a one-item /share/list."""
        listing = await self._share_list(surl, 1, 1, None, META_HOSTS)
        if not listing.meta.shareid or not listing.meta.sign:
            raise TeraboxError("could not read the share's metadata")
        return listing.meta

    async def list_share(self, link: TeraboxLink) -> Listing:
        """The first level of the share (or of `link.path`), with its metadata and dlinks."""
        try:
            listing = await self._wap(link.surl, link.path)
        except Exception as exc:
            logger.info("DLOAD", f"  TeraBox share page not usable ({exc}); using the share list API.")
            meta = ShareMeta()
            await self._complete_meta(link.surl, meta)
            if meta.complete:
                try:
                    files = await self.list_dir_signed(link.surl, meta, link.path)
                    if files:
                        return Listing(files, meta, len(files))
                except Exception as signed_exc:
                    logger.info("DLOAD", f"  TeraBox signed listing failed ({signed_exc}).")
            listing = await self._list_dir_api(link.surl, link.path)
            listing.meta.merge(meta)
            return listing
        await self._complete_meta(link.surl, listing.meta)
        # The page only renders its first items: the rest comes from the API.
        if listing.total > len(listing.files) or len(listing.files) >= WAP_FIRST_PAGE:
            seen = {f.fs_id for f in listing.files}
            if listing.meta.complete:                     # the signed listing reads the whole directory
                try:
                    full = await self.list_dir_signed(link.surl, listing.meta, link.path)
                except Exception as exc:
                    logger.info("DLOAD", f"  TeraBox signed listing failed ({exc}); trying the plain list API.")
                else:
                    listing.files.extend(f for f in full if f.fs_id not in seen)
                    return listing
            try:
                for page in range(1, MAX_PAGES + 1):
                    if listing.total and len(listing.files) >= listing.total:
                        break
                    more = await self._share_list(link.surl, page, PAGE_SIZE, link.path)
                    new = [f for f in more.files if f.fs_id not in seen]
                    if not new:
                        break
                    seen.update(f.fs_id for f in new)
                    listing.files.extend(new)
            except Exception as exc:
                logger.warning("DLOAD", f"  TeraBox: only the first {len(listing.files)} item(s) could be listed ({exc}).")
        return listing

    async def _complete_meta(self, surl: str, meta: ShareMeta) -> None:
        """Fill the share's metadata (and its randsk) from shorturlinfo, then from a one-item list."""
        if meta.complete and (meta.randsk or self._info_tried):
            return
        if not self._info_tried:
            self._info_tried = True
            try:
                # Terabox-DL signs its listing with shorturlinfo's values; the share page's sign may
                # not be accepted by /share/list for folders (errno 4000020), so these win.
                meta.override((await self.share_info(surl)).meta)
                have = [n for n in ("shareid", "uk", "sign", "timestamp", "randsk") if getattr(meta, n)]
                logger.info("DLOAD", f"  TeraBox: shorturlinfo credentials: {', '.join(have)}; "
                                     f"jsToken {'yes' if self._js_token else 'no'}.")
            except Exception as exc:
                logger.info("DLOAD", f"  TeraBox shorturlinfo unavailable: {exc}")
        if not meta.complete:
            try:
                meta.merge(await self.share_meta(surl))
            except Exception as exc:
                logger.info("DLOAD", f"  TeraBox share metadata unavailable: {exc}")

    async def _list_dir_api(self, surl: str, directory: str | None) -> Listing:
        """Every page of one directory from /share/list."""
        result = Listing()
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            part = await self._share_list(surl, page, PAGE_SIZE, directory)
            result.meta.merge(part.meta)
            new = [f for f in part.files if f.fs_id not in seen]
            if not new:
                break
            seen.update(f.fs_id for f in new)
            result.files.extend(new)
            if len(part.files) < PAGE_SIZE:
                break
        if not result.files and page == 1:
            raise TeraboxError("the share is empty or not available")
        result.total = len(result.files)
        return result

    async def list_folder(self, surl: str, directory: str, meta: ShareMeta | None = None) -> list[ShareFile]:
        """One folder, completely: the signed listing first (it is not lazy), then the plain list API,
        then the share page."""
        if meta is not None and meta.complete:
            try:
                return await self.list_dir_signed(surl, meta, directory)
            except Exception as signed_exc:
                logger.info("DLOAD", f"  TeraBox signed listing of {directory} failed ({signed_exc}); "
                                     "trying the plain list API.")
        try:
            return (await self._list_dir_api(surl, directory)).files
        except Exception as api_exc:
            try:                                          # the share page, with ?path=
                return (await self._wap(surl, directory)).files
            except Exception:
                raise api_exc

    async def _resolve_path(self, link: TeraboxLink) -> tuple[ShareMeta, ShareFile | None]:
        """Walk down from the share's root, one folder at a time, to the item `link.path` names.

        Every step lists the folder found in the previous one, and uses the item's own ``path`` as
        TeraBox reports it, so the link's spelling (case, encoding) does not have to be exact and no
        folder is read before its parent has shown it exists. (None: no such item.)"""
        segments = [part for part in (link.path or "").split("/") if part]
        root = await self.list_share(TeraboxLink(link.surl, None))
        meta, items, current = root.meta, root.files, ""
        for index, segment in enumerate(segments):
            want = f"{current}/{segment}"
            item = (next((f for f in items if (f.path or "").rstrip("/") == want), None)
                    or next((f for f in items if f.name == segment), None)
                    or next((f for f in items if f.name.lower() == segment.lower()), None))
            if item is None:
                if index == 0 and any((f.path or "").startswith(want + "/") for f in items):
                    current = want              # the root listing already shows the inside of this folder
                    continue
                return meta, None
            if index == len(segments) - 1:
                return meta, item
            if not item.is_dir:
                return meta, None
            current = (item.path or f"{current}/{item.name}").rstrip("/")
            logger.info("DLOAD", f"  TeraBox: looking in {current}")
            items = await self.list_folder(link.surl, current, meta)
        return meta, None

    async def _single_file(self, link: TeraboxLink) -> tuple[ShareMeta, list[tuple[ShareFile, tuple[str, ...]]]] | None:
        """If ``link.path`` names a file (not a folder), just that file; else None.

        TeraBox gives a file no link of its own, so a link to one file is the share plus the file's
        path: ``...?surl=<id>&path=%2FFolder%2Ffile.zip``. The file is looked up in its parent folder."""
        if not link.path:
            return None
        parent, _, name = link.path.rstrip("/").rpartition("/")
        wanted = link.path.rstrip("/")
        try:
            listing = await self.list_share(TeraboxLink(link.surl, parent or None))
        except Exception:
            listing = None                 # the parent cannot be read straight away: walk down to it
        if listing is not None:
            for item in listing.files:
                if item.path == wanted or (item.path is None and item.name.lower() == name.lower()):
                    if item.is_dir:
                        return None
                    return listing.meta, [(item, ())]
        try:
            meta, found = await self._resolve_path(link)
        except Exception as exc:
            logger.info("DLOAD", f"  TeraBox: could not walk to {link.path}: {exc}")
            return None
        if found is None or found.is_dir:
            return None
        return meta, [(found, ())]

    async def collect(self, link: TeraboxLink) -> tuple[ShareMeta, list[tuple[ShareFile, tuple[str, ...]]]]:
        """Every file of the share with the subfolders (names) it sits in; folders are followed
        breadth-first, MAX_DEPTH levels deep. A folder that cannot be read is logged and left out."""
        single = await self._single_file(link)
        if single is not None:
            return single
        try:
            listing = await self.list_share(link)
        except Exception as exc:
            if not link.path:
                raise
            meta, found = await self._resolve_path(link)          # walk down to the folder
            if found is None or not found.is_dir:
                raise exc
            directory = (found.path or link.path).rstrip("/")
            listing = Listing(await self.list_folder(link.surl, directory, meta), meta)
        out: list[tuple[ShareFile, tuple[str, ...]]] = []
        queue: deque[tuple[str, tuple[str, ...], int]] = deque()
        visited: set[str] = set()

        def take(items: list[ShareFile], base: tuple[str, ...], base_path: str | None, depth: int) -> None:
            for item in items:
                if item.is_dir:
                    sub = item.path or f"{(base_path or '').rstrip('/')}/{item.name}"
                    rel = base + (safe_filename(item.name, "folder"),)
                    if depth < MAX_DEPTH:
                        queue.append((sub, rel, depth + 1))
                    else:
                        logger.warning("DLOAD", f"  TeraBox: folder too deep, skipped: {sub}")
                else:
                    out.append((item, base))

        # A ?path= link starts inside that folder: its content is the root of the download.
        take(listing.files, (), link.path, 0)
        while queue:
            directory, rel, depth = queue.popleft()
            if directory in visited:
                continue
            visited.add(directory)
            try:
                items = await self.list_folder(link.surl, directory, listing.meta)
            except Exception as exc:
                logger.error("DLOAD", f"  TeraBox folder {directory} could not be read: {exc}")
                out.append((ShareFile("", directory, is_dir=True), rel))   # surfaced as a failure by the caller
                continue
            take(items, rel, directory, depth)
        return listing.meta, out

    # download links ---------------------------------------------------------
    async def _sharedownload_sekey(self, meta: ShareMeta, fs_id: str) -> str | None:
        """Terabox-DL's request: the share's randsk as the ``sekey``, on the chunlei channel."""
        if not meta.randsk:
            return None
        sekey = unquote(meta.randsk) if "%" in meta.randsk else meta.randsk
        extra = quote(json.dumps({"sekey": sekey}, separators=(",", ":")))
        body = (f"encrypt=0&extra={extra}&fid_list=[{fs_id}]&primaryid={meta.shareid}"
                f"&uk={meta.uk}&product=share&type=nolimit")
        for host in DOWNLOAD_HOSTS:
            url = (f"https://{host}/api/sharedownload?app_id={APP_ID}&channel=chunlei&clienttype=12"
                   f"&sign={meta.sign}&timestamp={meta.timestamp}&web=1")
            headers = self._headers(BROWSER_USER_AGENT, referer=f"https://{host}/", extra={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json, text/plain, */*", "X-Requested-With": "XMLHttpRequest"})
            try:
                resp = await self.client.post(url, headers=headers, content=body)
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except Exception:
                continue
            if isinstance(data, dict) and data.get("errno") == 0:
                items = data.get("list")
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    dlink = items[0].get("dlink")
                    if isinstance(dlink, str) and dlink:
                        return dlink
        return None

    async def _sharedownload(self, meta: ShareMeta, fs_id: str) -> str | None:
        dlink = await self._sharedownload_sekey(meta, fs_id)
        if dlink and await self._usable(dlink):
            return dlink
        signed = (f"encrypt=0&extra=%7B%22sekey%22%3A%22%22%7D&fid_list=%5B{fs_id}%5D&fs_ids=%5B{fs_id}%5D"
                  f"&primaryid={meta.shareid}&shareid={meta.shareid}&uk={meta.uk}&sign={meta.sign}"
                  f"&timestamp={meta.timestamp}&product=share&type=nolimit")
        bodies = (
            signed,
            f"encrypt=0&extra=%7B%22sekey%22%3A%22%22%7D&fid_list=%5B{fs_id}%5D&fs_ids=%5B{fs_id}%5D"
            f"&primaryid={meta.shareid}&shareid={meta.shareid}&uk={meta.uk}&product=share&type=nolimit",
            f"encrypt=0&extra=%7B%22sekey%22%3A%22%22%7D&fid_list=%5B{fs_id}%5D&primaryid={meta.shareid}"
            f"&uk={meta.uk}&product=share&type=nolimit",
            f"shareid={meta.shareid}&uk={meta.uk}&sign={meta.sign}&timestamp={meta.timestamp}"
            f"&primaryid={meta.shareid}&fs_ids=%5B{fs_id}%5D",
        )
        for host in DOWNLOAD_HOSTS:
            urls = (
                f"https://{host}/api/sharedownload?app_id={APP_ID}&web=1&channel=dubox&clienttype=0"
                f"&sign={meta.sign}&timestamp={meta.timestamp}",
                f"https://{host}/share/download?app_id={APP_ID}&web=1&channel=dubox&clienttype=0&jsToken=",
            )
            headers = self._headers(CLIENT_USER_AGENT, referer=f"https://{host}/",
                                    extra={"Content-Type": "application/x-www-form-urlencoded"})
            for url in urls:
                for body in bodies:
                    try:
                        resp = await self.client.post(url, headers=headers, content=body)
                        if resp.status_code != 200:
                            continue
                        data = resp.json()
                    except Exception:
                        continue
                    if not isinstance(data, dict) or data.get("errno") != 0:
                        continue
                    items = data.get("list")
                    if isinstance(items, list) and items and isinstance(items[0], dict):
                        dlink = items[0].get("dlink")
                        if isinstance(dlink, str) and dlink:
                            return dlink
        return None

    async def _usable(self, url: str) -> bool:
        """False if the CDN answers a one-byte probe with 403 (no / expired login)."""
        try:
            resp = await self.client.get(url, headers={**self.download_headers(url), "Range": "bytes=0-0"},
                                         timeout=PROBE_TIMEOUT)
        except Exception:
            return True               # the probe proves nothing: let the download decide
        return resp.status_code != 403

    async def _indexsubtitle(self, link: TeraboxLink, fs_id: str) -> str | None:
        url = (f"{INDEXSUBTITLE_BASE}/api/file?url={quote(link.share_url, safe='')}&fs_id={fs_id}"
               f"&direct=1&key={INDEXSUBTITLE_KEY}")
        try:
            resp = await self.client.get(url, headers={"User-Agent": BROWSER_USER_AGENT},
                                         follow_redirects=False, timeout=10.0)
        except Exception:
            return None
        location = resp.headers.get("location", "") if resp.is_redirect else ""
        if not location.startswith("http"):
            return None
        try:
            probe = await self.client.get(location, headers={"User-Agent": BROWSER_USER_AGENT,
                                          "Range": "bytes=0-100"}, timeout=5.0)
        except Exception:
            return None
        return location if probe.status_code in (200, 206) else None

    async def download_link(self, link: TeraboxLink, meta: ShareMeta, item: ShareFile) -> str:
        if not meta.complete or not meta.randsk:
            await self._complete_meta(link.surl, meta)
        if meta.complete:
            dlink = await self._sharedownload(meta, item.fs_id)
            if dlink and await self._usable(dlink):
                return dlink
        found = await self._indexsubtitle(link, item.fs_id)
        if found:
            return found
        if item.dlink:
            return item.dlink
        if self.cookie:
            raise TeraboxError("TeraBox refused the download: the ndus session looks expired "
                               "(delete sessions/terabox.json and log in again)")
        raise TeraboxError("TeraBox needs a login to download this file: give your ndus cookie "
                           "(TERABOX_NDUS or the prompt)")


# -- the task entry point --------------------------------------------------------------------------

def history_key(link: TeraboxLink, item: ShareFile) -> str:
    """A download link is temporary, so the history remembers a file by share and file id."""
    return f"terabox://{link.surl}/{item.fs_id}"


async def download_terabox(
    client, url: str, directory: Path, history: dict, skip: bool, session: str | None = None,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed). Every file of the share (or of the ``path=`` folder of
    the link) is downloaded; subfolders become subfolders of `directory`."""
    link = parse_terabox_url(url)
    if link is None:
        logger.error("DLOAD",
            f"Unsupported TeraBox link (use terabox.com/s/<id> or .../sharing/link?surl=<id>): {url}")
        return 0, 0, 1

    resolver = TeraboxResolver(client, session)
    logger.info("DLOAD", f"Reading TeraBox share: {link.surl}" + (f" ({link.path})" if link.path else ""))
    try:
        meta, entries = await resolver.collect(link)
    except Exception as exc:
        logger.error("DLOAD", f"  TeraBox share {link.surl} failed: {exc}")
        return 0, 0, 1

    unreadable = [rel for item, rel in entries if not item.fs_id]
    entries = [(item, rel) for item, rel in entries if item.fs_id]
    failed = len(unreadable)
    for _ in unreadable:
        progress.count("failed")
    if not entries:
        if not failed:
            logger.error("DLOAD", f"  No files found in TeraBox share {link.surl} (deleted or empty?).")
            return 0, 0, 1
        return 0, 0, failed
    logger.info("DLOAD", f"  Found {len(entries)} file(s).")

    directory.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONNECTIONS)
    ok = skipped = 0

    async def fetch(item: ShareFile, rel: tuple[str, ...]) -> str:
        """'ok' / 'skipped' / 'failed'"""
        key = history_key(link, item)
        name = safe_filename(item.name, item.fs_id)
        target_dir = directory.joinpath(*rel) if rel else directory
        if skip and key in history:
            logger.info("DLOAD", f"Skipping (already downloaded): {Path(history[key]).name}")
            progress.count("skipped")
            return "skipped"
        async with sem:
            try:
                dlink = await resolver.download_link(link, meta, item)
            except Exception as exc:
                logger.error("DLOAD", f"  Failed: {name} — {exc}")
                progress.count("failed")
                return "failed"
            target_dir.mkdir(parents=True, exist_ok=True)
            scratch: dict[str, str] = {}
            _, err, _ = await download_file(
                client, dlink, target_dir, name, scratch, False,
                filename=name, headers=resolver.download_headers(dlink))
        if err:
            logger.error("DLOAD", f"  Failed: {name} — {err}")
            return "failed"
        history[key] = scratch.get(dlink, str(target_dir / name))
        return "ok"

    for coro in asyncio.as_completed([fetch(item, rel) for item, rel in entries]):
        outcome = await coro
        if outcome == "ok":
            ok += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            failed += 1

    history_store.save_history(history)
    return ok, skipped, failed