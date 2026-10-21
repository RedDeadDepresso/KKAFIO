"""Anime, games, vtubers character index: runtime lookup on a tiny hand-made index, and the download /
commit-check cache (the index itself is built and published by agv-character-scraper)."""

import json

import pytest

from kkafio.cards import agv_index
from kkafio.cards.agv_text import norm


def _index(chars=None):
    return {
        "version": 1,
        "langs": ["en_us", "ja_jp"],
        "series": [["Fate/stay night", "フェイト/ステイナイト"], ["Sword Art Online", ""],
                   ["", "カウボーイビバップ"]],          # no English title
        "aliases": {norm("Cowboy Bebop"): 2, norm("カウボーイビバップ"): 2, norm("Fate/stay night"): 0, norm("Fate/stay night: Unlimited Blade Works"): 0,
                    norm("Sword Art Online"): 1, norm("Sword Art Online II"): 1},
        "chars": chars if chars is not None else [
            [["Tohsaka, Rin", "遠坂, 凛"], [0], 50000],
            [["Rin", ""], [0], 10],
            [["Rin", ""], [1], 5],
        ],
        "names": {norm("Tohsaka, Rin"): [0], norm("TohsakaRin"): [0], norm("RinTohsaka"): [0],
                  norm("遠坂凛"): [0], norm("Rin"): [1, 2]},
    }


@pytest.fixture
def index_file(tmp_path):
    p = tmp_path / "agv_character_index.json"
    p.write_text(json.dumps(_index(), ensure_ascii=False), encoding="utf-8")
    agv_index.set_index_path(p)
    yield p
    agv_index.set_index_path(None)


def test_default_path_is_config_dir():
    from kkafio.core.paths import CONFIG_DIR
    agv_index.set_index_path(None)
    assert agv_index._path() == CONFIG_DIR / "agv_character_index.json"


def test_lookup_full_name_both_orders_and_kanji(index_file):
    for last, first in (("Tohsaka", "Rin"), ("Rin", "Tohsaka"), ("遠坂", "凛")):
        got = agv_index.lookup(f"{last} | {first} |  | Pure Heart | black hair")
        assert got and got[0].series == ("Fate/stay night",), (last, first)
        assert (got[0].lastname, got[0].firstname) == ("Tohsaka", "Rin")


def test_single_name_returns_candidates_most_popular_first(index_file):
    got = agv_index.lookup("Rin |  |  | Pure Heart | black hair")
    assert [c.series for c in got] == [("Fate/stay night",), ("Sword Art Online",)]


def test_unknown_is_empty(index_file):
    assert agv_index.lookup("Zzz | Qqq |  | Pure Heart | red hair") == ()


def test_japanese_names_and_series(index_file):
    got = agv_index.lookup("Tohsaka | Rin |  | x | y", "ja_jp")
    assert got[0].name == "遠坂, 凛" and got[0].series == ("フェイト/ステイナイト",)
    assert agv_index.supports("ja_jp") and not agv_index.supports("ko_kr")


def test_canonical_series(index_file):
    assert agv_index.canonical_series("fate stay night") == "Fate/stay night"
    assert agv_index.canonical_series("Fate/stay night: Unlimited Blade Works") == "Fate/stay night"
    assert agv_index.canonical_series("Sword Art Online II") == "Sword Art Online"
    assert agv_index.canonical_series("Some Eroge Not In Any Index") == "Some Eroge Not In Any Index"
    assert agv_index.canonical_series("") == ""


def test_canonical_series_never_switches_language(index_file):
    # the index has no English title for this series: an English answer stays English
    assert agv_index.canonical_series("Cowboy Bebop", "en_us") == "Cowboy Bebop"
    assert agv_index.canonical_series("cowboy bebop") == "cowboy bebop"            # default language is English
    assert agv_index.canonical_series("Cowboy Bebop", "ja_jp") == "カウボーイビバップ"   # a title exists in ja_jp
    assert agv_index.canonical_series("カウボーイビバップ", "en_us") == "カウボーイビバップ"
    # a title in the requested language is still the canonical one
    assert agv_index.canonical_series("fate stay night", "en_us") == "Fate/stay night"


def test_missing_index_degrades(tmp_path):
    agv_index.set_index_path(tmp_path / "nope.json")
    try:
        assert not agv_index.available()
        assert agv_index.lookup("A | B |  | x | y") == ()
        assert agv_index.canonical_series("Anything") == "Anything"
    finally:
        agv_index.set_index_path(None)


# ---------------------------------------------------------------------------
# download / commit check
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    """Stands in for httpx.Client: a commits API answering `commit` and an index URL
    answering `payload`; counts index downloads."""

    def __init__(self, commit="aaa", payload=None):
        self.commit, self.payload, self.downloads = commit, payload if payload is not None else _index(), 0

    def get(self, url, params=None, headers=None):
        if url == agv_index.COMMITS_API:
            if self.commit is None:
                raise OSError("offline")
            return _Resp([{"sha": self.commit}])
        assert url == agv_index.INDEX_URL
        self.downloads += 1
        return _Resp(self.payload)


@pytest.fixture
def cache(tmp_path):
    p = tmp_path / "kkafio" / "agv_character_index.json"
    agv_index.set_index_path(p)
    yield p
    agv_index.set_index_path(None)


def test_urls():
    assert agv_index.INDEX_URL == "https://reddeaddepresso.github.io/agv-character-scraper/agv_character_index.json"
    assert agv_index.COMMITS_API == "https://api.github.com/repos/RedDeadDepresso/agv-character-scraper/commits"


def test_refresh_downloads_then_only_when_commit_changes(cache):
    c = _Client("aaa")
    assert agv_index.refresh("T", client=c) and agv_index.available()
    assert cache.exists() and (cache.parent / agv_index.COMMIT_FILE).read_text() == "aaa"
    assert agv_index.refresh("T", client=c) and c.downloads == 1          # same commit: cached
    c.commit = "bbb"
    assert agv_index.refresh("T", client=c) and c.downloads == 2          # new commit: downloaded
    assert (cache.parent / agv_index.COMMIT_FILE).read_text() == "bbb"


def test_refresh_redownloads_when_cache_file_is_gone(cache):
    c = _Client("aaa")
    agv_index.refresh("T", client=c)
    cache.unlink()
    agv_index.set_index_path(cache)
    assert agv_index.refresh("T", client=c) and c.downloads == 2


def test_refresh_offline_uses_cache_or_degrades(cache):
    assert not agv_index.refresh("T", client=_Client(None))               # nothing cached yet
    agv_index.refresh("T", client=_Client("aaa"))
    c = _Client(None)
    assert agv_index.refresh("T", client=c) and c.downloads == 0          # cached copy serves
    assert agv_index.lookup("Tohsaka | Rin |  | x | y")


def test_refresh_rejects_bad_download_and_keeps_cache(cache):
    agv_index.refresh("T", client=_Client("aaa"))
    assert agv_index.refresh("T", client=_Client("bbb", {"version": 99}))
    assert (cache.parent / agv_index.COMMIT_FILE).read_text() == "aaa"    # will retry next time
    assert agv_index.lookup("Tohsaka | Rin |  | x | y")
