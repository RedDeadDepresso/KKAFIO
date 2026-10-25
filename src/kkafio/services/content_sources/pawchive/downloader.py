"""Entry point: read a pawchive link's listing pages and process their posts."""

from __future__ import annotations

from pathlib import Path

from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.net import MAX_CONSECUTIVE_BAD_PAGES
from kkafio.services.content_sources.pawchive.access import PawchiveAuth, PawchiveThrottle, pawchive_get
from kkafio.services.content_sources.pawchive.parsing import PAWCHIVE_EXTENSIONS, parse_pawchive_url, pawchive_api_url, pawchive_posts_from, pawchive_url_page
from kkafio.services.content_sources.pawchive.posts import PawchiveRun, pawchive_process_posts

async def _download_pawchive_pages(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int, page_end: int | None, run: PawchiveRun,
) -> None:
    """Read listing pages page_start..page_end (None = until empty; reverse
    when page_start > page_end) and process the posts of each."""
    bad_pages = 0
    seen_ids: set[str] = set()

    if page_end is None:
        page_iter = iter(range(page_start, 10 ** 9))
        forward = True
    else:
        step = 1 if page_end >= page_start else -1
        page_iter = iter(range(page_start, page_end + step, step))
        forward = step == 1

    for page in page_iter:
        if page < 1:
            logger.warning("DLOAD", f"  Page {page} does not exist (pages start at 1) — skipped.")
            continue
        logger.info("DLOAD", f"  Page {page}...")
        try:
            r = await pawchive_get(client, pawchive_api_url(url, page), run.throttle)
            posts = pawchive_posts_from(r.json())
        except Exception as exc:
            # Not "end of results": we can't tell, so count it and go on.
            run.failed += 1
            bad_pages += 1
            logger.error("DLOAD", f"  Page {page} failed: {exc}")
            if bad_pages >= MAX_CONSECUTIVE_BAD_PAGES:
                logger.error("DLOAD",
                    f"  {bad_pages} pages in a row failed — stopping. Re-run later to resume.")
                break
            continue
        bad_pages = 0

        if not posts:
            if forward:
                logger.info("DLOAD", f"  Page {page} is empty — stopping.")
                break
            # Reverse range: an empty page just means we started past the end.
            logger.info("DLOAD", f"  Page {page} is empty — skipped.")
            continue

        ids = {str(p.get("id")) for p in posts}
        if ids <= seen_ids:
            logger.info("DLOAD", f"  Page {page} repeats earlier results — stopping.")
            break
        seen_ids |= ids

        await pawchive_process_posts(client, posts, directory, history, skip, run)
        history_store.save_history(history)   # checkpoint after every page


async def download_pawchive(
    client, url: str, directory: Path, history: dict, skip: bool,
    page_start: int | None = None, page_end: int | None = None,
    externals: dict | None = None, auth: PawchiveAuth | None = None,
    throttle: PawchiveThrottle | None = None,
) -> tuple[int, int, int]:
    """Returns (succeeded, skipped, failed).

    `externals` maps "gofile" / "mega" / "gdrive" to the clients used for the
    links found in post content. `auth` and `throttle` are shared by all
    pawchive links of a run: the session cookie is asked for at most once, and
    one 429 slows every request down, not just the one that was refused. For the two listings the page directive
    (``| N``, ``| N | M``, ``| all``) or, without one, the link's own ``?o=``
    picks the pages; with neither only page 1 is read (a listing can hold
    millions of posts, so everything has to be asked for with ``| all``).
    A single post ignores the directive.
    """
    parsed = parse_pawchive_url(url)
    if parsed is None:
        logger.error("DLOAD",
            "Unsupported pawchive link (use /posts, /<service>/user/ID or "
            f"/<service>/user/ID/post/ID): {url}")
        return 0, 0, 1
    kind = parsed[0]
    run = PawchiveRun(externals, auth, throttle)

    if kind == "post":
        api_url = pawchive_api_url(url)
        logger.info("DLOAD", f"Scraping pawchive post: {api_url}")
        r = await pawchive_get(client, api_url, run.throttle)
        await pawchive_process_posts(client, pawchive_posts_from(r.json()),
                                      directory, history, skip, run)
    else:
        if page_start is None:
            page_start = page_end = pawchive_url_page(url) or 1
        what = "search" if kind == "posts" else "creator"
        if page_end == page_start:
            logger.info("DLOAD",
                f"Scraping pawchive {what} (page {page_start}): "
                f"{pawchive_api_url(url, max(page_start, 1))}")
        else:
            logger.info("DLOAD",
                f"Scraping pawchive {what} (pages {page_start}–"
                f"{'end' if page_end is None else page_end}): {pawchive_api_url(url)}")
        await _download_pawchive_pages(
            client, url, directory, history, skip, page_start, page_end, run)

    if run.excluded:
        logger.info("DLOAD",
            f"  {run.excluded} file(s) skipped: not {' / '.join(PAWCHIVE_EXTENSIONS)}.")
    if run.filtered:
        logger.info("DLOAD",
            f"  {run.filtered} PNG(s) skipped: not 320x180 / 252x352-ratio.")
    return run.ok, run.skipped, run.failed
