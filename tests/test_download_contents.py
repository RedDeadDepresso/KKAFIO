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
    assert P("https://www.pixiv.net/en/tags/koikatsu") is None

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
