"""The per-card info cache shared by Group Characters and Rename Characters: the cache
class itself, then both tasks end to end with stand-in cards (so no real kkloader is needed
and the number of cards actually parsed can be counted)."""

import json
import os
from unittest import mock

from chara_fakes import FakeCard, fake_cards, make_info, write_cards, write_file
from kkafio.cards.chara import chara_info_cache as ckc
from kkafio.cards.chara.chara_key import CharaInfo
from kkafio.cards.cache_io import file_fp
from kkafio.cards.chara.chara_info_cache import CACHE_FILE, CharaInfoCache
from kkafio.tasks import group_chara, rename_chara


# ---------------------------------------------------------------------------
# the cache class
# ---------------------------------------------------------------------------

def test_lookup_hits_only_while_the_file_is_unchanged(tmp_path):
    a = write_file(tmp_path / "a.png", b"one")
    cache = CharaInfoCache([tmp_path])
    assert cache.lookup(a) == (False, None)
    cache.put(a, file_fp(a), make_info("A"))
    assert cache.lookup(a) == (True, make_info("A"))
    a.write_bytes(b"changed, and a different size")
    assert cache.lookup(a) == (False, None)                 # fingerprint no longer matches
    assert (cache.hits, cache.misses) == (1, 2)


def test_a_non_chara_png_is_cached_as_none(tmp_path):
    p = write_file(tmp_path / "p.png")
    cache = CharaInfoCache([tmp_path])
    cache.put(p, file_fp(p), None)
    assert cache.lookup(p) == (True, None)                  # a hit, whose key is "not a chara card"


def test_moved_entry_follows_the_file_and_changed_file_is_dropped(tmp_path):
    a = write_file(tmp_path / "a.png", b"data")
    cache = CharaInfoCache([tmp_path])
    cache.put(a, file_fp(a), make_info("K"))
    b = tmp_path / "Series" / "a.png"
    b.parent.mkdir()
    os.replace(a, b)
    cache.moved(a, b)
    assert cache.lookup(b) == (True, make_info("K")) and cache.lookup(a) == (False, None)

    c = tmp_path / "c.png"
    os.replace(b, c)
    c.write_bytes(b"edited meanwhile")                       # not a plain move: don't trust the old entry
    cache.moved(b, c)
    assert cache.lookup(c) == (False, None)


def test_updated_records_the_new_fields_and_fingerprint(tmp_path):
    a = write_file(tmp_path / "a.png", b"old")
    cache = CharaInfoCache([tmp_path])
    cache.put(a, file_fp(a), make_info("Old"))
    a.write_bytes(b"rewritten metadata")
    cache.updated(a, make_info("New"))
    assert cache.lookup(a) == (True, make_info("New"))
    cache.forget(a)
    assert cache.lookup(a) == (False, None)


def test_save_and_reload_round_trip_per_root_and_drop_missing_files(tmp_path):
    female, male = tmp_path / "female", tmp_path / "male"
    f1, f2 = write_file(female / "a.png", b"1"), write_file(female / "sub" / "b.png", b"22")
    m1, gone = write_file(male / "m.png", b"333"), write_file(male / "gone.png", b"4")
    cache = CharaInfoCache([female, male])
    for p, k in ((f1, "A"), (f2, "B"), (m1, "M"), (gone, "G")):
        cache.put(p, file_fp(p), make_info(k))
    gone.unlink()
    cache.save()
    assert (female / CACHE_FILE).exists() and (male / CACHE_FILE).exists()
    assert set(json.loads((female / CACHE_FILE).read_text())["files"]) == {str(f1), str(f2)}
    assert set(json.loads((male / CACHE_FILE).read_text())["files"]) == {str(m1)}   # gone.png pruned

    again = CharaInfoCache([female, male])
    assert again.lookup(f2) == (True, make_info("B")) and again.lookup(m1) == (True, make_info("M"))
    assert again.lookup(gone) == (False, None)


def test_unchanged_cache_is_not_rewritten(tmp_path):
    a = write_file(tmp_path / "a.png")
    cache = CharaInfoCache([tmp_path])
    cache.put(a, file_fp(a), make_info("k"))
    cache.save()
    first = (tmp_path / CACHE_FILE).stat().st_mtime_ns
    again = CharaInfoCache([tmp_path])
    again.lookup(a)                                           # reading changes nothing
    again.save()
    assert (tmp_path / CACHE_FILE).stat().st_mtime_ns == first


def test_cache_from_another_version_or_folder_or_garbage_is_ignored(tmp_path):
    a = write_file(tmp_path / "a.png")
    entry = {str(a): {"fp": file_fp(a), "info": make_info("k").to_json()}}
    cases = {
        "wrong version": {"version": ckc.CACHE_VERSION + 1, "dir": str(tmp_path), "files": entry},
        "other folder":  {"version": ckc.CACHE_VERSION, "dir": str(tmp_path / "elsewhere"), "files": entry},
        "files not a dict": {"version": ckc.CACHE_VERSION, "dir": str(tmp_path), "files": []},
    }
    for name, data in cases.items():
        (tmp_path / CACHE_FILE).write_text(json.dumps(data))
        assert CharaInfoCache([tmp_path]).lookup(a) == (False, None), name
    (tmp_path / CACHE_FILE).write_text("{not json")
    assert CharaInfoCache([tmp_path]).lookup(a) == (False, None)
    (tmp_path / CACHE_FILE).write_text(json.dumps(
        {"version": ckc.CACHE_VERSION, "dir": str(tmp_path), "files": entry}))
    assert CharaInfoCache([tmp_path]).lookup(a) == (True, make_info("k"))     # ...and a good one is used


def test_disabled_cache_reads_and_writes_nothing(tmp_path):
    a = write_file(tmp_path / "a.png")
    (tmp_path / CACHE_FILE).write_text(json.dumps(
        {"version": ckc.CACHE_VERSION, "dir": str(tmp_path), "files": {str(a): {"fp": file_fp(a), "info": make_info("k").to_json()}}}))
    before = (tmp_path / CACHE_FILE).read_bytes()
    cache = CharaInfoCache([tmp_path], enabled=False)
    assert cache.lookup(a) == (False, None)
    cache.put(a, file_fp(a), make_info("other")); cache.moved(a, tmp_path / "b.png"); cache.forget(a); cache.save()
    assert (tmp_path / CACHE_FILE).read_bytes() == before                # untouched


# ---------------------------------------------------------------------------
# what is stored: raw fields, with the key built from them
# ---------------------------------------------------------------------------

def test_info_round_trips_through_json_including_missing_values():
    for info in (make_info("Rin", first="Tohsaka", nick="R", personality=7, hair=(255, 0, 0), birth=(9, 28)),
                 make_info("Rin", hair=None, birth=(None, 5)),
                 make_info("", personality=99)):
        assert CharaInfo.from_json(json.loads(json.dumps(info.to_json()))) == info


def test_an_entry_missing_a_field_or_malformed_is_read_again(tmp_path):
    a = write_file(tmp_path / "a.png")
    good = make_info("Rin", hair=(1, 2, 3), birth=(9, 28)).to_json()
    bad = {
        "no birthday (written before it existed)": {k: v for k, v in good.items() if k != "birth"},
        "no hair field": {k: v for k, v in good.items() if k != "hair"},
        "hair of the wrong length": {**good, "hair": [1, 2]},
        "personality not an int": {**good, "pers": "calm"},
        "birth not a pair": {**good, "birth": [9]},
        "a name that is not text": {**good, "last": 5},
        "not even a dict": ["Rin"],
    }
    cache = CharaInfoCache([tmp_path])
    for name, info in bad.items():
        cache._data[str(a)] = {"fp": file_fp(a), "info": info}
        assert cache.lookup(a) == (False, None), name
    cache._data[str(a)] = {"fp": file_fp(a), "info": good}
    assert cache.lookup(a) == (True, make_info("Rin", hair=(1, 2, 3), birth=(9, 28)))
    cache._data[str(a)] = {"fp": file_fp(a), "key": "an entry from the old key cache"}
    assert cache.lookup(a) == (False, None)


def test_the_old_key_cache_file_is_not_read(tmp_path):
    a = write_file(tmp_path / "a.png")
    (tmp_path / "kkafio_chara_key_cache.json").write_text(json.dumps(
        {"version": 3, "dir": str(tmp_path), "files": {str(a): {"fp": file_fp(a), "key": "k", "birth": [1, 1]}}}))
    assert CACHE_FILE != "kkafio_chara_key_cache.json"
    assert CharaInfoCache([tmp_path]).lookup(a) == (False, None)


def test_key_of_builds_the_key_from_the_raw_fields():
    from kkafio.cards.chara.chara_key import key_of
    from kkafio.cards.chara.hair_color import hair_color_name
    from kkafio.cards.classifier import PERSONALITIES
    info = make_info("Toh|saka", first=" Rin ", nick="", personality=1, hair=(255, 0, 0), birth=(9, 28))
    assert key_of(info) == f"Toh/saka | Rin |  | {PERSONALITIES[1]} | {hair_color_name((255, 0, 0))} hair"
    assert info.last == "Toh|saka"                                       # stored as the card has it
    assert key_of(make_info("X", personality=len(PERSONALITIES) + 5, hair=None)).endswith(
        f"| {len(PERSONALITIES) + 5} | unknown hair")                    # unknown personality / no hair colour


def test_changing_how_keys_are_built_does_not_invalidate_the_cache(tmp_path):
    from kkafio.cards.chara import chara_key
    a = write_file(tmp_path / "a.png")
    cache = CharaInfoCache([tmp_path])
    cache.put(a, file_fp(a), make_info("Rin", hair=(255, 0, 0)))
    cache.save()
    _hit, info = CharaInfoCache([tmp_path]).lookup(a)
    before = chara_key.key_of(info)
    with mock.patch.object(chara_key, "hair_color_name", lambda rgb: "crimson"):
        hit, info = CharaInfoCache([tmp_path]).lookup(a)
        assert hit                                                    # still a hit, no card re-read
        assert chara_key.key_of(info) != before and chara_key.key_of(info).endswith("crimson hair")


class _Card(dict):
    """A loaded card as chara_info reads it: kc["Parameter"], kc["Custom"]["hair"]["parts"]."""
    def __init__(self, parameter, parts):
        super().__init__(Parameter=parameter, Custom={"hair": {"parts": parts}})


def test_chara_info_reads_raw_values_off_a_card():
    from kkafio.cards.chara.chara_key import chara_info
    card = _Card({"lastname": "Tohsaka", "firstname": None, "nickname": "A|B", "personality": 3,
                  "birthMonth": 9, "birthDay": True},                  # a bool is not a day
                 [{"id": 1, "baseColor": [1.0, 0.0, 0.0, 1.0]}])
    assert chara_info(card) == CharaInfo("Tohsaka", "", "A|B", 3, (255, 0, 0), (9, None))
    no_hair = _Card({"lastname": "", "firstname": "", "nickname": "", "personality": 0}, [{"id": 0}, {"id": 0}])
    assert chara_info(no_hair) == CharaInfo("", "", "", 0, None, (None, None))
    try:
        chara_info(_Card({"lastname": "x"}, []))                      # no firstname: an unreadable card
    except KeyError:
        pass
    else:
        raise AssertionError("a card without its name fields must not be cached as a character")


# ---------------------------------------------------------------------------
# both tasks, end to end, with stand-in cards
# ---------------------------------------------------------------------------


def test_group_chara_reads_each_card_once_then_reuses_the_cache_even_after_moving(tmp_path):
    cards = write_cards(tmp_path, a="Rin", b="Saber")
    write_file(tmp_path / "not_a_card.png", b"just a picture")
    with fake_cards():
        first = json.loads(group_chara.export(tmp_path))
        assert set(first) == {"Rin | calm | black hair", "Saber | calm | black hair"}
        assert FakeCard.parsed == 2                                  # the non-card was classified, not parsed

        FakeCard.parsed = 0
        assert json.loads(group_chara.export(tmp_path)) == first
        assert FakeCard.parsed == 0                                  # everything from the cache

        group_chara.process(tmp_path, json.dumps({"Rin | calm | black hair": "Fate"}))
        assert FakeCard.parsed == 0                                  # moving didn't re-read anything
        moved = tmp_path / "Fate" / "a.png"
        assert moved.exists() and not cards["a"].exists()

        # the entry followed the card to its new folder
        files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
        assert CharaInfo.from_json(files[str(moved)]["info"]) == make_info("Rin")
        assert str(cards["a"]) not in files
        assert files[str(tmp_path / "not_a_card.png")]["info"] is None

        FakeCard.parsed = 0
        group_chara.export(tmp_path, include_subfolders=True)
        assert FakeCard.parsed == 0                                  # nothing re-read after the move either


def test_group_chara_rereads_a_card_that_changed(tmp_path):
    cards = write_cards(tmp_path, a="Rin")
    with fake_cards():
        group_chara.export(tmp_path)
        cards["a"].write_bytes(b"CHARA|Rin but edited later")
        FakeCard.parsed = 0
        keys = json.loads(group_chara.export(tmp_path))
        assert FakeCard.parsed == 1 and "Rin but edited later | calm | black hair" in keys


def test_group_chara_without_cache_reads_every_time_and_writes_no_file(tmp_path):
    write_cards(tmp_path, a="Rin", b="Saber")
    with fake_cards():
        group_chara.export(tmp_path, use_cache=False)
        group_chara.export(tmp_path, use_cache=False)
        assert FakeCard.parsed == 4
        group_chara.process(tmp_path, json.dumps({"Rin | calm | black hair": "Fate"}), use_cache=False)
    assert not (tmp_path / CACHE_FILE).exists()
    assert (tmp_path / "Fate" / "a.png").exists()                    # the task itself still works


def test_group_chara_cache_is_saved_even_if_the_run_is_interrupted(tmp_path):
    write_cards(tmp_path, a="Rin", b="Saber")
    real_move = group_chara.shutil.move
    calls = []

    def stop_on_second(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_move(src, dst)

    mapping = json.dumps({"Rin | calm | black hair": "S1", "Saber | calm | black hair": "S2"})
    with fake_cards(), mock.patch.object(group_chara.shutil, "move", stop_on_second):
        try:
            group_chara.process(tmp_path, mapping)
        except KeyboardInterrupt:
            pass
    files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
    moved = [p for p in files if "S1" in p or "S2" in p]
    assert len(moved) == 1 and files[moved[0]]["info"]               # the one move that happened is recorded


def test_rename_chara_updates_the_cache_for_rewritten_and_renamed_cards(tmp_path):
    cards = write_cards(tmp_path, a="Rin", b="Saber")
    response = json.dumps({
        "Rin | calm | black hair": {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"},
    })
    with fake_cards():
        rename_chara.export(tmp_path)
        assert FakeCard.parsed == 2
        FakeCard.parsed = 0
        rename_chara.process([tmp_path], response, update_metadata=True, rename_files=True)
        assert FakeCard.parsed == 1            # only the card whose metadata is rewritten is loaded again

        renamed = tmp_path / "Tohsaka_Rin.png"
        assert renamed.exists() and not cards["a"].exists() and cards["b"].exists()
        assert renamed.read_bytes() == b"CHARA|Tohsaka Rin"          # the metadata was rewritten

        files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
        assert str(cards["a"]) not in files                           # old path gone
        entry = files[str(renamed)]
        assert CharaInfo.from_json(entry["info"]) == make_info("Tohsaka Rin")   # the NEW name
        assert entry["fp"] == file_fp(renamed)                        # and the new fingerprint

        FakeCard.parsed = 0
        rename_chara.export(tmp_path, skip_already_renamed=False)
        assert FakeCard.parsed == 0                                   # a fresh run reads nothing


def test_rename_chara_only_loads_cards_when_it_must(tmp_path):
    write_cards(tmp_path, a="Rin", b="Saber")
    response = json.dumps({
        "Rin | calm | black hair": {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"},
    })
    with fake_cards():
        rename_chara.export(tmp_path)                                  # warm the cache
        FakeCard.parsed = 0
        # file rename only: the keys come from the cache, so no card needs parsing at all
        rename_chara.process([tmp_path], response, update_metadata=False, rename_files=True)
        assert FakeCard.parsed == 0
    assert (tmp_path / "Tohsaka_Rin.png").exists()
    files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
    assert CharaInfo.from_json(files[str(tmp_path / "Tohsaka_Rin.png")]["info"]) == make_info("Rin")


def test_rename_chara_without_cache_matches_the_old_behaviour(tmp_path):
    write_cards(tmp_path, a="Rin")
    response = json.dumps({
        "Rin | calm | black hair": {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"},
    })
    with fake_cards():
        rename_chara.export(tmp_path, use_cache=False)
        rename_chara.process([tmp_path], response, update_metadata=True, rename_files=True, use_cache=False)
    assert not (tmp_path / CACHE_FILE).exists()
    assert (tmp_path / "Tohsaka_Rin.png").read_bytes() == b"CHARA|Tohsaka Rin"
    assert (tmp_path / "kkafio_rename_cache.json").exists()           # the translation cache is separate and always kept


def test_task_classes_read_use_cache_from_config():
    for module, cls, section in ((group_chara, group_chara.GroupChara, "group_chara"),
                                 (rename_chara, rename_chara.RenameChara, "rename_chara")):
        cfg = mock.Mock()
        setattr(cfg, section, {"UseCache": False})
        assert cls(cfg, None).use_cache is False
        setattr(cfg, section, {})
        assert cls(cfg, None).use_cache is True                       # on by default
