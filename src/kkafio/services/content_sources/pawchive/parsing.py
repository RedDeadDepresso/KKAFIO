"""Pawchive links, API endpoints and post contents — no network access here.

Turns a pasted link into the matching /api/v1 endpoint, picks the downloadable
files out of a post, and finds the mega / gofile / Google Drive links in its
text.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse

from kkafio.core.logger import logger
from kkafio.services.content_sources.gdrive import is_gdrive_url, parse_gdrive_url
from kkafio.services.content_sources.gofile import is_gofile_url, parse_gofile_url
from kkafio.services.content_sources.mega import is_mega_url
from kkafio.services.content_sources.net import safe_filename

PAWCHIVE_URL       = "https://pawchive.pw"
PAWCHIVE_API_URL   = f"{PAWCHIVE_URL}/api/v1"
PAWCHIVE_DATA_URL  = f"{PAWCHIVE_URL}/data"     # fallback when an entry has no "node"
PAWCHIVE_NODE_URL  = "https://n{node}.pawchive.pw/data"   # files live on the host named by "node"
PAWCHIVE_FILE_URL  = "https://file.pawchive.pw/data"   # the public file host: tried first
PAWCHIVE_TEMP_HOST = "t1.pawchive.pw"           # oversized files in temporary storage (signed links, login needed)
PAWCHIVE_COOKIE_DOMAIN = ".pawchive.pw"         # the session cookie goes to pawchive.pw and its subdomains only
PAWCHIVE_EXTENSIONS = (".rar", ".zip", ".7z", ".zipmod", ".png")   # the only file types downloaded
PAWCHIVE_PAGE_SIZE = 50                         # posts per listing page (the "o" step)


def is_pawchive_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ("pawchive.pw", "www.pawchive.pw")


def parse_pawchive_url(url: str) -> tuple[str, str, str, str] | None:
    """Classify a pawchive link.

    Returns ("posts", "", "", "") for the posts listing / search,
    ("user", service, user_id, "") for a creator's posts or
    ("post", service, user_id, post_id) for a single post; None for anything
    else. An already-converted ``/api/v1/...`` link is accepted too.
    """
    segs = [unquote(s) for s in urlparse(url).path.split("/") if s]
    if segs[:2] == ["api", "v1"]:
        segs = segs[2:]
    if segs == ["posts"]:
        return "posts", "", "", ""
    if len(segs) in (3, 4) and segs[1] == "user" and (len(segs) == 3 or segs[3] == "posts"):
        return "user", segs[0], segs[2], ""
    if len(segs) == 5 and segs[1] == "user" and segs[3] == "post":
        return "post", segs[0], segs[2], segs[4]
    return None


def pawchive_api_url(url: str, page: int | None = None) -> str:
    """The API endpoint for a pawchive link.

    Listings keep every query parameter of the link (search text, tags, ...)
    except ``o``, which is set from `page` (1-based). A single post takes no
    query.
    """
    parsed = parse_pawchive_url(url)
    if parsed is None:
        raise ValueError(f"Unsupported pawchive link: {url}")
    kind, service, user, post = parsed
    if kind == "posts":
        path = "/posts"
    elif kind == "user":
        path = f"/{quote(service, safe='')}/user/{quote(user, safe='')}"
    else:
        return (f"{PAWCHIVE_API_URL}/{quote(service, safe='')}/user/"
                f"{quote(user, safe='')}/post/{quote(post, safe='')}")

    pairs = [(k, v) for k, v in parse_qsl(urlparse(url).query, keep_blank_values=True)
             if k != "o"]
    if page is not None:
        if page < 1:
            raise ValueError(f"Page numbers start at 1 (got {page}).")
        pairs.append(("o", str((page - 1) * PAWCHIVE_PAGE_SIZE)))
    query = urlencode(pairs)
    return f"{PAWCHIVE_API_URL}{path}" + (f"?{query}" if query else "")


def pawchive_url_page(url: str) -> int | None:
    """The page a link's own ``?o=`` offset points at, or None."""
    value = dict(parse_qsl(urlparse(url).query)).get("o", "")
    if not value.isdigit():
        return None
    if int(value) % PAWCHIVE_PAGE_SIZE:
        logger.warning("DLOAD",
            f"  o={value} is not a multiple of {PAWCHIVE_PAGE_SIZE}; "
            f"reading the page that contains it.")
    return int(value) // PAWCHIVE_PAGE_SIZE + 1


def pawchive_posts_from(body) -> list[dict]:
    """The posts of an API response: a list (listings), a single post object,
    or an object wrapping them ({"post": {...}} / {"posts": [...]}).

    Raises ValueError for any other shape (an error object, say), so it is
    reported instead of mistaken for "no more posts".
    """
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict) and isinstance(body.get("post"), dict):
        items = [body["post"]]
    elif isinstance(body, dict) and isinstance(body.get("posts"), list):
        items = body["posts"]
    elif isinstance(body, dict) and "id" in body:
        items = [body]
    else:
        raise ValueError(f"Unexpected response from pawchive: {str(body)[:200]}")
    return [p for p in items if isinstance(p, dict)]


def _pawchive_filename(name, path: str) -> str:
    """A safe local file name for a file/attachment entry."""
    path_name = Path(path.replace("\\", "/")).name
    base = safe_filename(Path(str(name or "").replace("\\", "/")).name, "")
    if not base:
        return safe_filename(path_name, "file")
    if not Path(base).suffix and Path(path_name).suffix:
        base += Path(path_name).suffix           # name lost its extension
    return base


def _pawchive_extension_ok(*names) -> bool:
    """True if any of `names` (a file name and/or the stored path) ends in one
    of PAWCHIVE_EXTENSIONS. Both are looked at because an attachment's display
    name sometimes lacks the extension that its stored file has."""
    return any(
        Path(str(n or "").replace("\\", "/")).suffix.lower() in PAWCHIVE_EXTENSIONS
        for n in names)


def is_pawchive_temp_url(url: str) -> bool:
    """True for a file in Pawchive's temporary storage (t1.pawchive.pw)."""
    return (urlparse(url).hostname or "").lower() == PAWCHIVE_TEMP_HOST


def _pawchive_data_urls(path: str, node, name: str) -> tuple[str, list[str]]:
    """(history key, candidate URLs best first) of a file stored under `path`.

    The public file host comes first; the storage node that holds the file (or
    the main host when the entry names none) is the fallback -- on its own it
    often answers 403. Both get the file name as ``?f=``, like the website.
    """
    rel = path if path.startswith("/") else "/" + path
    if rel.startswith("/data/"):
        rel = rel[5:]
    query = "?f=" + quote(name, safe="")
    node = str(node or "").strip()
    base = PAWCHIVE_NODE_URL.format(node=node) if node.isdigit() else PAWCHIVE_DATA_URL
    return f"{PAWCHIVE_FILE_URL}{rel}", [f"{PAWCHIVE_FILE_URL}{rel}{query}", f"{base}{rel}{query}"]


def pawchive_item_key(item: dict) -> str:
    """What the download history knows a file by (stable: signed t1 links
    change on every visit, so their query string is not part of it)."""
    return item.get("key") or item["url"]


def pawchive_post_files(post: dict, excluded: list[str] | None = None) -> list[dict]:
    """The downloadable files of a post: its main "file" and every
    attachment, each once (the first attachment often repeats the main file),
    limited to PAWCHIVE_EXTENSIONS -- other types (images, videos, text, ...)
    are left out and their names appended to `excluded` when it is given.

    Returns dicts with url (the first candidate), urls (all candidates, best
    first), key (history key), name, path and check_size (True for PNGs, which
    are only kept when they look like a KK card/scene). Deferred attachments
    (no path yet) are not here: see pawchive_deferred_attachments.
    """
    entries = []
    if isinstance(post.get("file"), dict):
        entries.append(post["file"])
    entries += [a for a in (post.get("attachments") or []) if isinstance(a, dict)]

    files: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        path = path.strip()
        if path.startswith(("http://", "https://")):
            key = path.split("?", 1)[0]
            urls = [path]
            path = urlparse(path).path                # no query string in names
            name = _pawchive_filename(entry.get("name"), path)
        else:
            name = _pawchive_filename(entry.get("name"), path)
            key, urls = _pawchive_data_urls(path, entry.get("node"), name)
        if key in seen:
            continue
        seen.add(key)
        if not _pawchive_extension_ok(name, path):
            if excluded is not None:
                excluded.append(name)
            continue
        is_png = Path(name).suffix.lower() == ".png" or Path(path).suffix.lower() == ".png"
        files.append({"url": urls[0], "urls": urls, "key": key, "name": name,
                      "path": path, "check_size": is_png})
    return files


def pawchive_deferred_attachments(post: dict, excluded: list[str] | None = None) -> list[dict]:
    """Attachments flagged "deferred" that have no path yet: oversized files
    kept in Pawchive's temporary storage, whose signed links only the post's
    web page shows. Only those of a PAWCHIVE_EXTENSIONS type are returned (the
    others are appended to `excluded`), so the page is not even read for them."""
    deferred = [
        a for a in (post.get("attachments") or [])
        if isinstance(a, dict) and a.get("deferred")
        and not (isinstance(a.get("path"), str) and a["path"].strip())
    ]
    wanted = [a for a in deferred if _pawchive_extension_ok(a.get("name"))]
    if excluded is not None:
        excluded += [str(a.get("name") or "?") for a in deferred if a not in wanted]
    return wanted


def pawchive_post_page_url(post: dict) -> str | None:
    service, user, post_id = (str(post.get(k) or "").strip() for k in ("service", "user", "id"))
    if not (service and user and post_id):
        return None
    return (f"{PAWCHIVE_URL}/{quote(service, safe='')}/user/{quote(user, safe='')}"
            f"/post/{quote(post_id, safe='')}")


_PAWCHIVE_TEMP_LINK_RE = re.compile(
    r"""(?:href|src)=["'](https://t1\.pawchive\.pw/f/[^"']+)["']""", re.IGNORECASE)


def pawchive_temp_links(page_html: str) -> dict[str, str]:
    """{file name (lower case): signed t1.pawchive.pw link} found in a post page."""
    import html
    links: dict[str, str] = {}
    for raw in _PAWCHIVE_TEMP_LINK_RE.findall(page_html or ""):
        link = html.unescape(raw)
        name = unquote(urlparse(link).path.rsplit("/", 1)[-1]).lower()
        if name:
            links.setdefault(name, link)
    return links


def pawchive_temp_item(entry: dict, link: str) -> dict:
    """A download item for a deferred attachment, from its signed link."""
    path = urlparse(link).path
    name = _pawchive_filename(entry.get("name"), path)
    is_png = Path(name).suffix.lower() == ".png" or Path(path).suffix.lower() == ".png"
    return {"url": link, "urls": [link], "key": link.split("?", 1)[0], "name": name,
            "path": path, "check_size": is_png}


# A link in the post text: scheme and "www." are optional (people often type
# "mega.nz/file/..."), and the host must start at a word boundary so
# "evilmega.nz" never matches. Everything up to whitespace / a quote / a tag
# bracket belongs to the link, so its query string and #fragment (the Mega
# key!) are kept.
_PAWCHIVE_LINK_RE = re.compile(
    r"""(?<![\w.-])(?:https?://)?(?:[\w-]+\.)*?"""
    r"""(?:mega\.nz|mega\.co\.nz|gofile\.io|drive\.google\.com|"""
    r"""drive\.usercontent\.google\.com|docs\.google\.com/uc)"""
    r"""(?:[/?#][^\s"'<>]*)?""",
    re.IGNORECASE,
)
_PAWCHIVE_LINK_TRAILING = ".,;:!?)]}>*\u2026"


def _pawchive_link_usable(url: str) -> bool:
    """True if `url` is a supported host AND has something to download (a bare
    "mega.nz" or drive home page does not)."""
    if is_gofile_url(url):
        return parse_gofile_url(url) is not None
    if is_mega_url(url):
        parts = urlparse(url)
        return len([s for s in parts.path.split("/") if s]) >= 2 or bool(parts.fragment)
    if is_gdrive_url(url):
        return parse_gdrive_url(url) is not None
    return False


def pawchive_content_links(content) -> list[str]:
    """Mega, Gofile and Google Drive links found in a post's content, in order
    of appearance and without duplicates, each exactly as written (query and
    fragment included).

    Link text is often a shortened copy of the real href, or lacks the query
    string the href has; a link that is only the beginning of another found
    link is therefore dropped in favour of the longer one.
    """
    if not isinstance(content, str) or not content:
        return []
    import html
    text = html.unescape(content)

    found: list[str] = []
    for m in _PAWCHIVE_LINK_RE.finditer(text):
        link = m.group(0).rstrip(_PAWCHIVE_LINK_TRAILING)
        if not re.match(r"https?://", link, re.IGNORECASE):
            link = "https://" + link
        if _pawchive_link_usable(link) and link not in found:
            found.append(link)
    return [a for a in found if not any(b != a and b.startswith(a) for b in found)]


def pawchive_unique_name(item: dict, directory: Path, history: dict, taken: set[str]) -> str:
    """The local name for a file. Pawchive names are not unique ("000.png" in
    every post), so a name that is already used -- on disk or by another file
    of this run -- gets the start of the file's content hash (the last part of
    its path) added. A file downloaded before keeps the name it got."""
    previous = history.get(pawchive_item_key(item))
    if previous and Path(previous).parent == directory:
        name = Path(previous).name
        taken.add(name.lower())
        return name

    name = item["name"]
    if name.lower() in taken or (directory / name).exists():
        stem, suffix = Path(name).stem, Path(name).suffix
        tag = Path(item["path"]).stem[:8] or "dup"
        name = f"{stem}_{tag}{suffix}"
        n = 2
        while name.lower() in taken or (directory / name).exists():
            name = f"{stem}_{tag}_{n}{suffix}"
            n += 1
    taken.add(name.lower())
    return name
