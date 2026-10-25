"""Download Contents: `DownloadContents.run()` — which downloader gets which link.

The per-site downloaders are replaced by fakes, so this only covers the task's
own job: parsing the Links field, picking the client / downloader per host,
sharing the pawchive state, counting results and checkpointing the history.
"""

import pytest

from kkafio.services.content_sources import history as history_store
from kkafio.tasks import download_contents as dc

SITES = ("pawchive", "bepis", "koikatsu", "pixiv", "gofile", "mega", "gdrive")


class _Cfg:
    def __init__(self, links, out):
        self.download_contents = {"Links": links, "OutputDir": str(out), "SkipDownloaded": True}
        self.config_data = {"Core": {}}


@pytest.fixture
def rig(monkeypatch, tmp_path):
    calls, saves = [], []

    def fake(site):
        async def _fake(client, url, directory, history, skip, **kw):
            calls.append((site, url, directory, kw))
            if "boom" in url:
                raise RuntimeError("boom")
            return 1, 0, 0
        return _fake

    for site in SITES:
        monkeypatch.setattr(dc, f"download_{site}", fake(site))
    monkeypatch.setattr(history_store, "load_history", lambda: {})
    monkeypatch.setattr(history_store, "save_history", lambda h: saves.append(dict(h)))

    import kkafio.services.kkd_session as kkd
    import kkafio.services.pixiv_session as pxv
    session = {"kkd": None}
    monkeypatch.setattr(kkd, "get_or_prompt", lambda: session["kkd"])
    monkeypatch.setattr(pxv, "get_or_prompt", lambda: None)

    def run(links):
        dc.DownloadContents(_Cfg(links, tmp_path), None).run()
    return run, calls, saves, session, tmp_path


def test_each_link_goes_to_its_sites_downloader(rig):
    run, calls, saves, session, out = rig
    session["kkd"] = "KKD"
    run("# comment\n\nnot a url\n"
        "https://db.bepis.moe/koikatsu/view/1\n"
        "https://koikatsucards.com/contents/a\n"
        "https://www.pixiv.net/en/artworks/1\n"
        "https://gofile.io/d/x | 3\n"
        "https://mega.nz/file/a#b\n"
        "https://drive.google.com/file/d/1234567890abc/view\n"
        "https://pawchive.pw/posts?q=https://db.bepis.moe/x | 2 | 4\n")
    assert [(s, u) for s, u, *_ in calls] == [
        ("bepis", "https://db.bepis.moe/koikatsu/view/1"),
        ("koikatsu", "https://koikatsucards.com/contents/a"),
        ("pixiv", "https://www.pixiv.net/en/artworks/1"),
        ("gofile", "https://gofile.io/d/x"),
        ("mega", "https://mega.nz/file/a#b"),
        ("gdrive", "https://drive.google.com/file/d/1234567890abc/view"),
        ("pawchive", "https://pawchive.pw/posts?q=https://db.bepis.moe/x"),   # host wins over the query
    ]
    assert all(directory == out for _, _, directory, _ in calls)
    paw = calls[-1][3]
    assert (paw["page_start"], paw["page_end"]) == (2, 4)
    assert set(paw["externals"]) == {"gofile", "mega", "gdrive"}
    assert len(saves) == 7 + 1                   # after every URL, plus the final save


def test_pawchive_auth_and_throttle_are_shared_by_all_its_links(rig):
    run, calls, *_ = rig
    run("https://pawchive.pw/posts?q=a\nhttps://pawchive.pw/fanbox/user/1\n")
    (_, _, _, a), (_, _, _, b) = calls
    assert a["auth"] is b["auth"] and a["throttle"] is b["throttle"]


def test_a_failing_link_does_not_stop_the_rest_and_koikatsu_needs_a_session(rig):
    run, calls, saves, session, _ = rig
    run("https://gofile.io/d/boom\n"
        "https://koikatsucards.com/contents/a\n"        # no kkd session -> skipped, not attempted
        "https://mega.nz/file/a#b\n"
        "https://example.com/x\n")                      # unsupported
    assert [site for site, *_ in calls] == ["gofile", "mega"]
    assert len(saves) == 3 + 1                          # 3 attempted/unsupported URLs + the final save


def test_no_valid_links_does_nothing(rig):
    run, calls, saves, *_ = rig
    run("# only a comment\nnope\n")
    assert calls == [] and saves == []
