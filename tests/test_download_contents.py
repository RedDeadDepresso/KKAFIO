"""Download Contents: the `url | page` pagination formats."""

import pytest
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


@pytest.fixture(autouse=True)
def _reset_gofile_secret_cache():
    dc._gofile_secret_cache.update(secret="", until=0.0, rejected_at=0.0, error="", error_until=0.0)
    yield
    dc._gofile_secret_cache.update(secret="", until=0.0, rejected_at=0.0, error="", error_until=0.0)


def test_gofile_website_token_formula():
    import hashlib
    import time as _t
    orig = dc.time.time
    dc.time.time = lambda: 14400 * 7 + 5
    try:
        expect = hashlib.sha256(
            f"{dc.GOFILE_USER_AGENT}::en-US::tok::7::somesecret".encode()).hexdigest()
        assert dc._gofile_website_token("tok", "somesecret") == expect
        assert dc._gofile_website_token("tok", "other") != expect
    finally:
        dc.time.time = orig


def test_gofile_secret_is_decoded_from_the_script():
    script = open(Path(__file__).parent / "data" / "gofile-wt.obf.js", encoding="utf-8").read()
    assert dc._gofile_secret_from(script) == "12af056dacea0b"
    import pytest
    with pytest.raises(ValueError):
        dc._gofile_secret_from("var nothing = 1;")


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
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
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
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
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
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
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
        dc._gofile_secret_cache["rejected_at"] = 0.0     # a rejection starts a cooldown


def test_gofile_rejection_starts_a_cooldown(monkeypatch):
    import pytest
    monkeypatch.setenv("GOFILE_SALT", "testsecret")
    dc._gofile_signature_rejected()
    with pytest.raises(RuntimeError, match="wrongly signed"):
        asyncio.run(dc._gofile_ensure_secret(None))


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
    assert dc._is_mega_url("https://mega.nz/file/abc#" + key32)
    assert dc._is_mega_url("https://mega.co.nz/#!abc!" + key32)
    assert not dc._is_mega_url("https://notmega.nz/file/abc")

    l = dc._parse_mega_link(f"https://mega.nz/file/abc#{key32}")
    assert (l["folder"], l["handle"], l["node"], len(l["key"])) == (False, "abc", "", 32)
    l = dc._parse_mega_link(f"https://mega.nz/folder/FOL#{key16}")
    assert (l["folder"], l["handle"], len(l["key"])) == (True, "FOL", 16)
    l = dc._parse_mega_link(f"https://mega.nz/folder/FOL#{key16}/file/NODE")
    assert l["node"] == "NODE"
    l = dc._parse_mega_link(f"https://mega.nz/folder/FOL#{key16}/folder/SUB")   # subtree: whole share
    assert l["node"] == ""
    l = dc._parse_mega_link(f"https://mega.nz/#!abc!{key32}")
    assert (l["folder"], l["handle"]) == (False, "abc")
    l = dc._parse_mega_link(f"https://mega.nz/#F!FOL!{key16}!NODE")
    assert (l["folder"], l["handle"], l["node"]) == (True, "FOL", "NODE")

    with pytest.raises(dc.MegaError, match="no key"):
        dc._parse_mega_link("https://mega.nz/file/abc")
    with pytest.raises(dc.MegaError, match="expected 32"):
        dc._parse_mega_link(f"https://mega.nz/file/abc#{key16}")
    with pytest.raises(dc.MegaError, match="not a file or folder"):
        dc._parse_mega_link("https://mega.nz/")


def test_mega_attrs_reject_a_wrong_key():
    import pytest
    attrs = _enc_attrs("card.png", bytes(16))
    assert dc._mega_decrypt_attrs(attrs, bytes(16)) == {"n": "card.png"}
    with pytest.raises(ValueError, match="wrong key"):
        dc._mega_decrypt_attrs(attrs, bytes([1] * 16))


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
    monkeypatch.setattr(dc, "_save_history", lambda h: None)
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
            r = await dc._download_mega(client, f"https://mega.nz/file/FILEH#{_b64(rnd)}",
                                        Path(tmp_path), history, True)
            r2 = await dc._download_mega(client, f"https://mega.nz/file/FILEH#{_b64(rnd)}",
                                         Path(tmp_path), history, True)
            return r, r2
        finally:
            await client.aclose()

    first, second = asyncio.run(go())
    assert first == (1, 0, 0) and second == (0, 1, 0)           # second run skipped via history
    assert (Path(tmp_path) / "my card.png").read_bytes() == content
    assert not list(Path(tmp_path).glob("*.part"))


def test_mega_folder_download_keeps_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "_save_history", lambda h: None)
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
            return await dc._download_mega(client, f"https://mega.nz/folder/SHARE#{_b64(share)}",
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
            return await dc._download_mega(client, url, Path(tmp_path), {}, True)
        finally:
            await client.aclose()

    with pytest.raises(dc.MegaError, match="not found"):
        asyncio.run(go(f"https://mega.nz/file/X#{_b64(bytes(32))}"))
    state["api"] = lambda cmd, folder: [{"e": -17, "tl": 3600}]
    with pytest.raises(dc.MegaError, match="quota.*3600s"):
        asyncio.run(go(f"https://mega.nz/file/X#{_b64(bytes(32))}"))
    assert asyncio.run(go("https://mega.nz/file/X")) == (0, 0, 1)      # key missing → counted as failed


def test_resolve_filename_decodes_percent_escapes():
    class _R: headers = {}
    assert dc._resolve_filename(_R(), "https://store1.gofile.io/download/web/id/card%20one.png", "x") == "card one.png"
