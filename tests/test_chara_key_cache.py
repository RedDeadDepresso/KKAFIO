"""The per-card key cache shared by Group Characters and Rename Characters: the cache
class itself, then both tasks end to end with stand-in cards (so no real kkloader is needed
and the number of cards actually parsed can be counted)."""

import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from kkafio.cards import chara_key_cache as ckc
from kkafio.cards.cache_io import file_fp
from kkafio.cards.chara_key_cache import CACHE_FILE, CharaKeyCache
from kkafio.cards.classifier import CHARA_CARD_TYPES
from kkafio.tasks import group_chara, rename_chara

KK = CHARA_CARD_TYPES[0]


# ---------------------------------------------------------------------------
# the cache class
# ---------------------------------------------------------------------------

def _file(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_lookup_hits_only_while_the_file_is_unchanged(tmp_path):
    a = _file(tmp_path / "a.png", b"one")
    cache = CharaKeyCache([tmp_path])
    assert cache.lookup(a) == (False, None)
    cache.put(a, file_fp(a), "key A")
    assert cache.lookup(a) == (True, "key A")
    a.write_bytes(b"changed, and a different size")
    assert cache.lookup(a) == (False, None)                 # fingerprint no longer matches
    assert (cache.hits, cache.misses) == (1, 2)


def test_a_non_chara_png_is_cached_as_none(tmp_path):
    p = _file(tmp_path / "p.png")
    cache = CharaKeyCache([tmp_path])
    cache.put(p, file_fp(p), None)
    assert cache.lookup(p) == (True, None)                  # a hit, whose key is "not a chara card"


def test_moved_entry_follows_the_file_and_changed_file_is_dropped(tmp_path):
    a = _file(tmp_path / "a.png", b"data")
    cache = CharaKeyCache([tmp_path])
    cache.put(a, file_fp(a), "key")
    b = tmp_path / "Series" / "a.png"
    b.parent.mkdir()
    os.replace(a, b)
    cache.moved(a, b)
    assert cache.lookup(b) == (True, "key") and cache.lookup(a) == (False, None)

    c = tmp_path / "c.png"
    os.replace(b, c)
    c.write_bytes(b"edited meanwhile")                       # not a plain move: don't trust the old key
    cache.moved(b, c)
    assert cache.lookup(c) == (False, None)


def test_updated_records_the_new_key_and_fingerprint(tmp_path):
    a = _file(tmp_path / "a.png", b"old")
    cache = CharaKeyCache([tmp_path])
    cache.put(a, file_fp(a), "old key")
    a.write_bytes(b"rewritten metadata")
    cache.updated(a, "new key")
    assert cache.lookup(a) == (True, "new key")
    cache.forget(a)
    assert cache.lookup(a) == (False, None)


def test_save_and_reload_round_trip_per_root_and_drop_missing_files(tmp_path):
    female, male = tmp_path / "female", tmp_path / "male"
    f1, f2 = _file(female / "a.png", b"1"), _file(female / "sub" / "b.png", b"22")
    m1, gone = _file(male / "m.png", b"333"), _file(male / "gone.png", b"4")
    cache = CharaKeyCache([female, male])
    for p, k in ((f1, "A"), (f2, "B"), (m1, "M"), (gone, "G")):
        cache.put(p, file_fp(p), k)
    gone.unlink()
    cache.save()
    assert (female / CACHE_FILE).exists() and (male / CACHE_FILE).exists()
    assert set(json.loads((female / CACHE_FILE).read_text())["files"]) == {str(f1), str(f2)}
    assert set(json.loads((male / CACHE_FILE).read_text())["files"]) == {str(m1)}   # gone.png pruned

    again = CharaKeyCache([female, male])
    assert again.lookup(f2) == (True, "B") and again.lookup(m1) == (True, "M")
    assert again.lookup(gone) == (False, None)


def test_unchanged_cache_is_not_rewritten(tmp_path):
    a = _file(tmp_path / "a.png")
    cache = CharaKeyCache([tmp_path])
    cache.put(a, file_fp(a), "k")
    cache.save()
    first = (tmp_path / CACHE_FILE).stat().st_mtime_ns
    again = CharaKeyCache([tmp_path])
    again.lookup(a)                                           # reading changes nothing
    again.save()
    assert (tmp_path / CACHE_FILE).stat().st_mtime_ns == first


def test_cache_from_another_version_or_folder_or_garbage_is_ignored(tmp_path):
    a = _file(tmp_path / "a.png")
    entry = {str(a): {"fp": file_fp(a), "key": "k"}}
    cases = {
        "wrong version": {"version": ckc.CACHE_VERSION + 1, "dir": str(tmp_path), "files": entry},
        "other folder":  {"version": ckc.CACHE_VERSION, "dir": str(tmp_path / "elsewhere"), "files": entry},
        "files not a dict": {"version": ckc.CACHE_VERSION, "dir": str(tmp_path), "files": []},
    }
    for name, data in cases.items():
        (tmp_path / CACHE_FILE).write_text(json.dumps(data))
        assert CharaKeyCache([tmp_path]).lookup(a) == (False, None), name
    (tmp_path / CACHE_FILE).write_text("{not json")
    assert CharaKeyCache([tmp_path]).lookup(a) == (False, None)
    (tmp_path / CACHE_FILE).write_text(json.dumps(
        {"version": ckc.CACHE_VERSION, "dir": str(tmp_path), "files": entry}))
    assert CharaKeyCache([tmp_path]).lookup(a) == (True, "k")            # ...and a good one is used


def test_disabled_cache_reads_and_writes_nothing(tmp_path):
    a = _file(tmp_path / "a.png")
    (tmp_path / CACHE_FILE).write_text(json.dumps(
        {"version": ckc.CACHE_VERSION, "dir": str(tmp_path), "files": {str(a): {"fp": file_fp(a), "key": "k"}}}))
    before = (tmp_path / CACHE_FILE).read_bytes()
    cache = CharaKeyCache([tmp_path], enabled=False)
    assert cache.lookup(a) == (False, None)
    cache.put(a, file_fp(a), "other"); cache.moved(a, tmp_path / "b.png"); cache.forget(a); cache.save()
    assert (tmp_path / CACHE_FILE).read_bytes() == before                # untouched


# ---------------------------------------------------------------------------
# both tasks, end to end, with stand-in cards
# ---------------------------------------------------------------------------

class FakeCard:
    """Stands in for KoikatuCharaData. A card file is  b"CHARA|<name>"."""
    parsed = 0

    def __init__(self, path):
        FakeCard.parsed += 1
        self.name = Path(path).read_bytes().split(b"|", 1)[1].decode()
        self.parameter = {"lastname": "", "firstname": "", "nickname": ""}

    @classmethod
    def load(cls, path):
        return cls(path)

    def __getitem__(self, k):
        return self.parameter

    def display_name(self):
        last, first = self.parameter["lastname"], self.parameter["firstname"]
        return f"{last} {first}" if last or first else self.name

    def save(self, path):
        Path(path).write_bytes(b"CHARA|" + self.display_name().encode())


def _fake_key(card):
    return f"{card.display_name()} | calm | black hair"


def _fake_card_type(raw):
    return KK if raw.startswith(b"CHARA|") else None


@contextmanager
def fakes():
    FakeCard.parsed = 0
    with ExitStack() as st:
        for module in (group_chara, rename_chara):
            st.enter_context(mock.patch.object(module, "KoikatuCharaData", FakeCard))
            st.enter_context(mock.patch.object(module, "make_key", _fake_key))
            st.enter_context(mock.patch.object(module, "get_card_type", _fake_card_type))
        yield


def _cards(folder: Path, **names) -> dict[str, Path]:
    out = {}
    for stem, name in names.items():
        out[stem] = _file(folder / f"{stem}.png", b"CHARA|" + name.encode())
    return out


def test_group_chara_reads_each_card_once_then_reuses_the_cache_even_after_moving(tmp_path):
    cards = _cards(tmp_path, a="Rin", b="Saber")
    _file(tmp_path / "not_a_card.png", b"just a picture")
    with fakes():
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
        assert files[str(moved)]["key"] == "Rin | calm | black hair"
        assert str(cards["a"]) not in files
        assert files[str(tmp_path / "not_a_card.png")]["key"] is None

        FakeCard.parsed = 0
        group_chara.export(tmp_path, include_subfolders=True)
        assert FakeCard.parsed == 0                                  # nothing re-read after the move either


def test_group_chara_rereads_a_card_that_changed(tmp_path):
    cards = _cards(tmp_path, a="Rin")
    with fakes():
        group_chara.export(tmp_path)
        cards["a"].write_bytes(b"CHARA|Rin but edited later")
        FakeCard.parsed = 0
        keys = json.loads(group_chara.export(tmp_path))
        assert FakeCard.parsed == 1 and "Rin but edited later | calm | black hair" in keys


def test_group_chara_without_cache_reads_every_time_and_writes_no_file(tmp_path):
    _cards(tmp_path, a="Rin", b="Saber")
    with fakes():
        group_chara.export(tmp_path, use_cache=False)
        group_chara.export(tmp_path, use_cache=False)
        assert FakeCard.parsed == 4
        group_chara.process(tmp_path, json.dumps({"Rin | calm | black hair": "Fate"}), use_cache=False)
    assert not (tmp_path / CACHE_FILE).exists()
    assert (tmp_path / "Fate" / "a.png").exists()                    # the task itself still works


def test_group_chara_cache_is_saved_even_if_the_run_is_interrupted(tmp_path):
    _cards(tmp_path, a="Rin", b="Saber")
    real_move = group_chara.shutil.move
    calls = []

    def stop_on_second(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_move(src, dst)

    mapping = json.dumps({"Rin | calm | black hair": "S1", "Saber | calm | black hair": "S2"})
    with fakes(), mock.patch.object(group_chara.shutil, "move", stop_on_second):
        try:
            group_chara.process(tmp_path, mapping)
        except KeyboardInterrupt:
            pass
    files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
    moved = [p for p in files if "S1" in p or "S2" in p]
    assert len(moved) == 1 and files[moved[0]]["key"]                # the one move that happened is recorded


def test_rename_chara_updates_the_cache_for_rewritten_and_renamed_cards(tmp_path):
    cards = _cards(tmp_path, a="Rin", b="Saber")
    response = json.dumps({
        "Rin | calm | black hair": {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"},
    })
    with fakes():
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
        assert entry["key"] == "Tohsaka Rin | calm | black hair"      # the NEW key (the name changed)
        assert entry["fp"] == file_fp(renamed)                        # and the new fingerprint

        FakeCard.parsed = 0
        rename_chara.export(tmp_path, skip_already_renamed=False)
        assert FakeCard.parsed == 0                                   # a fresh run reads nothing


def test_rename_chara_only_loads_cards_when_it_must(tmp_path):
    _cards(tmp_path, a="Rin", b="Saber")
    response = json.dumps({
        "Rin | calm | black hair": {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"},
    })
    with fakes():
        rename_chara.export(tmp_path)                                  # warm the cache
        FakeCard.parsed = 0
        # file rename only: the keys come from the cache, so no card needs parsing at all
        rename_chara.process([tmp_path], response, update_metadata=False, rename_files=True)
        assert FakeCard.parsed == 0
    assert (tmp_path / "Tohsaka_Rin.png").exists()
    files = json.loads((tmp_path / CACHE_FILE).read_text())["files"]
    assert files[str(tmp_path / "Tohsaka_Rin.png")]["key"] == "Rin | calm | black hair"


def test_rename_chara_without_cache_matches_the_old_behaviour(tmp_path):
    _cards(tmp_path, a="Rin")
    response = json.dumps({
        "Rin | calm | black hair": {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"},
    })
    with fakes():
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
