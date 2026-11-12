"""The mod index (kkafio.indices.mod), the Drive / Mega downloader of a mod
(kkafio.services.mod_sources.filehosts) and the progress muting it relies on."""

import asyncio
import json
import zipfile
from pathlib import Path

import pytest

from kkafio.core import progress
from kkafio.indices import mod
from kkafio.indices.mod import SOURCE_ORDER, load_mod_index, normalize_sources, ordered_links
from kkafio.services.mod_sources import filehosts

from index_cache import age, read_meta

ENTRY = {"mega": ["https://mega.nz/file/a#k"], "drive": ["https://drive.google.com/file/d/abcdefghij/view"],
         "telegram": ["https://t.me/KK_archive_modlibrary/25"]}


# -- format and order -------------------------------------------------------

def test_sources_are_tried_telegram_then_drive_then_mega_then_the_other_hosts():
    assert SOURCE_ORDER == ("telegram", "drive", "mega", "gofile", "catbox", "mediafire", "pixeldrain", "dropbox")
    assert [s for s, _ in ordered_links(ENTRY)] == ["telegram", "drive", "mega"]


def test_ordered_links_keeps_the_order_of_the_index_within_a_source():
    entry = {"drive": ["d1", "d2"], "telegram": ["t1", "t2"], "mega": ["m1"]}
    assert ordered_links(entry) == [("telegram", "t1"), ("telegram", "t2"), ("drive", "d1"),
                                    ("drive", "d2"), ("mega", "m1")]
    assert ordered_links({"mega": ["m"]}) == [("mega", "m")]
    assert ordered_links({"catbox": ["c"], "gofile": ["g"]}) == [("gofile", "g"), ("catbox", "c")]
    assert ordered_links({}) == [] and ordered_links(None) == []


def test_ordered_links_follows_the_given_sources_and_only_uses_them():
    assert [s for s, _ in ordered_links(ENTRY, ("drive", "mega", "telegram"))] == ["drive", "mega", "telegram"]
    assert [s for s, _ in ordered_links(ENTRY, ("mega", "drive"))] == ["mega", "drive"]
    assert ordered_links(ENTRY, ("telegram",)) == [("telegram", "https://t.me/KK_archive_modlibrary/25")]
    assert ordered_links(ENTRY, ()) == []
    assert ordered_links({"drive": ["d"]}, ("mega", "telegram")) == []


def test_normalize_sources_keeps_the_order_and_drops_unknown_names_and_repeats():
    assert normalize_sources(["Drive", "Mega", "Telegram"]) == ("drive", "mega", "telegram")
    assert normalize_sources(["Telegram", "Drive"]) == ("telegram", "drive")
    assert normalize_sources(["telegram", " MEGA ", "Telegram", "Imgur", None]) == ("telegram", "mega")
    assert normalize_sources([]) == () and normalize_sources(None) == ()


def test_normalize_drops_whatever_is_not_a_list_of_links():
    raw = {
        "good": {"mega": [], "drive": ["  https://x  ", "", 5, None], "telegram": ["https://t.me/a/1"], "other": ["x"]},
        "no_links": {"mega": [], "drive": [], "telegram": []},
        "old_format": "https://t.me/KK_archive_modlibrary/25",
        "not_lists": {"mega": "https://mega.nz/file/a#k", "drive": None},
    }
    assert mod._normalize_index(raw) == {"good": {"drive": ["https://x"], "telegram": ["https://t.me/a/1"]}}
    with pytest.raises(ValueError):
        mod._normalize_index([])


# -- download and cache -----------------------------------------------------

class _Response:
    def __init__(self, payload, status=200, etag='"e1"'):
        self._payload, self.status_code, self.headers = payload, status, {"etag": etag}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Client:
    def __init__(self, *responses):
        self.responses, self.urls = list(responses), []

    async def get(self, url, headers=None, **kwargs):
        self.urls.append(url)
        return self.responses.pop(0)


def test_load_mod_index_downloads_and_caches_the_new_format(tmp_path):
    client = _Client(_Response({"com.a": ENTRY, "com.b": "https://t.me/x/1"}))
    index = asyncio.run(load_mod_index(client, tmp_path))
    assert index == {"com.a": ENTRY} and client.urls == [mod.MOD_INDEX_URL]
    assert json.loads((tmp_path / "mod.json").read_text(encoding="utf-8"))["com.a"] == ENTRY   # raw copy kept
    assert read_meta(tmp_path / "mod.json")["etag"] == '"e1"'

    again = _Client()                                              # checked less than an hour ago: no request
    assert asyncio.run(load_mod_index(again, tmp_path)) == {"com.a": ENTRY} and again.urls == []


def test_a_cached_index_in_the_old_format_is_downloaded_again(tmp_path):
    client = _Client(_Response({"com.a": ENTRY}))
    (tmp_path / "mod.json").write_text(json.dumps({"com.a": "https://t.me/KK_archive_modlibrary/25"}), encoding="utf-8")
    assert asyncio.run(load_mod_index(client, tmp_path)) == {"com.a": ENTRY}
    assert client.urls == [mod.MOD_INDEX_URL]


def test_an_index_that_is_not_an_object_falls_back_to_the_cached_copy(tmp_path):
    asyncio.run(load_mod_index(_Client(_Response({"com.a": ENTRY})), tmp_path))
    age(tmp_path / "mod.json", 2)
    bad = _Client(_Response(["not", "an", "object"]))
    assert asyncio.run(load_mod_index(bad, tmp_path)) == {"com.a": ENTRY}
    assert asyncio.run(load_mod_index(_Client(_Response([])), tmp_path / "empty")) == {}


# -- Drive / Mega downloads of one mod --------------------------------------

def _zipmod(path: Path, guid: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        if guid is not None:
            zf.writestr("manifest.xml", f"<manifest><guid>{guid}</guid></manifest>")


def _fake(monkeypatch, source, writes=None, counts=(1, 0, 0), raises=None, saved=None):
    """Replace the content downloader of `source`: it writes `writes` ({name: guid}) into the folder."""
    async def download(client, url, directory, history, skip):
        if saved is not None:
            saved.append((url, history, skip))
        if raises:
            raise raises
        for name, guid in (writes or {}).items():
            _zipmod(directory / name, guid)
        progress.count("failed")                                   # what the real ones do
        return counts
    monkeypatch.setitem(filehosts._DOWNLOADERS, source, (lambda url: True, download))


def _run(source, tmp_path, rel_path=None, guid="com.mod", url="https://x/y"):
    return asyncio.run(filehosts.download_via_host(source, url, guid, tmp_path, None, rel_path))


def test_a_drive_download_of_the_mod_is_saved_into_the_folder_of_rel_path(tmp_path, monkeypatch):
    _fake(monkeypatch, "drive", {"[a] mod.zipmod": "com.mod"})
    assert _run("drive", tmp_path, "Sideloader Modpack/Author/[a] mod.zipmod") is True
    assert (tmp_path / "Sideloader Modpack" / "Author" / "[a] mod.zipmod").is_file()
    assert [p.name for p in (tmp_path / "Sideloader Modpack" / "Author").iterdir()] == ["[a] mod.zipmod"]   # no scratch folder


def test_without_rel_path_it_is_saved_directly_in_the_mods_folder(tmp_path, monkeypatch):
    _fake(monkeypatch, "mega", {"mod.zip": "com.mod"})
    assert _run("mega", tmp_path) is True
    assert [p.name for p in tmp_path.iterdir()] == ["mod.zip"]


def test_the_mod_is_found_among_other_files_of_the_download(tmp_path, monkeypatch):
    _fake(monkeypatch, "mega", {"readme.txt": None, "other.zipmod": "com.other", "mine.zipmod": "com.mod"},
          counts=(3, 0, 0))
    assert _run("mega", tmp_path) is True
    assert [p.name for p in tmp_path.iterdir()] == ["mine.zipmod"]


def test_a_download_of_another_mod_is_rejected_and_leaves_nothing_behind(tmp_path, monkeypatch):
    _fake(monkeypatch, "drive", {"other.zipmod": "com.other"})
    assert _run("drive", tmp_path) is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("counts", [(0, 0, 1), (0, 0, 0), (0, 1, 0)])
def test_a_download_without_a_file_is_a_failure(tmp_path, monkeypatch, counts):
    _fake(monkeypatch, "drive", counts=counts)
    assert _run("drive", tmp_path) is False


def test_an_error_of_the_downloader_is_a_failure_not_an_exception(tmp_path, monkeypatch):
    _fake(monkeypatch, "mega", raises=RuntimeError("Mega: folder is empty."))
    assert _run("mega", tmp_path) is False
    assert list(tmp_path.iterdir()) == []


def test_an_existing_copy_of_the_mod_is_skipped(tmp_path, monkeypatch):
    _zipmod(tmp_path / "mod.zipmod", "com.mod")
    _fake(monkeypatch, "drive", {"mod.zipmod": "com.mod"})
    assert _run("drive", tmp_path) == "skipped"


def test_the_download_history_is_neither_used_nor_saved(tmp_path, monkeypatch):
    from kkafio.services.content_sources import history as history_store
    saves, saved = [], []
    original = history_store.save_history

    async def download(client, url, directory, history, skip):
        history_store.save_history(history)                        # what download_gdrive / download_mega end with
        history["k"] = "v"
        _zipmod(directory / "m.zipmod", "com.mod")
        saved.append((history, skip))
        return 1, 0, 0

    monkeypatch.setattr(history_store, "save_history", lambda h: saves.append(h))
    monkeypatch.setitem(filehosts._DOWNLOADERS, "drive", (lambda url: True, download))
    assert _run("drive", tmp_path) is True
    assert saves == [] and saved[0][1] is False                    # nothing written, "skip downloaded" off
    assert history_store.save_history is not original              # (the test's own stand-in is back in place)


def test_a_link_of_the_wrong_host_or_an_unknown_source_is_refused(tmp_path):
    assert _run("drive", tmp_path, url="https://mega.nz/file/a#k") is False
    assert _run("mega", tmp_path, url="https://drive.google.com/file/d/abcdefghij/view") is False
    assert _run("telegram", tmp_path) is False


# -- progress muting --------------------------------------------------------

def test_quiet_counts_mutes_file_counters_and_nothing_else():
    tracker = progress.ProgressTracker(items_total=3)
    progress.install(tracker)
    try:
        progress.count("failed")
        with progress.quiet_counts():
            progress.count("failed")
            progress.count("skipped")
            progress.begin("f", 10).close(ok=True)                 # a row still comes and goes, uncounted
            progress.item_done()
        progress.begin("g", 10).close(ok=True)
        snap = tracker.snapshot()
        assert (snap.ok, snap.skipped, snap.failed, snap.items_done) == (1, 0, 1, 1)
    finally:
        progress.uninstall()


def test_quiet_counts_without_a_window_is_harmless():
    with progress.quiet_counts():
        progress.count("failed")


# -- Gofile / Catbox --------------------------------------------------------

def test_gofile_catbox_and_mediafire_links_are_read_and_misfiled_ones_are_moved():
    raw = {"com.mod": {"mega": ["https://gofile.io/d/AbC123", "https://mega.nz/file/a#k"],
                       "gofile": ["https://gofile.io/d/Other1"],
                       "catbox": ["https://files.catbox.moe/abc.zipmod"]},
           "com.other": {"drive": ["https://catbox.moe/c/xyz", "https://www.mediafire.com/file/k1234567/x/file", "https://pixeldrain.com/u/AbC12345",
                                  "https://www.dropbox.com/scl/fi/abc123xyz/x.zipmod?rlkey=k&dl=0"]}}
    index = mod._normalize_index(raw)
    assert index["com.mod"] == {"mega": ["https://mega.nz/file/a#k"],
                                "gofile": ["https://gofile.io/d/Other1", "https://gofile.io/d/AbC123"],
                                "catbox": ["https://files.catbox.moe/abc.zipmod"]}
    assert index["com.other"] == {"catbox": ["https://catbox.moe/c/xyz"],
                                  "mediafire": ["https://www.mediafire.com/file/k1234567/x/file"],
                                  "pixeldrain": ["https://pixeldrain.com/u/AbC12345"],
                                  "dropbox": ["https://www.dropbox.com/scl/fi/abc123xyz/x.zipmod?rlkey=k&dl=0"]}
    assert normalize_sources(["Catbox", "Gofile", "catbox"]) == ("catbox", "gofile")


@pytest.mark.parametrize("source", ["gofile", "catbox", "mediafire", "pixeldrain", "dropbox"])
def test_a_gofile_catbox_or_mediafire_download_of_the_mod_is_saved(tmp_path, monkeypatch, source):
    _fake(monkeypatch, source, {"[a] mod.zipmod": "com.mod"})
    assert _run(source, tmp_path) is True
    assert (tmp_path / "[a] mod.zipmod").exists()


def test_the_real_gofile_catbox_and_mediafire_downloaders_are_registered():
    assert filehosts._DOWNLOADERS["gofile"][0]("https://gofile.io/d/AbC123")
    assert filehosts._DOWNLOADERS["catbox"][0]("https://files.catbox.moe/abc.zipmod")
    assert filehosts._DOWNLOADERS["mediafire"][0]("https://www.mediafire.com/file/abc123XYZ/x.zip/file")
    assert filehosts._DOWNLOADERS["pixeldrain"][0]("https://pixeldrain.com/u/AbC12345")
    assert filehosts._DOWNLOADERS["dropbox"][0]("https://www.dropbox.com/scl/fi/abc123xyz/x.zipmod?rlkey=k&dl=0")
    assert not filehosts._DOWNLOADERS["dropbox"][0]("https://mega.nz/file/a#k")
    assert not filehosts._DOWNLOADERS["pixeldrain"][0]("https://mega.nz/file/a#k")
    assert not filehosts._DOWNLOADERS["catbox"][0]("https://mega.nz/file/a#k")
    assert not filehosts._DOWNLOADERS["mediafire"][0]("https://mega.nz/file/a#k")
