"""Download Contents: pixeldrain.com — files and lists."""

import asyncio
from pathlib import Path

import httpx
import pytest

from kkafio.services.content_sources import history as history_store, pixeldrain


@pytest.fixture(autouse=True)
def _no_real_history(monkeypatch):
    monkeypatch.setattr(history_store, "save_history", lambda h: None)


def test_url_parsing():
    P = pixeldrain.parse_pixeldrain_url
    assert P("https://pixeldrain.com/u/AbC12345") == ("file", "AbC12345")
    assert P("https://pixeldrain.com/u/AbC12345?x=1") == ("file", "AbC12345")
    assert P("https://pixeldrain.net/api/file/AbC12345") == ("file", "AbC12345")
    assert P("https://www.pixeldrain.dev/l/Li5t1234") == ("list", "Li5t1234")
    assert P("https://pixeldrain.com/l/Li5t1234#item=2") == ("list", "Li5t1234")
    assert P("https://pixeldrain.com/api/list/Li5t1234") == ("list", "Li5t1234")
    assert P("https://pixeldrain.com/api/file/5YLNRZm4/info/zip/Sunglasses%20studio%20acc_F.zipmod") == ("file", "5YLNRZm4")
    assert P("https://pixeldrain.com/") is None and P("https://pixeldrain.com/user/files") is None
    assert P("https://pixeldrain.com/u/") is None and P("https://example.com/u/AbC12345") is None


def test_host_matching_is_exact():
    assert pixeldrain.is_pixeldrain_url("https://pixeldrain.com/u/x")
    assert pixeldrain.is_pixeldrain_url("https://www.pixeldrain.net/u/x")
    assert not pixeldrain.is_pixeldrain_url("https://notpixeldrain.com/u/x")
    assert not pixeldrain.is_pixeldrain_url("https://pixeldrain.com.evil.com/u/x")
    assert not pixeldrain.is_pixeldrain_url("https://example.com/?u=pixeldrain.com")


def _client(files: dict[str, tuple[str, bytes]], lists: dict[str, dict], seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        parts = req.url.path.split("/")
        if req.url.path.startswith("/api/list/"):
            body = lists.get(parts[3])
            return httpx.Response(200 if body else 404, json=body or {"success": False, "value": "not_found"})
        if req.url.path.startswith("/api/file/") and parts[3] in files:
            name, data = files[parts[3]]
            return httpx.Response(200, content=data, headers={
                "content-length": str(len(data)), "content-disposition": f'attachment; filename="{name}"'})
        return httpx.Response(404, json={"success": False, "value": "file_not_found"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _run(client, url, tmp_path, history=None, skip=True):
    async def go():
        try:
            return await pixeldrain.download_pixeldrain(client, url, Path(tmp_path), {} if history is None else history, skip)
        finally:
            await client.aclose()
    return asyncio.run(go())


def test_a_file_link_is_downloaded_under_the_name_pixeldrain_sends(tmp_path):
    history = {}
    client = _client({"AbC12345": ("mod.zipmod", b"ZIP")}, {}, [])
    assert _run(client, "https://pixeldrain.com/u/AbC12345", tmp_path, history) == (1, 0, 0)
    assert (tmp_path / "mod.zipmod").read_bytes() == b"ZIP"
    assert list(history) == ["https://pixeldrain.com/api/file/AbC12345?download"]
    assert not list(tmp_path.glob("*.part"))


def test_a_list_downloads_every_file(tmp_path):
    lists = {"Li5t1234": {"success": True, "title": "pack", "files": [
        {"id": "f1111111", "name": "a.zip"}, {"id": "f2222222", "name": "b.zip"}]}}
    files = {"f1111111": ("a.zip", b"one"), "f2222222": ("b.zip", b"two")}
    assert _run(_client(files, lists, []), "https://pixeldrain.com/l/Li5t1234", tmp_path) == (2, 0, 0)
    assert (tmp_path / "a.zip").read_bytes() == b"one" and (tmp_path / "b.zip").read_bytes() == b"two"


def test_already_downloaded_files_are_skipped_without_a_request(tmp_path):
    seen = []
    client = _client({"AbC12345": ("mod.zip", b"x")}, {}, seen)
    done = {"https://pixeldrain.com/api/file/AbC12345?download": str(tmp_path / "mod.zip")}
    assert _run(client, "https://pixeldrain.com/u/AbC12345", tmp_path, done) == (0, 1, 0)
    assert seen == []


def test_a_missing_file_is_one_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("kkafio.services.content_sources.transfer.retry_delay", lambda exc, attempt: 0.0)
    assert _run(_client({}, {}, []), "https://pixeldrain.com/u/Gone1234", tmp_path) == (0, 0, 1)


def test_a_missing_or_empty_list_is_one_failure(tmp_path):
    assert _run(_client({}, {}, []), "https://pixeldrain.com/l/Nope1234", tmp_path) == (0, 0, 1)
    empty = {"Em9ty123": {"success": True, "files": []}}
    assert _run(_client({}, empty, []), "https://pixeldrain.com/l/Em9ty123", tmp_path) == (0, 0, 1)


def test_an_unsupported_link_is_one_failure_without_a_request(tmp_path):
    seen = []
    assert _run(_client({}, {}, seen), "https://pixeldrain.com/user/files", tmp_path) == (0, 0, 1)
    assert seen == []


def test_a_file_inside_an_archive_downloads_only_that_file(tmp_path):
    url = "https://pixeldrain.com/api/file/5YLNRZm4/info/zip/Sunglasses%20studio%20acc_F.zipmod"
    assert pixeldrain.parse_pixeldrain_zip_member(url) == "Sunglasses studio acc_F.zipmod"
    seen, history = [], {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.raw_path.decode())
        if req.url.path == "/api/file/5YLNRZm4/info/zip/Sunglasses studio acc_F.zipmod":
            return httpx.Response(200, content=b"MOD", headers={"content-length": "3"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    assert _run(client, url, tmp_path, history) == (1, 0, 0)
    assert (tmp_path / "Sunglasses studio acc_F.zipmod").read_bytes() == b"MOD"
    assert seen == ["/api/file/5YLNRZm4/info/zip/Sunglasses%20studio%20acc_F.zipmod"]   # never the whole archive
    assert list(history) == [url]
    assert _run(_client({}, {}, []), url, tmp_path, history) == (0, 1, 0)
