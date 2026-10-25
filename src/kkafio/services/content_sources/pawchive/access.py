"""How a run talks to pawchive.pw: pacing, rate-limit cooldown, session cookie.

``PawchiveThrottle`` and ``PawchiveAuth`` are shared by every request of a run
(all workers, all links), because the limit and the login belong to the site,
not to one download.
"""

from __future__ import annotations

import asyncio
import time

from kkafio.core.logger import logger
from kkafio.services.content_sources.net import get_with_retry, is_rate_limited, retry_after_seconds
from kkafio.services.content_sources.pawchive.parsing import PAWCHIVE_COOKIE_DOMAIN

PAWCHIVE_PARALLEL  = 4                          # most files downloaded at once (the ceiling)
PAWCHIVE_START_PARALLEL = 2                     # ... a run starts with this many and works up
PAWCHIVE_FILE_DELAY = (0.25, 0.40)              # random gap (s) between two file downloads starting
PAWCHIVE_PAGE_DELAY = (0.35, 0.55)              # ... and between two API / page requests
PAWCHIVE_COOLDOWN  = 14.0                       # s every request waits after a 429 (x streak, or Retry-After)
PAWCHIVE_COOLDOWN_CAP = 120.0
PAWCHIVE_MAX_RATE_LIMITED = 6                   # consecutive 429s on one request before giving up on it


class PawchiveThrottle:
    """Keeps a run polite towards pawchive.pw and reacts to HTTP 429.

    Shared by every request of the run (all workers, all links), because the
    limit belongs to the site, not to one download:

      * pacing   -- a random gap between two file downloads starting
                    (PAWCHIVE_FILE_DELAY) and between two API / page requests
                    (PAWCHIVE_PAGE_DELAY), so nothing is ever sent in a burst;
      * adaptive concurrency -- starts at PAWCHIVE_START_PARALLEL files at once
                    and adds one after every few clean files, up to a ceiling;
      * cooldown -- after a 429 *everything* waits (not only the request that
                    was refused): the Retry-After, or PAWCHIVE_COOLDOWN
                    seconds, longer the more 429s in a row. The first 429 of a
                    burst also lowers the ceiling and the current concurrency,
                    and after 50 clean files the ceiling is probed upwards again.

    Plain monotonic timestamps and polling, no asyncio primitives, so it works
    on any event loop."""

    SCALE_UP_EVERY = 3         # clean files before one more worker is allowed
    PROBE_EVERY = 50           # clean files before a lowered ceiling is raised by one

    def __init__(self, max_parallel: int | None = None, start_parallel: int | None = None):
        self.max_parallel = max(1, max_parallel or PAWCHIVE_PARALLEL)
        self.ceiling = self.max_parallel
        self.limit = max(1, min(start_parallel or PAWCHIVE_START_PARALLEL, self.ceiling))
        self.active = 0
        self.clean = 0
        self.cooldown_until = 0.0
        self._next_start = {"file": 0.0, "api": 0.0}

    # -- waiting -----------------------------------------------------------------
    async def gate(self) -> None:
        """Wait out the cooldown (it may be extended while waiting)."""
        while (left := self.cooldown_until - time.monotonic()) > 0:
            await asyncio.sleep(min(left, 1.0))

    async def pace(self, kind: str) -> None:
        """Reserve the next start time of a "file" or "api" request and wait for it."""
        import random
        low, high = PAWCHIVE_FILE_DELAY if kind == "file" else PAWCHIVE_PAGE_DELAY
        now = time.monotonic()
        start = max(now, self._next_start[kind])
        self._next_start[kind] = start + random.uniform(low, high)
        if start > now:
            await asyncio.sleep(start - now)

    def slot(self):
        """``async with throttle.slot():`` around one file download: waits for a
        free place under the current concurrency limit, then for the cooldown
        and the pacing gap."""
        throttle = self

        class _Slot:
            async def __aenter__(self):
                while throttle.active >= throttle.limit:
                    await asyncio.sleep(0.05)
                throttle.active += 1               # no await between check and take
                try:
                    await throttle.gate()
                    await throttle.pace("file")
                except BaseException:
                    throttle.active -= 1
                    raise

            async def __aexit__(self, *exc):
                throttle.active -= 1
                return False
        return _Slot()

    # -- feedback ----------------------------------------------------------------
    def on_success(self) -> None:
        """A file went through: after a few in a row let one more worker run."""
        self.clean += 1
        if time.monotonic() < self.cooldown_until:
            return
        if self.limit < self.ceiling and self.clean % self.SCALE_UP_EVERY == 0:
            self.limit += 1
            logger.info("DLOAD", f"  pawchive: connection stable — {self.limit} files at once.")
        elif self.clean >= self.PROBE_EVERY and self.ceiling < self.max_parallel:
            self.ceiling += 1
            self.clean = 0
            logger.info("DLOAD", f"  pawchive: stable for a while — allowing up to {self.ceiling} files at once.")

    def on_rate_limited(self, retry_after: float | None = None, streak: int = 1) -> float:
        """A 429 came back. Starts (or extends) the shared cooldown and returns
        how long everything now waits. Only the first 429 of a burst lowers the
        concurrency: the other workers refused at the same moment must not each
        take it down again."""
        now = time.monotonic()
        wait = min(PAWCHIVE_COOLDOWN_CAP,
                   max(retry_after or 0.0, PAWCHIVE_COOLDOWN * max(1, min(streak, 4))))
        first_of_burst = now >= self.cooldown_until
        self.cooldown_until = max(self.cooldown_until, now + wait)
        if first_of_burst:
            before = self.limit
            self.ceiling = max(1, min(self.ceiling, self.limit - 1))
            self.limit = max(1, self.ceiling - 1)
            self.clean = 0
            logger.warning("DLOAD",
                f"  pawchive rate limit (HTTP 429): pausing {wait:.0f}s, files at once "
                f"{before} -> {self.limit} (stable ceiling {self.ceiling}).")
        else:
            logger.warning("DLOAD",
                f"  pawchive rate limit (HTTP 429) again: pause now {self.cooldown_until - now:.0f}s.")
        return wait


async def pawchive_get(client, url: str, throttle: PawchiveThrottle):
    """GET an API / page URL of pawchive.pw: paced, held back by the shared
    cooldown, and a 429 starts that cooldown and is retried (up to
    PAWCHIVE_MAX_RATE_LIMITED times in a row) rather than failing the page."""
    limited = 0
    while True:
        await throttle.gate()
        await throttle.pace("api")
        try:
            return await get_with_retry(client, url, retry_429=False)
        except Exception as exc:
            if not is_rate_limited(exc):
                raise
            limited += 1
            if limited > PAWCHIVE_MAX_RATE_LIMITED:
                raise
            throttle.on_rate_limited(retry_after_seconds(exc), limited)


class PawchiveAuth:
    """The pawchive.pw ``session`` cookie, asked for like pixiv's PHPSESSID and
    koikatsucards.com's kkd_session -- but lazily: only when a file in Pawchive's
    temporary storage (t1.pawchive.pw) is about to be downloaded, and at most
    once per run. Nothing else on pawchive.pw needs a login.

    `resolver` returns the cookie value (or None to go on without); by default
    it is pawchive_session.get_or_prompt, which opens the login page and shows
    the paste dialog. The dialog blocks, so it is only ever shown while no
    transfer is in flight (see pawchive_process_posts)."""

    def __init__(self, resolver=None):
        self._resolver = resolver
        self.asked = False
        self.cookie: str | None = None

    def ensure(self, client) -> bool:
        """Ask once, then put the cookie on `client` (scoped to pawchive.pw and
        its subdomains). True when a session is available."""
        if not self.asked:
            self.asked = True
            resolver = self._resolver
            if resolver is None:
                from kkafio.services import pawchive_session
                resolver = pawchive_session.get_or_prompt
            self.cookie = resolver() or None
        if self.cookie:
            client.cookies.set("session", self.cookie, domain=PAWCHIVE_COOKIE_DOMAIN)
        return bool(self.cookie)
