"""MAL index: the CSV -> index build step and the runtime lookup, on tiny fake datasets
(the real Kaggle files are far too big to ship in tests)."""

import importlib.util
import json
from pathlib import Path

import pytest

from kkafio.cards import mal_index

ROOT = Path(__file__).resolve().parents[1]


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_mal_index", ROOT / "tools" / "build_mal_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Same layout as the real Kaggle files: the character -> anime link lives in the anime
# CSV's `characters` JSON column; the characters CSV has no favorites.
ANIME = (
    "myanimelist_id,title,Members,characters\n"
    '1,Fate/stay night: Unlimited Blade Works,"900,000","[{""id"": 10, ""name"": ""Tohsaka, Rin""}, {""id"": 12, ""name"": ""Rin""}]"\n'
    '2,Fate/Zero,"800,000","[{""id"": 11, ""name"": ""Matou, Rin""}]"\n'
    '3,Sword Art Online 2nd Season,"700,000","[{""id"": 13, ""name"": ""Yuuki, Asuna""}, {""id"": 14, ""name"": ""Rin""}]"\n'
    '4,Tiny Obscure Show,10,"[{""id"": 15, ""name"": ""Yamada, Hanako""}]"\n'
    '5,Broken Json Show,"500,000","[{""id"": 16, ""name"": ""Broken, Bob"" oops"\n'
)
CHARS = (
    "character_id,full_name,alternate_name,name,url\n"
    "10,Rin Tohsaka,遠坂 凛,\"Tohsaka, Rin\",u\n"
    "11,Rin Matou,,\"Matou, Rin\",u\n"
    "12,Rin,,Rin,u\n"
    "13,Asuna Yuuki,結城 明日奈,\"Yuuki, Asuna\",u\n"
    "14,Rin,,Rin,u\n"
    "15,Hanako Yamada,,\"Yamada, Hanako\",u\n"
    "16,Bob Broken,,\"Broken, Bob\",u\n"
    "17,Nobody Anywhere,,\"Anywhere, Nobody\",u\n"
)


@pytest.fixture
def index(tmp_path):
    (tmp_path / "anime.csv").write_text(ANIME, encoding="utf-8")
    (tmp_path / "chars.csv").write_text(CHARS, encoding="utf-8")
    b = _load_builder()
    data = b.build(tmp_path / "chars.csv", tmp_path / "anime.csv",
                   min_score=10000, min_members=2000, forced={})
    out = tmp_path / "idx.json"
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mal_index.set_index_path(out)
    yield data
    mal_index.set_index_path(ROOT / "assets" / "data" / mal_index.INDEX_FILE)


def test_franchise_title_collapse():
    b = _load_builder()
    assert b.franchise_title("Fate/stay night: Unlimited Blade Works") == "Fate/stay night"
    assert b.franchise_title("Sword Art Online 2nd Season") == "Sword Art Online"
    assert b.franchise_title("Re:Zero kara Hajimeru Isekai Seikatsu") == "Re:Zero kara Hajimeru Isekai Seikatsu"


def test_build_filters(index):
    assert "tinyobscureshow" not in index["aliases"]            # below min_members
    assert norm_key("Hanako Yamada") not in index["names"]      # only in a dropped anime
    assert norm_key("Nobody Anywhere") not in index["names"]    # in no anime at all


def test_inverts_character_link_and_salvages_broken_json(index):
    names = {c[0] for c in index["chars"]}
    assert {"Tohsaka, Rin", "Matou, Rin", "Yuuki, Asuna"} <= names
    assert "Broken, Bob" in names        # malformed JSON cell: id still recovered by the regex


def norm_key(s):
    from kkafio.cards.mal_text import norm
    return norm(s)


def test_confident_full_name_both_orders_and_kanji(index):
    for last, first in (("Tohsaka", "Rin"), ("Rin", "Tohsaka"), ("遠坂", "凛")):
        m = mal_index.lookup(f"{last} | {first} |  | Pure Heart | black hair")
        assert m.tier == "confident", (last, first)
        assert m.best.series == ("Fate/stay night",)
        assert (m.best.lastname, m.best.firstname) == ("Tohsaka", "Rin")


def test_single_name_is_never_confident(index):
    m = mal_index.lookup("Rin |  |  | Pure Heart | black hair")
    assert m.tier == "ambiguous"
    assert m.best.series in (('Fate/stay night',), ('Sword Art Online',))


def test_unknown_is_none(index):
    assert mal_index.lookup("Zzz | Qqq |  | Pure Heart | red hair").tier == "none"


def test_canonical_series(index):
    assert mal_index.canonical_series("fate stay night") == "Fate/stay night"
    assert mal_index.canonical_series("Fate/stay night: Unlimited Blade Works") == "Fate/stay night"
    assert mal_index.canonical_series("Sword Art Online II") == "Sword Art Online"
    assert mal_index.canonical_series("Some Eroge Not On MAL") == "Some Eroge Not On MAL"
    assert mal_index.canonical_series("") == ""


def test_missing_index_degrades(tmp_path):
    mal_index.set_index_path(tmp_path / "nope.json")
    try:
        assert not mal_index.available()
        assert mal_index.lookup("A | B |  | x | y").tier == "none"
        assert mal_index.canonical_series("Anything") == "Anything"
    finally:
        mal_index.set_index_path(ROOT / "assets" / "data" / mal_index.INDEX_FILE)


# ---------------------------------------------------------------------------
# task integration
# ---------------------------------------------------------------------------

from kkafio.tasks import group_chara, rename_chara  # noqa: E402

KEYS = {
    "Tohsaka | Rin |  | Pure Heart | black hair": "",       # confident
    "Rin |  |  | Pure Heart | black hair": "",              # ambiguous
    "Zzz | Qqq |  | Pure Heart | red hair": "",             # unknown
}


def test_group_apply_mal_hints(index):
    out, resolved = group_chara.apply_mal_hints(json.dumps(KEYS))
    sent = json.loads(out)
    assert resolved == {"Tohsaka | Rin |  | Pure Heart | black hair": "Fate/stay night"}
    assert sent["Rin |  |  | Pure Heart | black hair"] == "Fate/stay night"   # pre-filled hint (most popular bare "Rin")
    assert sent["Zzz | Qqq |  | Pure Heart | red hair"] == ""                  # untouched
    assert "Tohsaka | Rin |  | Pure Heart | black hair" not in sent


def test_group_everything_resolved_means_no_llm_json(index):
    out, resolved = group_chara.apply_mal_hints(
        json.dumps({"Tohsaka | Rin |  | Pure Heart | black hair": ""}))
    assert out == "" and len(resolved) == 1


def test_group_process_merges_extra_and_canonicalizes(index, tmp_path):
    # no real cards needed: nothing matches, but the merge must not raise or bail early
    group_chara.process([tmp_path], "", extra={"k": "fate stay night"}, canonicalize=True,
                        use_cache=False)


def test_rename_apply_mal_hints(index):
    keys = {k: dict(rename_chara._EMPTY_NAME) for k in KEYS}
    out, resolved = rename_chara.apply_mal_hints(json.dumps(keys))
    sent = json.loads(out)
    assert resolved["Tohsaka | Rin |  | Pure Heart | black hair"] == {
        "lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"}
    assert sent["Rin |  |  | Pure Heart | black hair"]["firstname"] == "Rin"     # hint
    assert sent["Zzz | Qqq |  | Pure Heart | red hair"] == rename_chara._EMPTY_NAME


def test_apply_is_noop_without_index(tmp_path):
    mal_index.set_index_path(tmp_path / "missing.json")
    try:
        s = json.dumps(KEYS)
        assert group_chara.apply_mal_hints(s) == (s, {})
    finally:
        mal_index.set_index_path(ROOT / "assets" / "data" / mal_index.INDEX_FILE)


def test_insert_note_goes_above_the_json_line():
    p = group_chara._insert_note("Rules...\nJSON to fill in:\n", "NOTE")
    assert p.split("\n")[-1] == "JSON to fill in:" and "NOTE" in p
