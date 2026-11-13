"""Download Contents: mediafire.com — file pages, folders and direct links."""

import asyncio
import base64
from pathlib import Path

import httpx
import pytest

from kkafio.services.content_sources import mediafire

from kkafio.services.content_sources.shared import history as history_store
@pytest.fixture(autouse=True)
def _no_real_history(monkeypatch):
    monkeypatch.setattr(history_store, "save_history", lambda h: None)


def test_url_parsing():
    P = mediafire.parse_mediafire_url
    page = "https://www.mediafire.com/file/abc123XYZ/file"
    assert P("https://www.mediafire.com/file/abc123XYZ/my_mod.zip/file") == ("file", page, "my_mod.zip")
    assert P("https://mediafire.com/file/abc123XYZ/my_mod.zip") == ("file", page, "my_mod.zip")
    assert P("https://www.mediafire.com/file/abc123XYZ") == ("file", page, "")
    assert P("https://www.mediafire.com/view/abc123XYZ/x.zip") == ("file", page, "x.zip")
    assert P("https://www.mediafire.com/download/abc123XYZ") == ("file", page, "")
    assert P("https://www.mediafire.com/?abc123XYZ") == ("file", page, "")
    assert P("https://www.mediafire.com/folder/fld123abc/Mods") == ("folder", "fld123abc")
    assert P("https://www.mediafire.com/folder/fld123abc") == ("folder", "fld123abc")
    assert P("https://download1234.mediafire.com/tok/key/a.zip") == ("direct", "https://download1234.mediafire.com/tok/key/a.zip")
    assert P("https://www.mediafire.com/") is None and P("https://www.mediafire.com/myfiles.php") is None
    assert P("https://download1234.mediafire.com/") is None
    assert P("https://example.com/file/abc123XYZ") is None


def test_host_matching_is_exact():
    assert mediafire.is_mediafire_url("https://www.mediafire.com/file/x")
    assert mediafire.is_mediafire_url("https://download1.mediafire.com/x")
    assert not mediafire.is_mediafire_url("https://notmediafire.com/file/x")
    assert not mediafire.is_mediafire_url("https://mediafire.com.evil.com/file/x")
    assert not mediafire.is_mediafire_url("https://example.com/?u=mediafire.com")


def test_direct_link_is_read_from_the_download_button():
    direct = "https://download1234.mediafire.com/tok/key/mod.zip"
    plain = f'<a class="input popsok" id="downloadButton" href="{direct}?a=1&amp;b=2">Download</a>'
    assert mediafire.mediafire_direct_link(plain) == direct + "?a=1&b=2"
    scrambled = base64.b64encode(direct.encode()).decode().rstrip("=")
    new = f'<a id="downloadButton" href="" data-scrambled-url="{scrambled}">Download</a>'
    assert mediafire.mediafire_direct_link(new) == direct
    assert mediafire.mediafire_direct_link(f"<script>var u='{direct}';</script>") == direct
    assert mediafire.mediafire_direct_link("<html>This file has been removed</html>") is None


def _client(pages: dict[str, str], files: dict[str, bytes], folders: dict, seen: list):
    """`pages`: file key -> download link; `files`: path -> body; `folders`: key -> API content."""
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        host, path = req.url.host, req.url.path
        if host.endswith("mediafire.com") and path.startswith("/api/1.5/folder/get_content.php"):
            key = req.url.params["folder_key"]
            content = folders.get(key, {}).get(req.url.params["content_type"], {})
            if key not in folders:
                return httpx.Response(200, json={"response": {"result": "Error", "message": "Invalid folder"}})
            return httpx.Response(200, json={"response": {"result": "Success", "folder_content": content}})
        if host in ("www.mediafire.com", "mediafire.com") and path.startswith("/file/"):
            link = pages.get(path.split("/")[2])
            if link is None:
                return httpx.Response(200, text="<html>deleted</html>")
            return httpx.Response(200, text=f'<a id="downloadButton" href="{link}">Download</a>')
        if host.startswith("download"):
            body = files.get(path)
            if body is not None:
                return httpx.Response(200, content=body, headers={"content-length": str(len(body))})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _run(client, url, tmp_path, history=None, skip=True):
    async def go():
        try:
            return await mediafire.download_mediafire(client, url, Path(tmp_path), {} if history is None else history, skip)
        finally:
            await client.aclose()
    return asyncio.run(go())


def test_a_file_page_is_resolved_and_downloaded_under_the_name_of_the_link(tmp_path):
    seen, history = [], {}
    client = _client({"key12345": "https://download1.mediafire.com/t/key12345/mod.zipmod"},
                     {"/t/key12345/mod.zipmod": b"ZIP"}, {}, seen)
    page = "https://www.mediafire.com/file/key12345/file"
    assert _run(client, "https://www.mediafire.com/file/key12345/mod.zipmod/file", tmp_path, history) == (1, 0, 0)
    assert (tmp_path / "mod.zipmod").read_bytes() == b"ZIP"
    assert history == {page: str(tmp_path / "mod.zipmod")}          # keyed by the page, not the temporary link
    assert not list(tmp_path.glob("*.part"))


def test_a_direct_link_is_downloaded_as_it_is(tmp_path):
    seen = []
    client = _client({}, {"/t/k/a.zip": b"A"}, {}, seen)
    assert _run(client, "https://download9.mediafire.com/t/k/a.zip", tmp_path) == (1, 0, 0)
    assert (tmp_path / "a.zip").read_bytes() == b"A"
    assert not any("/file/" in u for u in seen)


def test_a_folder_downloads_its_files_and_subfolders(tmp_path):
    folders = {
        "root12345": {"files": {"files": [{"quickkey": "kA123456", "filename": "a.zip",
                                           "links": {"normal_download": "https://www.mediafire.com/file/kA123456/a.zip/file"}}],
                                "more_chunks": "no"},
                      "folders": {"folders": [{"folderkey": "sub12345", "name": "Sub: dir"}], "more_chunks": "no"}},
        "sub12345": {"files": {"files": [{"quickkey": "kB123456", "filename": "b.zip", "links": {}}], "more_chunks": "no"},
                     "folders": {"folders": [], "more_chunks": "no"}},
    }
    pages = {"kA123456": "https://download1.mediafire.com/t/kA123456/a.zip",
             "kB123456": "https://download1.mediafire.com/t/kB123456/b.zip"}
    files = {"/t/kA123456/a.zip": b"one", "/t/kB123456/b.zip": b"two"}
    client = _client(pages, files, folders, [])
    assert _run(client, "https://www.mediafire.com/folder/root12345/Mods", tmp_path) == (2, 0, 0)
    assert (tmp_path / "a.zip").read_bytes() == b"one"
    assert (tmp_path / "Sub_ dir" / "b.zip").read_bytes() == b"two"


def test_folder_listing_follows_chunks(tmp_path):
    calls = []
    def handler(req: httpx.Request) -> httpx.Response:
        if "get_content" in req.url.path:
            chunk, kind = int(req.url.params["chunk"]), req.url.params["content_type"]
            calls.append((kind, chunk))
            files = [{"quickkey": f"q{chunk}xxxxxx", "filename": f"f{chunk}.zip", "links": {}}] if kind == "files" else []
            more = "yes" if kind == "files" and chunk == 1 else "no"
            return httpx.Response(200, json={"response": {"result": "Success", "folder_content": {
                "files" if kind == "files" else "folders": files, "more_chunks": more}}})
        if "/file/" in req.url.path:
            return httpx.Response(200, text=f'<a id="downloadButton" href="https://download1.mediafire.com/t/{req.url.path.split("/")[2]}.zip">d</a>')
        return httpx.Response(200, content=b"x", headers={"content-length": "1"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    assert _run(client, "https://www.mediafire.com/folder/fldkey123", tmp_path) == (2, 0, 0)
    assert ("files", 1) in calls and ("files", 2) in calls


def test_already_downloaded_pages_are_skipped_without_a_request(tmp_path):
    page = "https://www.mediafire.com/file/key12345/file"
    seen = []
    client = _client({}, {}, {}, seen)
    assert _run(client, page, tmp_path, {page: str(tmp_path / "mod.zip")}) == (0, 1, 0)
    assert seen == []


def test_a_removed_file_is_one_failure(tmp_path):
    client = _client({}, {}, {}, [])
    assert _run(client, "https://www.mediafire.com/file/gone12345/x.zip/file", tmp_path) == (0, 0, 1)


def test_an_unavailable_folder_is_one_failure(tmp_path):
    client = _client({}, {}, {}, [])
    assert _run(client, "https://www.mediafire.com/folder/nofolder1/x", tmp_path) == (0, 0, 1)


def test_an_unsupported_link_is_one_failure_without_a_request(tmp_path):
    seen = []
    client = _client({}, {}, {}, seen)
    assert _run(client, "https://www.mediafire.com/myfiles.php", tmp_path) == (0, 0, 1)
    assert seen == []
