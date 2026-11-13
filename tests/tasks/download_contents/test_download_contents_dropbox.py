"""Download Contents: dropbox.com — shared files and folders."""

import asyncio
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from kkafio.services.content_sources import dropbox

from kkafio.services.content_sources.shared import history as history_store
@pytest.fixture(autouse=True)
def _no_real_history(monkeypatch):
    monkeypatch.setattr(history_store, "save_history", lambda h: None)


FILE_NEW = "https://www.dropbox.com/scl/fi/abc123xyz/My%20Mod.zipmod?rlkey=KEY123&st=zz&dl=0"
FILE_OLD = "https://www.dropbox.com/s/abc123xyz/My%20Mod.zipmod?dl=0"
FOLDER   = "https://www.dropbox.com/scl/fo/fld123xyz/KEYabc?rlkey=KEY123&dl=0"


def test_url_parsing():
    P = dropbox.parse_dropbox_url
    kind, link, name = P(FILE_NEW)
    assert (kind, name) == ("file", "My Mod.zipmod")
    assert link == "https://www.dropbox.com/scl/fi/abc123xyz/My%20Mod.zipmod?rlkey=KEY123&st=zz&dl=1"
    assert P(FILE_OLD) == ("file", "https://www.dropbox.com/s/abc123xyz/My%20Mod.zipmod?dl=1", "My Mod.zipmod")
    assert P("https://www.dropbox.com/s/abc123xyz/x.zip?raw=1&dl=0")[1].endswith("x.zip?dl=1")
    assert P("https://www.dropbox.com/s/abc123xyz/x.zip")[1].endswith("x.zip?dl=1")
    assert P(FOLDER) == ("folder", "https://www.dropbox.com/scl/fo/fld123xyz/KEYabc?rlkey=KEY123&dl=1", "")
    assert P("https://www.dropbox.com/sh/fld123xyz/AAbbCC?dl=0")[0] == "folder"
    assert P("https://dl.dropboxusercontent.com/s/abc123xyz/x.zip") == (
        "direct", "https://dl.dropboxusercontent.com/s/abc123xyz/x.zip", "x.zip")
    assert P("https://www.dropbox.com/") is None and P("https://www.dropbox.com/home/mods") is None
    assert P("https://example.com/s/abc123xyz/x.zip") is None


def test_host_matching_is_exact():
    assert dropbox.is_dropbox_url("https://dropbox.com/s/abcdef/x")
    assert dropbox.is_dropbox_url("https://www.dropbox.com/s/abcdef/x")
    assert dropbox.is_dropbox_url("https://dl.dropboxusercontent.com/s/abcdef/x")
    assert not dropbox.is_dropbox_url("https://notdropbox.com/s/abcdef/x")
    assert not dropbox.is_dropbox_url("https://dropbox.com.evil.com/s/abcdef/x")
    assert not dropbox.is_dropbox_url("https://example.com/?u=dropbox.com")


def _client(routes: dict[str, tuple[bytes, dict]], seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        hit = routes.get(req.url.path)
        if hit is None or req.url.params.get("dl") != "1" and req.url.host == "www.dropbox.com":
            return httpx.Response(404)
        data, headers = hit
        return httpx.Response(200, content=data, headers={"content-length": str(len(data)), **headers})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _run(client, url, tmp_path, history=None, skip=True):
    async def go():
        try:
            return await dropbox.download_dropbox(client, url, Path(tmp_path), {} if history is None else history, skip)
        finally:
            await client.aclose()
    return asyncio.run(go())


def test_a_file_link_is_downloaded_with_dl_1(tmp_path):
    seen, history = [], {}
    client = _client({"/scl/fi/abc123xyz/My Mod.zipmod": (b"MOD", {})}, seen)
    assert _run(client, FILE_NEW, tmp_path, history) == (1, 0, 0)
    assert (tmp_path / "My Mod.zipmod").read_bytes() == b"MOD"
    assert seen == ["https://www.dropbox.com/scl/fi/abc123xyz/My%20Mod.zipmod?rlkey=KEY123&st=zz&dl=1"]
    assert list(history) == seen
    assert not list(tmp_path.glob("*.part"))


def test_the_name_dropbox_sends_wins_and_old_links_work(tmp_path):
    client = _client({"/s/abc123xyz/My Mod.zipmod": (b"MOD", {"content-disposition": 'attachment; filename="Real.zipmod"'})}, [])
    assert _run(client, FILE_OLD, tmp_path) == (1, 0, 0)
    assert (tmp_path / "Real.zipmod").read_bytes() == b"MOD"


def test_already_downloaded_files_are_skipped_without_a_request(tmp_path):
    seen = []
    link = dropbox.parse_dropbox_url(FILE_NEW)[1]
    assert _run(_client({}, seen), FILE_NEW, tmp_path, {link: str(tmp_path / "My Mod.zipmod")}) == (0, 1, 0)
    assert seen == []


def test_a_dead_link_is_one_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("kkafio.services.content_sources.shared.transfer.retry_delay", lambda exc, attempt: 0.0)
    assert _run(_client({}, []), FILE_NEW, tmp_path) == (0, 0, 1)


def test_a_folder_is_unpacked_with_its_subfolders(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.png", b"A")
        zf.writestr("sub/b.png", b"B")
        zf.writestr("../evil.txt", b"X")          # must never leave the output folder
    out = tmp_path / "out"
    seen, history = [], {}
    client = _client({"/scl/fo/fld123xyz/KEYabc": (buf.getvalue(), {"content-disposition": 'attachment; filename="Folder.zip"'})}, seen)
    assert _run(client, FOLDER, out, history) == (2, 0, 0)
    assert (out / "a.png").read_bytes() == b"A" and (out / "sub" / "b.png").read_bytes() == b"B"
    assert not (tmp_path / "evil.txt").exists()
    assert sorted(p.name for p in out.iterdir()) == ["a.png", "sub"]          # no scratch left behind
    assert _run(_client({}, []), FOLDER, out, history) == (0, 1, 0)


def test_a_folder_that_is_not_a_zip_or_is_empty_is_one_failure(tmp_path):
    route = "/scl/fo/fld123xyz/KEYabc"
    assert _run(_client({route: (b"<html>", {})}, []), FOLDER, tmp_path / "a") == (0, 0, 1)
    empty = io.BytesIO()
    zipfile.ZipFile(empty, "w").close()
    assert _run(_client({route: (empty.getvalue(), {})}, []), FOLDER, tmp_path / "b") == (0, 0, 1)


def test_an_unsupported_link_is_one_failure_without_a_request(tmp_path):
    seen = []
    assert _run(_client({}, seen), "https://www.dropbox.com/home/mods", tmp_path) == (0, 0, 1)
    assert seen == []
