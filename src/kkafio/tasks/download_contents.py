"""
download_contents.py — Download Koikatsu character cards from web sources.

Supports:
  https://db.bepis.moe          — card pages (/view/) and listing pages
  https://koikatsucards.com     — card pages (/contents/) and listing pages
  https://www.pixiv.net         — artworks (/artworks/ID), galleries
                                  (/users/ID, /users/ID/illustrations,
                                  /users/ID/illustrations/TAG) and
                                  searches (/search?q=...); only PNG
                                  images that look like KK cards/scenes are kept
  https://pawchive.pw           — post searches (/posts?q=...), creators
                                  (/<service>/user/ID) and single posts
                                  (/<service>/user/ID/post/ID). Every file and
                                  attachment of a post of type .rar / .zip / .7z /
                                  .zipmod / .png is downloaded (a PNG only if it is
                                  320x180 / 252x352-ratio; other types are skipped), and the mega,
                                  gofile and Google Drive links in the post text
                                  are followed. The listings are paged 50 posts at a
                                  time (?o=), with the usual | N / | N | M / | all
                                  directives; without one only page 1 is read.
                                  Oversized files in Pawchive's temporary storage
                                  (t1.pawchive.pw) need a logged-in account: the
                                  `session` cookie is asked for (like pixiv's
                                  PHPSESSID) only when such a file is met
  https://gofile.io             — folders (/d/ID); every file is downloaded,
                                  subfolders into subdirectories; a password can
                                  be given as ?password=...
  https://drive.google.com      — public files (/file/d/ID, open?id=ID, uc?id=ID) and
                                  folders (/drive/folders/ID; subfolders go into
                                  subdirectories; Google Docs/Sheets are skipped).
                                  Must be shared as "anyone with the link"
  https://mega.nz               — files (/file/ID#KEY) and folders (/folder/ID#KEY);
                                  the #KEY part is required (it decrypts the
                                  names and the data); subfolders go into
                                  subdirectories

Input line formats (| is the separator — safe because URLs never contain |):
  https://...                   plain URL — single card page or one listing page
  https://... | all             listing: download all pages until empty
  https://... | 5               listing: just page 5 (replaces any ?page= in the link)
  https://... | 3 | 7           listing: pages 3 through 7 inclusive
  https://... | 7 | 3           listing: pages 7 down to 3 (reverse order)
  https://www.pixiv.net/... | f
                                pixiv only: also download the other pixiv artworks
                                mentioned in the artwork's description (and the ones
                                mentioned in theirs, and so on). Each artwork is
                                visited only once. Can be combined with a page
                                directive: "url | 1 | 3 | f"
                                (also accepted: fi, follow, follow illustrations)
                                The mega, Google Drive and gofile links in a pixiv
                                description are always downloaded, with or without f.
  # comment                     lines starting with # are ignored

Both card sites use ?page=N for pagination. Pixiv galleries are paged like the
site (48 works per page, ?p=N): "url | 2", "url | 1 | 3" and "url | all" work,
as does a link that already ends in ?p=2; with neither, the whole gallery is read.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from kkafio.core.logger import logger
from kkafio.services.content_sources import history as history_store
from kkafio.services.content_sources.bepis import BEPIS_URL, download_bepis
from kkafio.services.content_sources.gdrive import download_gdrive, is_gdrive_url
from kkafio.services.content_sources.gofile import GOFILE_USER_AGENT, download_gofile, is_gofile_url
from kkafio.services.content_sources.koikatsu import KOIKATSU_URL, download_koikatsu
from kkafio.services.content_sources.mega import download_mega, is_mega_url
from kkafio.services.content_sources.net import make_client
from kkafio.services.content_sources.pawchive import (
    PAWCHIVE_URL,
    PawchiveAuth,
    PawchiveThrottle,
    download_pawchive,
    is_pawchive_url,
)
from kkafio.services.content_sources.pixiv import (
    PIXIV_COOKIE_DOMAIN,
    PIXIV_URL,
    PixivFollowState,
    download_pixiv,
    is_pixiv_url,
)
from kkafio.services.pawchive_session import USER_AGENT as PAWCHIVE_USER_AGENT
from kkafio.tasks.base_task import BaseTask, open_in_default_app

if TYPE_CHECKING:
    import httpx

# One parsed input line: (url, page_start, page_end, follow_illustrations) — see
# _split_follow_flag and _parse_line.
_Link = tuple[str, int | None, int | None, bool]

# The words that switch on "follow illustrations" (case-insensitive, spaces / - / _ are
# interchangeable). "f" is the short spelling used in the README.
_FOLLOW_WORDS = {"follow illustrations", "follow", "followillustrations", "fi", "f"}


def _split_follow_flag(raw: str) -> tuple[str, bool]:
    """Take the "follow illustrations" option out of an input line.

    Returns (the line without it, whether it was there). It may stand anywhere
    after the url, e.g. ``url | follow illustrations`` or ``url | 1 | 3 | follow illustrations``.
    """
    parts = [p.strip() for p in raw.split("|")]
    kept = [parts[0]]
    follow = False
    for part in parts[1:]:
        if " ".join(part.lower().replace("-", " ").replace("_", " ").split()) in _FOLLOW_WORDS:
            follow = True
        else:
            kept.append(part)
    return " | ".join(kept), follow


# ---------------------------------------------------------------------------
# Line parser
# ---------------------------------------------------------------------------

def _parse_line(raw: str) -> tuple[str, int | None, int | None] | None:
    """Parse one input line.

    Returns (url, page_start, page_end) where:
      page_start=None, page_end=None  → plain URL, no pagination
      page_start=N,    page_end=None  → all pages from N until empty
      page_start=N,    page_end=N     → only page N ("url | N"); any ?page= already
                                        in the url is replaced by N
      page_start=N,    page_end=M     → pages N through M (inclusive, supports reverse)

    Returns None if the line is invalid.
    """
    parts = [p.strip() for p in raw.split("|")]
    url   = parts[0].strip()

    if not url.startswith("http"):
        return None

    if len(parts) == 1:
        return url, None, None

    directive = parts[1].strip().lower()

    if directive == "all":
        return url, 1, None

    try:
        p_start = int(parts[1].strip())
        p_end   = int(parts[2].strip()) if len(parts) > 2 else p_start
        return url, p_start, p_end
    except (ValueError, IndexError):
        logger.error("DLOAD",
            f"Could not parse: {raw!r} — expected 'url | all', 'url | N' or 'url | N | M'")
        return None


# ---------------------------------------------------------------------------
# Per-run state
# ---------------------------------------------------------------------------

@dataclass
class _Totals:
    ok: int = 0
    skipped: int = 0
    failed: int = 0

    def add(self, ok: int, skipped: int, failed: int) -> None:
        self.ok += ok
        self.skipped += skipped
        self.failed += failed


@dataclass
class _LinkResult:
    """What happened to one input line, for the report."""
    link: _Link
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    error: str = ""           # why the whole line failed (exception / skipped outright)


REPORT_NAME = "kkafio_download_contents_report.txt"


def _link_label(link: _Link) -> str:
    """The input line as the user would have written it."""
    url, page_start, page_end, follow = link
    label = url
    if page_start is not None:
        if page_end is None:
            label += " | all" if page_start == 1 else f" | {page_start} | …"
        else:
            label += f" | {page_start}" + ("" if page_end == page_start else f" | {page_end}")
    return label + (" | f" if follow else "")


@dataclass
class _Clients:
    """One HTTP client per site: each needs its own cookies / headers."""
    bepis: httpx.AsyncClient
    koikatsu: httpx.AsyncClient
    pixiv: httpx.AsyncClient
    gofile: httpx.AsyncClient
    mega: httpx.AsyncClient
    gdrive: httpx.AsyncClient
    pawchive: httpx.AsyncClient
    # Shared by all pawchive links of a run: the session cookie is asked for at
    # most once, and pacing / concurrency / the 429 cooldown are one budget.
    pawchive_auth: PawchiveAuth
    pawchive_throttle: PawchiveThrottle

    @property
    def pawchive_externals(self) -> dict[str, httpx.AsyncClient]:
        """Clients for the hosts of the links found inside pawchive posts."""
        return {"gofile": self.gofile, "mega": self.mega, "gdrive": self.gdrive}


@dataclass
class _Run:
    """Everything the URLs of one run share."""
    output_dir: Path
    history: dict[str, str]
    clients: _Clients
    kkd_session: str | None
    # Pixiv artworks whose description was read, shared by all lines of the run.
    pixiv_follow: PixivFollowState


# ---------------------------------------------------------------------------
# Main task class
# ---------------------------------------------------------------------------

class DownloadContents(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.download_contents
        self.links           : str  = cfg.get("Links", "")
        self.output_dir_str  : str  = cfg.get("OutputDir", "")
        self.skip_downloaded : bool = cfg.get("SkipDownloaded", True)
        self.open_report     : bool = cfg.get("OpenReport", True)

    def run(self) -> None:
        urls = self._parse_links()
        if not urls:
            logger.error("DLOAD",
                "No valid URLs found. Enter one URL per line in the Download Links field.")
            return

        output_dir = self._output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        self.log_start("DLOAD")
        logger.info("DLOAD", f"Output directory : {output_dir}")
        logger.info("DLOAD", f"Skip downloaded  : {self.skip_downloaded}")
        logger.info("DLOAD", f"URLs to process  : {len(urls)}")

        kkd_session = self._resolve_kkd_session(urls)
        pixiv_session = self._resolve_pixiv_session(urls)

        history = history_store.load_history()
        totals = _Totals()
        results: list[_LinkResult] = []

        try:
            asyncio.run(self._download_all(
                urls, output_dir, history, totals, kkd_session, pixiv_session, results))
        finally:
            # Also when interrupted: the links not reached are listed as such.
            report_path = self._write_report(output_dir, urls, results, totals)
            # Also runs on Stop / Ctrl+C (KeyboardInterrupt) and on errors, so
            # "Skip already downloaded" still works on the next run.
            try:
                history_store.save_history(history)
            except Exception as e:
                logger.error("DLOAD", f"Could not save download history: {e}")

        self.log_done("DLOAD", moved=totals.ok, skipped=totals.skipped,
                      extra=f"{totals.failed} failed, "
                            f"{totals.ok + totals.skipped + totals.failed} total")

        # Like Download Missing Mods: open the report only when something went wrong.
        if self.open_report and report_path is not None and totals.failed:
            logger.info("DLOAD", "Some downloads failed — opening the report")
            open_in_default_app("DLOAD", report_path)

    # -- report --------------------------------------------------------------

    def _write_report(
        self, output_dir: Path, urls: list[_Link], results: list[_LinkResult], totals: _Totals,
    ) -> Path | None:
        """Write kkafio_download_contents_report.txt to the output directory.

        Returns its path, or None if it couldn't be written."""
        not_reached = urls[len(results):]               # links are handled in order
        failed = [r for r in results if r.failed or r.error]
        fine = [r for r in results if not (r.failed or r.error)]

        lines: list[str] = [
            "KKAFIO — Download Contents Report",
            f"Generated        : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Output directory : {output_dir}",
            f"Skip downloaded  : {'Yes' if self.skip_downloaded else 'No'}",
            "",
            "=" * 60,
            "",
            f"Links processed         : {len(results)} of {len(urls)}",
            f"Downloaded successfully : {totals.ok}",
            f"Skipped (already have)  : {totals.skipped}",
            f"Failed                  : {totals.failed}",
            "",
        ]
        if failed:
            lines += [
                "=" * 60,
                f"Links with failures ({len(failed)}):",
                "Check your internet connection and try again; a link can also be private, "
                "deleted or",
                "need a login. The log lists every file that failed.",
                "",
            ]
            for r in failed:
                lines.append(f"  ✗ {_link_label(r.link)}")
                lines.append("    " + (r.error or
                    f"{r.failed} failed, {r.ok} downloaded, {r.skipped} skipped"))
            lines.append("")
        if not_reached:
            lines += [
                "=" * 60,
                f"Links not processed ({len(not_reached)}) — the run was stopped:",
                "",
            ]
            lines += [f"  … {_link_label(u)}" for u in not_reached]
            lines.append("")
        if fine:
            lines += ["=" * 60, f"Links completed ({len(fine)}):", ""]
            for r in fine:
                mark = "+" if r.ok else "~"
                lines.append(f"  {mark} {_link_label(r.link)}")
                lines.append(f"    {r.ok} downloaded, {r.skipped} skipped")
            lines.append("")

        report_path = output_dir / REPORT_NAME
        try:
            report_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("DLOAD", f"Report saved: {report_path}")
            return report_path
        except Exception as e:
            logger.warning("DLOAD", f"Could not write report: {e}")
            return None

    # -- setup ---------------------------------------------------------------

    def _parse_links(self) -> list[_Link]:
        """The valid lines of the Links field (blank lines and # comments dropped)."""
        urls: list[_Link] = []
        for raw_line in self.links.splitlines():
            raw_line = raw_line.strip()
            if not raw_line or raw_line.startswith("#"):
                continue
            raw_line, follow = _split_follow_flag(raw_line)
            parsed = _parse_line(raw_line)
            if parsed:
                urls.append((*parsed, follow))
        return urls

    def _output_dir(self) -> Path:
        return (
            Path(self.output_dir_str) if self.output_dir_str
            else Path(self.config.config_data["Core"].get(
                "DownloadsPath",
                Path.home() / "Downloads"
            ))
        )

    @staticmethod
    def _resolve_kkd_session(urls: list[_Link]) -> str | None:
        """The koikatsucards.com ``kkd_session`` cookie — prompts if missing or
        expired. None when no link needs it or the prompt is cancelled."""
        if not any(KOIKATSU_URL in url_str for url_str, *_ in urls):
            return None
        import kkafio.services.kkd_session as _kkd
        kkd_session = _kkd.get_or_prompt()
        if not kkd_session:
            logger.warning("DLOAD",
                "No kkd_session — koikatsucards.com downloads will be skipped.")
        return kkd_session

    @staticmethod
    def _resolve_pixiv_session(urls: list[_Link]) -> str | None:
        """The pixiv ``PHPSESSID`` — optional: without it only public artworks
        are reachable, so cancelling the dialog is not fatal."""
        if not any(is_pixiv_url(url_str) for url_str, *_ in urls):
            return None
        import kkafio.services.pixiv_session as _pxv
        return _pxv.get_or_prompt()

    # -- the run -------------------------------------------------------------

    @asynccontextmanager
    async def _open_clients(self, kkd_session: str | None, pixiv_session: str | None):
        """Open one client per site (closed again, in reverse order, on exit)."""
        kkd_cookies = {"kkd_session": kkd_session} if kkd_session else None
        pixiv_cookies = {"PHPSESSID": pixiv_session} if pixiv_session else None

        async with AsyncExitStack() as stack:
            enter = stack.enter_async_context
            # The PHPSESSID is scoped to .pixiv.net, so it is never sent on to
            # i.pximg.net (the image host) or anywhere else; the image host only
            # needs the pixiv Referer, which the client sends on every request.
            yield _Clients(
                bepis=await enter(make_client()),
                koikatsu=await enter(make_client(
                    cookies=kkd_cookies, cookie_domain="koikatsucards.com")),
                pixiv=await enter(make_client(
                    cookies=pixiv_cookies, cookie_domain=PIXIV_COOKIE_DOMAIN,
                    extra_headers={"Referer": PIXIV_URL + "/"})),
                gofile=await enter(make_client(
                    extra_headers={"User-Agent": GOFILE_USER_AGENT})),
                mega=await enter(make_client()),
                gdrive=await enter(make_client()),
                pawchive=await enter(make_client(
                    extra_headers={"User-Agent": PAWCHIVE_USER_AGENT,
                                   "Referer": PAWCHIVE_URL + "/",
                                   "Accept": "*/*"},
                    http2=False)),
                # Asks for the pawchive.pw session cookie, but only if a file on
                # t1.pawchive.pw turns up; shared so it is asked for once per run.
                pawchive_auth=PawchiveAuth(),
                # Pacing, concurrency and the 429 cooldown are one budget for
                # the whole run, so they are shared the same way.
                pawchive_throttle=PawchiveThrottle(),
            )

    async def _download_all(
        self, urls: list[_Link], output_dir: Path, history: dict[str, str],
        totals: _Totals, kkd_session: str | None, pixiv_session: str | None,
        results: list[_LinkResult] | None = None,
    ) -> None:
        async with self._open_clients(kkd_session, pixiv_session) as clients:
            run = _Run(output_dir, history, clients, kkd_session, PixivFollowState())
            for link in urls:
                url_str, page_start, page_end, follow_illustrations = link
                entry = _LinkResult(link)
                try:
                    result = await self._download_url(
                        run, url_str, page_start, page_end, follow_illustrations)
                    if result is None:           # skipped outright: nothing to checkpoint
                        totals.add(0, 0, 1)
                        entry.failed = 1
                        entry.error = "Skipped — no session / unsupported link (see the log)"
                        if results is not None:
                            results.append(entry)
                        continue
                    entry.ok, entry.skipped, entry.failed = result
                except Exception as exc:
                    # One bad URL must not abort the remaining ones.
                    logger.error("DLOAD", f"Failed to process {url_str}: {exc}")
                    entry.failed += 1
                    entry.error = f"Error: {exc}"
                totals.add(entry.ok, entry.skipped, entry.failed)
                if results is not None:
                    results.append(entry)
                # Persist after every URL so an interrupted run keeps
                # everything downloaded so far.
                history_store.save_history(history)

    async def _download_url(
        self, run: _Run, url_str: str, page_start: int | None, page_end: int | None,
        follow_illustrations: bool = False,
    ) -> tuple[int, int, int] | None:
        """Hand one input line to the downloader of its site.

        Returns (succeeded, skipped, failed), or None for a link that was
        skipped without being attempted (counted as one failure).
        """
        clients, out, history, skip = run.clients, run.output_dir, run.history, self.skip_downloaded

        # Matched by host first: a pawchive search may carry
        # another site's address in its query string.
        if is_pawchive_url(url_str):
            return await download_pawchive(
                clients.pawchive, url_str, out, history, skip,
                page_start=page_start, page_end=page_end,
                externals=clients.pawchive_externals,
                auth=clients.pawchive_auth, throttle=clients.pawchive_throttle)

        if BEPIS_URL in url_str:
            return await download_bepis(
                clients.bepis, url_str, out, history, skip,
                page_start=page_start, page_end=page_end)

        if KOIKATSU_URL in url_str:
            if not run.kkd_session:
                logger.skipped("DLOAD",
                    f"Skipping koikatsucards.com URL (no session): {url_str}")
                return None
            return await download_koikatsu(
                clients.koikatsu, url_str, out, history, skip,
                page_start=page_start, page_end=page_end)

        if is_pixiv_url(url_str):
            return await download_pixiv(
                clients.pixiv, url_str, out, history, skip,
                page_start=page_start, page_end=page_end,
                follow_illustrations=follow_illustrations, externals=clients.pawchive_externals,
                follow_state=run.pixiv_follow)

        if follow_illustrations:
            logger.info("DLOAD", "  'follow illustrations' only applies to pixiv links — ignored.")

        if is_gofile_url(url_str):
            self._note_page_directive_ignored("Gofile", page_start)
            return await download_gofile(clients.gofile, url_str, out, history, skip)

        if is_mega_url(url_str):
            self._note_page_directive_ignored("Mega", page_start)
            return await download_mega(clients.mega, url_str, out, history, skip)

        if is_gdrive_url(url_str):
            self._note_page_directive_ignored("Google Drive", page_start)
            return await download_gdrive(clients.gdrive, url_str, out, history, skip)

        logger.error("DLOAD",
            f"Unsupported URL (must be db.bepis.moe, koikatsucards.com, pixiv.net, pawchive.pw, gofile.io, mega.nz or drive.google.com): {url_str}")
        return 0, 0, 1

    @staticmethod
    def _note_page_directive_ignored(site: str, page_start: int | None) -> None:
        if page_start is not None:
            logger.info("DLOAD", f"  Page directive is ignored for {site} links.")
