"""Download Contents: catbox.moe — file links, Litterbox links and albums."""

import asyncio
from pathlib import Path

import httpx
import pytest

from kkafio.services.content_sources import catbox, history as history_store


@pytest.fixture(autouse=True)
def _no_real_history(monkeypatch):
    monkeypatch.setattr(history_store, "save_history", lambda h: None)


def test_catbox_url_parsing():
    P = catbox.parse_catbox_url
    assert P("https://files.catbox.moe/abc123.png") == ("file", "https://files.catbox.moe/abc123.png")
    assert P("http://files.catbox.moe/abc123.png?x=1") == ("file", "https://files.catbox.moe/abc123.png")
    assert P("https://litter.catbox.moe/q1w2e3.zip") == ("file", "https://litter.catbox.moe/q1w2e3.zip")
    assert P("https://catbox.moe/c/abc123") == ("album", "abc123")
    assert P("https://www.catbox.moe/c/abc123/") == ("album", "abc123")
    assert P("https://catbox.moe/") is None and P("https://catbox.moe/user/api.php") is None
    assert P("https://files.catbox.moe/") is None and P("https://files.catbox.moe/a/b.png") is None
    assert P("https://catbox.moe/c/") is None


def test_catbox_host_matching_is_exact():
    assert catbox.is_catbox_url("https://catbox.moe/c/x")
    assert catbox.is_catbox_url("https://files.catbox.moe/x.png")
    assert catbox.is_catbox_url("https://litter.catbox.moe/x.png")
    assert not catbox.is_catbox_url("https://notcatbox.moe/c/x")
    assert not catbox.is_catbox_url("https://example.com/?u=catbox.moe")
    assert not catbox.is_catbox_url("https://catbox.moe.evil.com/c/x")


def test_album_page_files_are_found_once_and_in_order():
    html = (
        '<a href="https://files.catbox.moe/b2.png"><img src="//files.catbox.moe/b2.png"></a>'
        '<a href="//files.catbox.moe/a1.zip">a</a>'
        '<a href="http://litter.catbox.moe/c3.7z">c</a>'
        '<a href="https://example.com/files.catbox.moe/not-a-file">x</a>'
        '<a href="https://files.catbox.moe/">root</a>')
    assert catbox.catbox_page_files(html) == [
        "https://files.catbox.moe/b2.png",
        "https://files.catbox.moe/a1.zip",
        "https://litter.catbox.moe/c3.7z",
    ]
    assert catbox.catbox_page_files("<html>no files</html>") == []


def _client(files: dict[str, bytes], album_html: dict[str, str], seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "catbox.moe" and req.url.path.startswith("/c/"):
            html = album_html.get(req.url.path[3:])
            return httpx.Response(200 if html is not None else 404, text=html or "gone")
        body = files.get(req.url.path.lstrip("/"))
        if req.url.host in catbox.CATBOX_FILE_HOSTS and body is not None:
            return httpx.Response(200, content=body, headers={"content-length": str(len(body))})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _run(client, url, tmp_path, history=None, skip=True):
    async def go():
        try:
            return await catbox.download_catbox(client, url, Path(tmp_path), {} if history is None else history, skip)
        finally:
            await client.aclose()
    return asyncio.run(go())


def test_a_file_link_is_downloaded_under_its_own_name(tmp_path):
    seen, history = [], {}
    client = _client({"abc123.png": b"PNGDATA"}, {}, seen)
    assert _run(client, "https://files.catbox.moe/abc123.png", tmp_path, history) == (1, 0, 0)
    assert (tmp_path / "abc123.png").read_bytes() == b"PNGDATA"
    assert history["https://files.catbox.moe/abc123.png"] == str(tmp_path / "abc123.png")
    assert not list(tmp_path.glob("*.part"))


def test_an_album_downloads_every_file(tmp_path):
    seen = []
    page = ('<a href="https://files.catbox.moe/a1.png">1</a><a href="//files.catbox.moe/b2.zip">2</a>'
            '<img src="https://files.catbox.moe/a1.png">')
    client = _client({"a1.png": b"one", "b2.zip": b"two"}, {"alb": page}, seen)
    assert _run(client, "https://catbox.moe/c/alb", tmp_path) == (2, 0, 0)
    assert (tmp_path / "a1.png").read_bytes() == b"one"
    assert (tmp_path / "b2.zip").read_bytes() == b"two"
    assert sum(u.endswith("/a1.png") for u in seen) == 1           # a repeated link is fetched once


def test_already_downloaded_files_are_skipped(tmp_path):
    url = "https://files.catbox.moe/abc123.png"
    seen = []
    client = _client({"abc123.png": b"x"}, {}, seen)
    assert _run(client, url, tmp_path, {url: str(tmp_path / "abc123.png")}) == (0, 1, 0)
    assert seen == []


def test_one_missing_file_in_an_album_counts_as_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("kkafio.services.content_sources.transfer.retry_delay", lambda exc, attempt: 0.0)
    page = '<a href="https://files.catbox.moe/ok.png">1</a><a href="https://files.catbox.moe/gone.png">2</a>'
    client = _client({"ok.png": b"fine"}, {"alb": page}, [])
    assert _run(client, "https://catbox.moe/c/alb", tmp_path) == (1, 0, 1)
    assert (tmp_path / "ok.png").exists() and not (tmp_path / "gone.png").exists()


def test_an_empty_album_is_one_failure(tmp_path):
    client = _client({}, {"alb": "<html>nothing here</html>"}, [])
    assert _run(client, "https://catbox.moe/c/alb", tmp_path) == (0, 0, 1)


def test_an_unsupported_catbox_link_is_one_failure_without_a_request(tmp_path):
    seen = []
    client = _client({}, {}, seen)
    assert _run(client, "https://catbox.moe/user/profile", tmp_path) == (0, 0, 1)
    assert seen == []


def test_pixiv_hands_catbox_links_to_the_catbox_client(tmp_path, monkeypatch):
    from kkafio.services.content_sources import pixiv

    calls = []

    async def fake(client, url, directory, history, skip):
        calls.append((client, url))
        return 2, 0, 0

    monkeypatch.setattr(pixiv, "download_catbox", fake)
    run = lambda link, ext: asyncio.run(pixiv._download_external_link(link, ext, Path(tmp_path), {}, True))
    assert run("https://catbox.moe/c/alb123", {"catbox": "CATBOX"}) == (2, 0, 0)
    assert calls == [("CATBOX", "https://catbox.moe/c/alb123")]
    assert run("https://catbox.moe/c/alb123", {}) == (0, 0, 1)            # no client: one failure, no call
    assert len(calls) == 1
