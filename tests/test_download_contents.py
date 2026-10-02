"""Download Contents: the `url | page` pagination formats."""

import asyncio
from pathlib import Path

from kkafio.tasks import download_contents as dc


def test_parse_line_formats():
    u = "https://db.bepis.moe/koikatsu"
    assert dc._parse_line(u) == (u, None, None)
    assert dc._parse_line(f"{u} | all") == (u, 1, None)
    assert dc._parse_line(f"{u} | 5") == (u, 5, 5)          # single page
    assert dc._parse_line(f"{u}|5") == (u, 5, 5)
    assert dc._parse_line(f"{u} | 3 | 7") == (u, 3, 7)
    assert dc._parse_line(f"{u} | 7 | 3") == (u, 7, 3)
    assert dc._parse_line(f"{u} | nope") is None


def test_single_page_replaces_page_in_link():
    link = "https://db.bepis.moe/koikatsu?tag=a&tag=b&page=2&sort=new"
    _, start, end = dc._parse_line(f"{link} | 5")
    assert (start, end) == (5, 5)
    clean = dc._strip_page(link)
    assert "page=" not in clean and clean.count("tag=") == 2
    assert dc._set_page(clean, start).count("page=5") == 1
    assert dc._set_page(link, 5).count("page=") == 1         # replaced, not duplicated


def test_single_page_fetches_only_that_page(tmp_path):
    seen: list[int] = []

    async def fake_pages(client, url, page):
        seen.append(page)
        return [], 0, False

    asyncio.run(dc._download_pages(
        None, "https://db.bepis.moe/koikatsu?page=2", Path(tmp_path), {}, True,
        5, 5, fake_pages, lambda u: "x"))
    assert seen == [5]
