"""pixiv.net — artworks, user galleries and searches.

Same approach as megaloader's Pixiv plugin: the site's own /ajax JSON API,
authenticated with the PHPSESSID cookie, and the i.pximg.net originals
fetched with a pixiv Referer. The /pages endpoint reports the width/height
of every image, so the size filter runs before anything is downloaded.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse

from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources import transfer
from kkafio.services.content_sources.card_size import kk_size_matches
from kkafio.services.content_sources.gdrive import download_gdrive, is_gdrive_url
from kkafio.services.content_sources.gofile import download_gofile, is_gofile_url
from kkafio.services.content_sources.mega import download_mega, is_mega_url
from kkafio.services.content_sources.net import get_with_retry
from kkafio.services.content_sources.pawchive.parsing import pawchive_content_links

PIXIV_URL     = "https://www.pixiv.net"
PIXIV_COOKIE_DOMAIN = ".pixiv.net"

PIXIV_META_CONCURRENCY = 4     # simultaneous /ajax requests (pixiv rate-limits)
PIXIV_PAGE_SIZE        = 48    # works per page on pixiv's gallery tabs (?p=N)
PIXIV_CHUNK_SIZE       = PIXIV_PAGE_SIZE   # artworks resolved, then downloaded, per round

PIXIV_EXTENSIONS = (".png",)   # only these image types are downloaded (originals keep their real extension)

_LANG_PREFIX = re.compile(r"^[a-z]{2}(?:[_-][a-z]{2,4})?$", re.IGNORECASE)


def is_pixiv_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ("www.pixiv.net", "pixiv.net")


def _parse_pixiv_url(url: str) -> tuple[str, str, str] | None:
    """Classify a pixiv link.

    Returns ("artwork", artwork_id, "") or ("user", user_id, scope) where scope
    is "artworks" (illustrations + manga), "illustrations" or "manga", or
    ("search", keyword, scope) for a search / tag page.
    A bare /users/ID means /users/ID/artworks. A tag after the scope
    (/users/ID/illustrations/TAG) is read by _pixiv_url_tag. Returns None for
    anything else.
    """
    parts = urlparse(url)
    segs = [s for s in parts.path.split("/") if s]
    if segs and _LANG_PREFIX.match(segs[0]) and segs[0].lower() not in ("users", "artworks"):
        segs = segs[1:]                      # drop the /en/ language prefix

    if len(segs) >= 2 and segs[0] == "artworks" and segs[1].isdigit():
        return "artwork", segs[1], ""
    if len(segs) >= 2 and segs[0] == "users" and segs[1].isdigit():
        scope = segs[2] if len(segs) >= 3 else "artworks"
        if scope in ("artworks", "illustrations", "manga"):
            return "user", segs[1], scope
        return None                          # /bookmarks, /following, ...

    query = dict(parse_qsl(parts.query))

    # Search: /search?q=KEYWORD&type=artwork|illust|manga&...
    if segs == ["search"] and query.get("q"):
        scope = {"artwork": "artworks", "illust": "illustrations", "manga": "manga"}.get(
            query.get("type", "artwork"))
        return ("search", query["q"], scope) if scope else None
    # Tag page: /tags/KEYWORD[/artworks|illustrations|manga]
    if len(segs) >= 2 and segs[0] == "tags":
        scope = segs[2] if len(segs) >= 3 else "artworks"
        if scope in ("artworks", "illustrations", "manga"):
            return "search", unquote(segs[1]), scope
        return None

    # Legacy links
    if segs and segs[0] == "member.php" and query.get("id", "").isdigit():
        return "user", query["id"], "artworks"
    if segs and segs[0] == "member_illust.php" and query.get("illust_id", "").isdigit():
        return "artwork", query["illust_id"], ""
    return None


def _pixiv_url_tag(url: str) -> str | None:
    """The tag of a tag-filtered gallery link -- /users/ID/illustrations/TAG
    (also /artworks/TAG, /manga/TAG) -- or None. The tag is percent-decoded."""
    parsed = _parse_pixiv_url(url)
    if parsed is None or parsed[0] != "user":
        return None
    segs = [s for s in urlparse(url).path.split("/") if s]
    if segs and _LANG_PREFIX.match(segs[0]) and segs[0].lower() not in ("users", "artworks"):
        segs = segs[1:]
    return unquote(segs[3]) if len(segs) >= 4 and segs[3] else None


def _pixiv_url_page(url: str) -> int | None:
    """The ?p=N page number of a pixiv gallery link, or None."""
    value = dict(parse_qsl(urlparse(url).query)).get("p", "")
    return int(value) if value.isdigit() and int(value) >= 1 else None


def _pixiv_select_page_ids(ids: list[str], page_start: int, page_end: int | None) -> list[str]:
    """Pick the works shown on pages page_start..page_end (inclusive, either
    direction; None = through the last page) of a gallery whose works are
    listed newest first, PIXIV_PAGE_SIZE per page like pixiv's own ?p=N."""
    pages = max(1, -(-len(ids) // PIXIV_PAGE_SIZE))
    last = pages if page_end is None else page_end
    step = 1 if last >= page_start else -1
    selected: list[str] = []
    for page in range(page_start, last + step, step):
        if page < 1 or page > pages:
            logger.warning("DLOAD",
                f"  Page {page} is outside this gallery ({pages} page(s)) — skipped.")
            continue
        selected += ids[(page - 1) * PIXIV_PAGE_SIZE: page * PIXIV_PAGE_SIZE]
    return selected


def _normalize_pixiv_url(url: str) -> str | None:
    """Canonical form of a pixiv link: users/ID → users/ID/artworks."""
    parsed = _parse_pixiv_url(url)
    if parsed is None:
        return None
    kind, ident, scope = parsed
    if kind == "artwork":
        return f"{PIXIV_URL}/en/artworks/{ident}"
    if kind == "search":
        return url
    tag = _pixiv_url_tag(url)
    return f"{PIXIV_URL}/en/users/{ident}/{scope}" + (f"/{quote(tag, safe='')}" if tag else "")


def _pixiv_default_name(url: str) -> str:
    return Path(urlparse(url).path).name


async def _pixiv_api(client, endpoint: str, params: dict | None = None):
    """Call pixiv's /ajax JSON API and return its "body"."""
    url = f"{PIXIV_URL}/ajax{endpoint}"
    if params:
        url += "?" + urlencode(params)
    r = await get_with_retry(client, url)
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data.get("message") or "Pixiv API returned an error")
    return data.get("body")


async def _pixiv_artwork_images(client, artwork_id: str) -> tuple[list[str], int]:
    """Return (original image URLs that are PNGs and pass the size filter,
    number rejected -- by type or by size)."""
    pages = await _pixiv_api(client, f"/illust/{artwork_id}/pages")
    urls: list[str] = []
    rejected = 0
    for page in pages or []:
        original = (page.get("urls") or {}).get("original")
        if not original or not isinstance(original, str):
            continue
        if Path(urlparse(original).path).suffix.lower() not in PIXIV_EXTENSIONS:
            rejected += 1                      # a jpg / gif / ...: not a KK image
        elif kk_size_matches(page.get("width"), page.get("height")):
            urls.append(original)
        else:
            rejected += 1
    return urls, rejected


# ---------------------------------------------------------------------------
# Artwork descriptions: download links, and (optionally) follow other artworks
# ---------------------------------------------------------------------------

# pixiv wraps outside links as /jump.php?<url-encoded address> (older pages:
# /jump.php?url=<...>); the real address has to be decoded out of it.
_JUMP_RE = re.compile(r"/jump\.php\?(?:url=)?([^\s\"'<>]+)", re.IGNORECASE)
_ARTWORK_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?pixiv\.net/(?:[a-z]{2}(?:[_-][a-z]{2,4})?/)?artworks/(\d+)",
    re.IGNORECASE)
_ARTWORK_HREF_RE = re.compile(
    r"""href=["']/(?:[a-z]{2}(?:[_-][a-z]{2,4})?/)?artworks/(\d+)""", re.IGNORECASE)
_LEGACY_ARTWORK_RE = re.compile(r"member_illust\.php\?[^\s\"'<>]*?illust_id=(\d+)", re.IGNORECASE)
# pixiv's own shorthand, shown (and linked) in descriptions as "illust/12345"
_ILLUST_SHORTHAND_RE = re.compile(r"(?<![\w/])illust/(\d+)", re.IGNORECASE)


def pixiv_description_links(content) -> tuple[list[str], list[str]]:
    """The references found in an artwork's description (HTML or plain text).

    Returns (artwork_ids, external_links): the ids of other pixiv artworks, and
    the mega / gofile / Google Drive links -- each in order of appearance,
    without duplicates.
    """
    if not isinstance(content, str) or not content:
        return [], []
    text = html.unescape(content)
    # Swap every jump.php wrapper for the address it carries, so links pixiv
    # wrapped (Mega's #key included) are seen like plain ones, in place.
    text = _JUMP_RE.sub(lambda m: " " + unquote(m.group(1)) + " ", text)

    hits = sorted(
        (m.start(), m.group(1))
        for rx in (_ARTWORK_LINK_RE, _ARTWORK_HREF_RE, _LEGACY_ARTWORK_RE, _ILLUST_SHORTHAND_RE)
        for m in rx.finditer(text))
    ids = list(dict.fromkeys(artwork_id for _, artwork_id in hits))     # order of appearance
    return ids, pawchive_content_links(text)


async def _pixiv_artwork_description(client, artwork_id: str) -> str:
    """The description (caption) of an artwork, as pixiv sends it (HTML)."""
    body = await _pixiv_api(client, f"/illust/{artwork_id}", {"lang": "en"}) or {}
    parts = [body.get(key) for key in ("description", "illustComment")]
    return "\n".join(p for p in parts if isinstance(p, str) and p)


async def _download_external_link(
    link: str, externals: dict | None, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Hand a link found in a description to the downloader for its host.
    `externals` maps "gofile" / "mega" / "gdrive" to their HTTP clients."""
    if is_gofile_url(link):
        kind, fn = "gofile", download_gofile
    elif is_mega_url(link):
        kind, fn = "mega", download_mega
    elif is_gdrive_url(link):
        kind, fn = "gdrive", download_gdrive
    else:
        logger.error("DLOAD", f"  Unsupported link in description: {link}")
        return 0, 0, 1
    client = (externals or {}).get(kind)
    if client is None:
        logger.error("DLOAD", f"  No {kind} client available for: {link}")
        return 0, 0, 1
    return await fn(client, link, directory, history, skip)


@dataclass
class PixivFollowState:
    """What the description reading has already handled during a run.

    Shared by every pixiv line of the run, so an artwork that several
    descriptions point to (or that points back, A -> B -> A) is only read once
    and the crawl always ends.
    """
    visited: set[str] = field(default_factory=set)         # artworks whose description was read
    links: set[str] = field(default_factory=set)           # external links already downloaded


async def _read_pixiv_descriptions(
    client, artwork_ids: list[str], directory: Path, history: dict, skip: bool,
    externals: dict | None, state: PixivFollowState, follow_artworks: bool,
) -> tuple[int, int, int]:
    """Read the description of each artwork and download the mega / gofile /
    Google Drive links in it. With `follow_artworks`, the other pixiv artworks
    it mentions are downloaded too (their images), and their descriptions are
    read in turn. `artwork_ids` themselves are NOT downloaded here -- the
    caller already did that.

    Works in rounds: each round holds the artworks found by the one before, so
    nothing is recursive and the visited set stops loops.
    """
    total_ok = total_skip = total_fail = 0
    sem = asyncio.Semaphore(PIXIV_META_CONCURRENCY)

    async def _describe(artwork_id: str) -> str:
        async with sem:
            return await _pixiv_artwork_description(client, artwork_id)

    current = [(a, False) for a in dict.fromkeys(artwork_ids)]      # (id, still to download)
    while current:
        current = [(a, ref) for a, ref in current if a not in state.visited]
        if not current:
            break
        state.visited.update(a for a, _ in current)

        # The artworks other descriptions pointed to: download their images.
        for artwork_id, is_reference in current:
            if not is_reference:
                continue
            logger.info("DLOAD", f"  Following pixiv artwork: {artwork_id}")
            try:
                urls, _rejected = await _pixiv_artwork_images(client, artwork_id)
                ok, skipped, fail = await transfer.download_files(
                    client, urls, directory, _pixiv_default_name, history, skip)
            except Exception as exc:
                logger.error("DLOAD", f"  Could not read artwork {artwork_id}: {exc}")
                ok = skipped = 0
                fail = 1
            total_ok += ok
            total_skip += skipped
            total_fail += fail
            history_store.save_history(history)

        descriptions = await asyncio.gather(
            *[_describe(a) for a, _ in current], return_exceptions=True)

        next_round: list[tuple[str, bool]] = []
        for (artwork_id, _), res in zip(current, descriptions, strict=True):
            if isinstance(res, BaseException):
                if not isinstance(res, Exception):
                    raise res                          # Cancelled / KeyboardInterrupt
                total_fail += 1
                logger.error("DLOAD",
                    f"  Could not read the description of artwork {artwork_id}: {res}")
                continue
            ref_ids, links = pixiv_description_links(res)
            ref_ids = ([a for a in ref_ids if a != artwork_id and a not in state.visited]
                       if follow_artworks else [])
            if ref_ids or links:
                logger.info("DLOAD",
                    f"  Artwork {artwork_id}: "
                    + (f"{len(ref_ids)} linked artwork(s), " if follow_artworks else "")
                    + f"{len(links)} download link(s) in its description.")
            next_round += [(a, True) for a in ref_ids]

            # One link at a time: mega meters transfer per address and gofile
            # is sensitive about request bursts.
            for link in links:
                if link in state.links:
                    continue
                state.links.add(link)
                logger.info("DLOAD", f"  Link: {link}")
                try:
                    ok, skipped, fail = await _download_external_link(
                        link, externals, directory, history, skip)
                except Exception as exc:
                    logger.error("DLOAD", f"  Failed: {link} — {exc}")
                    ok = skipped = 0
                    fail = 1
                total_ok += ok
                total_skip += skipped
                total_fail += fail
                history_store.save_history(history)
        current = next_round

    return total_ok, total_skip, total_fail


async def _pixiv_user_artwork_ids(client, user_id: str, scope: str) -> list[str]:
    """All artwork IDs of a user, newest first."""
    body = await _pixiv_api(client, f"/user/{user_id}/profile/all") or {}

    def _ids(section) -> list[str]:
        # pixiv sends [] (not {}) when a section is empty
        return list(section.keys()) if isinstance(section, dict) else []

    ids: list[str] = []
    if scope in ("artworks", "illustrations"):
        ids += _ids(body.get("illusts"))
    if scope in ("artworks", "manga"):
        ids += _ids(body.get("manga"))
    return sorted(set(ids), key=int, reverse=True)


# Scope of a gallery link -> the /ajax/user/ID/<kind>/tag endpoint listing a tag's works.
_PIXIV_USER_TAG_ENDPOINT = {"artworks": "illustmanga", "illustrations": "illusts", "manga": "manga"}
PIXIV_TAG_PAGE_SIZE = 48


async def _pixiv_user_tag_artwork_ids(client, user_id: str, scope: str, tag: str) -> list[str]:
    """The artwork IDs of a user that carry `tag`, newest first (the user's
    /users/ID/illustrations/TAG page)."""
    kind = _PIXIV_USER_TAG_ENDPOINT[scope]
    ids: list[str] = []
    offset = 0
    while True:
        body = await _pixiv_api(client, f"/user/{user_id}/{kind}/tag", {
            "tag": tag, "offset": offset, "limit": PIXIV_TAG_PAGE_SIZE, "lang": "en"}) or {}
        works = body.get("works") if isinstance(body, dict) else None
        works = [w for w in works or [] if isinstance(w, dict) and str(w.get("id", "")).isdigit()]
        ids += [str(w["id"]) for w in works]
        total = body.get("total") if isinstance(body, dict) else None
        offset += PIXIV_TAG_PAGE_SIZE
        if len(works) < PIXIV_TAG_PAGE_SIZE or (isinstance(total, int) and offset >= total):
            break
    return sorted(set(ids), key=int, reverse=True)


# Web search params -> /ajax/search path + "type" value.
_PIXIV_SEARCH_ENDPOINT = {
    "artworks":      ("artworks",      "all"),
    "illustrations": ("illustrations", "illust"),
    "manga":         ("manga",         "manga"),
}
# Params the request builder sets itself; everything else in the link (the
# filters: s_mode, mode, order, ai_type, scd/ecd, wlt/wgt, tool, ...) is kept.
_PIXIV_SEARCH_OWN_PARAMS = {"q", "word", "p", "page", "type", "lang"}


def _pixiv_search_request(url: str, page: int) -> tuple[str, list[tuple[str, str]]]:
    """Build the /ajax/search endpoint + params for a search link and page,
    carrying over every filter query parameter of the link unchanged."""
    kind, keyword, scope = _parse_pixiv_url(url)
    path, type_ = _PIXIV_SEARCH_ENDPOINT[scope]
    pairs = [(k, v) for k, v in parse_qsl(urlparse(url).query, keep_blank_values=True)
             if k not in _PIXIV_SEARCH_OWN_PARAMS]
    present = {k for k, _ in pairs}
    for key, default in (("order", "date_d"), ("mode", "all"), ("s_mode", "s_tag")):
        if key not in present:
            pairs.append((key, default))
    pairs += [("word", keyword), ("type", type_), ("p", str(page)), ("lang", "en")]
    return f"/search/{path}/{quote(keyword, safe='')}", pairs


def _pixiv_search_items(body) -> list[dict]:
    """The work list of a search response (its key depends on the endpoint)."""
    if isinstance(body, dict):
        for key in ("illustManga", "illust", "manga", "artworks"):
            section = body.get(key)
            if isinstance(section, dict) and isinstance(section.get("data"), list):
                return [w for w in section["data"]
                        if isinstance(w, dict) and str(w.get("id", "")).isdigit()]
    return []


async def _get_pixiv_search_page_urls(
    client, url: str, page: int, checked_ids: list[str] | None = None,
) -> tuple[list[str], int, bool]:
    """Returns (file_urls, card_failures, listing_empty) for one search page.

    The search results already carry the first image's size, so a single-page
    work that fails the filter is dropped without another request; every other
    work is checked through /pages like a gallery work.
    """
    endpoint, params = _pixiv_search_request(url, page)
    body = await _pixiv_api(client, endpoint, params)
    works = _pixiv_search_items(body)
    if not works:
        return [], 0, True

    to_check: list[str] = []
    for w in works:
        single = w.get("pageCount") == 1
        if single and not kk_size_matches(w.get("width"), w.get("height")):
            continue
        to_check.append(str(w["id"]))
    if checked_ids is not None:
        checked_ids.extend(to_check)              # for "follow illustrations"
    logger.info("DLOAD",
        f"  {len(works)} work(s) on page {page}, {len(to_check)} to check "
        f"({len(works) - len(to_check)} dropped by size).")

    sem = asyncio.Semaphore(PIXIV_META_CONCURRENCY)

    async def _resolve(artwork_id: str):
        async with sem:
            return await _pixiv_artwork_images(client, artwork_id)

    results = await asyncio.gather(*[_resolve(a) for a in to_check], return_exceptions=True)
    urls: list[str] = []
    failed = 0
    for artwork_id, res in zip(to_check, results, strict=True):
        if isinstance(res, BaseException):
            if not isinstance(res, Exception):
                raise res                          # Cancelled / KeyboardInterrupt
            failed += 1
            logger.error("DLOAD", f"  Could not read artwork {artwork_id}: {res}")
        else:
            urls += res[0]
    return urls, failed, False


async def _download_pixiv_search(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None, page_end: int | None,
    follow_illustrations: bool = False, externals: dict | None = None,
    follow_state: PixivFollowState | None = None,
) -> tuple[int, int, int]:
    """Search results are paged (60 per page) like the other listing sources:
    ``| N`` / ``| N | M`` / ``| all`` pick the pages; without a directive the
    link's own ``?p=N`` (else page 1) is read — a search can be huge, so
    "everything" has to be asked for with ``| all``."""
    if page_start is None:
        page_start = page_end = _pixiv_url_page(url) or 1
    logger.info("DLOAD",
        f"Scraping pixiv search (page {page_start}): {url}" if page_end == page_start else
        f"Scraping pixiv search (pages {page_start}–{'end' if page_end is None else page_end}): {url}")

    checked_ids: list[str] = []

    async def _page_urls(c, u, page):
        return await _get_pixiv_search_page_urls(c, u, page, checked_ids)

    ok, skipped, failed = await transfer.download_pages(
        client, url, directory, history, skip, page_start, page_end,
        _page_urls, _pixiv_default_name)
    if checked_ids:
        f_ok, f_skip, f_fail = await _read_pixiv_descriptions(
            client, checked_ids, directory, history, skip, externals,
            follow_state or PixivFollowState(), follow_illustrations)
        ok, skipped, failed = ok + f_ok, skipped + f_skip, failed + f_fail
    return ok, skipped, failed


async def download_pixiv(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None = None, page_end: int | None = None,
    follow_illustrations: bool = False, externals: dict | None = None,
    follow_state: PixivFollowState | None = None,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed).

    The description of every artwork is searched for mega / gofile / Google
    Drive links, which are always downloaded too. With ``follow_illustrations`` the
    other pixiv artworks it mentions are downloaded as well, and their
    descriptions are searched the same way.
    `externals` holds the gofile / mega / gdrive clients and `follow_state` the
    already-visited artworks (share one per run).

    For a user gallery the page directive (``| N``, ``| N | M``, ``| all``) or,
    without one, the link's own ``?p=N`` picks which pages are read; with
    neither the whole gallery is read. Artwork links ignore it.
    """
    parsed = _parse_pixiv_url(url)
    if parsed is None:
        logger.error("DLOAD",
            f"Unsupported pixiv link (use /artworks/ID, /users/ID or a /search link): {url}")
        return 0, 0, 1
    kind, ident, scope = parsed

    if kind == "search":
        return await _download_pixiv_search(
            client, url, directory, history, skip, page_start, page_end,
            follow_illustrations, externals, follow_state)

    if kind == "artwork":
        logger.info("DLOAD", f"Scraping pixiv artwork: {ident}")
        artwork_ids = [ident]
    else:
        # Print the normalised link so users/ID visibly became users/ID/artworks.
        logger.info("DLOAD", f"Scraping pixiv gallery: {_normalize_pixiv_url(url)}")
        tag = _pixiv_url_tag(url)
        artwork_ids = (await _pixiv_user_tag_artwork_ids(client, ident, scope, tag) if tag
                       else await _pixiv_user_artwork_ids(client, ident, scope))
        logger.info("DLOAD", f"  Found {len(artwork_ids)} artwork(s).")
        if page_start is None:
            link_page = _pixiv_url_page(url)       # ...?p=2 means just page 2
            if link_page is not None:
                page_start = page_end = link_page
        if page_start is not None:
            artwork_ids = _pixiv_select_page_ids(artwork_ids, page_start, page_end)
            logger.info("DLOAD",
                f"  Page {page_start}" if page_end == page_start else
                f"  Pages {page_start}–{'end' if page_end is None else page_end}"
                f": {len(artwork_ids)} artwork(s) selected.")

    sem = asyncio.Semaphore(PIXIV_META_CONCURRENCY)

    async def _resolve(artwork_id: str):
        async with sem:
            return await _pixiv_artwork_images(client, artwork_id)

    total_ok = total_skip = total_fail = 0
    kept = filtered = 0
    seen: set[str] = set()

    for i in range(0, len(artwork_ids), PIXIV_CHUNK_SIZE):
        chunk = artwork_ids[i:i + PIXIV_CHUNK_SIZE]
        results = await asyncio.gather(*[_resolve(a) for a in chunk], return_exceptions=True)

        file_urls: list[str] = []
        for artwork_id, res in zip(chunk, results, strict=True):
            if isinstance(res, BaseException):
                if not isinstance(res, Exception):
                    raise res                      # Cancelled / KeyboardInterrupt
                total_fail += 1
                logger.error("DLOAD", f"  Could not read artwork {artwork_id}: {res}")
                continue
            urls, rejected = res
            filtered += rejected
            kept += len(urls)
            file_urls += [u for u in urls if u not in seen]
            seen.update(urls)

        if len(artwork_ids) > PIXIV_CHUNK_SIZE:
            logger.info("DLOAD",
                f"  Checked {min(i + PIXIV_CHUNK_SIZE, len(artwork_ids))}"
                f"/{len(artwork_ids)} artwork(s)...")

        ok, skipped, fail = await transfer.download_files(
            client, file_urls, directory, _pixiv_default_name, history, skip)
        total_ok   += ok
        total_skip += skipped
        total_fail += fail
        history_store.save_history(history)       # checkpoint after every chunk

    logger.info("DLOAD",
        f"  {kept} PNG image(s) matched 320x180 / 252x352-ratio, "
        f"{filtered} filtered out (not a PNG, or wrong size).")

    if artwork_ids:
        f_ok, f_skip, f_fail = await _read_pixiv_descriptions(
            client, artwork_ids, directory, history, skip, externals,
            follow_state or PixivFollowState(), follow_illustrations)
        total_ok   += f_ok
        total_skip += f_skip
        total_fail += f_fail
    return total_ok, total_skip, total_fail
