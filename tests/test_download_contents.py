"""Download Contents: the `url | page` pagination formats."""

import pytest
import asyncio
import time
from pathlib import Path

from kkafio.tasks import download_contents as dc
from kkafio.services.content_sources import card_size, gdrive, gofile, history as history_store, mega, net, pixiv, transfer
from kkafio.services.content_sources.pawchive import access as pw_access, downloader as pw_downloader, parsing as pw_parsing, posts as pw_posts


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
    clean = net.strip_page(link)
    assert "page=" not in clean and clean.count("tag=") == 2
    assert net.set_page(clean, start).count("page=5") == 1
    assert net.set_page(link, 5).count("page=") == 1         # replaced, not duplicated


def test_single_page_fetches_only_that_page(tmp_path):
    seen: list[int] = []

    async def fake_pages(client, url, page):
        seen.append(page)
        return [], 0, False

    asyncio.run(transfer.download_pages(
        None, "https://db.bepis.moe/koikatsu?page=2", Path(tmp_path), {}, True,
        5, 5, fake_pages, lambda u: "x"))
    assert seen == [5]


# ---------------------------------------------------------------------------
# pixiv
# ---------------------------------------------------------------------------

def test_pixiv_url_parsing_and_normalisation():
    P = pixiv._parse_pixiv_url
    assert P("https://www.pixiv.net/en/users/85712366") == ("user", "85712366", "artworks")
    assert P("https://www.pixiv.net/en/users/85712366/illustrations") == ("user", "85712366", "illustrations")
    assert P("https://www.pixiv.net/users/85712366/manga") == ("user", "85712366", "manga")
    assert P("https://www.pixiv.net/en/artworks/151074285") == ("artwork", "151074285", "")
    assert P("https://www.pixiv.net/ja/artworks/151074285") == ("artwork", "151074285", "")
    assert P("https://www.pixiv.net/member.php?id=123") == ("user", "123", "artworks")
    assert P("https://www.pixiv.net/en/users/85712366/bookmarks/artworks") is None
    assert P("https://www.pixiv.net/en/ranking.php?mode=daily") is None

    # bare users/ID becomes users/ID/artworks
    assert pixiv._normalize_pixiv_url("https://www.pixiv.net/en/users/85712366") \
        == "https://www.pixiv.net/en/users/85712366/artworks"
    assert pixiv._normalize_pixiv_url("https://www.pixiv.net/en/users/85712366/illustrations") \
        == "https://www.pixiv.net/en/users/85712366/illustrations"


def test_pixiv_url_is_not_confused_with_other_hosts():
    assert pixiv.is_pixiv_url("https://www.pixiv.net/en/artworks/1")
    assert not pixiv.is_pixiv_url("https://db.bepis.moe/koikatsu?q=www.pixiv.net")
    assert not pixiv.is_pixiv_url("https://evilpixiv.net/artworks/1")


def test_pixiv_size_filter():
    ok = card_size.kk_size_matches
    assert ok(320, 180)                      # scene thumbnail, exact
    assert ok(252, 352)                      # chara card
    assert ok(504, 704) and ok(1008, 1408)   # scaled card, same ratio
    assert not ok(321, 180) and not ok(640, 360)   # only exactly 320x180
    assert not ok(1920, 1080) and not ok(800, 800) and not ok(352, 252)
    assert not ok(0, 0) and not ok(None, 100) and not ok("x", 1)


def _fake_pixiv_client(works: dict[str, list[tuple[int, int]]], failing=()):
    """Client whose .get() serves the /ajax endpoints from `works`."""
    import httpx

    class _Resp:
        def __init__(self, body): self._b = body
        def raise_for_status(self): pass
        def json(self): return {"error": False, "message": "", "body": self._b}

    class _Client:
        async def get(self, url):
            path = httpx.URL(url).path
            if path.endswith("/profile/all"):
                return _Resp({"illusts": {k: None for k in works}, "manga": []})
            if not path.endswith("/pages"):                  # /illust/ID: the description
                return _Resp({"description": ""})
            aid = path.split("/")[-2]
            if aid in failing:
                raise RuntimeError("boom")
            return _Resp([
                {"urls": {"original": f"https://i.pximg.net/img-original/{aid}_p{i}.png"},
                 "width": w, "height": h}
                for i, (w, h) in enumerate(works[aid])
            ])
    return _Client()


def test_pixiv_gallery_only_downloads_matching_sizes(tmp_path, monkeypatch):
    works = {
        "30": [(252, 352)],                    # card
        "20": [(320, 180), (1920, 1080)],      # scene + unrelated page
        "10": [(1000, 1000)],                  # unrelated
    }
    fetched: list[str] = []

    async def fake_download_files(client, urls, directory, name_fn, history, skip):
        fetched.extend(urls)
        for u in urls:
            history[u] = name_fn(u)
        return len(urls), 0, 0

    monkeypatch.setattr(transfer, "download_files", fake_download_files)
    monkeypatch.setattr(history_store, "save_history", lambda h: None)

    history: dict = {}
    res = asyncio.run(pixiv.download_pixiv(
        _fake_pixiv_client(works), "https://www.pixiv.net/en/users/85712366",
        Path(tmp_path), history, True))

    assert res == (2, 0, 0)
    assert sorted(fetched) == [
        "https://i.pximg.net/img-original/20_p0.png",
        "https://i.pximg.net/img-original/30_p0.png",
    ]


def test_pixiv_one_bad_artwork_does_not_abort_gallery(tmp_path, monkeypatch):
    works = {"2": [(252, 352)], "1": [(252, 352)]}

    async def fake_download_files(client, urls, directory, name_fn, history, skip):
        return len(urls), 0, 0

    monkeypatch.setattr(transfer, "download_files", fake_download_files)
    monkeypatch.setattr(history_store, "save_history", lambda h: None)

    ok, skipped, failed = asyncio.run(pixiv.download_pixiv(
        _fake_pixiv_client(works, failing={"2"}),
        "https://www.pixiv.net/en/users/9/artworks", Path(tmp_path), {}, True))
    assert (ok, skipped, failed) == (1, 0, 1)


def test_pixiv_profile_all_with_empty_sections():
    import httpx

    class _R:
        def raise_for_status(self): pass
        def json(self): return {"error": False, "body": {"illusts": [], "manga": []}}

    class _C:
        async def get(self, url): return _R()

    assert asyncio.run(pixiv._pixiv_user_artwork_ids(_C(), "1", "artworks")) == []


def test_pixiv_cookie_is_scoped_to_pixiv_only():
    import httpx
    client = net.make_client(cookies={"PHPSESSID": "1_abc"},
                             cookie_domain=pixiv.PIXIV_COOKIE_DOMAIN,
                             extra_headers={"Referer": pixiv.PIXIV_URL + "/"})
    try:
        def cookie_header(url):
            req = client.build_request("GET", url)
            client.cookies.set_cookie_header(req)
            return req.headers.get("cookie", "")
        assert "PHPSESSID=1_abc" in cookie_header("https://www.pixiv.net/ajax/illust/1/pages")
        assert cookie_header("https://i.pximg.net/img-original/x.png") == ""
        assert client.headers["Referer"] == "https://www.pixiv.net/"
    finally:
        asyncio.run(client.aclose())


def test_pixiv_session_cleaning_and_shape():
    from kkafio.services.content_sources.pixiv import session as ps
    good = "12345678_AbCdEf0123456789AbCdEf0123456789"
    assert ps.looks_valid(good)
    assert ps.clean_session(f"PHPSESSID={good}; path=/") == good
    assert ps.clean_session(f'  "{good}" ') == good
    assert not ps.looks_valid("not a cookie") and not ps.looks_valid("")


def test_pixiv_page_selection():
    ids = [str(i) for i in range(100, 0, -1)]            # 100 works -> 3 pages (48/48/4)
    sel = pixiv._pixiv_select_page_ids
    assert sel(ids, 1, 1) == ids[:48]
    assert sel(ids, 2, 2) == ids[48:96]
    assert sel(ids, 3, 3) == ids[96:]
    assert sel(ids, 2, None) == ids[48:]                  # "all" from page 2 on
    assert sel(ids, 1, None) == ids
    assert sel(ids, 3, 2) == ids[96:] + ids[48:96]        # reverse order
    assert sel(ids, 4, 4) == [] and sel(ids, 0, 0) == []   # out of range
    assert pixiv._pixiv_url_page("https://www.pixiv.net/en/users/1/illustrations?p=2") == 2
    assert pixiv._pixiv_url_page("https://www.pixiv.net/en/users/1/illustrations") is None
    assert pixiv._pixiv_url_page("https://www.pixiv.net/en/users/1/illustrations?p=x") is None


def test_pixiv_directive_and_link_page(tmp_path, monkeypatch):
    works = {str(i): [(252, 352)] for i in range(1, 101)}
    seen: list[list[str]] = []

    async def fake_download_files(client, urls, directory, name_fn, history, skip):
        seen.append(list(urls)); return len(urls), 0, 0

    monkeypatch.setattr(transfer, "download_files", fake_download_files)
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    base = "https://www.pixiv.net/en/users/9/illustrations"

    def run(url, s=None, e=None):
        seen.clear()
        asyncio.run(pixiv.download_pixiv(_fake_pixiv_client(works), url, Path(tmp_path), {}, True, s, e))
        return sum(len(x) for x in seen)

    assert run(base) == 100                    # no page -> whole gallery
    assert run(base + "?p=3") == 4             # ?p= in the link -> that page only
    assert run(base + "?p=3", 1, 1) == 48      # directive beats ?p=
    assert run(base, 2, None) == 52            # page 2 to the end


# ---------------------------------------------------------------------------
# pixiv search
# ---------------------------------------------------------------------------

SEARCH_URL = ("https://www.pixiv.net/search?q=%E3%82%AD%E3%83%A3%E3%83%A9%E9%85%8D%E5%B8%83%28"
              "%E3%82%B3%E3%82%A4%E3%82%AB%E3%83%84%29%20%E5%8E%9F%E7%A5%9E"
              "&s_mode=tag&type=artwork&ai_type=1&r=1")
KEYWORD = "キャラ配布(コイカツ) 原神"


def test_pixiv_search_url_parsing():
    assert pixiv._parse_pixiv_url(SEARCH_URL) == ("search", KEYWORD, "artworks")
    assert pixiv._parse_pixiv_url("https://www.pixiv.net/en/search?q=a&type=illust") == ("search", "a", "illustrations")
    assert pixiv._parse_pixiv_url("https://www.pixiv.net/search?q=a&type=manga") == ("search", "a", "manga")
    assert pixiv._parse_pixiv_url("https://www.pixiv.net/search?q=a&type=novel") is None
    assert pixiv._parse_pixiv_url("https://www.pixiv.net/search") is None
    assert pixiv._parse_pixiv_url("https://www.pixiv.net/en/tags/%E5%8E%9F%E7%A5%9E/illustrations") \
        == ("search", "原神", "illustrations")
    assert pixiv._pixiv_url_page(SEARCH_URL + "&p=4") == 4


def test_pixiv_search_request_keeps_filters():
    endpoint, params = pixiv._pixiv_search_request(SEARCH_URL + "&p=9", 3)
    assert endpoint == "/search/artworks/" + "%E3%82%AD%E3%83%A3%E3%83%A9%E9%85%8D%E5%B8%83%28" \
        "%E3%82%B3%E3%82%A4%E3%82%AB%E3%83%84%29%20%E5%8E%9F%E7%A5%9E"
    d = dict(params)
    assert d["word"] == KEYWORD and d["p"] == "3"          # page argument beats ?p=9
    assert d["s_mode"] == "tag" and d["ai_type"] == "1" and d["r"] == "1"
    assert d["type"] == "all" and d["order"] == "date_d" and d["mode"] == "all"
    assert [k for k, _ in params].count("p") == 1
    _, p2 = pixiv._pixiv_search_request("https://www.pixiv.net/search?q=x&type=illust&order=date&mode=r18", 1)
    assert dict(p2)["order"] == "date" and dict(p2)["mode"] == "r18" and dict(p2)["type"] == "illust"


def _fake_search_client(pages_of_works, size_of):
    """pages_of_works: {page: [(id, w, h, pageCount)]}; size_of: {id: [(w, h)]}."""
    import httpx
    calls = {"search": [], "pages": []}

    class _Resp:
        def __init__(self, body): self._b = body
        def raise_for_status(self): pass
        def json(self): return {"error": False, "message": "", "body": self._b}

    class _Client:
        async def get(self, url):
            u = httpx.URL(url)
            if "/ajax/search/" in u.path:
                q = dict(u.params)
                calls["search"].append(q)
                data = [{"id": str(i), "width": w, "height": h, "pageCount": n}
                        for i, w, h, n in pages_of_works.get(int(q["p"]), [])]
                data.insert(0, {"isAdContainer": True})            # ad entry without an id
                return _Resp({"illustManga": {"data": data if len(data) > 1 else [], "total": 1}})
            if not u.path.endswith("/pages"):                # /illust/ID: the description
                return _Resp({"description": ""})
            aid = u.path.split("/")[-2]
            calls["pages"].append(aid)
            return _Resp([{"urls": {"original": f"https://i.pximg.net/img-original/{aid}_p{i}.png"},
                           "width": w, "height": h} for i, (w, h) in enumerate(size_of[aid])])
    return _Client(), calls


def test_pixiv_search_page_skips_single_page_mismatches_without_extra_requests():
    client, calls = _fake_search_client(
        {1: [(1, 252, 352, 1), (2, 1000, 1000, 1), (3, 320, 180, 1), (4, 800, 600, 2)]},
        {"1": [(252, 352)], "3": [(320, 180)], "4": [(800, 600), (504, 704)]})
    urls, failed, empty = asyncio.run(pixiv._get_pixiv_search_page_urls(client, SEARCH_URL, 1))
    assert not empty and failed == 0
    assert sorted(calls["pages"]) == ["1", "3", "4"]       # work 2 never hit /pages
    assert sorted(urls) == [f"https://i.pximg.net/img-original/{n}.png" for n in ("1_p0", "3_p0", "4_p1")]
    assert asyncio.run(pixiv._get_pixiv_search_page_urls(client, SEARCH_URL, 2))[2] is True


def test_pixiv_search_paging_and_no_match_pages_do_not_stop_run(tmp_path, monkeypatch):
    # pages 1-4 have results but nothing matches; page 5 has a card; page 6 is empty
    works = {p: [(p * 10, 1000, 1000, 1)] for p in range(1, 5)}
    works[5] = [(50, 252, 352, 1)]
    client, calls = _fake_search_client(works, {"50": [(252, 352)]})
    got: list[str] = []

    async def fake_download_files(c, urls, directory, name_fn, history, skip):
        got.extend(urls); return len(urls), 0, 0

    monkeypatch.setattr(transfer, "download_files", fake_download_files)
    monkeypatch.setattr(history_store, "save_history", lambda h: None)

    run = lambda s, e: asyncio.run(pixiv._download_pixiv_search(
        client, SEARCH_URL, Path(tmp_path), {}, True, s, e))

    assert run(1, None) == (1, 0, 0)                        # "| all" runs through 4 empty-match pages
    assert got == ["https://i.pximg.net/img-original/50_p0.png"]
    assert [c["p"] for c in calls["search"]] == ["1", "2", "3", "4", "5", "6"]

    calls["search"].clear()
    run(None, None)                                         # no directive -> page 1 only
    assert [c["p"] for c in calls["search"]] == ["1"]
    calls["search"].clear()
    asyncio.run(pixiv._download_pixiv_search(client, SEARCH_URL + "&p=5", Path(tmp_path), {}, True, None, None))
    assert [c["p"] for c in calls["search"]] == ["5"]       # ?p= in the link


# ---------------------------------------------------------------------------
# gofile
# ---------------------------------------------------------------------------

def test_gofile_url_parsing():
    P = gofile.parse_gofile_url
    assert P("https://gofile.io/d/AbC123") == ("AbC123", None)
    assert P("https://gofile.io/d/AbC123/") == ("AbC123", None)
    assert P("https://gofile.io/f/AbC-123") == ("AbC-123", None)
    assert P("https://gofile.io/d/AbC123?password=hunter2") == ("AbC123", "hunter2")
    assert P("https://gofile.io/d/AbC123#password=hunter2") == ("AbC123", "hunter2")
    assert P("https://gofile.io/") is None and P("https://gofile.io/contents/x") is None
    assert gofile.is_gofile_url("https://gofile.io/d/x") and gofile.is_gofile_url("https://www.gofile.io/d/x")
    assert not gofile.is_gofile_url("https://notgofile.io/d/x")
    assert not gofile.is_gofile_url("https://example.com/?u=gofile.io")


@pytest.fixture(autouse=True)
def _reset_gofile_secret_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(gofile.GOFILE_STORE, "path", tmp_path / "gofile.json")   # never touch the real one
    gofile._gofile_secret_cache.update(secret="", until=0.0, rejected_at=0.0, error="", error_until=0.0)
    yield
    gofile._gofile_secret_cache.update(secret="", until=0.0, rejected_at=0.0, error="", error_until=0.0)


def test_gofile_website_token_formula():
    import hashlib
    import time as _t
    orig = time.time
    time.time = lambda: 14400 * 7 + 5
    try:
        expect = hashlib.sha256(
            f"{gofile.GOFILE_USER_AGENT}::en-US::tok::7::somesecret".encode()).hexdigest()
        assert gofile._gofile_website_token("tok", "somesecret") == expect
        assert gofile._gofile_website_token("tok", "other") != expect
    finally:
        time.time = orig


def test_gofile_secret_is_decoded_from_the_script():
    script = open(Path(__file__).parent / "data" / "gofile-wt.obf.js", encoding="utf-8").read()
    assert gofile._gofile_secret_from(script) == "12af056dacea0b"
    import pytest
    with pytest.raises(ValueError):
        gofile._gofile_secret_from("var nothing = 1;")


def _gofile_mock_client(tree, record, fail_status=None):
    """httpx client on a MockTransport emulating the gofile API + file servers."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        u = request.url
        record.append((request.method, str(u), dict(request.headers)))
        if u.host == "api.gofile.io" and u.path == "/accounts":
            return httpx.Response(200, json={"status": "ok", "data": {"token": "GUEST"}})
        if u.host == "api.gofile.io" and u.path.startswith("/contents/"):
            if fail_status:
                return httpx.Response(404, json={"status": fail_status, "data": {}})
            node = tree[u.path.rsplit("/", 1)[1]]
            return httpx.Response(200, json={"status": "ok", "data": node})
        if u.host.startswith("store"):
            return httpx.Response(200, content=b"data:" + u.path.encode())
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             headers={"User-Agent": gofile.GOFILE_USER_AGENT}, follow_redirects=True)


def _gf_file(fid, name):
    return {"type": "file", "id": fid, "name": name,
            "link": f"https://store1.gofile.io/download/web/{fid}/{name.replace(' ', '%20')}"}


def test_gofile_folder_download_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
    tree = {
        "ROOT": {"name": "pack", "children": {
            "f1": _gf_file("f1", "card one.png"),
            "d1": {"type": "folder", "id": "SUB", "name": "extras"},
        }},
        "SUB": {"name": "extras", "children": {"f2": _gf_file("f2", "card one.png")}},  # same name!
    }
    record: list = []
    monkeypatch.setattr(history_store, "save_history", lambda h: None)

    async def go():
        client = _gofile_mock_client(tree, record)
        try:
            return await gofile.download_gofile(client, "https://gofile.io/d/ROOT", Path(tmp_path), {}, True)
        finally:
            await client.aclose()

    assert asyncio.run(go()) == (2, 0, 0)
    assert (Path(tmp_path) / "card one.png").read_bytes().startswith(b"data:")
    assert (Path(tmp_path) / "extras" / "card one.png").exists()       # no overwrite

    api = [r for r in record if r[1].startswith("https://api.gofile.io/contents/ROOT")][0]
    assert api[2]["authorization"] == "Bearer GUEST"
    assert len(api[2]["x-website-token"]) == 64 and api[2]["x-bl"] == "en-US"
    assert api[2]["user-agent"] == gofile.GOFILE_USER_AGENT
    assert sum(1 for r in record if r[1].endswith("/accounts")) == 1    # one guest account per client
    dl = [r for r in record if r[1].startswith("https://store1.gofile.io")]
    assert dl and all("accountToken=GUEST" in r[2].get("cookie", "") for r in dl)
    # the cookie is scoped to .gofile.io: sent to gofile hosts, never anywhere else
    import httpx
    jar = httpx.Cookies(); jar.set("accountToken", "GUEST", domain=gofile.GOFILE_DOMAIN)
    other = httpx.Request("GET", "https://example.com/x"); jar.set_cookie_header(other)
    assert "cookie" not in other.headers


def test_gofile_link_to_a_single_file_is_downloaded(tmp_path, monkeypatch):
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
    one = _gf_file("f1", "2024-5.zip")
    tree = {"ONE": {**one, "canAccess": True, "code": "ONE", "servers": ["store2"]}}   # a file, no "children"
    record: list = []
    monkeypatch.setattr(history_store, "save_history", lambda h: None)

    async def go():
        client = _gofile_mock_client(tree, record)
        try:
            return await gofile.download_gofile(client, "https://gofile.io/d/ONE", Path(tmp_path), {}, True)
        finally:
            await client.aclose()

    assert asyncio.run(go()) == (1, 0, 0)
    assert (Path(tmp_path) / "2024-5.zip").read_bytes().startswith(b"data:")


def test_gofile_password_is_sent_hashed(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
    record: list = []
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    tree = {"ROOT": {"name": "x", "children": {"f": _gf_file("f", "a.png")}}}

    async def go():
        client = _gofile_mock_client(tree, record)
        try:
            return await gofile.download_gofile(
                client, "https://gofile.io/d/ROOT?password=secret", Path(tmp_path), {}, True)
        finally:
            await client.aclose()

    assert asyncio.run(go()) == (1, 0, 0)
    assert "password=" + hashlib.sha256(b"secret").hexdigest() in \
        [r for r in record if "/contents/ROOT" in r[1]][0][1]


def test_gofile_api_errors_become_clear_messages(tmp_path, monkeypatch):
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
    import pytest
    for status, fragment in (("error-notFound", "not found"), ("error-passwordRequired", "password"),
                             ("error-notPremium", "GOFILE_SALT")):
        async def go():
            client = _gofile_mock_client({}, [], fail_status=status)
            try:
                return await gofile.download_gofile(client, "https://gofile.io/d/X", Path(tmp_path), {}, True)
            finally:
                await client.aclose()
        with pytest.raises(RuntimeError, match=fragment):
            asyncio.run(go())
        gofile._gofile_secret_cache["rejected_at"] = 0.0     # a rejection starts a cooldown


def test_gofile_cooldown_survives_a_restart(monkeypatch):
    import pytest
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
    gofile._gofile_signature_rejected()
    gofile._gofile_secret_cache.update(secret="", until=0.0, rejected_at=0.0)     # a fresh process: memory is empty
    with pytest.raises(RuntimeError, match="wrongly signed"):
        asyncio.run(gofile._gofile_ensure_secret(None))
    gofile._gofile_save_state(rejected_at=0)                                       # cooldown over
    assert asyncio.run(gofile._gofile_ensure_secret(None)) == "testsecret"


def test_gofile_guest_token_is_kept_between_runs(monkeypatch):
    import httpx
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    posts = []

    def handler(req: httpx.Request):
        posts.append(req.url.path)
        return httpx.Response(200, json={"status": "ok", "data": {"token": f"tok{len(posts)}"}})

    async def run():
        c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await gofile._gofile_token(c)
        finally:
            await c.aclose()

    assert asyncio.run(run()) == "tok1"
    assert asyncio.run(run()) == "tok1"            # a new client (= a new run) reuses it
    assert len(posts) == 1
    gofile._gofile_save_state(token_at=0)              # expired
    assert asyncio.run(run()) == "tok2"


def _gofile_file_client(handler):
    import httpx
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_gofile_busy_server_html_is_not_saved_and_next_server_is_tried(tmp_path, monkeypatch):
    import httpx
    sleeps = []

    async def fake_sleep(t):
        sleeps.append(t)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    hosts = []

    def handler(req: httpx.Request):
        hosts.append(req.url.host)
        if req.url.host == "store1.gofile.io":
            return httpx.Response(200, text="<html>gofile web page</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"REAL", headers={"content-type": "application/octet-stream"})

    async def go():
        c = _gofile_file_client(handler)
        try:
            h = {}
            r = await gofile._download_gofile_file(c, "https://store1.gofile.io/download/web/abc/card.png",
                                               ["store1", "store2"], Path(tmp_path), h, True)
            return r, h
        finally:
            await c.aclose()

    (link, err, skipped), history = asyncio.run(go())
    assert err is None and not skipped
    assert hosts == ["store1.gofile.io", "store2.gofile.io"]
    assert sleeps == [gofile.GOFILE_BUSY_BASE]
    assert (Path(tmp_path) / "card.png").read_bytes() == b"REAL"       # the page was never written
    assert history[link].endswith("card.png")
    assert not list(Path(tmp_path).glob("*.part"))


def test_gofile_busy_forever_gives_up_without_saving_anything(tmp_path, monkeypatch):
    import httpx

    async def fake_sleep(t):
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    n = {"calls": 0}

    def handler(req: httpx.Request):
        n["calls"] += 1
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    async def go():
        c = _gofile_file_client(handler)
        try:
            h = {}
            return await gofile._download_gofile_file(c, "https://store1.gofile.io/download/web/abc/card.png",
                                                  [], Path(tmp_path), h, True), h
        finally:
            await c.aclose()

    (link, err, skipped), history = asyncio.run(go())
    assert err is not None and "busy" in str(err) and history == {}
    assert n["calls"] == gofile.GOFILE_BUSY_LIMIT + 1
    assert not list(Path(tmp_path).iterdir())


def _gofile_resume(tmp_path, prefix_len: int, honour_range: bool = True, servers=()):
    import httpx
    content = bytes((i * 13) % 251 for i in range(30000))
    link = "https://store1.gofile.io/download/web/abc/card%20one.png"
    seen: list = []
    (Path(tmp_path) / "card one.png.part").write_bytes(content[:prefix_len])

    def handler(req: httpx.Request):
        rng = req.headers.get("range")
        seen.append(rng)
        head = {"content-type": "application/octet-stream"}
        if rng and honour_range:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            if start >= len(content):
                return httpx.Response(416, headers={"content-range": f"bytes */{len(content)}"})
            return httpx.Response(206, content=content[start:], headers={
                **head, "content-range": f"bytes {start}-{len(content) - 1}/{len(content)}"})
        return httpx.Response(200, content=content, headers=head)

    async def go():
        c = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        try:
            return await gofile._download_gofile_file(c, link, list(servers), Path(tmp_path), {}, False)
        finally:
            await c.aclose()

    return content, asyncio.run(go()), seen


def test_gofile_download_resumes_from_a_part_file(tmp_path):
    content, (link, err, skipped), seen = _gofile_resume(tmp_path, 12345)
    assert err is None and not skipped
    assert seen == ["bytes=12345-"]
    assert (Path(tmp_path) / "card one.png").read_bytes() == content
    assert not list(Path(tmp_path).glob("*.part"))


def test_gofile_resume_falls_back_when_range_is_ignored(tmp_path):
    content, (link, err, skipped), seen = _gofile_resume(tmp_path, 12345, honour_range=False)
    assert err is None
    assert (Path(tmp_path) / "card one.png").read_bytes() == content     # the whole body replaced the part
    assert not list(Path(tmp_path).glob("*.part"))


def test_gofile_part_that_is_not_a_prefix_restarts_after_416(tmp_path):
    content, (link, err, skipped), seen = _gofile_resume(tmp_path, 30000)    # as long as the file
    assert err is None
    assert seen == ["bytes=30000-", None]                    # 416, then the file from the start
    assert (Path(tmp_path) / "card one.png").read_bytes() == content


def test_gofile_short_body_is_resumed_on_retry(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(gofile, "retry_delay", lambda exc, attempt: 0.0)
    content = bytes((i * 7) % 251 for i in range(20000))
    link = "https://store1.gofile.io/download/web/abc/card.png"
    seen: list = []
    head = {"content-type": "application/octet-stream"}

    def handler(req: httpx.Request):
        rng = req.headers.get("range")
        seen.append(rng)
        if not rng:       # the first try announces the full size but ends early
            return httpx.Response(200, content=content[:6000], headers={**head, "content-length": str(len(content))})
        start = int(rng.removeprefix("bytes=").rstrip("-"))
        return httpx.Response(206, content=content[start:], headers={
            **head, "content-range": f"bytes {start}-{len(content) - 1}/{len(content)}"})

    async def go():
        c = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        try:
            return await gofile._download_gofile_file(c, link, [], Path(tmp_path), {}, False)
        finally:
            await c.aclose()

    link_, err, skipped = asyncio.run(go())
    assert err is None
    assert (Path(tmp_path) / "card.png").read_bytes() == content
    assert seen[0] is None and seen[1] == "bytes=6000-"


def test_gofile_stale_saved_token_is_replaced_once(tmp_path, monkeypatch):
    import httpx
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    gofile._gofile_save_state(token="oldtok", token_at=__import__("time").time())
    seen = []

    def handler(req: httpx.Request):
        if req.url.host == "api.gofile.io" and req.url.path == "/accounts":
            return httpx.Response(200, json={"status": "ok", "data": {"token": "newtok"}})
        if req.url.host == "api.gofile.io":
            tok = req.headers["authorization"].split()[-1]
            seen.append(tok)
            if tok == "oldtok":
                return httpx.Response(401, json={"status": "error-wrongToken"})
            return httpx.Response(200, json={"status": "ok", "data": {"type": "folder", "children": {
                "1": {"type": "file", "id": "1", "name": "a.png", "link": "https://store1.gofile.io/download/web/1/a.png"}}}})
        return httpx.Response(200, content=b"X", headers={"content-type": "application/octet-stream"})

    async def go():
        c = _gofile_file_client(handler)
        try:
            return await gofile.download_gofile(c, "https://gofile.io/d/AbC123", Path(tmp_path), {}, True)
        finally:
            await c.aclose()

    assert asyncio.run(go()) == (1, 0, 0)
    assert seen == ["oldtok", "newtok"]
    assert gofile._gofile_load_state()["token"] == "newtok"


def test_gofile_rejection_starts_a_cooldown(monkeypatch):
    import pytest
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
    gofile._gofile_signature_rejected()
    with pytest.raises(RuntimeError, match="wrongly signed"):
        asyncio.run(gofile._gofile_ensure_secret(None))


# ---------------------------------------------------------------------------
# mega
# ---------------------------------------------------------------------------

def _b64(b: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _aes_ecb(key, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(data) + e.finalize()


def _enc_attrs(name, aes_key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    import json as _json
    plain = b"MEGA" + _json.dumps({"n": name}).encode()
    plain += bytes(-len(plain) % 16)
    e = Cipher(algorithms.AES(aes_key), modes.CBC(bytes(16))).encryptor()
    return _b64(e.update(plain) + e.finalize())


def _mega_file(name, content: bytes, seed: int):
    """(32-byte file key, aes key, nonce, encrypted attrs, encrypted payload)."""
    import os as _os
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    rnd = bytes((seed * 31 + i) % 256 for i in range(32))
    aes_key, nonce = bytes(a ^ b for a, b in zip(rnd[:16], rnd[16:])), rnd[16:24]
    e = Cipher(algorithms.AES(aes_key), modes.CTR(nonce + bytes(8))).encryptor()
    return rnd, aes_key, nonce, _enc_attrs(name, aes_key), e.update(content) + e.finalize()


def test_mega_url_and_link_parsing():
    import pytest
    key16, key32 = _b64(bytes(range(16))), _b64(bytes(range(32)))
    assert mega.is_mega_url("https://mega.nz/file/abc#" + key32)
    assert mega.is_mega_url("https://mega.co.nz/#!abc!" + key32)
    assert not mega.is_mega_url("https://notmega.nz/file/abc")

    l = mega._parse_mega_link(f"https://mega.nz/file/abc#{key32}")
    assert (l["folder"], l["handle"], l["node"], len(l["key"])) == (False, "abc", "", 32)
    l = mega._parse_mega_link(f"https://mega.nz/folder/FOL#{key16}")
    assert (l["folder"], l["handle"], len(l["key"])) == (True, "FOL", 16)
    l = mega._parse_mega_link(f"https://mega.nz/folder/FOL#{key16}/file/NODE")
    assert l["node"] == "NODE"
    l = mega._parse_mega_link(f"https://mega.nz/folder/FOL#{key16}/folder/SUB")   # subtree: whole share
    assert l["node"] == ""
    l = mega._parse_mega_link(f"https://mega.nz/#!abc!{key32}")
    assert (l["folder"], l["handle"]) == (False, "abc")
    l = mega._parse_mega_link(f"https://mega.nz/#F!FOL!{key16}!NODE")
    assert (l["folder"], l["handle"], l["node"]) == (True, "FOL", "NODE")

    with pytest.raises(mega.MegaError, match="no key"):
        mega._parse_mega_link("https://mega.nz/file/abc")
    with pytest.raises(mega.MegaError, match="expected 32"):
        mega._parse_mega_link(f"https://mega.nz/file/abc#{key16}")
    with pytest.raises(mega.MegaError, match="not a file or folder"):
        mega._parse_mega_link("https://mega.nz/")


def test_mega_attrs_reject_a_wrong_key():
    import pytest
    attrs = _enc_attrs("card.png", bytes(16))
    assert mega._mega_decrypt_attrs(attrs, bytes(16)) == {"n": "card.png"}
    with pytest.raises(ValueError, match="wrong key"):
        mega._mega_decrypt_attrs(attrs, bytes([1] * 16))


def _mega_mock_client(handler_state):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        u = request.url
        if u.host == "g.api.mega.co.nz":
            import json as _json
            cmd = _json.loads(request.content)[0]
            handler_state["cmds"].append((cmd, u.params.get("n")))
            return httpx.Response(200, json=handler_state["api"](cmd, u.params.get("n")))
        if u.host == "storage.example":
            return httpx.Response(200, content=handler_state["blobs"][u.path])
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_mega_single_file_is_decrypted(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    content = b"PNGDATA" * 5000 + b"tail"
    rnd, aes_key, nonce, at, blob = _mega_file("my card.png", content, 1)
    state = {"cmds": [], "blobs": {"/dl1": blob}}

    def api(cmd, folder):
        assert cmd["p"] == "FILEH" and folder is None
        if cmd.get("g"):
            return [{"g": "http://storage.example/dl1", "s": len(content), "at": at}]
        return [{"s": len(content), "at": at}]
    state["api"] = api

    async def go():
        client = _mega_mock_client(state)
        try:
            history = {}
            r = await mega.download_mega(client, f"https://mega.nz/file/FILEH#{_b64(rnd)}",
                                        Path(tmp_path), history, True)
            r2 = await mega.download_mega(client, f"https://mega.nz/file/FILEH#{_b64(rnd)}",
                                         Path(tmp_path), history, True)
            return r, r2
        finally:
            await client.aclose()

    first, second = asyncio.run(go())
    assert first == (1, 0, 0) and second == (0, 1, 0)           # second run skipped via history
    assert (Path(tmp_path) / "my card.png").read_bytes() == content
    assert not list(Path(tmp_path).glob("*.part"))


def _mega_range_client(blob: bytes, seen: list, honour_range: bool):
    """A mock mega client whose storage host serves `blob`, optionally with Range support."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "g.api.mega.co.nz":
            return httpx.Response(200, json=[{"g": "http://storage.example/dl1", "s": len(blob), "at": ""}])
        rng = request.headers.get("range")
        seen.append(rng)
        if rng and honour_range:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=blob[start:])
        return httpx.Response(200, content=blob)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _resume_case(tmp_path, honour_range: bool):
    content = bytes(range(256)) * 40 + b"tail!"              # 10245 bytes, not a block multiple
    rnd, aes_key, nonce, at, blob = _mega_file("big.bin", content, 7)
    file = {"handle": "H", "name": "big.bin", "dir": (), "size": len(content), "key": aes_key, "nonce": nonce}
    part = mega._mega_part_path(Path(tmp_path) / "big.bin", aes_key)
    part.write_bytes(content[:1005])                         # 1005 is not a multiple of 16
    seen: list = []

    async def go():
        client = _mega_range_client(blob, seen, honour_range)
        try:
            return await mega._download_mega_file(client, file, "", Path(tmp_path), {}, False)
        finally:
            await client.aclose()

    result = asyncio.run(go())
    return content, result, seen, part


def test_mega_download_resumes_from_a_part_file(tmp_path, monkeypatch):
    content, (hkey, err, skipped), seen, part = _resume_case(tmp_path, honour_range=True)
    assert err is None and not skipped
    assert seen == ["bytes=992-"]                            # 1005 rounded down to a 16-byte block
    assert (Path(tmp_path) / "big.bin").read_bytes() == content
    assert not part.exists()


def test_mega_resume_falls_back_when_range_is_ignored(tmp_path, monkeypatch):
    content, (hkey, err, skipped), seen, part = _resume_case(tmp_path, honour_range=False)
    assert err is None
    assert seen == ["bytes=992-"]
    assert (Path(tmp_path) / "big.bin").read_bytes() == content     # the whole body replaced the part
    assert not part.exists()


def test_mega_ctr_decryptor_offset_must_be_block_aligned():
    import pytest
    with pytest.raises(ValueError):
        mega._mega_ctr_decryptor(bytes(16), bytes(8), 5)


def test_mega_folder_download_keeps_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    share = bytes(range(100, 116))

    def wrap(raw_key, node_handle):
        return f"{node_handle}:{_b64(_aes_ecb(share, raw_key))}"

    root_key = bytes([7] * 16)
    sub_key = bytes([8] * 16)
    c1, c2 = b"one" * 1000, b"two" * 777
    k1, ak1, n1, at1, b1 = _mega_file("a.png", c1, 2)
    k2, ak2, n2, at2, b2 = _mega_file("a.png", c2, 3)      # same name, other folder
    nodes = [
        {"h": "ROOT", "p": "", "t": 1, "a": _enc_attrs("pack", root_key), "k": wrap(root_key, "ROOT")},
        {"h": "SUB", "p": "ROOT", "t": 1, "a": _enc_attrs("extras", sub_key), "k": wrap(sub_key, "SUB")},
        {"h": "F1", "p": "ROOT", "t": 0, "s": len(c1), "a": at1, "k": wrap(k1, "F1")},
        {"h": "F2", "p": "SUB", "t": 0, "s": len(c2), "a": at2, "k": wrap(k2, "F2")},
    ]
    state = {"cmds": [], "blobs": {"/b1": b1, "/b2": b2}}

    def api(cmd, folder):
        assert folder == "SHARE"
        if cmd["a"] == "f":
            return [{"f": nodes}]
        return [{"g": "https://storage.example/" + {"F1": "b1", "F2": "b2"}[cmd["n"]],
                 "s": {"F1": len(c1), "F2": len(c2)}[cmd["n"]]}]
    state["api"] = api

    async def go():
        client = _mega_mock_client(state)
        try:
            return await mega.download_mega(client, f"https://mega.nz/folder/SHARE#{_b64(share)}",
                                           Path(tmp_path), {}, True)
        finally:
            await client.aclose()

    assert asyncio.run(go()) == (2, 0, 0)
    assert (Path(tmp_path) / "a.png").read_bytes() == c1
    assert (Path(tmp_path) / "extras" / "a.png").read_bytes() == c2     # no overwrite, root omitted


def test_mega_errors_become_clear_messages(tmp_path, monkeypatch):
    import pytest
    state = {"cmds": [], "blobs": {}, "api": lambda cmd, folder: -9}

    async def go(url):
        client = _mega_mock_client(state)
        try:
            return await mega.download_mega(client, url, Path(tmp_path), {}, True)
        finally:
            await client.aclose()

    with pytest.raises(mega.MegaError, match="not found"):
        asyncio.run(go(f"https://mega.nz/file/X#{_b64(bytes(32))}"))
    state["api"] = lambda cmd, folder: [{"e": -17, "tl": 3600}]
    with pytest.raises(mega.MegaError, match="quota.*3600s"):
        asyncio.run(go(f"https://mega.nz/file/X#{_b64(bytes(32))}"))
    assert asyncio.run(go("https://mega.nz/file/X")) == (0, 0, 1)      # key missing → counted as failed


# ---------------------------------------------------------------------------
# google drive
# ---------------------------------------------------------------------------

_GD_ID = "1AbCdEfGhIjKlMnOpQrStUv"
_GD_ID2 = "1ZzYyXxWwVvUuTtSsRrQqPp"


def test_gdrive_url_parsing():
    assert gdrive.is_gdrive_url("https://drive.google.com/file/d/x/view")
    assert gdrive.is_gdrive_url("https://docs.google.com/uc?id=" + _GD_ID)
    assert not gdrive.is_gdrive_url("https://docs.google.com/document/d/" + _GD_ID)
    assert not gdrive.is_gdrive_url("https://example.com/file/d/" + _GD_ID)
    p = gdrive.parse_gdrive_url
    assert p(f"https://drive.google.com/file/d/{_GD_ID}/view?usp=sharing") == ("file", _GD_ID)
    assert p(f"https://drive.google.com/file/u/0/d/{_GD_ID}/view") == ("file", _GD_ID)
    assert p(f"https://drive.google.com/open?id={_GD_ID}") == ("file", _GD_ID)
    assert p(f"https://drive.google.com/uc?export=download&id={_GD_ID}") == ("file", _GD_ID)
    assert p(f"https://drive.google.com/drive/folders/{_GD_ID}?usp=sharing") == ("folder", _GD_ID)
    assert p(f"https://drive.google.com/drive/u/1/folders/{_GD_ID}") == ("folder", _GD_ID)
    assert p("https://drive.google.com/drive/my-drive") is None


def _gdrive_client(handler):
    import httpx
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_gdrive_small_file_streams_directly(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url)
        assert req.url.host == "drive.usercontent.google.com"
        return httpx.Response(200, content=b"BYTES", headers={
            "content-type": "application/octet-stream",
            "content-disposition": "attachment; filename=\"fallback.png\"; filename*=UTF-8''%E3%82%AB%E3%83%BC%E3%83%89.png"})

    async def go():
        c = _gdrive_client(handler)
        try:
            h = {}
            r1 = await gdrive.download_gdrive(c, f"https://drive.google.com/file/d/{_GD_ID}/view", Path(tmp_path), h, True)
            r2 = await gdrive.download_gdrive(c, f"https://drive.google.com/file/d/{_GD_ID}/view", Path(tmp_path), h, True)
            return r1, r2
        finally:
            await c.aclose()

    r1, r2 = asyncio.run(go())
    assert r1 == (1, 0, 0) and r2 == (0, 1, 0)
    assert (Path(tmp_path) / "カード.png").read_bytes() == b"BYTES"    # filename* wins
    assert len(seen) == 1


def test_gdrive_large_file_goes_through_confirmation(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    page = ('<html><span class="uc-name-size"><a href="/open?id=x">big pack.7z</a> (2G)</span>'
            '<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
            f'<input type="hidden" name="id" value="{_GD_ID}"><input type="hidden" name="export" value="download">'
            '<input type="hidden" name="uuid" value="tok-123"><input type="hidden" name="at" value="AB"></form></html>')
    calls = []

    def handler(req: httpx.Request):
        calls.append(dict(req.url.params))
        if req.url.params.get("uuid") == "tok-123":
            return httpx.Response(200, content=b"REALDATA", headers={"content-type": "application/octet-stream"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    async def go():
        c = _gdrive_client(handler)
        try:
            return await gdrive.download_gdrive(c, f"https://drive.google.com/uc?id={_GD_ID}", Path(tmp_path), {}, True)
        finally:
            await c.aclose()

    assert asyncio.run(go()) == (1, 0, 0)
    assert (Path(tmp_path) / "big pack.7z").read_bytes() == b"REALDATA"     # name from the page
    assert calls[1]["uuid"] == "tok-123" and calls[1]["at"] == "AB"
    assert not list(Path(tmp_path).glob("*.part"))


def _gdrive_range_handler(content: bytes, seen: list, honour_range: bool = True):
    import httpx

    def handler(req: httpx.Request):
        rng = req.headers.get("range")
        seen.append(rng)
        head = {"content-type": "application/octet-stream", "content-disposition": 'attachment; filename="card.bin"'}
        if rng and honour_range:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            if start >= len(content):
                return httpx.Response(416, headers={"content-range": f"bytes */{len(content)}"})
            return httpx.Response(206, content=content[start:], headers={
                **head, "content-range": f"bytes {start}-{len(content) - 1}/{len(content)}"})
        return httpx.Response(200, content=content, headers=head)

    return handler


def _gdrive_resume(tmp_path, prefix: bytes, honour_range: bool = True):
    content = bytes((i * 13) % 251 for i in range(30000))
    seen: list = []
    (Path(tmp_path) / f"drive-{_GD_ID}.part").write_bytes(content[:len(prefix)])

    async def go():
        c = _gdrive_client(_gdrive_range_handler(content, seen, honour_range))
        try:
            return await gdrive._download_gdrive_file(c, _GD_ID, "", Path(tmp_path), {}, False)
        finally:
            await c.aclose()

    return content, asyncio.run(go()), seen


def test_gdrive_download_resumes_from_a_part_file(tmp_path):
    content, (hkey, err, skipped), seen = _gdrive_resume(tmp_path, b"x" * 12345)
    assert err is None and not skipped
    assert seen == ["bytes=12345-"]
    assert (Path(tmp_path) / "card.bin").read_bytes() == content
    assert not list(Path(tmp_path).glob("*.part"))


def test_gdrive_resume_falls_back_when_range_is_ignored(tmp_path):
    content, (hkey, err, skipped), seen = _gdrive_resume(tmp_path, b"x" * 12345, honour_range=False)
    assert err is None
    assert (Path(tmp_path) / "card.bin").read_bytes() == content     # the whole body replaced the part
    assert not list(Path(tmp_path).glob("*.part"))


def test_gdrive_part_that_is_not_a_prefix_restarts_after_416(tmp_path):
    content, (hkey, err, skipped), seen = _gdrive_resume(tmp_path, b"x" * 30000)    # as long as the file
    assert err is None
    assert seen == ["bytes=30000-", None]                    # 416, then the file from the start
    assert (Path(tmp_path) / "card.bin").read_bytes() == content


def test_gdrive_short_body_is_resumed_on_retry(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(net, "retry_delay", lambda exc, attempt: 0.0)
    monkeypatch.setattr(gdrive, "retry_delay", lambda exc, attempt: 0.0)
    content = bytes((i * 7) % 251 for i in range(20000))
    seen: list = []
    head = {"content-type": "application/octet-stream", "content-disposition": 'attachment; filename="card.bin"'}

    def handler(req: httpx.Request):
        rng = req.headers.get("range")
        seen.append(rng)
        if not rng:       # the first try announces the full size but ends early
            return httpx.Response(200, content=content[:6000], headers={**head, "content-length": str(len(content))})
        start = int(rng.removeprefix("bytes=").rstrip("-"))
        return httpx.Response(206, content=content[start:], headers={
            **head, "content-range": f"bytes {start}-{len(content) - 1}/{len(content)}"})

    async def go():
        c = _gdrive_client(handler)
        try:
            return await gdrive._download_gdrive_file(c, _GD_ID, "", Path(tmp_path), {}, False)
        finally:
            await c.aclose()

    hkey, err, skipped = asyncio.run(go())
    assert err is None
    assert (Path(tmp_path) / "card.bin").read_bytes() == content
    assert seen[0] is None and seen[1] == "bytes=6000-"


def test_gdrive_refusals_are_reported_not_retried(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    n = {"calls": 0}

    def handler(req: httpx.Request):
        n["calls"] += 1
        return httpx.Response(200, text="<html>Sorry, you can't view or download this file at this time. "
                              "Too many users have viewed or downloaded this file recently.</html>",
                              headers={"content-type": "text/html"})

    async def go():
        c = _gdrive_client(handler)
        try:
            return await gdrive.download_gdrive(c, f"https://drive.google.com/file/d/{_GD_ID}/view", Path(tmp_path), {}, True)
        finally:
            await c.aclose()

    assert asyncio.run(go()) == (0, 0, 1)
    assert n["calls"] == 1
    assert not list(Path(tmp_path).iterdir())


def test_gdrive_folder_listing_and_download(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(history_store, "save_history", lambda h: None)

    def entry(eid, href, title):
        return (f'<div class="flip-entry" id="entry-{eid}" tabindex="0"><a href="{href}" target="_blank">'
                f'<div class="flip-entry-info"><div class="flip-entry-title">{title}</div></div></a></div>')

    root = "<html><title>pack</title><body>" + "".join([
        entry("F1", "https://drive.google.com/file/d/FILEONE0001/view?usp=drive_web", "a.png"),
        entry("F2", "https://drive.google.com/file/d/FILETWO0002/view?usp=drive_web", "a.png"),   # duplicate name
        entry("D1", "https://drive.google.com/drive/folders/SUBFOLDER001", "extras"),
        entry("G1", "https://docs.google.com/document/d/DOC0000001/edit", "notes"),                # a Google Doc
    ]) + "</body></html>"
    sub = "<html><body>" + entry("F3", "https://drive.google.com/file/d/FILETHREE03/view", "b &amp; c.png") + "</body></html>"
    blobs = {"FILEONE0001": b"one", "FILETWO0002": b"two", "FILETHREE03": b"three"}

    def handler(req: httpx.Request):
        if req.url.host == "drive.google.com" and req.url.path == "/embeddedfolderview":
            return httpx.Response(200, text={"ROOTFOLDER01": root, "SUBFOLDER001": sub}[req.url.params["id"]],
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, content=blobs[req.url.params["id"]], headers={"content-type": "application/octet-stream"})

    async def go():
        c = _gdrive_client(handler)
        try:
            return await gdrive.download_gdrive(c, "https://drive.google.com/drive/folders/ROOTFOLDER01", Path(tmp_path), {}, True)
        finally:
            await c.aclose()

    assert asyncio.run(go()) == (3, 0, 0)
    assert (Path(tmp_path) / "a.png").read_bytes() == b"one"
    assert (Path(tmp_path) / "a (FILETW).png").read_bytes() == b"two"        # duplicate name kept, not overwritten
    assert (Path(tmp_path) / "extras" / "b & c.png").read_bytes() == b"three"


def test_resolve_filename_decodes_percent_escapes():
    class _R: headers = {}
    assert net.resolve_filename(_R(), "https://store1.gofile.io/download/web/id/card%20one.png", "x") == "card one.png"


# ---------------------------------------------------------------------------
# pawchive
# ---------------------------------------------------------------------------

def test_pawchive_url_parsing():
    P = pw_parsing.parse_pawchive_url
    assert P("https://pawchive.pw/posts") == ("posts", "", "", "")
    assert P("https://pawchive.pw/posts?q=koikatsu&o=50") == ("posts", "", "", "")
    assert P("https://pawchive.pw/patreon/user/58585170") == ("user", "patreon", "58585170", "")
    assert P("https://pawchive.pw/patreon/user/58585170/") == ("user", "patreon", "58585170", "")
    assert P("https://pawchive.pw/patreon/user/58585170/post/173280929") \
        == ("post", "patreon", "58585170", "173280929")
    # already an API link
    assert P("https://pawchive.pw/api/v1/posts") == ("posts", "", "", "")
    assert P("https://pawchive.pw/api/v1/fanbox/user/9/posts") == ("user", "fanbox", "9", "")
    # not supported
    assert P("https://pawchive.pw/") is None
    assert P("https://pawchive.pw/patreon/user/1/profile") is None
    assert P("https://pawchive.pw/creators") is None


def test_pawchive_host_is_not_confused_with_other_hosts():
    assert pw_parsing.is_pawchive_url("https://pawchive.pw/posts")
    assert pw_parsing.is_pawchive_url("https://www.pawchive.pw/posts")
    assert not pw_parsing.is_pawchive_url("https://evilpawchive.pw/posts")
    assert not pw_parsing.is_pawchive_url("https://db.bepis.moe/koikatsu?q=pawchive.pw")


def test_pawchive_api_url_conversion():
    A = pw_parsing.pawchive_api_url
    assert A("https://pawchive.pw/posts") == "https://pawchive.pw/api/v1/posts"
    assert A("https://pawchive.pw/patreon/user/58585170") \
        == "https://pawchive.pw/api/v1/patreon/user/58585170"
    assert A("https://pawchive.pw/patreon/user/58585170/post/173280929") \
        == "https://pawchive.pw/api/v1/patreon/user/58585170/post/173280929"

    # page N -> o=(N-1)*50
    assert A("https://pawchive.pw/posts", 1) == "https://pawchive.pw/api/v1/posts?o=0"
    assert A("https://pawchive.pw/posts", 2).endswith("?o=50")
    assert A("https://pawchive.pw/patreon/user/1", 3).endswith("/user/1?o=100")

    # query parameters are kept (repeated ones too); the link's own o is replaced
    url = A("https://pawchive.pw/posts?q=chara+card&tag=a&tag=b&o=200", 2)
    assert url == "https://pawchive.pw/api/v1/posts?q=chara+card&tag=a&tag=b&o=50"
    # a single post takes no query
    assert "?" not in A("https://pawchive.pw/patreon/user/1/post/2?foo=bar")

    for bad in (0, -1):
        try:
            A("https://pawchive.pw/posts", bad)
        except ValueError:
            pass
        else:
            raise AssertionError("page < 1 must be rejected")


def test_pawchive_link_offset_to_page():
    assert pw_parsing.pawchive_url_page("https://pawchive.pw/posts") is None
    assert pw_parsing.pawchive_url_page("https://pawchive.pw/posts?o=0") == 1
    assert pw_parsing.pawchive_url_page("https://pawchive.pw/posts?o=100") == 3
    assert pw_parsing.pawchive_url_page("https://pawchive.pw/posts?o=abc") is None


def test_pawchive_response_shapes():
    P = pw_parsing.pawchive_posts_from
    assert P([{"id": "1"}, {"id": "2"}, "junk"]) == [{"id": "1"}, {"id": "2"}]
    assert P([]) == []
    assert P({"id": "1", "title": "x"}) == [{"id": "1", "title": "x"}]        # single post
    assert P({"post": {"id": "1"}, "attachments": []}) == [{"id": "1"}]      # wrapped
    assert P({"posts": [{"id": "1"}]}) == [{"id": "1"}]
    for bad in ({"error": "nope"}, "text", None, 5):
        try:
            P(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} must be reported, not read as 'no posts'")


def test_pawchive_post_files():
    post = {
        "file": {"name": "000.png", "node": 2, "path": "/7c/9b/7c9bc148.png"},
        "attachments": [
            {"name": "000.png", "node": 2, "path": "/7c/9b/7c9bc148.png"},   # repeat of file
            {"name": "excelwalter.rar", "node": 2, "path": "/18/fb/18fb6c9e.rar"},
            {"name": "noext", "path": "/aa/bb/aabb.zip"},                    # extension from path
            {"name": "x.zip"},                                               # no path: ignored
            "junk",
        ],
    }
    files = pw_parsing.pawchive_post_files(post)
    # the public file host comes first, with the name as ?f=
    assert [f["url"] for f in files] == [
        "https://file.pawchive.pw/data/7c/9b/7c9bc148.png?f=000.png",
        "https://file.pawchive.pw/data/18/fb/18fb6c9e.rar?f=excelwalter.rar",
        "https://file.pawchive.pw/data/aa/bb/aabb.zip?f=noext.zip",
    ]
    # ... then the storage node that holds the file (the main host without a node)
    assert [f["urls"][1] for f in files] == [
        "https://n2.pawchive.pw/data/7c/9b/7c9bc148.png?f=000.png",
        "https://n2.pawchive.pw/data/18/fb/18fb6c9e.rar?f=excelwalter.rar",
        "https://pawchive.pw/data/aa/bb/aabb.zip?f=noext.zip",
    ]
    # the history key is the file host without the query
    assert [f["key"] for f in files] == [
        "https://file.pawchive.pw/data/7c/9b/7c9bc148.png",
        "https://file.pawchive.pw/data/18/fb/18fb6c9e.rar",
        "https://file.pawchive.pw/data/aa/bb/aabb.zip",
    ]
    assert [f["name"] for f in files] == ["000.png", "excelwalter.rar", "noext.zip"]
    assert [f["check_size"] for f in files] == [True, False, False]   # only PNGs are size-checked

    # the "node" of an entry picks the host the file is served from
    node_post = {"file": {"name": "a.png", "node": 2, "path": "/7c/9b/a.png"},
                 "attachments": [{"name": "b.rar", "node": "13", "path": "/18/fb/b.rar"},
                                 {"name": "c.rar", "node": "bad", "path": "/18/fb/c.rar"}]}
    assert [f["urls"][1] for f in pw_parsing.pawchive_post_files(node_post)] == [
        "https://n2.pawchive.pw/data/7c/9b/a.png?f=a.png",
        "https://n13.pawchive.pw/data/18/fb/b.rar?f=b.rar",
        "https://pawchive.pw/data/18/fb/c.rar?f=c.rar",      # no usable node: main host
    ]

    assert pw_parsing.pawchive_post_files({"file": {}, "attachments": []}) == []
    assert pw_parsing.pawchive_post_files({"file": None, "attachments": None}) == []
    assert pw_parsing.pawchive_post_files({}) == []
    # path separators in a name never escape the download folder
    f = pw_parsing.pawchive_post_files({"file": {"name": "../../evil.rar", "path": "/a/b/c.rar"}})[0]
    assert f["name"] == "evil.rar"


def test_pawchive_content_links():
    key = "KeyKeyKeyKeyKeyKeyKeyKeyKeyKeyKeyKey123"
    content = (
        f'<p>Download: <a href="https://mega.nz/file/AbCdEfGh#{key}">'
        f'https://mega.nz/file/AbCdEfGh#KeyKey…</a></p>'
        '<p>gofile: https://gofile.io/d/Xy12Zz?password=hunter2.</p>'
        '<p><a href="https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing&amp;x=1">d</a>'
        ' https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp</p>'
        '<p>mirror: mega.nz/folder/Zz9Yy8Xx#abcdefghijklmnopqrstuv<br>'
        'evilmega.nz/file/abc#def https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit '
        'https://mega.nz/ https://gofile.io https://example.com/x</p>'
    )
    assert pw_parsing.pawchive_content_links(content) == [
        f"https://mega.nz/file/AbCdEfGh#{key}",          # fragment kept, shortened text dropped
        "https://gofile.io/d/Xy12Zz?password=hunter2",   # query kept, sentence dot removed
        "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing&x=1",  # &amp; decoded
        "https://mega.nz/folder/Zz9Yy8Xx#abcdefghijklmnopqrstuv",   # scheme added
    ]
    assert pw_parsing.pawchive_content_links("") == []
    assert pw_parsing.pawchive_content_links(None) == []
    assert pw_parsing.pawchive_content_links("<p></p>") == []
    # the same link twice is one link; legacy mega and uc links are recognised
    text = "https://mega.nz/#!AbCdEfGh!key https://mega.nz/#!AbCdEfGh!key https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOp&export=download"
    assert pw_parsing.pawchive_content_links(text) == [
        "https://mega.nz/#!AbCdEfGh!key",
        "https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOp&export=download",
    ]


def test_pawchive_unique_names(tmp_path):
    (tmp_path / "000.png").write_bytes(b"old")
    a = {"url": "u1", "name": "000.png", "path": "/aa/bb/aaaaaaaaaaaa.png"}
    b = {"url": "u2", "name": "000.png", "path": "/cc/dd/bbbbbbbbbbbb.png"}
    c = {"url": "u3", "name": "pack.rar", "path": "/ee/ff/cccccccc.rar"}
    d = {"url": "u4", "name": "pack.rar", "path": "/ee/ff/cccccccc.rar"}   # same hash twice
    taken: set[str] = set()
    names = [pw_parsing.pawchive_unique_name(x, Path(tmp_path), {}, taken) for x in (a, b, c, d)]
    assert names[0] == "000_aaaaaaaa.png"           # exists on disk
    assert names[1] == "000_bbbbbbbb.png"
    assert names[2] == "pack.rar"                   # first one keeps its name
    assert names[3] == "pack_cccccccc.rar"          # same name in this run
    assert len(set(n.lower() for n in names)) == 4

    # a file downloaded before keeps the name it got, even though it exists
    (tmp_path / "pack.rar").write_bytes(b"x")
    hist = {"u3": str(tmp_path / "pack.rar")}
    assert pw_parsing.pawchive_unique_name(c, Path(tmp_path), hist, set()) == "pack.rar"


# --- a fake pawchive server -------------------------------------------------

def _png(width: int, height: int, extra: int = 500) -> bytes:
    import struct
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"\0\0\0\0"
            + b"x" * extra)


class _PawResp:
    def __init__(self, body=b"", data=None, chunk=65536, status_error=None, text=""):
        self._body, self._data, self._chunk, self._err = body, data, chunk, status_error
        self.text = text

    def raise_for_status(self):
        if isinstance(self._err, Exception):
            raise self._err
        if self._err:
            raise RuntimeError(self._err)

    def json(self):
        return self._data

    async def aiter_bytes(self, chunk_size=65536):
        for i in range(0, len(self._body), self._chunk):
            yield self._body[i:i + self._chunk]


class _PawStream:
    def __init__(self, resp): self._r = resp
    async def __aenter__(self): return self._r
    async def __aexit__(self, *exc): return False


class _PawClient:
    """pages: {offset: [posts]} for listings; singles: {post_id: post};
    blobs: {url: bytes}. Records every API call and every file fetch."""

    def __init__(self, pages=None, singles=None, blobs=None, chunk=65536, html=None, deny=(),
                 limited=None, retry_after=None):
        import httpx
        # {url without query: how many 429s to answer before the real answer}
        self.limited = dict(limited or {})
        self.retry_after = retry_after                             # Retry-After header of those 429s
        self.times: list[float] = []                               # when each request arrived
        self.pages, self.singles, self.blobs, self.chunk = pages or {}, singles or {}, blobs or {}, chunk
        self.html = {u: list(v) for u, v in (html or {}).items()}   # post web pages: every read pops one
        self.deny = set(deny)                                      # hosts that answer 403
        self.cookies = httpx.Cookies()
        self.api_calls: list[str] = []
        self.fetched: list[str] = []

    def _maybe_429(self, url):
        import time
        import httpx
        self.times.append(time.monotonic())
        base = url.split("?")[0]
        if self.limited.get(base, 0) > 0:
            self.limited[base] -= 1
            headers = {"Retry-After": str(self.retry_after)} if self.retry_after is not None else {}
            resp = httpx.Response(429, headers=headers, request=httpx.Request("GET", url))
            return _PawResp(status_error=httpx.HTTPStatusError("429 Too Many Requests", request=resp.request, response=resp))
        return None

    async def get(self, url):
        from urllib.parse import parse_qsl, urlparse
        self.api_calls.append(url)
        if (r429 := self._maybe_429(url)) is not None:
            return r429
        parts = urlparse(url)
        if url in self.html:
            seq = self.html[url]
            return _PawResp(text=seq.pop(0) if len(seq) > 1 else seq[0])
        if "/post/" in parts.path:
            return _PawResp(data=self.singles[parts.path.rsplit("/", 1)[-1]])
        offset = int(dict(parse_qsl(parts.query)).get("o", "0"))
        return _PawResp(data=self.pages.get(offset, []))

    def stream(self, method, url):
        from urllib.parse import urlparse
        self.fetched.append(url)
        if (r429 := self._maybe_429(url)) is not None:
            return _PawStream(r429)
        if urlparse(url).hostname in self.deny:
            return _PawStream(_PawResp(status_error="403 Forbidden"))
        base = url.split("?")[0]                  # blobs are keyed without the ?f= name
        if base not in self.blobs:
            return _PawStream(_PawResp(status_error="404 Not Found"))
        return _PawStream(_PawResp(body=self.blobs[base], chunk=self.chunk))


@pytest.fixture(autouse=True)
def _fast_pawchive(monkeypatch):
    """No pacing gaps and a tiny 429 cooldown, so the tests run instantly;
    the throttle tests set their own values where timing is the point."""
    monkeypatch.setattr(pw_access, "PAWCHIVE_FILE_DELAY", (0.0, 0.0))
    monkeypatch.setattr(pw_access, "PAWCHIVE_PAGE_DELAY", (0.0, 0.0))
    monkeypatch.setattr(pw_access, "PAWCHIVE_COOLDOWN", 0.05)


def _data(path): return "https://file.pawchive.pw/data" + path


def test_pawchive_png_size_filter_and_downloads(tmp_path):
    card, scene, big, odd = "/01/aa/card.png", "/02/bb/scene.png", "/03/cc/big.png", "/04/dd/not.png"
    posts = [{
        "id": "1", "title": "t", "content": "",
        "file": {"name": "card.png", "path": card},
        "attachments": [
            {"name": "scene.png", "path": scene},
            {"name": "big.png", "path": big},               # 1920x1080: rejected
            {"name": "broken.png", "path": odd},            # not a PNG at all: rejected
            {"name": "mods.rar", "path": "/05/ee/mods.rar"},   # not a PNG: no check
        ],
    }]
    blobs = {
        _data(card): _png(252, 352), _data(scene): _png(320, 180), _data(big): _png(1920, 1080),
        _data(odd): b"this is not a png" * 20, _data("/05/ee/mods.rar"): b"Rar!" * 100,
    }
    # chunk=7 makes the first chunks shorter than a PNG header
    client = _PawClient(blobs=blobs, chunk=7)
    history: dict = {}
    run = pw_posts.PawchiveRun(None)
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), history, True, run))

    assert (run.ok, run.skipped, run.failed, run.filtered) == (3, 0, 0, 2)
    assert sorted(p.name for p in Path(tmp_path).iterdir()) == ["card.png", "mods.rar", "scene.png"]
    assert (Path(tmp_path) / "mods.rar").read_bytes() == b"Rar!" * 100
    assert set(history) == {_data(card), _data(scene), _data("/05/ee/mods.rar")}   # rejects not recorded
    assert not list(Path(tmp_path).glob("*.part"))                  # nothing half-written is left


def test_pawchive_png_ratio_is_accepted_and_abort_is_early(tmp_path):
    scaled, huge = "/01/aa/scaled.png", "/02/bb/huge.png"
    blobs = {_data(scaled): _png(504, 704), _data(huge): _png(4000, 3000, extra=300_000)}
    posts = [{"id": "1", "file": {"name": "scaled.png", "path": scaled},
              "attachments": [{"name": "huge.png", "path": huge}]}]

    consumed = []

    class _Counting(_PawClient):
        def stream(self, method, url):
            s = super().stream(method, url)
            orig = s._r.aiter_bytes

            async def counted(chunk_size=65536):
                async for c in orig(chunk_size):
                    consumed.append((url, len(c)))
                    yield c
            s._r.aiter_bytes = counted
            return s

    client = _Counting(blobs=blobs, chunk=1000)
    run = pw_posts.PawchiveRun(None)
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), {}, True, run))
    assert (run.ok, run.filtered) == (1, 1)                         # 504x704 has the card ratio
    # the 300 KB rejected PNG was abandoned after its first chunk
    assert sum(n for u, n in consumed if u.startswith(_data(huge))) <= 1000


def test_pawchive_failed_file_is_counted_and_others_continue(tmp_path):
    posts = [{"id": "1", "file": {"name": "gone.rar", "path": "/00/00/gone.rar"},
              "attachments": [{"name": "ok.rar", "path": "/00/00/ok.rar"}]}]
    client = _PawClient(blobs={_data("/00/00/ok.rar"): b"data"})
    run = pw_posts.PawchiveRun(None)
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), {}, True, run))
    assert (run.ok, run.failed) == (1, 1)
    assert [p.name for p in Path(tmp_path).iterdir()] == ["ok.rar"]


def test_pawchive_skip_downloaded_uses_history(tmp_path):
    posts = [{"id": "1", "file": {"name": "a.rar", "path": "/00/00/a.rar"}}]
    client = _PawClient(blobs={_data("/00/00/a.rar"): b"data"})
    history = {_data("/00/00/a.rar"): str(tmp_path / "a.rar")}
    run = pw_posts.PawchiveRun(None)
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), history, True, run))
    assert (run.ok, run.skipped) == (0, 1) and client.fetched == []

    run = pw_posts.PawchiveRun(None)          # skip off: fetched again
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), history, False, run))
    assert (run.ok, run.skipped) == (1, 0) and client.fetched == [_data("/00/00/a.rar") + "?f=a.rar"]


def _patch_externals(monkeypatch):
    calls: list[tuple[str, str]] = []

    def make(kind):
        async def fake(client, url, directory, history, skip):
            calls.append((kind, url))
            assert client == kind                      # the right client is used for each host
            return 1, 0, 0
        return fake

    monkeypatch.setattr(pw_posts, "download_gofile", make("gofile"))
    monkeypatch.setattr(pw_posts, "download_mega", make("mega"))
    monkeypatch.setattr(pw_posts, "download_gdrive", make("gdrive"))
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    return calls


EXTERNALS = {"gofile": "gofile", "mega": "mega", "gdrive": "gdrive"}


def test_pawchive_listing_pages_until_empty(tmp_path, monkeypatch):
    calls = _patch_externals(monkeypatch)
    pages = {
        0: [{"id": "3", "title": "a", "file": {"name": "a.rar", "path": "/00/00/a.rar"},
             "content": '<a href="https://mega.nz/file/AbCdEfGh#KEY1">x</a>'}],
        50: [{"id": "2", "title": "b", "attachments": [{"name": "b.zip", "path": "/00/00/b.zip"}],
              "content": "https://gofile.io/d/Xy12Zz?password=pw https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing"}],
        # offset 100 is empty -> end
    }
    client = _PawClient(pages=pages, blobs={_data("/00/00/a.rar"): b"a", _data("/00/00/b.zip"): b"b"})
    res = asyncio.run(pw_downloader.download_pawchive(
        client, "https://pawchive.pw/posts?q=chara", Path(tmp_path), {}, True,
        page_start=1, page_end=None, externals=EXTERNALS))

    assert client.api_calls == [
        "https://pawchive.pw/api/v1/posts?q=chara&o=0",
        "https://pawchive.pw/api/v1/posts?q=chara&o=50",
        "https://pawchive.pw/api/v1/posts?q=chara&o=100",
    ]
    assert res == (5, 0, 0)       # 2 files + 3 links
    assert sorted(calls) == sorted([
        ("mega", "https://mega.nz/file/AbCdEfGh#KEY1"),
        ("gofile", "https://gofile.io/d/Xy12Zz?password=pw"),                           # query kept
        ("gdrive", "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing"),
    ])
    assert sorted(p.name for p in Path(tmp_path).iterdir()) == ["a.rar", "b.zip"]


def test_pawchive_default_reads_one_page_and_link_offset_is_honoured(tmp_path, monkeypatch):
    _patch_externals(monkeypatch)
    full = {o: [{"id": str(o + 1)}] for o in (0, 50, 100)}

    c1 = _PawClient(pages=full)
    asyncio.run(pw_downloader.download_pawchive(c1, "https://pawchive.pw/patreon/user/9",
                                      Path(tmp_path), {}, True, externals=EXTERNALS))
    assert c1.api_calls == ["https://pawchive.pw/api/v1/patreon/user/9?o=0"]

    c2 = _PawClient(pages=full)
    asyncio.run(pw_downloader.download_pawchive(c2, "https://pawchive.pw/patreon/user/9?o=100",
                                      Path(tmp_path), {}, True, externals=EXTERNALS))
    assert c2.api_calls == ["https://pawchive.pw/api/v1/patreon/user/9?o=100"]

    c3 = _PawClient(pages=full)         # "| 2 | 3" -> offsets 50 and 100
    asyncio.run(pw_downloader.download_pawchive(c3, "https://pawchive.pw/patreon/user/9",
                                      Path(tmp_path), {}, True, page_start=2, page_end=3,
                                      externals=EXTERNALS))
    assert [u.rsplit("o=", 1)[1] for u in c3.api_calls] == ["50", "100"]


def test_pawchive_reverse_range_survives_an_empty_first_page(tmp_path, monkeypatch):
    _patch_externals(monkeypatch)
    client = _PawClient(pages={0: [{"id": "1"}], 50: [{"id": "2"}]})   # page 3 (o=100) is empty
    asyncio.run(pw_downloader.download_pawchive(client, "https://pawchive.pw/posts", Path(tmp_path), {}, True,
                                      page_start=3, page_end=1, externals=EXTERNALS))
    assert [u.rsplit("o=", 1)[1] for u in client.api_calls] == ["100", "50", "0"]


def test_pawchive_repeated_pages_stop_an_open_ended_run(tmp_path, monkeypatch):
    _patch_externals(monkeypatch)

    class _Clamping(_PawClient):          # a server that keeps serving the last page
        async def get(self, url):
            self.api_calls.append(url)
            return _PawResp(data=[{"id": "1"}])

    client = _Clamping()
    asyncio.run(pw_downloader.download_pawchive(client, "https://pawchive.pw/posts", Path(tmp_path), {}, True,
                                      page_start=1, page_end=None, externals=EXTERNALS))
    assert len(client.api_calls) == 2     # page 2 repeated page 1 -> stop


def test_pawchive_bad_response_is_a_failed_page_not_the_end(tmp_path, monkeypatch):
    _patch_externals(monkeypatch)

    class _Erroring(_PawClient):
        async def get(self, url):
            self.api_calls.append(url)
            return _PawResp(data={"error": "rate limited"})

    client = _Erroring()
    ok, skipped, failed = asyncio.run(pw_downloader.download_pawchive(
        client, "https://pawchive.pw/posts", Path(tmp_path), {}, True,
        page_start=1, page_end=None, externals=EXTERNALS))
    assert (ok, skipped) == (0, 0) and failed == net.MAX_CONSECUTIVE_BAD_PAGES
    assert len(client.api_calls) == net.MAX_CONSECUTIVE_BAD_PAGES


def test_pawchive_single_post(tmp_path, monkeypatch):
    calls = _patch_externals(monkeypatch)
    post = {"id": "173280929", "user": "58585170", "service": "patreon", "title": "t",
            "content": "<p>https://mega.nz/folder/Zz9Yy8Xx#abcdefghijklmnopqrstuv</p>",
            "file": {"name": "000.png", "path": "/01/aa/c.png"},
            "attachments": [{"name": "c.rar", "path": "/01/aa/c.rar"}]}
    client = _PawClient(singles={"173280929": post},
                        blobs={_data("/01/aa/c.png"): _png(252, 352), _data("/01/aa/c.rar"): b"zz"})
    res = asyncio.run(pw_downloader.download_pawchive(
        client, "https://pawchive.pw/patreon/user/58585170/post/173280929",
        Path(tmp_path), {}, True, page_start=5, page_end=5, externals=EXTERNALS))   # directive ignored
    assert client.api_calls == ["https://pawchive.pw/api/v1/patreon/user/58585170/post/173280929"]
    assert res == (3, 0, 0)
    assert calls == [("mega", "https://mega.nz/folder/Zz9Yy8Xx#abcdefghijklmnopqrstuv")]

    # the wrapped shape works the same
    client = _PawClient(singles={"1": {"post": {"id": "1", "attachments": [{"name": "d.rar", "path": "/d.rar"}]}}},
                        blobs={_data("/d.rar"): b"d"})
    assert asyncio.run(pw_downloader.download_pawchive(
        client, "https://pawchive.pw/fanbox/user/2/post/1", Path(tmp_path), {}, True,
        externals=EXTERNALS)) == (1, 0, 0)


def test_pawchive_same_file_and_link_in_many_posts_is_handled_once(tmp_path, monkeypatch):
    calls = _patch_externals(monkeypatch)
    post = lambda i: {"id": str(i), "file": {"name": "a.rar", "path": "/00/00/a.rar"},
                      "content": "https://gofile.io/d/Xy12Zz"}
    client = _PawClient(blobs={_data("/00/00/a.rar"): b"a"})
    run = pw_posts.PawchiveRun(EXTERNALS)
    asyncio.run(pw_posts.pawchive_process_posts(client, [post(1), post(2)], Path(tmp_path), {}, True, run))
    assert client.fetched == [_data("/00/00/a.rar") + "?f=a.rar"] and len(calls) == 1
    assert (run.ok, run.failed) == (2, 0)            # 1 file + 1 link


def test_pawchive_failing_link_does_not_stop_the_rest(tmp_path, monkeypatch):
    _patch_externals(monkeypatch)

    async def boom(client, url, directory, history, skip):
        raise RuntimeError("mega: quota")

    monkeypatch.setattr(pw_posts, "download_mega", boom)
    posts = [{"id": "1", "content": "https://mega.nz/file/AbCdEfGh#KEY https://gofile.io/d/Xy12Zz"}]
    run = pw_posts.PawchiveRun(EXTERNALS)
    asyncio.run(pw_posts.pawchive_process_posts(_PawClient(), posts, Path(tmp_path), {}, True, run))
    assert (run.ok, run.failed) == (1, 1)


def test_pawchive_unsupported_link(tmp_path):
    assert asyncio.run(pw_downloader.download_pawchive(
        _PawClient(), "https://pawchive.pw/creators", Path(tmp_path), {}, True)) == (0, 0, 1)


# --- pawchive: host fallback, temporary (t1) files, session cookie --------------

def test_pawchive_falls_back_to_the_node_host(tmp_path):
    path = "/01/aa/pack.rar"
    node = "https://n2.pawchive.pw/data" + path
    posts = [{"id": "1", "file": {"name": "pack.rar", "node": 2, "path": path}}]

    # the public file host refuses (403): the storage node is tried next
    client = _PawClient(blobs={node: b"Rar!" * 10}, deny={"file.pawchive.pw"})
    history: dict = {}
    run = pw_posts.PawchiveRun(None)
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), history, True, run))
    assert (run.ok, run.failed) == (1, 0)
    assert client.fetched == [_data(path) + "?f=pack.rar", node + "?f=pack.rar"]
    assert (Path(tmp_path) / "pack.rar").read_bytes() == b"Rar!" * 10
    assert history == {_data(path): str(Path(tmp_path) / "pack.rar")}     # keyed by the file host

    # every host refuses: one failure, reported once, nothing left behind
    other = Path(tmp_path) / "other"
    other.mkdir()
    client = _PawClient(deny={"file.pawchive.pw", "n2.pawchive.pw"})
    run = pw_posts.PawchiveRun(None)
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, other, {}, True, run))
    assert (run.ok, run.failed) == (0, 1)
    assert len(client.fetched) == 2 and not list(other.iterdir())


def test_pawchive_file_host_is_preferred_and_node_not_touched(tmp_path):
    path = "/01/aa/ok.rar"
    client = _PawClient(blobs={_data(path): b"x"})
    run = pw_posts.PawchiveRun(None)
    asyncio.run(pw_posts.pawchive_process_posts(
        client, [{"id": "1", "file": {"name": "ok.rar", "node": 2, "path": path}}],
        Path(tmp_path), {}, True, run))
    assert (run.ok, run.failed) == (1, 0) and client.fetched == [_data(path) + "?f=ok.rar"]


def test_pawchive_data_prefix_and_odd_names_in_urls():
    key, urls = pw_parsing._pawchive_data_urls("/data/ab/cd/x.rar", None, "a b&c.rar")
    assert key == "https://file.pawchive.pw/data/ab/cd/x.rar"        # /data/ is not doubled
    assert urls[0] == key + "?f=a%20b%26c.rar"                       # the name is quoted
    # a path that already is a full URL is used as it is (no candidates added)
    f = pw_parsing.pawchive_post_files({"file": {"name": "z.zip", "path": "https://t1.pawchive.pw/f/h/z.zip?e=1&s=2"}})[0]
    assert f["urls"] == ["https://t1.pawchive.pw/f/h/z.zip?e=1&s=2"]
    assert f["key"] == "https://t1.pawchive.pw/f/h/z.zip" and f["name"] == "z.zip"


T1 = "https://t1.pawchive.pw/f/9f8e7d/Big%20Pack.rar?e=1700000000&s=abc123"
PAGE = "https://pawchive.pw/patreon/user/9/post/77"


def _t1_post(**extra):
    return {"id": "77", "user": "9", "service": "patreon", "title": "t",
            "attachments": [{"name": "Big Pack.rar", "deferred": True},
                            {"name": "small.rar", "path": "/00/00/small.rar"}], **extra}


def test_pawchive_temp_link_parsing():
    html = (f'<a href="{T1.replace("&", "&amp;")}">dl</a> <img src=\'https://t1.pawchive.pw/f/a/B.png?e=1\'>'
            '<a href="https://evil.example/f/x/y.rar">no</a><a href="https://file.pawchive.pw/data/a.rar">no</a>')
    assert pw_parsing.pawchive_temp_links(html) == {          # &amp; decoded, name decoded and lower-cased
        "big pack.rar": T1, "b.png": "https://t1.pawchive.pw/f/a/B.png?e=1"}
    assert pw_parsing.pawchive_temp_links("") == {} and pw_parsing.pawchive_temp_links(None) == {}

    post = _t1_post()
    assert [a["name"] for a in pw_parsing.pawchive_deferred_attachments(post)] == ["Big Pack.rar"]
    assert [f["name"] for f in pw_parsing.pawchive_post_files(post)] == ["small.rar"]   # deferred: no path, no file yet
    assert pw_parsing.pawchive_post_page_url(post) == PAGE
    assert pw_parsing.pawchive_post_page_url({"id": "1"}) is None
    item = pw_parsing.pawchive_temp_item(post["attachments"][0], T1)
    assert item["urls"] == [T1] and item["key"] == "https://t1.pawchive.pw/f/9f8e7d/Big%20Pack.rar"
    assert item["name"] == "Big Pack.rar" and pw_parsing.is_pawchive_temp_url(T1)
    assert not pw_parsing.is_pawchive_temp_url("https://file.pawchive.pw/data/a.rar")


def test_pawchive_cookie_is_asked_only_for_t1_files_and_only_once(tmp_path):
    asked: list[int] = []

    def resolver():
        asked.append(1)
        return "tok_" + "a" * 20

    small = "/00/00/small.rar"
    page_html = f'<a href="{T1}">x</a>'.replace("&", "&amp;")

    # a post without temporary files never asks
    client = _PawClient(blobs={_data(small): b"s"})
    auth = pw_access.PawchiveAuth(resolver)
    run = pw_posts.PawchiveRun(None, auth)
    asyncio.run(pw_posts.pawchive_process_posts(
        client, [{"id": "1", "file": {"name": "small.rar", "path": small}}], Path(tmp_path), {}, True, run))
    assert asked == [] and not auth.asked and len(client.cookies.jar) == 0

    # a temporary file asks once, before it is downloaded; two posts still ask once
    big = T1.split("?")[0]
    client = _PawClient(blobs={_data(small): b"s", big: b"B" * 50}, html={PAGE: [page_html]})
    history: dict = {}
    run = pw_posts.PawchiveRun(None, auth)
    posts = [_t1_post(), _t1_post(id="78", attachments=[{"name": "Big Pack.rar", "deferred": True}])]
    client.html["https://pawchive.pw/patreon/user/9/post/78"] = [page_html]
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), history, True, run))
    assert asked == [1]
    assert (run.ok, run.failed) == (2, 0)              # small.rar + the same big file, handled once
    assert (Path(tmp_path) / "Big Pack.rar").read_bytes() == b"B" * 50
    assert T1 in client.fetched                        # the signed link, query kept
    assert history[big] == str(Path(tmp_path) / "Big Pack.rar")    # ... but stored without it
    assert [c.name for c in client.cookies.jar] == ["session"]


def test_pawchive_login_is_asked_when_the_page_hides_the_link(tmp_path):
    asked: list[int] = []
    big = T1.split("?")[0]
    # first read (no cookie): no link; second read (with the cookie): the link
    client = _PawClient(blobs={big: b"B"}, html={PAGE: ["<html>log in</html>", f'<a href="{T1}">x</a>']})
    auth = pw_access.PawchiveAuth(lambda: (asked.append(1), "tok_" + "b" * 20)[1])
    run = pw_posts.PawchiveRun(None, auth)
    asyncio.run(pw_posts.pawchive_process_posts(
        client, [_t1_post(attachments=[{"name": "Big Pack.rar", "deferred": True}])],
        Path(tmp_path), {}, True, run))
    assert asked == [1] and (run.ok, run.failed) == (1, 0)
    assert client.api_calls == [PAGE, PAGE]


def test_pawchive_login_cancelled_counts_the_missing_file(tmp_path, ):
    asked: list[int] = []
    client = _PawClient(html={PAGE: ["<html>log in</html>"]})
    auth = pw_access.PawchiveAuth(lambda: asked.append(1))          # dialog cancelled -> None
    run = pw_posts.PawchiveRun(None, auth)
    asyncio.run(pw_posts.pawchive_process_posts(
        client, [_t1_post(attachments=[{"name": "Big Pack.rar", "deferred": True}])],
        Path(tmp_path), {}, True, run))
    assert asked == [1] and (run.ok, run.failed) == (0, 1)
    assert client.fetched == [] and len(client.cookies.jar) == 0
    assert client.api_calls == [PAGE]                         # no cookie: nothing to read again

    # a post with no way to find its page is counted as failed, without asking
    asked.clear()
    run = pw_posts.PawchiveRun(None, pw_access.PawchiveAuth(lambda: asked.append(1)))
    asyncio.run(pw_posts.pawchive_process_posts(
        _PawClient(), [{"id": "5", "attachments": [{"name": "x.rar", "deferred": True}]}],
        Path(tmp_path), {}, True, run))
    assert asked == [] and run.failed == 1


def test_pawchive_auth_scopes_the_cookie_to_pawchive():
    client = net.make_client(extra_headers={"Referer": pw_parsing.PAWCHIVE_URL + "/"}, http2=False)
    try:
        auth = pw_access.PawchiveAuth(lambda: "tok_" + "c" * 20)
        assert auth.ensure(client) is True and auth.ensure(client) is True   # asked once, set each time

        def cookie_header(url):
            req = client.build_request("GET", url)
            client.cookies.set_cookie_header(req)
            return req.headers.get("cookie", "")
        for url in ("https://t1.pawchive.pw/f/h/a.rar", "https://file.pawchive.pw/data/a.rar",
                    "https://pawchive.pw/api/v1/posts"):
            assert "session=tok_" in cookie_header(url), url
        for url in ("https://mega.nz/file/x", "https://api.gofile.io/x", "https://evilpawchive.pw/x"):
            assert cookie_header(url) == "", url
    finally:
        asyncio.run(client.aclose())

    none = pw_access.PawchiveAuth(lambda: None)
    c2 = net.make_client()
    try:
        assert none.ensure(c2) is False and none.asked and len(c2.cookies.jar) == 0
    finally:
        asyncio.run(c2.aclose())


def test_pawchive_session_cleaning_and_shape():
    from kkafio.services.content_sources.pawchive import session as ps
    tok = "eyJhbGciOiJIUzI1NiJ9.abc-DEF_123.sig456"
    assert ps.looks_valid(tok) and not ps.looks_valid("") and not ps.looks_valid("short")
    assert not ps.looks_valid("has space in it, not a token")
    assert ps.clean_session(tok) == tok
    assert ps.clean_session(f"session={tok}") == tok
    assert ps.clean_session(f'  "{tok}"  ') == tok
    assert ps.clean_session(f"theme=dark; session={tok}; other=1") == tok      # a whole Cookie header
    assert ps.clean_session("theme=dark") == "" and ps.clean_session("") == ""
    assert ps.USER_AGENT.startswith("KKAFIO/")


def test_pawchive_session_get_or_prompt(monkeypatch):
    import dataclasses
    from kkafio.services.content_sources import session_flow
    from kkafio.services.content_sources.pawchive import session as ps
    tok, new = "eyJ" + "a" * 30, "eyJ" + "n" * 30
    saved: list[str] = []
    prompts: list[int] = []
    state = {"stored": tok, "valid": {tok: True, new: True}, "paste": new}

    class FakeStore:
        def load(self):
            return {"session": state["stored"]} if state["stored"] else {}

        def save(self, data):
            saved.append(data["session"])
            return True

    monkeypatch.delenv(ps.ENV_VAR, raising=False)
    monkeypatch.setattr(ps, "SPEC", dataclasses.replace(
        ps.SPEC, store=FakeStore(), is_valid=lambda s: state["valid"].get(s)))
    monkeypatch.setattr(session_flow, "_prompt", lambda spec: (prompts.append(1), state["paste"])[1])

    assert ps.get_or_prompt() == tok and prompts == []               # stored + valid: no dialog

    state["valid"][tok] = None                                       # cannot be checked: used as it is
    assert ps.get_or_prompt() == tok and prompts == []

    state["valid"][tok] = False                                      # expired: a new one is asked for
    assert ps.get_or_prompt() == new and prompts == [1] and saved == [new]

    state["stored"] = None                                           # nothing stored: asked for
    saved.clear(); prompts.clear()
    assert ps.get_or_prompt() == new and prompts == [1] and saved == [new]

    state["paste"] = None                                            # dialog cancelled
    saved.clear()
    assert ps.get_or_prompt() is None and saved == []

    state["paste"] = "not a token"                                   # clearly not a cookie
    assert ps.get_or_prompt() is None and saved == []

    state["paste"] = new; state["valid"][new] = False                # pawchive rejects it
    assert ps.get_or_prompt() is None and saved == []

    state["valid"][new] = None                                       # cannot be checked: saved anyway
    assert ps.get_or_prompt() == new and saved == [new]

    monkeypatch.setenv(ps.ENV_VAR, f"session={tok}")                 # the environment comes first
    state["valid"][tok] = True
    prompts.clear()
    assert ps.get_or_prompt() == tok and prompts == []


def test_session_store_roundtrip_and_failures(tmp_path):
    from kkafio.core.session_store import SessionStore
    store = SessionStore(tmp_path / "sub" / "svc.json", "TEST")
    assert store.load() == {}                                         # missing file
    assert store.save({"session": "abc"}) and store.load() == {"session": "abc"}
    assert store.update(extra=1) and store.load() == {"session": "abc", "extra": 1}   # merged
    assert not list((tmp_path / "sub").glob("*.part"))                # atomic write leaves no temp file
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() == {}                                         # corrupt -> as if empty
    store.path.write_text("[1, 2]", encoding="utf-8")
    assert store.load() == {}                                         # not an object -> as if empty
    blocked = SessionStore(tmp_path / "svc.json" / "x.json", "TEST")  # parent is a file: write fails softly
    (tmp_path / "svc.json").write_text("", encoding="utf-8")
    assert blocked.save({"a": 1}) is False


def test_session_flow_without_site_check_and_with_env(monkeypatch):
    import dataclasses
    from kkafio.services.content_sources import session_flow
    from kkafio.services.content_sources.pixiv import session as px
    good, other = "12345678_" + "a" * 20, "87654321_" + "b" * 20
    saved: list[str] = []
    state = {"stored": good, "paste": other}

    class FakeStore:
        def load(self):
            return {"session": state["stored"]} if state["stored"] else {}

        def save(self, data):
            saved.append(data["session"])
            return True

    prompts: list[int] = []
    monkeypatch.delenv(px.ENV_VAR, raising=False)
    monkeypatch.setattr(px, "SPEC", dataclasses.replace(px.SPEC, store=FakeStore()))
    monkeypatch.setattr(session_flow, "_prompt", lambda spec: (prompts.append(1), state["paste"])[1])

    assert px.get_or_prompt() == good and prompts == []               # shape alone decides: no network check
    monkeypatch.setenv(px.ENV_VAR, f"PHPSESSID={other}; path=/")
    assert px.get_or_prompt() == other and prompts == []              # environment first, cleaned
    monkeypatch.setenv(px.ENV_VAR, "garbage")
    assert px.get_or_prompt() == good                                 # malformed env ignored, stored used
    state["stored"] = "garbage"
    assert px.get_or_prompt() == other and prompts == [1] and saved == [other]   # malformed stored -> asked
    state["paste"] = "garbage"; saved.clear()
    assert px.get_or_prompt() is None and saved == []                 # pasted junk is not saved
    state["paste"] = None
    assert px.get_or_prompt() is None and saved == []                 # dialog cancelled


def test_session_flow_kkd_site_check(monkeypatch):
    import dataclasses
    from kkafio.services.content_sources import session_flow
    from kkafio.services.content_sources.koikatsucards import session as kk
    saved: list[str] = []
    state = {"stored": "old", "valid": {"old": False, "new": True}, "paste": "  new "}

    class FakeStore:
        def load(self):
            return {"session": state["stored"]} if state["stored"] else {}

        def save(self, data):
            saved.append(data["session"])
            return True

    monkeypatch.setattr(kk, "SPEC", dataclasses.replace(
        kk.SPEC, store=FakeStore(), is_valid=lambda s: state["valid"].get(s)))
    monkeypatch.setattr(session_flow, "_prompt", lambda spec: spec.clean(state["paste"]) or None)

    assert kk.get_or_prompt() == "new" and saved == ["new"]           # expired -> new one, trimmed, saved
    state["valid"]["new"] = False; saved.clear()
    assert kk.get_or_prompt() is None and saved == []                 # new one rejected too
    state["valid"]["new"] = None
    assert kk.get_or_prompt() == "new" and saved == ["new"]           # cannot be checked: saved anyway


def test_pawchive_session_validation_status_mapping(monkeypatch):
    import httpx
    from kkafio.services.content_sources.pawchive import session as ps
    seen: dict = {}

    def fake_get(status):
        def _get(url, **kw):
            seen.update(url=url, **kw)
            if status == "boom":
                raise httpx.ConnectError("down")
            return httpx.Response(status, request=httpx.Request("GET", url))
        return _get

    expected = {200: True, 302: False, 303: False, 401: False,
                403: None, 429: None, 503: None, "boom": None}   # 403 may be a bot block: no verdict
    for status, verdict in expected.items():
        monkeypatch.setattr(ps.httpx, "get", fake_get(status))
        assert ps._is_valid("tok_" + "d" * 20) is verdict, status
    assert seen["url"] == ps.ACCOUNT_URL and seen["follow_redirects"] is False   # a redirect IS the answer
    assert seen["headers"]["User-Agent"] == ps.USER_AGENT
    assert [(c.name, c.domain) for c in seen["cookies"].jar] == [("session", "pawchive.pw")]


# --- pawchive: throttle (pacing, adaptive concurrency, shared 429 cooldown) -------

def test_throttle_scales_up_and_backs_off(monkeypatch):
    monkeypatch.setattr(pw_access, "PAWCHIVE_COOLDOWN", 14.0)
    t = pw_access.PawchiveThrottle()
    assert (t.limit, t.ceiling) == (2, 4)                     # starts low, may grow to 4

    for _ in range(3):
        t.on_success()
    assert t.limit == 3                                       # every 3 clean files, one more worker
    for _ in range(30):
        t.on_success()
    assert (t.limit, t.ceiling) == (4, 4)                     # never above the ceiling

    wait = t.on_rate_limited()                                # first 429 of a burst
    assert wait == 14.0 and (t.ceiling, t.limit) == (3, 2)    # ceiling = old limit - 1, limit lower still
    assert t.cooldown_until > 0
    t.on_rate_limited()                                       # same burst: other refused workers
    assert (t.ceiling, t.limit) == (3, 2)                     # ... must not lower it again

    before = t.limit
    for _ in range(9):                                        # no scaling up inside the cooldown
        t.on_success()
    assert t.limit == before

    t.cooldown_until = 0.0                                    # cooldown over
    t.clean = 0
    for _ in range(3):
        t.on_success()
    assert t.limit == 3                                       # grows again, up to the new ceiling
    t.clean = 49
    t.on_success()                                            # 50 clean files: probe a higher ceiling
    assert t.ceiling == 4 and t.clean == 0

    low = pw_access.PawchiveThrottle()                              # at the bottom it stays at 1
    low.on_rate_limited()
    assert (low.ceiling, low.limit) == (1, 1)
    low.cooldown_until = 0.0
    low.on_rate_limited()
    assert (low.ceiling, low.limit) == (1, 1)


def test_throttle_cooldown_length(monkeypatch):
    monkeypatch.setattr(pw_access, "PAWCHIVE_COOLDOWN", 14.0)
    t = pw_access.PawchiveThrottle()
    assert t.on_rate_limited(retry_after=3) == 14.0           # never shorter than the base cooldown
    t.cooldown_until = 0.0
    assert t.on_rate_limited(retry_after=30) == 30.0          # Retry-After wins when longer
    t.cooldown_until = 0.0
    assert t.on_rate_limited(retry_after=9999) == pw_access.PAWCHIVE_COOLDOWN_CAP     # ... within the cap
    t.cooldown_until = 0.0
    assert t.on_rate_limited(streak=3) == 42.0                # longer after repeated 429s
    t.cooldown_until = 0.0
    assert t.on_rate_limited(streak=50) == 56.0               # streak factor is capped at 4


def test_retry_after_parsing():
    import datetime as _dt
    import email.utils
    import httpx

    def err(value):
        headers = {} if value is None else {"Retry-After": value}
        resp = httpx.Response(429, headers=headers, request=httpx.Request("GET", "https://x/"))
        return httpx.HTTPStatusError("429", request=resp.request, response=resp)

    assert net.retry_after_seconds(err("7")) == 7.0
    assert net.retry_after_seconds(err("9999")) == 120.0
    assert net.retry_after_seconds(err(None)) is None
    assert net.retry_after_seconds(err("soon")) is None
    assert net.retry_after_seconds(RuntimeError("no response")) is None
    when = email.utils.format_datetime(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=60))
    assert 50 <= net.retry_after_seconds(err(when)) <= 60     # an HTTP date works too
    assert net.is_rate_limited(err("1")) and not net.is_rate_limited(RuntimeError("429"))


def test_throttle_pacing_gate_and_slots(monkeypatch):
    import time
    monkeypatch.setattr(pw_access, "PAWCHIVE_FILE_DELAY", (0.06, 0.06))
    monkeypatch.setattr(pw_access, "PAWCHIVE_PAGE_DELAY", (0.06, 0.06))

    async def scenario():
        t = pw_access.PawchiveThrottle()
        t0 = time.monotonic()
        await asyncio.gather(*(t.pace("api") for _ in range(3)))   # three requests: two gaps
        assert time.monotonic() - t0 >= 0.11
        t0 = time.monotonic()
        await asyncio.gather(*(t.pace("file") for _ in range(3)))  # files are paced separately
        assert time.monotonic() - t0 >= 0.11

        t.cooldown_until = time.monotonic() + 0.15                 # a cooldown holds back everything
        t0 = time.monotonic()
        await t.gate()
        assert time.monotonic() - t0 >= 0.13
        t.cooldown_until = time.monotonic() + 0.15
        t0 = time.monotonic()
        async with t.slot():
            assert time.monotonic() - t0 >= 0.13                   # ... a new download included
        assert t.active == 0

        # never more downloads in flight than the current limit
        monkeypatch.setattr(pw_access, "PAWCHIVE_FILE_DELAY", (0.0, 0.0))
        for limit in (1, 2):
            t = pw_access.PawchiveThrottle()
            t.limit = limit
            peak = 0

            async def job():
                nonlocal peak
                async with t.slot():
                    peak = max(peak, t.active)
                    await asyncio.sleep(0.02)
            await asyncio.gather(*(job() for _ in range(6)))
            assert peak == limit and t.active == 0

        # a slot is given back when the download inside it fails or is cancelled
        t = pw_access.PawchiveThrottle()
        with pytest.raises(RuntimeError):
            async with t.slot():
                raise RuntimeError("boom")
        assert t.active == 0
    asyncio.run(scenario())


def test_pawchive_file_429_waits_and_retries_the_same_host(tmp_path):
    path = "/01/aa/pack.rar"
    client = _PawClient(blobs={_data(path): b"Rar!" * 4}, limited={_data(path): 2}, retry_after=0)
    throttle = pw_access.PawchiveThrottle()
    run = pw_posts.PawchiveRun(None, throttle=throttle)
    posts = [{"id": "1", "file": {"name": "pack.rar", "node": 2, "path": path}}]
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), {}, True, run))

    assert (run.ok, run.failed) == (1, 0)                     # not failed: waited and tried again
    assert client.fetched == [_data(path) + "?f=pack.rar"] * 3   # same host each time, node untouched
    gaps = [b - a for a, b in zip(client.times, client.times[1:])]
    assert min(gaps[:2]) >= pw_access.PAWCHIVE_COOLDOWN * 0.8        # the cooldown was really waited out
    assert throttle.ceiling == 1 and throttle.limit == 1      # and concurrency went down for good
    assert (Path(tmp_path) / "pack.rar").read_bytes() == b"Rar!" * 4


def test_pawchive_file_gives_up_after_repeated_429(tmp_path, monkeypatch):
    monkeypatch.setattr(pw_access, "PAWCHIVE_COOLDOWN", 0.005)
    path = "/01/aa/pack.rar"
    node = "https://n2.pawchive.pw/data" + path
    client = _PawClient(limited={_data(path): 1000})
    run = pw_posts.PawchiveRun(None)
    posts = [{"id": "1", "file": {"name": "pack.rar", "node": 2, "path": path}}]
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), {}, True, run))
    assert (run.ok, run.failed) == (0, 1)
    file_host = [u for u in client.fetched if u.startswith(_data(path))]
    assert len(file_host) == pw_access.PAWCHIVE_MAX_RATE_LIMITED + 1      # a bounded number of waits ...
    assert client.fetched[-1].startswith(node)                     # ... then the next host gets its turn
    assert not list(Path(tmp_path).iterdir())


def test_pawchive_api_429_uses_the_shared_cooldown(tmp_path):
    post_url = "https://pawchive.pw/patreon/user/9/post/77"
    api = pw_parsing.pawchive_api_url(post_url).split("?")[0]
    path = "/00/00/a.rar"
    single = {"id": "77", "user": "9", "service": "patreon", "file": {"name": "a.rar", "path": path}}
    client = _PawClient(singles={"77": single}, blobs={_data(path): b"x"},
                        limited={api: 2}, retry_after=0)
    throttle = pw_access.PawchiveThrottle()
    ok, skipped, failed = asyncio.run(pw_downloader.download_pawchive(
        client, post_url, Path(tmp_path), {}, True, throttle=throttle))
    assert (ok, failed) == (1, 0)
    assert len([u for u in client.api_calls if u.split("?")[0] == api]) == 3   # 2 x 429, then the post
    assert throttle.ceiling < pw_access.PAWCHIVE_PARALLEL                              # the API 429 slowed the run

    # a listing page that is refused again and again fails that page only
    listing = "https://pawchive.pw/patreon/user/9"
    page_api = pw_parsing.pawchive_api_url(listing, 1).split("?")[0]
    bad = _PawClient(limited={page_api: 1000})
    pw_access.PAWCHIVE_COOLDOWN = 0.002
    try:
        ok, skipped, failed = asyncio.run(pw_downloader.download_pawchive(
            bad, listing, Path(tmp_path), {}, True, page_start=1, page_end=1))
    finally:
        pw_access.PAWCHIVE_COOLDOWN = 0.05
    assert ok == 0 and failed >= 1
    assert len(bad.api_calls) == pw_access.PAWCHIVE_MAX_RATE_LIMITED + 1


def test_get_with_retry_can_hand_429_to_the_caller():
    import httpx
    url = "https://pawchive.pw/api/v1/x"
    client = _PawClient(limited={url: 5})
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(net.get_with_retry(client, url, retry_429=False))
    assert len(client.api_calls) == 1                          # raised at once, no private backoff


def test_pawchive_skipped_files_cost_no_slot_or_delay(tmp_path, monkeypatch):
    monkeypatch.setattr(pw_access, "PAWCHIVE_FILE_DELAY", (30.0, 30.0))   # a paced file would take 30 s
    paths = [f"/00/00/f{i}.rar" for i in range(4)]
    posts = [{"id": str(i), "file": {"name": f"f{i}.rar", "path": p}} for i, p in enumerate(paths)]
    history = {_data(p): str(Path(tmp_path) / f"f{i}.rar") for i, p in enumerate(paths)}
    client = _PawClient()
    run = pw_posts.PawchiveRun(None)
    asyncio.run(asyncio.wait_for(
        pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), history, True, run), timeout=3))
    assert (run.ok, run.skipped, run.failed) == (0, 4, 0) and client.fetched == []


# --- file type restrictions: pixiv PNG only, pawchive archives + PNG --------------

def test_pixiv_artwork_images_are_png_only(monkeypatch):
    base = "https://i.pximg.net/img-original/img/2024/01/01/"
    pages = [
        {"urls": {"original": base + "1_p0.png"}, "width": 252, "height": 352},      # kept
        {"urls": {"original": base + "1_p1.jpg"}, "width": 252, "height": 352},      # right size, wrong type
        {"urls": {"original": base + "1_p2.gif"}, "width": 320, "height": 180},      # wrong type
        {"urls": {"original": base + "1_p3.PNG?x=1"}, "width": 320, "height": 180},  # kept: case + query
        {"urls": {"original": base + "1_p4.png"}, "width": 1920, "height": 1080},    # PNG, wrong size
        {"urls": {}, "width": 252, "height": 352},                                    # no original: ignored
    ]

    async def fake_api(client, endpoint, params=None):
        return pages
    monkeypatch.setattr(pixiv, "_pixiv_api", fake_api)

    urls, rejected = asyncio.run(pixiv._pixiv_artwork_images(None, "1"))
    assert urls == [base + "1_p0.png", base + "1_p3.PNG?x=1"]
    assert rejected == 3                                       # jpg + gif + the 1920x1080 PNG
    assert pixiv.PIXIV_EXTENSIONS == (".png",)


def test_pawchive_only_archives_and_png_are_downloaded():
    assert pw_parsing.PAWCHIVE_EXTENSIONS == (".rar", ".zip", ".7z", ".zipmod", ".png")
    post = {"id": "1",
            "file": {"name": "cover.jpg", "node": 2, "path": "/aa/bb/cover.jpg"},
            "attachments": [
                {"name": "cover.jpg", "node": 2, "path": "/aa/bb/cover.jpg"},      # the main file again
                {"name": "mod.zipmod", "node": 2, "path": "/00/01/mod.zipmod"},
                {"name": "Pack.RAR", "node": 2, "path": "/00/02/pack.rar"},        # case-insensitive
                {"name": "chars.7z", "node": 2, "path": "/00/03/chars.7z"},
                {"name": "bundle.zip", "node": 2, "path": "/00/04/bundle.zip"},
                {"name": "card.png", "node": 2, "path": "/00/05/card.png"},
                {"name": "notes.txt", "node": 2, "path": "/00/06/notes.txt"},
                {"name": "clip.mp4", "node": 2, "path": "/00/07/clip.mp4"},
                {"name": "photo.jpeg", "node": 2, "path": "/00/08/photo.jpeg"},
                {"name": "anim.gif", "node": 2, "path": "/00/09/anim.gif"},
                {"name": "setup.exe", "node": 2, "path": "/00/0a/setup.exe"},
                {"name": "My Mod v1.2", "node": 2, "path": "/00/0b/hash.zip"},     # odd display name, real .zip
                {"name": "readme", "node": 2, "path": "/00/0c/readme"},            # no extension at all
            ]}
    excluded: list[str] = []
    files = pw_parsing.pawchive_post_files(post, excluded)
    assert [f["name"] for f in files] == [
        "mod.zipmod", "Pack.RAR", "chars.7z", "bundle.zip", "card.png", "My Mod v1.2"]
    assert excluded == ["cover.jpg", "notes.txt", "clip.mp4", "photo.jpeg", "anim.gif",
                        "setup.exe", "readme"]                  # the repeated main file is counted once
    assert [f["check_size"] for f in files] == [False, False, False, False, True, False]
    assert [f["name"] for f in pw_parsing.pawchive_post_files(post)] == [f["name"] for f in files]   # `excluded` is optional

    # a full URL path is judged by its file name, not its query string
    t1 = {"file": {"name": "big.rar", "path": "https://t1.pawchive.pw/f/h/big.rar?e=1&s=x.jpg"},
          "attachments": [{"name": "x.mp4", "path": "https://t1.pawchive.pw/f/h/x.mp4?e=1&s=y.zip"}]}
    assert [f["name"] for f in pw_parsing.pawchive_post_files(t1)] == ["big.rar"]


def test_pawchive_deferred_attachments_are_filtered_before_the_page_is_read(tmp_path):
    post = {"id": "77", "user": "9", "service": "patreon", "attachments": [
        {"name": "Big Pack.rar", "deferred": True},
        {"name": "movie.mp4", "deferred": True},
        {"name": "noext", "deferred": True},
    ]}
    excluded: list[str] = []
    assert [a["name"] for a in pw_parsing.pawchive_deferred_attachments(post, excluded)] == ["Big Pack.rar"]
    assert excluded == ["movie.mp4", "noext"]

    # only unwanted temporary files: no page request, no login prompt
    asked: list[int] = []
    client = _PawClient()
    run = pw_posts.PawchiveRun(None, pw_access.PawchiveAuth(lambda: asked.append(1)))
    only_video = {"id": "78", "user": "9", "service": "patreon",
                  "attachments": [{"name": "movie.mp4", "deferred": True}]}
    asyncio.run(pw_posts.pawchive_process_posts(client, [only_video], Path(tmp_path), {}, True, run))
    assert client.api_calls == [] and asked == [] and run.excluded == 1 and run.failed == 0


def test_pawchive_other_file_types_are_never_requested(tmp_path):
    keep, skip_ = "/00/00/keep.zip", "/00/00/skip.mp4"
    client = _PawClient(blobs={_data(keep): b"PK", _data(skip_): b"video"})
    run = pw_posts.PawchiveRun(None)
    posts = [{"id": "1", "file": {"name": "skip.mp4", "path": skip_},
              "attachments": [{"name": "keep.zip", "path": keep},
                              {"name": "skip.mp4", "path": skip_}]}]
    asyncio.run(pw_posts.pawchive_process_posts(client, posts, Path(tmp_path), {}, True, run))
    assert (run.ok, run.failed, run.excluded) == (1, 0, 1)
    assert client.fetched == [_data(keep) + "?f=keep.zip"]
    assert [f.name for f in Path(tmp_path).iterdir()] == ["keep.zip"]

# ---------------------------------------------------------------------------
# "follow illustrations" (pixiv descriptions)
# ---------------------------------------------------------------------------

def test_split_follow_flag():
    u = "https://www.pixiv.net/en/artworks/1"
    assert dc._split_follow_flag(u) == (u, False)
    assert dc._split_follow_flag(f"{u} | follow illustrations") == (u, True)
    assert dc._split_follow_flag(f"{u} | Follow-Illustrations") == (u, True)
    assert dc._split_follow_flag(f"{u} | f") == (u, True)
    assert dc._split_follow_flag(f"{u} | FI") == (u, True)
    assert dc._split_follow_flag(f"{u} | 1 | 3 | f") == (f"{u} | 1 | 3", True)
    assert dc._split_follow_flag(f"{u} | 1 | 3 | follow illustrations") == (f"{u} | 1 | 3", True)
    assert dc._split_follow_flag(f"{u} | follow | all") == (f"{u} | all", True)


def test_parse_links_keeps_follow_flag():
    task = dc.DownloadContents.__new__(dc.DownloadContents)
    task.links = ("https://www.pixiv.net/en/artworks/1 | follow illustrations\n"
                  "https://www.pixiv.net/en/users/2 | 2 | follow illustrations\n"
                  "https://www.pixiv.net/en/artworks/3")
    assert task._parse_links() == [
        ("https://www.pixiv.net/en/artworks/1", None, None, True),
        ("https://www.pixiv.net/en/users/2", 2, 2, True),
        ("https://www.pixiv.net/en/artworks/3", None, None, False),
    ]


def test_pixiv_description_links():
    desc = (
        'See <a href="/artworks/111">illust/111</a> and '
        'https://www.pixiv.net/en/artworks/222, also '
        '<a href="/jump.php?https%3A%2F%2Fmega.nz%2Ffile%2FABCDEFGH%23KEYKEYKEY">mega</a> '
        'and <a href="/jump.php?url=https%3A%2F%2Fdrive.google.com%2Ffile%2Fd%2F1AbCdEfGhIjKlMnOpQrStUvWxYz%2Fview">gd</a> '
        'https://gofile.io/d/AbC123 https://www.pixiv.net/en/artworks/111'
    )
    ids, links = pixiv.pixiv_description_links(desc)
    assert ids == ["111", "222"]
    assert links == [
        "https://mega.nz/file/ABCDEFGH#KEYKEYKEY",
        "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view",
        "https://gofile.io/d/AbC123",
    ]
    assert pixiv.pixiv_description_links("") == ([], [])
    assert pixiv.pixiv_description_links(None) == ([], [])


def test_pixiv_follow_illustrations_visits_each_artwork_once(tmp_path, monkeypatch):
    # 1 -> 2 (+ mega link), 2 -> 1 (loop) and 3, 3 -> 2 again and a mega link seen before.
    descriptions = {
        "1": 'illust/2',
        "2": 'illust/1 illust/3 https://mega.nz/file/AAAAAAAA#KKKKKKKK',
        "3": 'illust/2 https://mega.nz/file/AAAAAAAA#KKKKKKKK https://gofile.io/d/Zed999',
    }
    described: list[str] = []
    images: list[str] = []
    external: list[str] = []

    class _Resp:
        def __init__(self, body): self._b = body
        def raise_for_status(self): pass
        def json(self): return {"error": False, "body": self._b}

    class _Client:
        async def get(self, url, **kw):
            aid = url.split("/illust/")[1].split("?")[0].split("/")[0]
            if url.split("?")[0].endswith("/pages"):
                return _Resp([{"urls": {"original": f"https://i.pximg.net/{aid}.png"},
                               "width": 252, "height": 352}])
            described.append(aid)
            return _Resp({"description": descriptions[aid]})

    async def fake_download_files(client, urls, directory, name_fn, history, skip):
        images.extend(urls)
        return len(urls), 0, 0

    async def fake_external(link, externals, directory, history, skip):
        external.append(link)
        return 1, 0, 0

    monkeypatch.setattr(transfer, "download_files", fake_download_files)
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    monkeypatch.setattr(pixiv, "_download_external_link", fake_external)
    monkeypatch.setattr(pixiv, "get_with_retry", lambda client, url, **kw: client.get(url))

    state = pixiv.PixivFollowState()
    res = asyncio.run(pixiv.download_pixiv(
        _Client(), "https://www.pixiv.net/en/artworks/1", Path(tmp_path), {}, True,
        follow_illustrations=True, externals={}, follow_state=state))

    assert sorted(described) == ["1", "2", "3"]               # each description read once
    assert sorted(images) == [                                # root image + 2 and 3 (not 1 twice)
        "https://i.pximg.net/1.png", "https://i.pximg.net/2.png", "https://i.pximg.net/3.png"]
    assert external == ["https://mega.nz/file/AAAAAAAA#KKKKKKKK", "https://gofile.io/d/Zed999"]
    assert state.visited == {"1", "2", "3"}
    assert res == (5, 0, 0)


def test_pixiv_external_links_are_always_downloaded_but_artworks_only_when_followed(
        tmp_path, monkeypatch):
    descriptions = {
        "1": "illust/2 https://mega.nz/file/AAAAAAAA#KKKKKKKK",
        "2": "https://gofile.io/d/Zed999",
    }
    described: list[str] = []
    images: list[str] = []
    external: list[str] = []

    class _Resp:
        def __init__(self, body): self._b = body
        def raise_for_status(self): pass
        def json(self): return {"error": False, "body": self._b}

    class _Client:
        async def get(self, url, **kw):
            aid = url.split("/illust/")[1].split("?")[0].split("/")[0]
            if url.split("?")[0].endswith("/pages"):
                return _Resp([{"urls": {"original": f"https://i.pximg.net/{aid}.png"},
                               "width": 252, "height": 352}])
            described.append(aid)
            return _Resp({"description": descriptions[aid]})

    async def fake_download_files(client, urls, directory, name_fn, history, skip):
        images.extend(urls)
        return len(urls), 0, 0

    async def fake_external(link, externals, directory, history, skip):
        external.append(link)
        return 1, 0, 0

    monkeypatch.setattr(transfer, "download_files", fake_download_files)
    monkeypatch.setattr(history_store, "save_history", lambda h: None)
    monkeypatch.setattr(pixiv, "_download_external_link", fake_external)
    monkeypatch.setattr(pixiv, "get_with_retry", lambda client, url, **kw: client.get(url))

    # Without follow: the mega link of artwork 1 is downloaded, artwork 2 is not touched.
    res = asyncio.run(pixiv.download_pixiv(
        _Client(), "https://www.pixiv.net/en/artworks/1", Path(tmp_path), {}, True,
        externals={}, follow_state=pixiv.PixivFollowState()))
    assert described == ["1"]
    assert images == ["https://i.pximg.net/1.png"]
    assert external == ["https://mega.nz/file/AAAAAAAA#KKKKKKKK"]
    assert res == (2, 0, 0)


# ---------------------------------------------------------------------------
# Tag-filtered galleries: /users/ID/illustrations/TAG
# ---------------------------------------------------------------------------

TAG_URL = ("https://www.pixiv.net/en/users/3986523/illustrations/"
           "%E3%82%AD%E3%83%A3%E3%83%A9%E9%85%8D%E5%B8%83(%E3%82%B3%E3%82%A4%E3%82%AB%E3%83%84)")
TAG = "キャラ配布(コイカツ)"


def test_pixiv_tag_gallery_url_is_parsed():
    assert pixiv._parse_pixiv_url(TAG_URL) == ("user", "3986523", "illustrations")
    assert pixiv._pixiv_url_tag(TAG_URL) == TAG
    assert pixiv._pixiv_url_tag(TAG_URL.replace("/en/", "/")) == TAG
    assert pixiv._pixiv_url_tag("https://www.pixiv.net/en/users/3986523/illustrations") is None
    assert pixiv._pixiv_url_tag("https://www.pixiv.net/en/users/3986523") is None
    assert pixiv._pixiv_url_tag("https://www.pixiv.net/en/artworks/1") is None
    assert pixiv._pixiv_url_page(TAG_URL + "?p=2") == 2


def test_pixiv_tag_gallery_reads_only_the_tagged_works(tmp_path, monkeypatch):
    import httpx
    requested: list[dict] = []
    tagged = [str(i) for i in range(200, 100, -1)]          # 100 works -> 3 requests of 48

    class _Resp:
        def __init__(self, body): self._b = body
        def raise_for_status(self): pass
        def json(self): return {"error": False, "message": "", "body": self._b}

    class _Client:
        async def get(self, url):
            u = httpx.URL(url)
            if u.path == "/ajax/user/3986523/illusts/tag":
                q = dict(u.params)
                requested.append(q)
                off, lim = int(q["offset"]), int(q["limit"])
                return _Resp({"works": [{"id": i} for i in tagged[off:off + lim]],
                              "total": len(tagged)})
            if u.path.endswith("/pages"):
                return _Resp([])
            return _Resp({"description": ""})

    async def fake_download_files(client, urls, directory, name_fn, history, skip):
        return len(urls), 0, 0

    monkeypatch.setattr(transfer, "download_files", fake_download_files)
    monkeypatch.setattr(history_store, "save_history", lambda h: None)

    ids = asyncio.run(pixiv._pixiv_user_tag_artwork_ids(_Client(), "3986523", "illustrations", TAG))
    assert ids == tagged
    assert [q["offset"] for q in requested] == ["0", "48", "96"]
    assert all(q["tag"] == TAG for q in requested)

    requested.clear()
    asyncio.run(pixiv.download_pixiv(_Client(), TAG_URL, Path(tmp_path), {}, True, 2, 2))
    assert requested and all(q["tag"] == TAG for q in requested)   # tag was used, not the whole profile
