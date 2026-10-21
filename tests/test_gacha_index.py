"""Gacha index: lookup rules, priority over the anime, games, vtubers index, and the download/cache step."""

import json
from pathlib import Path

import pytest

from kkafio.cards import char_index, gacha_index, agv_index

ROOT = Path(__file__).resolve().parents[1]

DATA = {
    "version": 1,
    "series": [
        {"title": "Genshin Impact", "aliases": ["Genshin", "原神"]},
        {"title": "Fate/Grand Order", "aliases": ["FGO"]},
    ],
    "chars": [
        {"name": "Ganyu", "series": [0], "score": 100, "aliases": ["甘雨"]},
        {"name": "Kamisato, Ayaka", "series": [0], "score": 90, "aliases": ["神里綾華", "Ayaka"]},
        {"name": "Rin", "series": [1], "score": 10},
        {"name": "Rin", "series": [0], "score": 9},
    ],
}


@pytest.fixture
def gacha(tmp_path):
    p = tmp_path / "gacha.json"
    p.write_text(json.dumps(DATA, ensure_ascii=False), encoding="utf-8")
    gacha_index.set_index_path(p)
    yield p
    gacha_index.set_index_path(None)


def test_mononym_is_confident_with_one_field(gacha):
    m = gacha_index.lookup("Ganyu |  |  | Pure Heart | blue hair")
    assert (m.tier, m.source, m.best.series) == ("confident", "gacha", ("Genshin Impact",))


def test_native_alias_and_either_name_order(gacha):
    assert gacha_index.lookup("甘雨 |  |  | x | y").best.name == "Ganyu"
    for k in ("神里 | 綾華 |  | x | y", "Ayaka | Kamisato |  | x | y", "Kamisato | Ayaka |  | x | y"):
        assert gacha_index.lookup(k).tier == "confident"


def test_one_field_never_confident_for_a_character_with_a_family_name(gacha):
    assert gacha_index.lookup("Ayaka |  |  | x | y").tier == "ambiguous"


def test_duplicate_names_are_ambiguous_unless_dominant(gacha):
    m = gacha_index.lookup("Rin |  |  | x | y")
    assert m.tier == "ambiguous" and len(m.candidates) == 2


def test_snap_series(gacha):
    assert gacha_index.snap_series("genshin") == "Genshin Impact"
    assert gacha_index.snap_series("FGO") == "Fate/Grand Order"
    assert gacha_index.snap_series("Not A Game") is None


def test_gacha_beats_agv_and_falls_back(gacha):
    # The anime, games, vtubers index also knows a confident "Rin Tohsaka"; the gacha index has the same card as Fate/Grand Order.
    d = dict(DATA, chars=DATA["chars"] + [{"name": "Tohsaka, Rin", "series": [1], "score": 50}])
    gacha.write_text(json.dumps(d), encoding="utf-8")
    gacha_index.set_index_path(gacha)
    m = char_index.lookup("Tohsaka | Rin |  | x | y")
    assert (m.source, m.best.series) == ("gacha", ("Fate/Grand Order",))
    # Not in the gacha index -> the anime, games, vtubers index answers.
    if agv_index.available():
        m = char_index.lookup("Yuuki | Asuna |  | x | y")
        assert m.source == "mal"
    # Series snapping tries gacha first, then the anime, games, vtubers index, then gives the name back.
    assert char_index.canonical_series("原神") == "Genshin Impact"
    assert char_index.canonical_series("Some Eroge Not In Any Index") == "Some Eroge Not In Any Index"


def test_confident_agv_beats_ambiguous_gacha(gacha, monkeypatch):
    amb = agv_index.Match("ambiguous", (agv_index.Candidate("X", "", "X", ("S",), 1),), "gacha")
    sure = agv_index.Match("confident", (agv_index.Candidate("Y, Z", "Y", "Z", ("T",), 1),))
    monkeypatch.setattr(gacha_index, "lookup", lambda k: amb)
    monkeypatch.setattr(agv_index, "lookup", lambda k: sure)
    assert char_index.lookup("k").best.series == ("T",)


def test_unavailable_is_no_help(tmp_path):
    gacha_index.set_index_path(tmp_path / "nope.json")
    try:
        assert not gacha_index.available()
        assert gacha_index.lookup("A | B |  | x | y").tier == "none"
        assert gacha_index.snap_series("Anything") is None
    finally:
        gacha_index.set_index_path(None)


class _Resp:
    def __init__(self, status=200, data=None, etag=None):
        self.status_code, self._data = status, data
        self.headers = {"etag": etag} if etag else {}

    def json(self):
        return self._data


def test_refresh_downloads_then_revalidates(tmp_path, monkeypatch):
    p = tmp_path / "cfg" / "idx.json"
    gacha_index.set_index_path(p)
    calls = []

    def fake(url, headers, timeout):
        calls.append(dict(headers))
        return _Resp(304) if headers.get("If-None-Match") == '"v1"' else _Resp(200, DATA, '"v1"')

    monkeypatch.setattr(gacha_index, "_fetch", fake)
    try:
        assert gacha_index.refresh("T") and gacha_index.available()
        assert gacha_index.refresh("T")
        assert calls == [{}, {"If-None-Match": '"v1"'}]
    finally:
        gacha_index.set_index_path(None)


def test_refresh_offline_keeps_cache_and_bad_data_is_rejected(gacha, monkeypatch):
    def boom(*a):
        raise OSError("offline")
    monkeypatch.setattr(gacha_index, "_fetch", boom)
    assert gacha_index.refresh("T")                       # cached copy still serves
    monkeypatch.setattr(gacha_index, "_fetch", lambda *a: _Resp(200, {"version": 99}))
    assert gacha_index.refresh("T")
    assert gacha_index.lookup("Ganyu |  |  | x | y").tier == "confident"   # good cache untouched
    monkeypatch.setattr(gacha_index, "_fetch", lambda *a: _Resp(200, {"version": 1, "series": [], "chars": [{}]}))
    assert gacha_index.refresh("T")
    assert gacha_index.lookup("Ganyu |  |  | x | y").tier == "confident"
