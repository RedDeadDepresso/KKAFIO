"""Group Characters' Resolve Locally: the per-card birthday in the key cache, the local
decision (one candidate / birthday match / series without a title in the hints language),
and the end-to-end move with stand-in cards (no real kkloader needed)."""

import json
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from kkafio.cards import char_index
from kkafio.cards.agv_index import Candidate
from kkafio.cards.cache_io import file_fp
from kkafio.cards.chara_key_cache import CACHE_FILE, CharaKeyCache
from kkafio.cards.classifier import CHARA_CARD_TYPES
from kkafio.tasks import group_chara

KK = CHARA_CARD_TYPES[0]


def _cand(name, series, month=None, day=None, native=True, score=1):
    last, _, first = name.partition(", ")
    return Candidate(name, last, first, tuple(series), score, month, day, native)


def _file(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@contextmanager
def index(table):
    """char_index.lookup answering from {key: [Candidate, ...]}."""
    with mock.patch.object(char_index, "lookup", lambda key, lang=None, limit=5: tuple(table.get(key, ()))):
        yield


# ---------------------------------------------------------------------------
# the cache keeps the birthday apart from the key
# ---------------------------------------------------------------------------

def test_birthday_is_stored_beside_the_key_and_old_entries_are_read_again(tmp_path):
    a = _file(tmp_path / "a.png", b"one")
    cache = CharaKeyCache([tmp_path])
    cache.put(a, file_fp(a), "key")                                  # no birthday recorded
    assert cache.lookup(a) == (True, "key")
    assert cache.lookup(a, birth=True) == (False, None)              # asked for it: read again
    cache.put(a, file_fp(a), "key", (9, 28))
    assert cache.lookup(a, birth=True) == (True, "key") and cache.birth_of(a) == (9, 28)
    cache.save()
    entry = json.loads((tmp_path / CACHE_FILE).read_text())["files"][str(a)]
    assert entry["key"] == "key" and entry["birth"] == [9, 28]       # separate from the key string
    assert CharaKeyCache([tmp_path]).birth_of(a) == (9, 28)


def test_birthday_follows_a_move_and_survives_a_metadata_update(tmp_path):
    a = _file(tmp_path / "a.png", b"data")
    cache = CharaKeyCache([tmp_path])
    cache.put(a, file_fp(a), "key", (None, 5))                       # a missing month is kept as null
    b = tmp_path / "S" / "a.png"
    b.parent.mkdir()
    a.replace(b)
    cache.moved(a, b)
    assert cache.birth_of(b) == (None, 5)
    cache.updated(b, "new key")
    assert cache.lookup(b, birth=True) == (True, "new key") and cache.birth_of(b) == (None, 5)


def test_a_non_chara_png_never_needs_a_birthday(tmp_path):
    p = _file(tmp_path / "p.png")
    cache = CharaKeyCache([tmp_path])
    cache.put(p, file_fp(p), None)
    assert cache.lookup(p, birth=True) == (True, None)


# ---------------------------------------------------------------------------
# resolve_locally
# ---------------------------------------------------------------------------

def test_a_single_candidate_is_assigned_whatever_the_birthday():
    with index({"k": [_cand("Rin", ["Fate"], 1, 1)]}):
        resolved, narrowed = group_chara.resolve_locally({"k": {(5, 5), (None, None)}}, "en_us")
    assert resolved == {("k", (5, 5)): "Fate", ("k", (None, None)): "Fate"} and narrowed == {}


def test_among_several_the_one_with_the_cards_birthday_wins():
    cands = [_cand("Rin", ["Fate"], 2, 3), _cand("Rin", ["SAO"], 9, 28), _cand("Rin", ["Other"])]
    with index({"k": cands}):
        resolved, narrowed = group_chara.resolve_locally({"k": {(9, 28), (4, 4), (None, None)}}, "en_us")
    assert resolved == {("k", (9, 28)): "SAO"}                         # no match / no birthday: undecided
    assert narrowed == {}


def test_two_candidates_with_the_same_birthday_decide_nothing():
    cands = [_cand("Rin", ["A"], 9, 28), _cand("Rin", ["B"], 9, 28)]
    with index({"k": cands}):
        assert group_chara.resolve_locally({"k": {(9, 28)}}, "en_us") == ({}, {})


def test_cards_sharing_a_key_are_resolved_one_by_one():
    cands = [_cand("Rin", ["A"], 1, 2), _cand("Rin", ["B"], 3, 4)]
    with index({"k": cands}):
        resolved, _ = group_chara.resolve_locally({"k": {(1, 2), (3, 4)}}, "en_us")
    assert resolved == {("k", (1, 2)): "A", ("k", (3, 4)): "B"}


def test_a_series_without_a_title_in_the_hints_language_goes_to_the_llm_with_only_that_hint():
    only = _cand("Rin", ["Fate"], 9, 28, native=False)
    other = _cand("Rin", ["SAO"], 1, 1)
    with index({"one": [only], "many": [only, other], "undecided": [only, other]}):
        resolved, narrowed = group_chara.resolve_locally(
            {"one": {(5, 5)}, "many": {(9, 28)}, "undecided": {(9, 28), (7, 7)}}, "ja_jp")
    assert resolved == {}
    assert narrowed == {"one": [only], "many": [only]}                 # "undecided" keeps its full list


def test_unknown_names_are_left_alone():
    with index({}):
        assert group_chara.resolve_locally({"k": {(1, 1)}}, "en_us") == ({}, {})


def test_resolved_keys_are_dropped_from_the_prompt_and_hints_can_be_narrowed():
    cards = {"a": {(1, 1)}, "b": {(1, 1), (2, 2)}, "c": {(1, 1)}}
    resolved = {("a", (1, 1)): "S", ("b", (1, 1)): "S"}              # b still has a card left
    left = json.loads(group_chara.keys_left_for_llm(json.dumps({k: "" for k in cards}), cards, resolved))
    assert set(left) == {"b", "c"}

    keep = _cand("Rin", ["Fate"], native=False)
    with index({}), mock.patch.object(char_index, "available", lambda: True), \
            mock.patch.object(char_index, "hints", lambda key, lang=None: ["full list"]):
        out = json.loads(group_chara.add_index_hints(json.dumps({"b": "", "c": ""}), "ja_jp", {"b": [keep]}))
    assert out["b"]["hints"] == [keep.hint] and out["c"]["hints"] == ["full list"]


# ---------------------------------------------------------------------------
# end to end with stand-in cards:  b"CHARA|<name>|<month>|<day>"
# ---------------------------------------------------------------------------

class FakeCard:
    parsed = 0

    def __init__(self, path):
        FakeCard.parsed += 1
        name, month, day = Path(path).read_bytes().split(b"|", 3)[1:]
        self.p = {"birthMonth": int(month), "birthDay": int(day), "name": name.decode()}

    @classmethod
    def load(cls, path):
        return cls(path)

    def __getitem__(self, k):
        return self.p


def _fake_key(card):
    return f"{card.p['name']} | calm | black hair"


@contextmanager
def fakes():
    FakeCard.parsed = 0
    with ExitStack() as st:
        st.enter_context(mock.patch.object(group_chara, "KoikatuCharaData", FakeCard))
        st.enter_context(mock.patch.object(group_chara, "make_key", _fake_key))
        st.enter_context(mock.patch.object(group_chara, "get_card_type",
                                           lambda raw: KK if raw.startswith(b"CHARA|") else None))
        yield


def _card(folder: Path, stem: str, name: str, month: int, day: int) -> Path:
    return _file(folder / f"{stem}.png", f"CHARA|{name}|{month}|{day}".encode())


def test_resolved_cards_move_without_the_llm_and_the_rest_follow_its_answer(tmp_path):
    a = _card(tmp_path, "a", "Rin", 9, 28)       # two Rins: the birthday picks the series
    b = _card(tmp_path, "b", "Rin", 1, 1)        # ...this one matches neither: left to the LLM
    c = _card(tmp_path, "c", "Saber", 5, 5)      # one candidate
    d = _card(tmp_path, "d", "Zed", 5, 5)        # unknown
    table = {"Rin | calm | black hair": [_cand("Rin", ["SAO"], 9, 28), _cand("Rin", ["Fate"], 2, 2)],
             "Saber | calm | black hair": [_cand("Saber", ["Fate"])]}
    with fakes(), index(table):
        birthdays: dict = {}
        json_str = group_chara.export(tmp_path, birthdays=birthdays)
        assert birthdays["Rin | calm | black hair"] == {(9, 28), (1, 1)}
        local, narrowed = group_chara.resolve_locally(birthdays, "en_us")
        left = json.loads(group_chara.keys_left_for_llm(json_str, birthdays, local))
        assert set(left) == {"Rin | calm | black hair", "Zed | calm | black hair"}   # Saber is settled

        FakeCard.parsed = 0
        llm = json.dumps({"Rin | calm | black hair": "Llm Pick", "Zed | calm | black hair": "Zed Series",
                          "Saber | calm | black hair": "Ignored"})
        group_chara.process(tmp_path, llm, local=local)
        assert FakeCard.parsed == 0                                  # key and birthday came from the cache
    assert (tmp_path / "SAO" / "a.png").exists()                     # local beats the LLM's answer
    assert (tmp_path / "Llm Pick" / "b.png").exists()
    assert (tmp_path / "Fate" / "c.png").exists()                    # not "Ignored"
    assert (tmp_path / "Zed Series" / "d.png").exists()


def test_the_birthday_is_always_cached_resolve_locally_or_not(tmp_path):
    _card(tmp_path, "a", "Rin", 9, 28)
    with fakes():
        group_chara.export(tmp_path)                                 # no birthdays asked for
        files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
        assert next(iter(files.values()))["birth"] == [9, 28]
        FakeCard.parsed = 0
        group_chara.process(tmp_path, json.dumps({"Rin | calm | black hair": "SAO"}))
        assert FakeCard.parsed == 0                                  # nothing read again
        files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
    assert files[str(tmp_path / "SAO" / "a.png")]["birth"] == [9, 28]


def test_a_cache_entry_from_before_birthdays_is_read_once_more(tmp_path):
    a = _card(tmp_path, "a", "Rin", 9, 28)
    with fakes():
        cache = CharaKeyCache([tmp_path])
        cache.put(a, file_fp(a), "Rin | calm | black hair")        # old layout: no birthday
        cache.save()
        FakeCard.parsed = 0
        group_chara.export(tmp_path)
        assert FakeCard.parsed == 1
        group_chara.export(tmp_path)
        assert FakeCard.parsed == 1


# ---------------------------------------------------------------------------
# Rename Characters' Resolve Locally: only the matching candidate is hinted
# ---------------------------------------------------------------------------

from kkafio.tasks import rename_chara


def test_narrow_returns_the_decided_candidates_or_nothing():
    sao, fate = _cand("Rin", ["SAO"], 9, 28), _cand("Rin", ["Fate"], 2, 2)
    with index({"one": [fate], "two": [sao, fate], "none": []}):
        assert char_index.narrow("one", {(5, 5)}, "en_us") == [fate]             # single: any birthday
        assert char_index.narrow("two", {(9, 28)}, "en_us") == [sao]
        assert char_index.narrow("two", {(9, 28), (2, 2)}, "en_us") == [sao, fate] or \
            char_index.narrow("two", {(9, 28), (2, 2)}, "en_us") == [fate, sao]   # each card decided
        assert char_index.narrow("two", {(9, 28), (7, 7)}, "en_us") == []        # one card undecided
        assert char_index.narrow("none", {(9, 28)}, "en_us") == []


def test_rename_hints_use_only_the_narrowed_candidates(tmp_path):
    keep = _cand("Tohsaka, Rin", ["Fate"], 2, 3)
    with mock.patch.object(char_index, "available", lambda: True), \
            mock.patch.object(char_index, "hints", lambda key, lang=None: ["a", "b", "c"]):
        out = json.loads(rename_chara.add_index_hints(
            json.dumps({"x": {"lastname": ""}, "y": {"lastname": ""}}), "en_us", {"x": [keep]}))
    assert out["x"]["hints"] == [keep.hint] and out["y"]["hints"] == ["a", "b", "c"]


def test_rename_export_collects_the_birthdays_of_the_characters_it_returns(tmp_path):
    _card(tmp_path, "a", "Rin", 9, 28)
    _card(tmp_path, "b", "Rin", 1, 1)
    _card(tmp_path, "c", "Saber", 5, 5)
    with ExitStack() as st:
        FakeCard.parsed = 0
        st.enter_context(mock.patch.object(rename_chara, "KoikatuCharaData", FakeCard))
        st.enter_context(mock.patch.object(rename_chara, "make_key", _fake_key))
        st.enter_context(mock.patch.object(rename_chara, "get_card_type",
                                           lambda raw: KK if raw.startswith(b"CHARA|") else None))
        births: dict = {}
        out = json.loads(rename_chara.export(tmp_path, birthdays=births))
        assert set(out) == {"Rin | calm | black hair", "Saber | calm | black hair"}
        assert births == {"Rin | calm | black hair": {(9, 28), (1, 1)}, "Saber | calm | black hair": {(5, 5)}}
        FakeCard.parsed = 0
        again: dict = {}
        rename_chara.export(tmp_path, birthdays=again)                           # from the cache
        assert FakeCard.parsed == 0 and again == births
