"""Processing the posts of one listing page: their files and linked content."""

from __future__ import annotations

import asyncio
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.services.content_sources.gdrive import download_gdrive, is_gdrive_url
from kkafio.services.content_sources.gofile import download_gofile, is_gofile_url
from kkafio.services.content_sources.mega import download_mega, is_mega_url
from kkafio.services.content_sources.pawchive.access import PawchiveAuth, PawchiveThrottle, pawchive_get
from kkafio.services.content_sources.pawchive.files import download_pawchive_file, pawchive_already_downloaded
from kkafio.services.content_sources.pawchive.parsing import is_pawchive_temp_url, pawchive_content_links, pawchive_deferred_attachments, pawchive_item_key, pawchive_post_files, pawchive_post_page_url, pawchive_temp_item, pawchive_temp_links, pawchive_unique_name

async def _download_pawchive_link(
    link: str, externals: dict, directory: Path, history: dict, skip: bool,
) -> tuple[int, int, int]:
    """Hand a link found in a post to the downloader for its host. `externals`
    maps "gofile" / "mega" / "gdrive" to their HTTP clients."""
    if is_gofile_url(link):
        kind, fn = "gofile", download_gofile
    elif is_mega_url(link):
        kind, fn = "mega", download_mega
    elif is_gdrive_url(link):
        kind, fn = "gdrive", download_gdrive
    else:
        logger.error("DLOAD", f"  Unsupported link in post: {link}")
        return 0, 0, 1
    client = externals.get(kind)
    if client is None:
        logger.error("DLOAD", f"  No {kind} client available for: {link}")
        return 0, 0, 1
    return await fn(client, link, directory, history, skip)


class PawchiveRun:
    """What one pawchive link has seen so far, so a file or link repeated in
    several posts (re-uploads, the same mega folder in every post of a
    series) is only handled once."""

    def __init__(self, externals: dict | None, auth: PawchiveAuth | None = None,
                 throttle: PawchiveThrottle | None = None):
        self.externals = externals or {}
        self.auth = auth or PawchiveAuth()
        self.throttle = throttle or PawchiveThrottle()
        self.files: set[str] = set()
        self.links: set[str] = set()
        self.ok = self.skipped = self.failed = self.filtered = 0
        self.excluded = 0              # files of a type outside PAWCHIVE_EXTENSIONS


async def _pawchive_fetch_temp_links(client, page_url: str,
                                     throttle: PawchiveThrottle) -> dict[str, str]:
    r = await pawchive_get(client, page_url, throttle)
    return pawchive_temp_links(r.text)


async def _pawchive_resolve_deferred(
    client, post: dict, deferred: list[dict], run: PawchiveRun,
) -> list[dict]:
    """Download items for a post's deferred (temporary storage) attachments.

    Their signed t1.pawchive.pw links are only shown on the post's web page.
    If some are missing the page may be showing them to logged-in accounts
    only, so the session cookie is asked for (once per run) and the page read
    again. Attachments that stay unavailable are counted as failed.
    """
    page_url = pawchive_post_page_url(post)
    label = f"post {post.get('id', '?')}"
    if page_url is None:
        run.failed += len(deferred)
        logger.error("DLOAD",
            f"  {label}: {len(deferred)} temporary file(s) skipped — the post has no "
            "service / user / id to find its web page with.")
        return []

    def _missing(links: dict[str, str]) -> list[dict]:
        return [d for d in deferred if str(d.get("name") or "").lower() not in links]

    try:
        links = await _pawchive_fetch_temp_links(client, page_url, run.throttle)
        if _missing(links) and not run.auth.asked and run.auth.ensure(client):
            links = await _pawchive_fetch_temp_links(client, page_url, run.throttle)
    except Exception as exc:
        run.failed += len(deferred)
        logger.error("DLOAD", f"  {label}: could not read {page_url} for its temporary files — {exc}")
        return []

    items: list[dict] = []
    for entry in deferred:
        link = links.get(str(entry.get("name") or "").lower())
        if link:
            items.append(pawchive_temp_item(entry, link))
        else:
            run.failed += 1
            logger.error("DLOAD",
                f"  {label}: temporary file {entry.get('name')!r} is not available "
                "(its link expired" + ("" if run.auth.cookie else ", or a login is needed") + ").")
    return items


async def pawchive_process_posts(
    client, posts: list[dict], directory: Path, history: dict, skip: bool, run: PawchiveRun,
) -> None:
    """Download the files and the linked mega/gofile/gdrive content of posts."""
    jobs: list[tuple[dict, str]] = []
    links: list[str] = []
    taken: set[str] = set()

    for post in posts:
        excluded: list[str] = []
        files = pawchive_post_files(post, excluded)
        deferred = pawchive_deferred_attachments(post, excluded)
        run.excluded += len(excluded)
        if deferred:
            files += await _pawchive_resolve_deferred(client, post, deferred, run)
        files = [f for f in files if pawchive_item_key(f) not in run.files]
        run.files.update(pawchive_item_key(f) for f in files)
        post_links = [l for l in pawchive_content_links(post.get("content"))
                      if l not in run.links]
        run.links.update(post_links)
        if files or post_links:
            logger.info("DLOAD",
                f"  Post {post.get('id', '?')}: {str(post.get('title') or '').strip()[:60]!r} "
                f"— {len(files)} file(s), {len(post_links)} link(s)")
        jobs += [(f, pawchive_unique_name(f, directory, history, taken)) for f in files]
        links += post_links

    if jobs:
        # Files in temporary storage need the account's session cookie. Ask for
        # it here, before any transfer starts: the paste dialog blocks, and
        # must not stall downloads that are already running.
        if any(is_pawchive_temp_url(item["url"]) for item, _ in jobs):
            run.auth.ensure(client)

        async def _one(item: dict, name: str):
            if pawchive_already_downloaded(item, history, skip):
                # No request, so no slot and no pacing: a resumed run must be
                # able to skip thousands of finished files at once.
                return await download_pawchive_file(client, item, directory, name,
                                                     history, skip, run.throttle)
            async with run.throttle.slot():
                return await download_pawchive_file(client, item, directory, name,
                                                     history, skip, run.throttle)

        for coro in asyncio.as_completed([_one(i, n) for i, n in jobs]):
            url, err, status = await coro
            if err:
                run.failed += 1
                logger.error("DLOAD", f"  Failed: {url} — {err}")
            elif status == "skipped":
                run.skipped += 1
            elif status == "filtered":
                run.filtered += 1
            else:
                run.ok += 1

    # One link at a time: mega meters transfer per address and gofile is
    # sensitive about request bursts.
    for link in links:
        logger.info("DLOAD", f"  Link: {link}")
        try:
            ok, skipped, failed = await _download_pawchive_link(
                link, run.externals, directory, history, skip)
        except Exception as exc:
            # One bad link must not stop the rest of the posts.
            logger.error("DLOAD", f"  Failed: {link} — {exc}")
            ok = skipped = 0
            failed = 1
        run.ok += ok
        run.skipped += skipped
        run.failed += failed
