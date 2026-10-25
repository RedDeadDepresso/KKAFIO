"""pawchive.pw — post searches, creators and single posts.

pawchive.pw archives creator posts (patreon, fanbox, ...) and exposes a JSON
API next to the website. The link pasted by the user is turned into the
matching API endpoint:

  https://pawchive.pw/posts[?q=...]                    -> /api/v1/posts[?q=...]
  https://pawchive.pw/<service>/user/<id>              -> /api/v1/<service>/user/<id>
  https://pawchive.pw/<service>/user/<id>/post/<post>  -> /api/v1/<service>/user/<id>/post/<post>

The two listings return a JSON list of posts, 50 per page, paged with the
"o" offset query parameter (page N is o=(N-1)*50). A single post comes back
as one object. Every post is processed the same way:

  * its "file" and "attachments" are downloaded from the public file host
    (https://file.pawchive.pw/data<path>?f=<name>), falling back to the storage
    node that holds them (node 2 -> https://n2.pawchive.pw/data<path>?f=<name>)
    when that fails -- the node host on its own tends to answer 403. A PNG is
    only kept when it is 320x180 or has the 252x352 ratio, like pixiv images --
    the size is read from the first bytes, so non-matching PNGs are aborted
    after the first chunk;
  * requests are paced and rate-limited politely (see PawchiveThrottle): a
    random gap between requests, 2 files at once growing to at most 4, and on
    HTTP 429 a cooldown that every worker waits out (Retry-After honoured), a
    lower concurrency, and the refused request retried afterwards;
  * attachments flagged "deferred" (oversized files in Pawchive's temporary
    storage) have no path: their signed t1.pawchive.pw links are read from the
    post's web page, and downloading them needs a logged-in account, so the
    `session` cookie is asked for -- only then -- like pixiv's PHPSESSID;
  * its "content" is searched for mega.nz, gofile.io and Google Drive links,
    which are handed to the matching downloader with the link unchanged
    (query parameters such as ?password= are kept).
"""

from __future__ import annotations

from kkafio.services.content_sources.pawchive.access import PawchiveAuth, PawchiveThrottle
from kkafio.services.content_sources.pawchive.downloader import download_pawchive
from kkafio.services.content_sources.pawchive.parsing import PAWCHIVE_URL, is_pawchive_url

__all__ = [
    "PAWCHIVE_URL",
    "PawchiveAuth",
    "PawchiveThrottle",
    "download_pawchive",
    "is_pawchive_url",
]
