"""Gacha index: lookup rules, priority over the anime, games, vtubers index, and the download/cache step."""

import json
from pathlib import Path

import pytest

from kkafio.indices import chara, gacha, agv

ROOT = Path(__file__).resolve().parents[1]

DATA = {
    "version": 1,
    "series": [
        {"title": {"en_us": "Genshin Impact", "ja_jp": "原神"}, "aliases": ["Genshin"]},
        {"title": {"en_us": "Fate/Grand Order"}, "aliases": ["FGO"]},
    ],
    "chars": [
        {"name": {"en_us": "Ganyu"}, "series": [0], "score": 100, "aliases": ["甘雨"]},
        {"name": {"en_us": "Kamisato, Ayaka", "ja_jp": "神里, 綾華"}, "series": [0], "score": 90,
         "aliases": ["神里綾華", "Ayaka"], "birthMonth": 9, "birthDay": 28},
        {"name": {"en_us": "Rin"}, "series": [1], "score": 10},
        {"name": {"en_us": "Rin"}, "series": [0], "score": 9},
    ],
}


@pytest.fixture
def gacha_file(tmp_path):
    p = tmp_path / "gacha.json"
    p.write_text(json.dumps(DATA, ensure_ascii=False), encoding="utf-8")
    gacha.set_index_path(p)
    yield p
    gacha.set_index_path(None)


def _names(cands):
    return [c.name for c in cands]


def test_mononym_matches_with_one_field(gacha_file):
    (c,) = gacha.lookup("Ganyu |  |  | Pure Heart | blue hair")
    assert (c.name, c.series, c.score) == ("Ganyu", ("Genshin Impact",), 100)


def test_native_alias_and_either_name_order(gacha_file):
    assert _names(gacha.lookup("甘雨 |  |  | x | y")) == ["Ganyu"]
    for k in ("神里 | 綾華 |  | x | y", "Ayaka | Kamisato |  | x | y", "Kamisato | Ayaka |  | x | y"):
        assert _names(gacha.lookup(k)) == ["Kamisato, Ayaka"], k


def test_alias_alone_finds_a_character_with_a_family_name(gacha_file):
    (c,) = gacha.lookup("Ayaka |  |  | x | y")
    assert (c.lastname, c.firstname) == ("Kamisato", "Ayaka")
    assert (c.birth_month, c.birth_day) == (9, 28)


def test_names_and_series_come_back_in_the_requested_language(gacha_file):
    (c,) = gacha.lookup("Kamisato | Ayaka |  | x | y", lang="ja_jp")
    assert (c.name, c.series) == ("神里, 綾華", ("原神",))
    (c,) = gacha.lookup("Ganyu |  |  | x | y", lang="ja_jp")      # no ja_jp name: falls back to English
    assert c.name == "Ganyu"


def test_duplicate_names_are_all_returned_most_popular_first(gacha_file):
    cands = gacha.lookup("Rin |  |  | x | y")
    assert [(c.name, c.series, c.score) for c in cands] == [
        ("Rin", ("Fate/Grand Order",), 10), ("Rin", ("Genshin Impact",), 9)]
    assert len(gacha.lookup("Rin |  |  | x | y", limit=1)) == 1


def test_snap_series(gacha_file):
    assert gacha.snap_series("genshin") == "Genshin Impact"
    assert gacha.snap_series("原神") == "Genshin Impact"
    assert gacha.snap_series("原神", lang="ja_jp") == "原神"
    assert gacha.snap_series("FGO") == "Fate/Grand Order"
    assert gacha.snap_series("Not A Game") is None


def test_gacha_beats_agv_and_falls_back(gacha_file):
    # The anime, games, vtubers index may also know a "Rin Tohsaka"; the gacha index has the same card as Fate/Grand Order.
    d = dict(DATA, chars=DATA["chars"] + [
        {"name": {"en_us": "Tohsaka, Rin"}, "series": [1], "score": 50}])
    gacha_file.write_text(json.dumps(d), encoding="utf-8")
    gacha.set_index_path(gacha_file)
    cands = chara.lookup("Tohsaka | Rin |  | x | y")
    assert (cands[0].name, cands[0].series) == ("Tohsaka, Rin", ("Fate/Grand Order",))
    # Series snapping tries gacha first, then the anime, games, vtubers index, then gives the name back.
    assert chara.canonical_series("原神") == "Genshin Impact"
    assert chara.canonical_series("Some Eroge Not In Any Index") == "Some Eroge Not In Any Index"


def test_dominant_index_goes_first_then_the_lists_alternate(gacha_file, monkeypatch):
    def cand(name, series, score):
        return agv.Candidate(name, "", name, (series,), score)

    g = (cand("G1", "S", 10), cand("G2", "S", 9))        # not dominant: 10 < 5 * 9
    a = (cand("A1", "T", 1),)                            # the only candidate: dominant
    monkeypatch.setattr(gacha, "lookup", lambda *args, **kw: g)
    monkeypatch.setattr(agv, "lookup", lambda *args, **kw: a)
    assert _names(chara.lookup("k")) == ["A1", "G1", "G2"]


def test_the_same_character_from_both_indexes_is_listed_once(gacha_file, monkeypatch):
    g = (agv.Candidate("Rin", "", "Rin", ("S",), 5),)
    a = (agv.Candidate("Rin", "", "Rin", ("S",), 3, 5, 11),)
    monkeypatch.setattr(gacha, "lookup", lambda *args, **kw: g)
    monkeypatch.setattr(agv, "lookup", lambda *args, **kw: a)
    (c,) = chara.lookup("k")
    assert (c.score, c.birth_month, c.birth_day) == (5, 5, 11)      # gacha's entry, plus the birthday only agv had


def test_unavailable_is_no_help(tmp_path):
    gacha.set_index_path(tmp_path / "nope.json")
    try:
        assert not gacha.available()
        assert gacha.lookup("A | B |  | x | y") == ()
        assert gacha.snap_series("Anything") is None
    finally:
        gacha.set_index_path(None)


class _Resp:
    def __init__(self, status=200, data=None, etag=None):
        self.status_code, self._data = status, data
        self.headers = {"etag": etag} if etag else {}

    def json(self):
        return self._data


def test_refresh_downloads_then_revalidates(tmp_path, monkeypatch):
    p = tmp_path / "cfg" / "idx.json"
    gacha.set_index_path(p)
    calls = []

    def fake(url, headers, timeout):
        calls.append(dict(headers))
        return _Resp(304) if headers.get("If-None-Match") == '"v1"' else _Resp(200, DATA, '"v1"')

    monkeypatch.setattr(gacha, "_fetch", fake)
    try:
        assert gacha.refresh("T") and gacha.available()
        assert gacha.refresh("T")
        assert calls == [{}, {"If-None-Match": '"v1"'}]
    finally:
        gacha.set_index_path(None)


def test_refresh_offline_keeps_cache_and_bad_data_is_rejected(gacha_file, monkeypatch):
    def boom(*a):
        raise OSError("offline")
    monkeypatch.setattr(gacha, "_fetch", boom)
    assert gacha.refresh("T")                       # cached copy still serves
    monkeypatch.setattr(gacha, "_fetch", lambda *a: _Resp(200, {"version": 99}))
    assert gacha.refresh("T")
    assert _names(gacha.lookup("Ganyu |  |  | x | y")) == ["Ganyu"]   # good cache untouched
    monkeypatch.setattr(gacha, "_fetch", lambda *a: _Resp(200, {"version": 1, "series": [], "chars": [{}]}))
    assert gacha.refresh("T")
    assert _names(gacha.lookup("Ganyu |  |  | x | y")) == ["Ganyu"]
