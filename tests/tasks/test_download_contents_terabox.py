"""Download Contents: terabox.com — shares and folders (resolver ported from Teralace)."""

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from kkafio.services.content_sources import history as history_store, terabox, terabox_session


@pytest.fixture(autouse=True)
def _no_real_history(monkeypatch):
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    monkeypatch.setattr("kkafio.services.content_sources.transfer.retry_delay", lambda exc, attempt: 0.0)


# -- links -----------------------------------------------------------------------------------------

def test_parse_terabox_url():
    P = terabox.parse_terabox_url
    assert P("https://www.terabox.com/s/1DcGWQPuMVDgkXrFhP7AlcQ") == terabox.TeraboxLink("DcGWQPuMVDgkXrFhP7AlcQ")
    assert P("https://1024terabox.com/s/1DcGWQPuMVDgkXrFhP7AlcQ?pwd=1234").surl == "DcGWQPuMVDgkXrFhP7AlcQ"
    assert P("https://terasharelink.com/s/1abcDEF123").surl == "abcDEF123"
    assert P("https://www.terabox.app/sharing/link?surl=DcGWQPuMVDgkXrFhP7AlcQ").surl == "DcGWQPuMVDgkXrFhP7AlcQ"
    assert P("https://www.terabox.com/wap/share/filelist?surl=abcDEF123").surl == "abcDEF123"
    assert P("https://www.terabox.com/sharing/link?surl=abcDEF123&path=%2FMods%2FHair").path == "/Mods/Hair"
    assert P("https://www.terabox.com/sharing/link?surl=abcDEF123&dir=Mods").path == "/Mods"
    assert P("https://www.terabox.com/sharing/link?surl=abcDEF123&path=%2F").path is None
    # query form: a real 22-character id may itself start with "1"; a 23-character one has the prefix
    assert P("https://www.terabox.com/sharing/link?surl=1ZpGkkYh2mj_6296Dsb_nA").surl == "1ZpGkkYh2mj_6296Dsb_nA"
    assert P("https://www.terabox.com/sharing/link?surl=1bZpGkkYh2mj_6296Dsb_nA").surl == "bZpGkkYh2mj_6296Dsb_nA"
    assert P("https://www.1024tera.com/sharing/link?surl=bZpGkkYh2mj_6296Dsb_nA&path=/Cards").path == "/Cards"
    assert P("https://www.terabox.com/") is None and P("https://www.terabox.com/login") is None
    assert P("https://www.terabox.com/s/") is None and P("https://example.com/s/1abcDEF123") is None


def test_is_terabox_url_matches_hosts_not_text():
    assert terabox.is_terabox_url("https://nephobox.com/s/1x")
    assert terabox.is_terabox_url("https://www.teraboxapp.com/s/1x")
    assert terabox.is_terabox_url("https://www.1024tera.com/s/1x")
    assert not terabox.is_terabox_url("https://terabox.com.evil.com/s/1x")
    assert not terabox.is_terabox_url("https://example.com/?u=terabox.com")
    assert not terabox.is_terabox_url("https://pixeldrain.com/u/x")


def test_session_cleaning_and_cookie_header():
    C = terabox_session.clean_session
    assert C(' "ndus=AbCdEf0123456789xyz" ') == "AbCdEf0123456789xyz"
    assert C("a=b; ndus=AbCdEf0123456789xyz; c=d") == "AbCdEf0123456789xyz"
    assert C("AbCdEf0123456789xyz") == "AbCdEf0123456789xyz"
    assert C("a=b; c=d") == ""
    assert terabox_session.looks_valid("AbCdEf0123456789xyz") and not terabox_session.looks_valid("short")
    assert terabox.cookie_header("abc") == "ndus=abc"
    assert terabox.cookie_header("ndus=abc; x=y") == "ndus=abc; x=y"
    assert terabox.cookie_header("  ") is None and terabox.cookie_header(None) is None


# -- the share page --------------------------------------------------------------------------------

def test_initial_state_is_cut_out_of_the_page():
    html = 'x<script>window.__INITIAL_STATE__={"a":"}{\\"}","b":{"c":1}};window.y=1</script>'
    assert terabox.extract_initial_state(html) == {"a": '}{"}', "b": {"c": 1}}
    with pytest.raises(terabox.TeraboxError):
        terabox.extract_initial_state("<html></html>")
    with pytest.raises(terabox.TeraboxError):
        terabox.extract_initial_state("window.__INITIAL_STATE__={\"a\":1")


def test_wap_state_is_read_into_files_and_meta():
    state = {"share": {"fileList": [
        {"fs_id": 11, "server_filename": "a.zip", "size": "5", "isdir": "0", "dlink": "https://d.terabox.com/a"},
        {"fs_id": "22", "server_filename": "Sub", "isdir": 1, "path": "/Sub"}],
        "shareInfo": {"shareid": 7, "uk": "8", "sign": "SIG", "timestamp": 9, "filesTotal": 2}}}
    listing = terabox.parse_wap_state(state)
    assert [(f.fs_id, f.name, f.is_dir, f.size, f.dlink) for f in listing.files] == [
        ("11", "a.zip", False, 5, "https://d.terabox.com/a"), ("22", "Sub", True, None, None)]
    assert (listing.meta.shareid, listing.meta.uk, listing.meta.sign, listing.meta.timestamp) == (7, 8, "SIG", 9)
    assert listing.meta.complete and listing.total == 2


# -- a fake TeraBox --------------------------------------------------------------------------------

class FakeTerabox:
    """One share. Files: {fs_id: (path, bytes)}; a path's folders are derived from it."""

    def __init__(self, files, wap=True, login_needed=False, sharedownload=True, wap_dlinks=False, wap_limit=None):
        self.files, self.wap, self.login_needed = files, wap, login_needed
        self.wap_limit = wap_limit            # the share page renders only this many items
        self.sharedownload, self.wap_dlinks = sharedownload, wap_dlinks
        self.seen: list[str] = []
        self.cookies: list[str | None] = []

    def _entries(self, directory):
        """The direct children of `directory` ("" = root) as share/list items."""
        out, folders = [], set()
        prefix = directory.rstrip("/") + "/"
        for fs_id, (path, data) in self.files.items():
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            if "/" in rest:
                folders.add(prefix + rest.split("/")[0])
            else:
                item = {"fs_id": int(fs_id), "server_filename": rest, "size": str(len(data)),
                        "isdir": "0", "path": path}
                if self.wap_dlinks:
                    item["dlink"] = f"https://d.terabox.com/dl/{fs_id}"
                out.append(item)
        for i, folder in enumerate(sorted(folders)):
            out.append({"fs_id": 9000 + i, "server_filename": folder.rsplit("/", 1)[-1], "isdir": "1", "path": folder})
        return out

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(f"{req.method} {req.url.host}{req.url.path}")
        self.cookies.append(req.headers.get("cookie"))
        q = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
        path = req.url.path
        meta = {"shareid": 1234, "uk": 55, "sign": "SIGN", "timestamp": 1700000000}
        if path == "/wap/share/filelist":
            if not self.wap:
                return httpx.Response(500)
            directory = q.get("path", "")
            items = self._entries(directory) if directory else self._entries("/pack")
            shown = items[:self.wap_limit] if self.wap_limit else items
            state = {"share": {"fileList": shown, "shareInfo": {**meta, "filesTotal": len(items)}}}
            return httpx.Response(200, text=f"<html><script>window.__INITIAL_STATE__={json.dumps(state)};</script></html>")
        if path == "/share/list":
            directory = q.get("dir")
            items = self._entries(directory if directory else "/pack")
            if "dir" not in q and "root" not in q:
                return httpx.Response(200, json={"errno": 2})
            page, num = int(q.get("page", 1)), int(q.get("num", 100))
            chunk = items[(page - 1) * num: page * num]
            return httpx.Response(200, json={"errno": 0, "list": chunk, "total": len(items), **meta})
        if path in ("/api/sharedownload", "/share/download") and req.method == "POST":
            if not self.sharedownload:
                return httpx.Response(200, json={"errno": 31045})
            body = parse_qs(req.content.decode())
            fs_id = body["fs_ids"][0].strip("[]") if "fs_ids" in body else body["fid_list"][0].strip("[]")
            return httpx.Response(200, json={"errno": 0, "list": [{"dlink": f"https://d.terabox.com/dl/{fs_id}"}]})
        if req.url.host == "d.terabox.com" and path.startswith("/dl/"):
            fs_id = path.rsplit("/", 1)[-1]
            if self.login_needed and "ndus=" not in (req.headers.get("cookie") or ""):
                return httpx.Response(403)
            data = self.files[fs_id][1]
            if req.headers.get("range") == "bytes=0-0":
                return httpx.Response(206, content=data[:1])
            return httpx.Response(200, content=data, headers={"content-length": str(len(data))})
        if req.url.host == "tbx.indexsubtitle.cc":
            return httpx.Response(404)
        return httpx.Response(404)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), follow_redirects=True)


def _run(fake, url, tmp_path, history=None, skip=True, session=None):
    async def go():
        client = fake.client()
        try:
            return await terabox.download_terabox(
                client, url, Path(tmp_path), {} if history is None else history, skip, session)
        finally:
            await client.aclose()
    return asyncio.run(go())


SHARE = "https://www.terabox.com/s/1AbCdEf12345"


def test_a_share_is_downloaded_with_its_subfolders(tmp_path):
    fake = FakeTerabox({"1": ("/pack/a.zipmod", b"AAA"), "2": ("/pack/Hair/b.png", b"BBB"),
                        "3": ("/pack/Hair/Long/c.png", b"CCC")})
    history = {}
    assert _run(fake, SHARE, tmp_path, history) == (3, 0, 0)
    assert (tmp_path / "a.zipmod").read_bytes() == b"AAA"
    assert (tmp_path / "Hair" / "b.png").read_bytes() == b"BBB"
    assert (tmp_path / "Hair" / "Long" / "c.png").read_bytes() == b"CCC"
    assert sorted(history) == ["terabox://AbCdEf12345/1", "terabox://AbCdEf12345/2", "terabox://AbCdEf12345/3"]
    assert not list(tmp_path.rglob("*.part"))


def test_finished_files_are_skipped_without_resolving_a_link(tmp_path):
    fake = FakeTerabox({"1": ("/pack/a.zip", b"A")})
    history = {"terabox://AbCdEf12345/1": str(tmp_path / "a.zip")}
    assert _run(fake, SHARE, tmp_path, history) == (0, 1, 0)
    assert not any("sharedownload" in s or "/dl/" in s for s in fake.seen)
    assert _run(FakeTerabox({"1": ("/pack/a.zip", b"A")}), SHARE, tmp_path, dict(history), skip=False) == (1, 0, 0)


def test_a_path_link_takes_only_that_folder_as_the_root(tmp_path):
    fake = FakeTerabox({"1": ("/pack/a.zip", b"A"), "2": ("/pack/Hair/b.png", b"B")})
    assert _run(fake, SHARE + "?path=%2Fpack%2FHair", tmp_path) == (1, 0, 0)
    assert (tmp_path / "b.png").exists() and not (tmp_path / "a.zip").exists()


def test_a_path_to_a_file_downloads_only_that_file(tmp_path):
    fake = FakeTerabox({"1": ("/pack/a.zip", b"A"), "2": ("/pack/Hair/b.png", b"B"), "3": ("/pack/Hair/c.png", b"C")})
    history = {}
    assert _run(fake, SHARE + "?path=%2Fpack%2FHair%2Fc.png", tmp_path, history) == (1, 0, 0)
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["c.png"]
    assert list(history) == ["terabox://AbCdEf12345/3"]


def test_a_path_to_a_missing_file_is_one_failure(tmp_path):
    fake = FakeTerabox({"1": ("/pack/a.zip", b"A")})
    assert _run(fake, SHARE + "?path=%2Fpack%2Fnope.zip", tmp_path) == (0, 0, 1)


def test_without_the_share_page_the_list_api_is_used(tmp_path):
    fake = FakeTerabox({"1": ("/pack/a.zip", b"A"), "2": ("/pack/Sub/b.zip", b"B")}, wap=False)
    assert _run(fake, SHARE, tmp_path) == (2, 0, 0)
    assert (tmp_path / "Sub" / "b.zip").read_bytes() == b"B"


def test_a_long_listing_is_paged(tmp_path, monkeypatch):
    files = {str(i): (f"/pack/f{i:03}.zip", b"x") for i in range(1, 26)}
    monkeypatch.setattr(terabox, "WAP_FIRST_PAGE", 5)
    monkeypatch.setattr(terabox, "PAGE_SIZE", 10)
    fake = FakeTerabox(files, wap_limit=5)
    # the share page shows 5 of 25: the rest comes from the API, 10 per page, without duplicates
    assert _run(fake, SHARE, tmp_path) == (25, 0, 0)
    assert len(list(tmp_path.glob("*.zip"))) == 25
    assert sum(1 for s in fake.seen if s == "GET www.terabox.com/share/list") >= 3


def test_the_listings_dlink_is_the_last_resort(tmp_path):
    fake = FakeTerabox({"1": ("/pack/a.zip", b"A")}, sharedownload=False, wap_dlinks=True)
    assert _run(fake, SHARE, tmp_path) == (1, 0, 0)
    assert (tmp_path / "a.zip").read_bytes() == b"A"


def test_a_file_that_needs_a_login_fails_without_one_and_downloads_with_one(tmp_path):
    fake = FakeTerabox({"1": ("/pack/a.zip", b"A")}, login_needed=True, wap_dlinks=False)
    assert _run(fake, SHARE, tmp_path / "anon") == (0, 0, 1)
    fake = FakeTerabox({"1": ("/pack/a.zip", b"A")}, login_needed=True)
    assert _run(fake, SHARE, tmp_path / "in", session="AbCdEf0123456789xyz") == (1, 0, 0)
    assert (tmp_path / "in" / "a.zip").read_bytes() == b"A"
    assert any(c == "ndus=AbCdEf0123456789xyz" for c in fake.cookies)


def test_the_cookie_is_only_sent_to_terabox_hosts():
    resolver = terabox.TeraboxResolver(None, "AbCdEf0123456789xyz")
    assert resolver.download_headers("https://d.terabox.com/dl/1")["Cookie"] == "ndus=AbCdEf0123456789xyz"
    assert "Cookie" not in resolver.download_headers("https://cdn.example.com/file")
    assert resolver.download_headers("https://d.terabox.com/x")["User-Agent"] == terabox.CLIENT_USER_AGENT


def test_an_empty_or_missing_share_is_one_failure(tmp_path):
    assert _run(FakeTerabox({}), SHARE, tmp_path) == (0, 0, 1)


def test_an_unsupported_link_is_one_failure_without_a_request(tmp_path):
    fake = FakeTerabox({})
    assert _run(fake, "https://www.terabox.com/login", tmp_path) == (0, 0, 1)
    assert fake.seen == []
