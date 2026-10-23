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


# ---------------------------------------------------------------------------
# pixiv
# ---------------------------------------------------------------------------

def test_pixiv_url_parsing_and_normalisation():
    P = dc._parse_pixiv_url
    assert P("https://www.pixiv.net/en/users/85712366") == ("user", "85712366", "artworks")
    assert P("https://www.pixiv.net/en/users/85712366/illustrations") == ("user", "85712366", "illustrations")
    assert P("https://www.pixiv.net/users/85712366/manga") == ("user", "85712366", "manga")
    assert P("https://www.pixiv.net/en/artworks/151074285") == ("artwork", "151074285", "")
    assert P("https://www.pixiv.net/ja/artworks/151074285") == ("artwork", "151074285", "")
    assert P("https://www.pixiv.net/member.php?id=123") == ("user", "123", "artworks")
    assert P("https://www.pixiv.net/en/users/85712366/bookmarks/artworks") is None
    assert P("https://www.pixiv.net/en/ranking.php?mode=daily") is None

    # bare users/ID becomes users/ID/artworks
    assert dc._normalize_pixiv_url("https://www.pixiv.net/en/users/85712366") \
        == "https://www.pixiv.net/en/users/85712366/artworks"
    assert dc._normalize_pixiv_url("https://www.pixiv.net/en/users/85712366/illustrations") \
        == "https://www.pixiv.net/en/users/85712366/illustrations"


def test_pixiv_url_is_not_confused_with_other_hosts():
    assert dc._is_pixiv_url("https://www.pixiv.net/en/artworks/1")
    assert not dc._is_pixiv_url("https://db.bepis.moe/koikatsu?q=www.pixiv.net")
    assert not dc._is_pixiv_url("https://evilpixiv.net/artworks/1")


def test_pixiv_size_filter():
    ok = dc._pixiv_size_matches
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

    monkeypatch.setattr(dc, "_download_files", fake_download_files)
    monkeypatch.setattr(dc, "_save_history", lambda h: None)

    history: dict = {}
    res = asyncio.run(dc._download_pixiv(
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

    monkeypatch.setattr(dc, "_download_files", fake_download_files)
    monkeypatch.setattr(dc, "_save_history", lambda h: None)

    ok, skipped, failed = asyncio.run(dc._download_pixiv(
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

    assert asyncio.run(dc._pixiv_user_artwork_ids(_C(), "1", "artworks")) == []


def test_pixiv_cookie_is_scoped_to_pixiv_only():
    import httpx
    client = dc._make_client(cookies={"PHPSESSID": "1_abc"},
                             cookie_domain=dc.PIXIV_COOKIE_DOMAIN,
                             extra_headers={"Referer": dc.PIXIV_URL + "/"})
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
    from kkafio.services import pixiv_session as ps
    good = "12345678_AbCdEf0123456789AbCdEf0123456789"
    assert ps.looks_valid(good)
    assert ps.clean_session(f"PHPSESSID={good}; path=/") == good
    assert ps.clean_session(f'  "{good}" ') == good
    assert not ps.looks_valid("not a cookie") and not ps.looks_valid("")


def test_pixiv_page_selection():
    ids = [str(i) for i in range(100, 0, -1)]            # 100 works -> 3 pages (48/48/4)
    sel = dc._pixiv_select_page_ids
    assert sel(ids, 1, 1) == ids[:48]
    assert sel(ids, 2, 2) == ids[48:96]
    assert sel(ids, 3, 3) == ids[96:]
    assert sel(ids, 2, None) == ids[48:]                  # "all" from page 2 on
    assert sel(ids, 1, None) == ids
    assert sel(ids, 3, 2) == ids[96:] + ids[48:96]        # reverse order
    assert sel(ids, 4, 4) == [] and sel(ids, 0, 0) == []   # out of range
    assert dc._pixiv_url_page("https://www.pixiv.net/en/users/1/illustrations?p=2") == 2
    assert dc._pixiv_url_page("https://www.pixiv.net/en/users/1/illustrations") is None
    assert dc._pixiv_url_page("https://www.pixiv.net/en/users/1/illustrations?p=x") is None


def test_pixiv_directive_and_link_page(tmp_path, monkeypatch):
    works = {str(i): [(252, 352)] for i in range(1, 101)}
    seen: list[list[str]] = []

    async def fake_download_files(client, urls, directory, name_fn, history, skip):
        seen.append(list(urls)); return len(urls), 0, 0

    monkeypatch.setattr(dc, "_download_files", fake_download_files)
    monkeypatch.setattr(dc, "_save_history", lambda h: None)
    base = "https://www.pixiv.net/en/users/9/illustrations"

    def run(url, s=None, e=None):
        seen.clear()
        asyncio.run(dc._download_pixiv(_fake_pixiv_client(works), url, Path(tmp_path), {}, True, s, e))
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
    assert dc._parse_pixiv_url(SEARCH_URL) == ("search", KEYWORD, "artworks")
    assert dc._parse_pixiv_url("https://www.pixiv.net/en/search?q=a&type=illust") == ("search", "a", "illustrations")
    assert dc._parse_pixiv_url("https://www.pixiv.net/search?q=a&type=manga") == ("search", "a", "manga")
    assert dc._parse_pixiv_url("https://www.pixiv.net/search?q=a&type=novel") is None
    assert dc._parse_pixiv_url("https://www.pixiv.net/search") is None
    assert dc._parse_pixiv_url("https://www.pixiv.net/en/tags/%E5%8E%9F%E7%A5%9E/illustrations") \
        == ("search", "原神", "illustrations")
    assert dc._pixiv_url_page(SEARCH_URL + "&p=4") == 4


def test_pixiv_search_request_keeps_filters():
    endpoint, params = dc._pixiv_search_request(SEARCH_URL + "&p=9", 3)
    assert endpoint == "/search/artworks/" + "%E3%82%AD%E3%83%A3%E3%83%A9%E9%85%8D%E5%B8%83%28" \
        "%E3%82%B3%E3%82%A4%E3%82%AB%E3%83%84%29%20%E5%8E%9F%E7%A5%9E"
    d = dict(params)
    assert d["word"] == KEYWORD and d["p"] == "3"          # page argument beats ?p=9
    assert d["s_mode"] == "tag" and d["ai_type"] == "1" and d["r"] == "1"
    assert d["type"] == "all" and d["order"] == "date_d" and d["mode"] == "all"
    assert [k for k, _ in params].count("p") == 1
    _, p2 = dc._pixiv_search_request("https://www.pixiv.net/search?q=x&type=illust&order=date&mode=r18", 1)
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
            aid = u.path.split("/")[-2]
            calls["pages"].append(aid)
            return _Resp([{"urls": {"original": f"https://i.pximg.net/img-original/{aid}_p{i}.png"},
                           "width": w, "height": h} for i, (w, h) in enumerate(size_of[aid])])
    return _Client(), calls


def test_pixiv_search_page_skips_single_page_mismatches_without_extra_requests():
    client, calls = _fake_search_client(
        {1: [(1, 252, 352, 1), (2, 1000, 1000, 1), (3, 320, 180, 1), (4, 800, 600, 2)]},
        {"1": [(252, 352)], "3": [(320, 180)], "4": [(800, 600), (504, 704)]})
    urls, failed, empty = asyncio.run(dc._get_pixiv_search_page_urls(client, SEARCH_URL, 1))
    assert not empty and failed == 0
    assert sorted(calls["pages"]) == ["1", "3", "4"]       # work 2 never hit /pages
    assert sorted(urls) == [f"https://i.pximg.net/img-original/{n}.png" for n in ("1_p0", "3_p0", "4_p1")]
    assert asyncio.run(dc._get_pixiv_search_page_urls(client, SEARCH_URL, 2))[2] is True


def test_pixiv_search_paging_and_no_match_pages_do_not_stop_run(tmp_path, monkeypatch):
    # pages 1-4 have results but nothing matches; page 5 has a card; page 6 is empty
    works = {p: [(p * 10, 1000, 1000, 1)] for p in range(1, 5)}
    works[5] = [(50, 252, 352, 1)]
    client, calls = _fake_search_client(works, {"50": [(252, 352)]})
    got: list[str] = []

    async def fake_download_files(c, urls, directory, name_fn, history, skip):
        got.extend(urls); return len(urls), 0, 0

    monkeypatch.setattr(dc, "_download_files", fake_download_files)
    monkeypatch.setattr(dc, "_save_history", lambda h: None)

    run = lambda s, e: asyncio.run(dc._download_pixiv_search(
        client, SEARCH_URL, Path(tmp_path), {}, True, s, e))

    assert run(1, None) == (1, 0, 0)                        # "| all" runs through 4 empty-match pages
    assert got == ["https://i.pximg.net/img-original/50_p0.png"]
    assert [c["p"] for c in calls["search"]] == ["1", "2", "3", "4", "5", "6"]

    calls["search"].clear()
    run(None, None)                                         # no directive -> page 1 only
    assert [c["p"] for c in calls["search"]] == ["1"]
    calls["search"].clear()
    asyncio.run(dc._download_pixiv_search(client, SEARCH_URL + "&p=5", Path(tmp_path), {}, True, None, None))
    assert [c["p"] for c in calls["search"]] == ["5"]       # ?p= in the link


# ---------------------------------------------------------------------------
# gofile
# ---------------------------------------------------------------------------

def test_gofile_url_parsing():
    P = dc._parse_gofile_url
    assert P("https://gofile.io/d/AbC123") == ("AbC123", None)
    assert P("https://gofile.io/d/AbC123/") == ("AbC123", None)
    assert P("https://gofile.io/f/AbC-123") == ("AbC-123", None)
    assert P("https://gofile.io/d/AbC123?password=hunter2") == ("AbC123", "hunter2")
    assert P("https://gofile.io/d/AbC123#password=hunter2") == ("AbC123", "hunter2")
    assert P("https://gofile.io/") is None and P("https://gofile.io/contents/x") is None
    assert dc._is_gofile_url("https://gofile.io/d/x") and dc._is_gofile_url("https://www.gofile.io/d/x")
    assert not dc._is_gofile_url("https://notgofile.io/d/x")
    assert not dc._is_gofile_url("https://example.com/?u=gofile.io")


def test_gofile_website_token_matches_megaloader_formula(monkeypatch):
    import hashlib
    monkeypatch.delenv("GOFILE_SALT", raising=False)
    monkeypatch.setattr(dc.time, "time", lambda: 14400 * 7 + 5)
    expect = hashlib.sha256(
        f"{dc.GOFILE_USER_AGENT}::en-US::tok::7::9844d94d963d30".encode()).hexdigest()
    assert dc._gofile_website_token("tok") == expect
    monkeypatch.setenv("GOFILE_SALT", "newsalt")
    assert dc._gofile_website_token("tok") != expect


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
                             headers={"User-Agent": dc.GOFILE_USER_AGENT}, follow_redirects=True)


def _gf_file(fid, name):
    return {"type": "file", "id": fid, "name": name,
            "link": f"https://store1.gofile.io/download/web/{fid}/{name.replace(' ', '%20')}"}


def test_gofile_folder_download_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    monkeypatch.delenv("GOFILE_SALT", raising=False)
    tree = {
        "ROOT": {"name": "pack", "children": {
            "f1": _gf_file("f1", "card one.png"),
            "d1": {"type": "folder", "id": "SUB", "name": "extras"},
        }},
        "SUB": {"name": "extras", "children": {"f2": _gf_file("f2", "card one.png")}},  # same name!
    }
    record: list = []
    monkeypatch.setattr(dc, "_save_history", lambda h: None)

    async def go():
        client = _gofile_mock_client(tree, record)
        try:
            return await dc._download_gofile(client, "https://gofile.io/d/ROOT", Path(tmp_path), {}, True)
        finally:
            await client.aclose()

    assert asyncio.run(go()) == (2, 0, 0)
    assert (Path(tmp_path) / "card one.png").read_bytes().startswith(b"data:")
    assert (Path(tmp_path) / "extras" / "card one.png").exists()       # no overwrite

    api = [r for r in record if r[1].startswith("https://api.gofile.io/contents/ROOT")][0]
    assert api[2]["authorization"] == "Bearer GUEST"
    assert len(api[2]["x-website-token"]) == 64 and api[2]["x-bl"] == "en-US"
    assert api[2]["user-agent"] == dc.GOFILE_USER_AGENT
    assert sum(1 for r in record if r[1].endswith("/accounts")) == 1    # one guest account per client
    dl = [r for r in record if r[1].startswith("https://store1.gofile.io")]
    assert dl and all("accountToken=GUEST" in r[2].get("cookie", "") for r in dl)
    # the cookie is scoped to .gofile.io: sent to gofile hosts, never anywhere else
    import httpx
    jar = httpx.Cookies(); jar.set("accountToken", "GUEST", domain=dc.GOFILE_DOMAIN)
    other = httpx.Request("GET", "https://example.com/x"); jar.set_cookie_header(other)
    assert "cookie" not in other.headers


def test_gofile_password_is_sent_hashed(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    record: list = []
    monkeypatch.setattr(dc, "_save_history", lambda h: None)
    tree = {"ROOT": {"name": "x", "children": {"f": _gf_file("f", "a.png")}}}

    async def go():
        client = _gofile_mock_client(tree, record)
        try:
            return await dc._download_gofile(
                client, "https://gofile.io/d/ROOT?password=secret", Path(tmp_path), {}, True)
        finally:
            await client.aclose()

    assert asyncio.run(go()) == (1, 0, 0)
    assert "password=" + hashlib.sha256(b"secret").hexdigest() in \
        [r for r in record if "/contents/ROOT" in r[1]][0][1]


def test_gofile_api_errors_become_clear_messages(tmp_path, monkeypatch):
    monkeypatch.delenv("GOFILE_TOKEN", raising=False)
    import pytest
    for status, fragment in (("error-notFound", "not found"), ("error-passwordRequired", "password"),
                             ("error-notPremium", "GOFILE_SALT")):
        async def go():
            client = _gofile_mock_client({}, [], fail_status=status)
            try:
                return await dc._download_gofile(client, "https://gofile.io/d/X", Path(tmp_path), {}, True)
            finally:
                await client.aclose()
        with pytest.raises(RuntimeError, match=fragment):
            asyncio.run(go())


def test_resolve_filename_decodes_percent_escapes():
    class _R: headers = {}
    assert dc._resolve_filename(_R(), "https://store1.gofile.io/download/web/id/card%20one.png", "x") == "card one.png"
