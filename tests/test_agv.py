"""Anime, games, vtubers character index: runtime lookup on a tiny hand-made index, and the download /
commit-check cache (the index itself is built and published by agv-character-scraper)."""

import json

import pytest

from kkafio.indices import agv
from kkafio.indices.text import norm


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
    agv.set_index_path(p)
    yield p
    agv.set_index_path(None)


def test_default_path_is_config_dir():
    from kkafio.core.paths import CONFIG_DIR
    agv.set_index_path(None)
    assert agv._path() == CONFIG_DIR / "agv_character_index.json"


def test_lookup_full_name_both_orders_and_kanji(index_file):
    for last, first in (("Tohsaka", "Rin"), ("Rin", "Tohsaka"), ("遠坂", "凛")):
        got = agv.lookup(f"{last} | {first} |  | Pure Heart | black hair")
        assert got and got[0].series == ("Fate/stay night",), (last, first)
        assert (got[0].lastname, got[0].firstname) == ("Tohsaka", "Rin")


def test_single_name_returns_candidates_most_popular_first(index_file):
    got = agv.lookup("Rin |  |  | Pure Heart | black hair")
    assert [c.series for c in got] == [("Fate/stay night",), ("Sword Art Online",)]


def test_unknown_is_empty(index_file):
    assert agv.lookup("Zzz | Qqq |  | Pure Heart | red hair") == ()


def test_japanese_names_and_series(index_file):
    got = agv.lookup("Tohsaka | Rin |  | x | y", "ja_jp")
    assert got[0].name == "遠坂, 凛" and got[0].series == ("フェイト/ステイナイト",)
    assert agv.supports("ja_jp") and not agv.supports("ko_kr")


def test_canonical_series(index_file):
    assert agv.canonical_series("fate stay night") == "Fate/stay night"
    assert agv.canonical_series("Fate/stay night: Unlimited Blade Works") == "Fate/stay night"
    assert agv.canonical_series("Sword Art Online II") == "Sword Art Online"
    assert agv.canonical_series("Some Eroge Not In Any Index") == "Some Eroge Not In Any Index"
    assert agv.canonical_series("") == ""


def test_canonical_series_never_switches_language(index_file):
    # the index has no English title for this series: an English answer stays English
    assert agv.canonical_series("Cowboy Bebop", "en_us") == "Cowboy Bebop"
    assert agv.canonical_series("cowboy bebop") == "cowboy bebop"            # default language is English
    assert agv.canonical_series("Cowboy Bebop", "ja_jp") == "カウボーイビバップ"   # a title exists in ja_jp
    assert agv.canonical_series("カウボーイビバップ", "en_us") == "カウボーイビバップ"
    # a title in the requested language is still the canonical one
    assert agv.canonical_series("fate stay night", "en_us") == "Fate/stay night"


def test_missing_index_degrades(tmp_path):
    agv.set_index_path(tmp_path / "nope.json")
    try:
        assert not agv.available()
        assert agv.lookup("A | B |  | x | y") == ()
        assert agv.canonical_series("Anything") == "Anything"
    finally:
        agv.set_index_path(None)


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
        if url == agv.COMMITS_API:
            if self.commit is None:
                raise OSError("offline")
            return _Resp([{"sha": self.commit}])
        assert url == agv.INDEX_URL
        self.downloads += 1
        return _Resp(self.payload)


@pytest.fixture
def cache(tmp_path):
    p = tmp_path / "kkafio" / "agv_character_index.json"
    agv.set_index_path(p)
    yield p
    agv.set_index_path(None)


def test_urls():
    assert agv.INDEX_URL == "https://reddeaddepresso.github.io/agv-character-scraper/agv_character_index.json"
    assert agv.COMMITS_API == "https://api.github.com/repos/RedDeadDepresso/agv-character-scraper/commits"


def test_refresh_downloads_then_only_when_commit_changes(cache):
    c = _Client("aaa")
    assert agv.refresh("T", client=c) and agv.available()
    assert cache.exists() and (cache.parent / agv.COMMIT_FILE).read_text() == "aaa"
    assert agv.refresh("T", client=c) and c.downloads == 1          # same commit: cached
    c.commit = "bbb"
    assert agv.refresh("T", client=c) and c.downloads == 2          # new commit: downloaded
    assert (cache.parent / agv.COMMIT_FILE).read_text() == "bbb"


def test_refresh_redownloads_when_cache_file_is_gone(cache):
    c = _Client("aaa")
    agv.refresh("T", client=c)
    cache.unlink()
    agv.set_index_path(cache)
    assert agv.refresh("T", client=c) and c.downloads == 2


def test_refresh_offline_uses_cache_or_degrades(cache):
    assert not agv.refresh("T", client=_Client(None))               # nothing cached yet
    agv.refresh("T", client=_Client("aaa"))
    c = _Client(None)
    assert agv.refresh("T", client=c) and c.downloads == 0          # cached copy serves
    assert agv.lookup("Tohsaka | Rin |  | x | y")


def test_refresh_rejects_bad_download_and_keeps_cache(cache):
    agv.refresh("T", client=_Client("aaa"))
    assert agv.refresh("T", client=_Client("bbb", {"version": 99}))
    assert (cache.parent / agv.COMMIT_FILE).read_text() == "aaa"    # will retry next time
    assert agv.lookup("Tohsaka | Rin |  | x | y")


# ---- suggest_series: autocompletion of series titles ---------------------------------------------

def test_suggest_series_prefix_then_contained_then_popularity(index_file):
    assert agv.suggest_series("fate") == ["Fate/stay night"]
    assert agv.suggest_series("FATE/") == ["Fate/stay night"]            # case and punctuation don't matter
    assert agv.suggest_series("night") == ["Fate/stay night"]            # contained, not a prefix
    assert agv.suggest_series("sword art online ii") == ["Sword Art Online"]


def test_suggest_series_prefix_beats_better_known_series(tmp_path):
    idx = _index(chars=[[["A", ""], [0], 1], [["B", ""], [1], 99999]])
    idx["series"] = [["Online Heroes", ""], ["Heroes Online", ""]]
    idx["aliases"] = {norm("Online Heroes"): 0, norm("Heroes Online"): 1}
    p = tmp_path / "i.json"
    p.write_text(json.dumps(idx), encoding="utf-8")
    agv.set_index_path(p)
    try:
        assert agv.suggest_series("online") == ["Online Heroes", "Heroes Online"]   # prefix first
        assert agv.suggest_series("heroes") == ["Heroes Online", "Online Heroes"]
    finally:
        agv.set_index_path(None)


def test_suggest_series_language_limit_and_nothing(index_file):
    assert agv.suggest_series("bebop", "ja_jp") == ["カウボーイビバップ"]
    assert agv.suggest_series("fate", "ja_jp") == ["フェイト/ステイナイト"]
    assert agv.suggest_series("o", limit=1) and len(agv.suggest_series("o", limit=1)) == 1
    assert agv.suggest_series("") == [] and agv.suggest_series("  ") == []
    assert agv.suggest_series("zzzz") == []


def test_suggest_series_without_an_index_is_empty(tmp_path):
    agv.set_index_path(tmp_path / "missing.json")
    try:
        assert agv.suggest_series("fate") == []
    finally:
        agv.set_index_path(None)
