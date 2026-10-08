"""Group Coordinates with Ignore Accessories / Clothes Tolerance: same or similar clothes, any accessories."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import card_factory as cf
from kkafio.cards import outfits
from kkafio.core.errors import InputError
from kkafio.tasks import group_coordinates as gc


# --- outfit signatures / accessory difference -------------------------------------------------------

def _outfit(top_id=1, accessories=((5, 1), (6, 1)), version="0.0.1"):
    """An outfit whose accessory parts are (id, type) pairs; type 120 (with id 0) is an empty slot."""
    return {
        "clothes": {"version": version, "parts": [{"id": top_id, "colorInfo": []}], "subPartsId": [0, 0, 0]},
        "accessory": {"version": "0.0.0",
                      "parts": [{"type": 120 if t == 120 else 120 + t, "id": i, "parentKey": "a_n_none"}
                                for i, t in accessories]},
    }


_INFOS = [{"Property": "ChaFileClothes.ClothesTop", "Slot": 1, "LocalSlot": 1001, "ModID": "mod.top"},
          {"Property": "accessory0.ChaFileAccessory.PartsInfo.id", "Slot": 5, "LocalSlot": 1005, "ModID": "mod.acc"}]


def test_signature_ignores_empty_slots_and_accessory_order():
    base = outfits.outfit_signature(_outfit(accessories=((5, 1), (6, 1))), [])
    assert len(base[1]) == 2
    assert outfits.outfit_signature(_outfit(accessories=((5, 1), (0, 120), (6, 1))), []) == base
    assert outfits.outfit_signature(_outfit(accessories=((6, 1), (5, 1))), []) == base     # slots don't matter


def test_signature_separates_clothes_from_accessories():
    a = outfits.outfit_signature(_outfit(top_id=1), [])
    b = outfits.outfit_signature(_outfit(top_id=2), [])
    c = outfits.outfit_signature(_outfit(top_id=1, accessories=((5, 1),)), [])
    assert a[0] != b[0] and a[1] == b[1]
    assert a[0] == c[0] and a[1] != c[1]


def test_clothes_tokens_are_per_slot():
    def outfit(*ids):
        o = _outfit()
        o["clothes"]["parts"] = [{"id": i, "colorInfo": []} for i in ids]
        return o
    sig = lambda *ids: outfits.outfit_signature(outfit(*ids), [])[0]       # noqa: E731
    assert len(sig(1, 2, 3)) == 4                                           # three slots + the rest
    assert outfits.clothes_difference(sig(1, 2, 3), sig(1, 2, 3)) == 0
    assert outfits.clothes_difference(sig(1, 2, 3), sig(1, 9, 3)) == 1
    assert outfits.clothes_difference(sig(1, 2, 3), sig(3, 2, 1)) == 2      # same items, other slots
    assert outfits.clothes_difference(sig(1, 2, 3), sig(7, 8, 9)) == 3


def test_signature_resolves_modded_ids_like_the_digest_does():
    by_slot = outfits.outfit_signature(_outfit(top_id=1, accessories=((5, 1),)), _INFOS)
    by_local = outfits.outfit_signature(_outfit(top_id=1001, accessories=((1005, 1),)), _INFOS)
    assert by_slot == by_local


def test_accessory_difference_counts_changes_not_slots():
    d = outfits.accessory_difference
    assert d(["a", "b"], ["a", "b"]) == 0
    assert d(["a", "b"], ["a", "c"]) == 1            # swap
    assert d(["a", "b"], ["a"]) == 1                 # removal
    assert d(["a"], ["a", "b", "c"]) == 2            # additions
    assert d(["a", "a"], ["a"]) == 1                 # duplicates are counted
    assert d([], []) == 0


def test_signature_difference_counts_clothes_and_accessories_separately():
    sa = outfits.outfit_signature(_outfit(top_id=1), [])
    sb = outfits.outfit_signature(_outfit(top_id=2), [])
    sc = outfits.outfit_signature(_outfit(top_id=1, accessories=((5, 1),)), [])
    sd = outfits.outfit_signature(_outfit(top_id=2, accessories=((5, 1),)), [])
    assert outfits.signature_difference(sa, sb) == (1, 0)
    assert outfits.signature_difference(sa, sc) == (0, 1)
    assert outfits.signature_difference(sa, sd) == (1, 1)


# --- the matcher --------------------------------------------------------------------------------------

def _sig(clothes, *accs):
    """A signature; `clothes` is a string of slot letters, e.g. "ABC" is three clothes slots."""
    return tuple(f"{i}:{c}" for i, c in enumerate(clothes)), tuple(sorted(accs))


def test_matcher_ignores_accessories_but_requires_same_clothes():
    chara = {Path("Alice.png"): [_sig("ABC", "a", "b", "c")]}
    m = gc.fuzzy_matcher(chara, True)
    assert m(_sig("ABC", "a", "b", "c")) == [("Alice", 0, 0)]
    assert m(_sig("ABC", "a", "b", "x")) == [("Alice", 0, 0)]
    assert m(_sig("ABC")) == [("Alice", 0, 0)]
    assert m(_sig("ABC", "x", "y", "z", "w")) == [("Alice", 0, 0)]
    assert m(_sig("ABD", "a", "b", "c")) == []                  # other clothes


def test_matcher_without_ignore_accessories_needs_identical_accessories():
    chara = {Path("Alice.png"): [_sig("ABC", "a", "b", "c")]}
    m = gc.fuzzy_matcher(chara, False, 1)
    assert m(_sig("ABC", "a", "b", "c")) == [("Alice", 0, 0)]
    assert m(_sig("ABX", "a", "b", "c")) == [("Alice", 1, 0)]   # clothes tolerance only
    assert m(_sig("ABC", "a", "b", "x")) == []
    assert m(_sig("ABC", "a", "b")) == []


def test_matcher_combines_ignore_accessories_with_clothes_tolerance():
    chara = {Path("Alice.png"): [_sig("ABCD", "a", "b")]}
    m = gc.fuzzy_matcher(chara, True, 1)
    assert m(_sig("ABCX", "a", "b")) == [("Alice", 1, 0)]
    assert m(_sig("ABCX", "a", "x", "y")) == [("Alice", 1, 0)]  # accessories don't count
    assert m(_sig("ABXY", "a", "b")) == []                      # 2 clothes parts, limit 1
    assert gc.fuzzy_matcher(chara, False, 1)(_sig("ABCX", "a", "x")) == []   # accessories must match
    assert gc.fuzzy_matcher(chara, False, 2)(_sig("ABXY", "a", "b")) == [("Alice", 2, 0)]


def test_clothes_tolerance_does_not_miss_matches_the_index_could_skip():
    """Brute-force cross-check of the candidate lookup (shared common tokens, k = 0..3)."""
    import random
    rng = random.Random(1)
    letters = "ABCDEFGH"
    chara = {Path(f"c{n:02d}.png"): [_sig("".join(rng.choice("AAAB" if i == 0 else letters) for i in range(6)))
                                     for _ in range(3)] for n in range(25)}
    for k in range(4):
        m = gc.fuzzy_matcher(chara, True, k)
        for _ in range(60):
            q = _sig("".join(rng.choice("AAAB" if i == 0 else letters) for i in range(6)))
            expected = sorted({p.stem for p, sigs in chara.items()
                               for s in sigs if outfits.clothes_difference(q[0], s[0]) <= k})
            assert sorted(n for n, _, _ in m(q)) == expected, (k, q)


def test_matcher_prefers_fewest_clothes_differences_then_filename():
    chara = {Path("Zed.png"): [_sig("AB", "a")],
             Path("Bob.png"): [_sig("AC", "a")],
             Path("Amy.png"): [_sig("AD", "a")]}
    m = gc.fuzzy_matcher(chara, True, 1)
    assert m(_sig("AB", "a")) == [("Zed", 0, 0), ("Amy", 1, 0), ("Bob", 1, 0)]


def test_matcher_uses_a_characters_closest_outfit():
    chara = {Path("Amy.png"): [_sig("AB", "a"), _sig("AC", "a")]}
    assert gc.fuzzy_matcher(chara, True, 1)(_sig("AC", "a")) == [("Amy", 0, 0)]


# --- the task ------------------------------------------------------------------------------------------

def _task(chara_dir, coord_dir, ignore=False, sub=True, use_cache=True, clothes=0):
    cfg = {"UseCache": use_cache, "IncludeSubfolders": sub, "IgnoreAccessories": ignore,
           "ClothesTolerance": clothes,
           "CharaDir": str(chara_dir), "CoordDir": str(coord_dir)}
    config = SimpleNamespace(group_coordinates=cfg, game_path={
        "charaFemale": chara_dir, "charaMale": chara_dir, "coordinate": coord_dir})
    return gc.GroupCoordinates(config, None)


def _run(tmp_path, chara_sigs, coord_sigs, ignore, sub=True, clothes=0):
    chara_dir, coord_dir = tmp_path / "chara", tmp_path / "coord"
    chara_dir.mkdir(exist_ok=True)
    coord_dir.mkdir(exist_ok=True)
    for n in coord_sigs:
        (coord_dir / n).parent.mkdir(parents=True, exist_ok=True)
        (coord_dir / n).write_bytes(b"png")
    with mock.patch.object(gc, "collect_chara_signatures",
                           return_value={chara_dir / n: s for n, s in chara_sigs.items()}), \
         mock.patch.object(gc, "build_coord_sig_cache",
                           return_value={str(coord_dir / n): s for n, s in coord_sigs.items()}), \
         mock.patch.object(gc, "collect_chara_digests", side_effect=AssertionError("exact path used")), \
         mock.patch.object(gc, "build_coord_cache", side_effect=AssertionError("exact path used")):
        _task(chara_dir, coord_dir, ignore, sub, clothes=clothes).run()
    return coord_dir


def test_ignore_accessories_moves_any_accessories_and_leaves_other_clothes(tmp_path):
    out = _run(tmp_path, {"Alice.png": [_sig("C", "a", "b", "c")]},
               {"exact.png": _sig("C", "a", "b", "c"), "swap.png": _sig("C", "a", "b", "z"),
                "far.png": _sig("C", "x", "y", "z"), "clothes.png": _sig("D", "a", "b", "c"),
                "notcoord.png": None}, ignore=True)
    for n in ("exact.png", "swap.png", "far.png"):
        assert (out / "Alice" / n).exists()
    for n in ("clothes.png", "notcoord.png"):
        assert (out / n).exists()


def test_clothes_tolerance_alone_still_compares_accessories(tmp_path):
    out = _run(tmp_path, {"Alice.png": [_sig("ABCD", "a", "b")]},
               {"one.png": _sig("ABCX", "a", "b"), "two.png": _sig("ABXY", "a", "b"),
                "both.png": _sig("ABCX", "a", "x"), "acc_only.png": _sig("ABCD", "a", "x")},
               ignore=False, clothes=1)
    assert (out / "Alice" / "one.png").exists()
    assert (out / "two.png").exists()                           # 2 clothes parts, limit 1
    assert (out / "both.png").exists() and (out / "acc_only.png").exists()   # accessories differ


def test_ignore_accessories_and_clothes_tolerance_together(tmp_path):
    out = _run(tmp_path, {"Alice.png": [_sig("ABCD", "a", "b")]},
               {"both.png": _sig("ABCX", "a", "x"), "too_far.png": _sig("ABXY", "a", "x")},
               ignore=True, clothes=1)
    assert (out / "Alice" / "both.png").exists() and (out / "too_far.png").exists()


def test_best_match_wins_over_filename_order(tmp_path):
    out = _run(tmp_path, {"Amy.png": [_sig("ABD", "a")], "Zed.png": [_sig("ABC", "a")]},
               {"c.png": _sig("ABC", "a")}, ignore=True, clothes=1)
    assert (out / "Zed" / "c.png").exists()


def test_coord_already_in_a_matching_folder_stays(tmp_path):
    out = _run(tmp_path, {"Amy.png": [_sig("ABC", "a")], "Zed.png": [_sig("ABD", "a")]},
               {"Zed/c.png": _sig("ABC", "a")}, ignore=True, clothes=1)
    assert (out / "Zed" / "c.png").exists()                    # Amy matches better, but it's already filed


def test_sig_cache_follows_moves(tmp_path):
    coord_dir = tmp_path / "coord"
    coord_dir.mkdir()
    old, new = str(coord_dir / "a.png"), str(coord_dir / "Alice" / "a.png")
    cache = coord_dir / gc.SIG_CACHE_FILE
    cache.write_text(json.dumps({"version": gc.SIG_CACHE_VERSION, "coord_dir": str(coord_dir),
                                 "files": {old: [1, 2]}, "sigs": {old: ["C", ["a"]]}}))
    _run(tmp_path, {"Alice.png": [_sig("C", "a")]}, {"a.png": _sig("C", "a")}, ignore=True)
    data = json.loads(cache.read_text())
    assert new in data["sigs"] and old not in data["sigs"] and new in data["files"]


@pytest.mark.parametrize("raw", ["-1", -2, "abc", "1.5"])
def test_bad_clothes_tolerance_is_an_input_error(tmp_path, raw):
    with pytest.raises(InputError):
        _task(tmp_path, tmp_path, clothes=raw)._clothes_tolerance()


def test_clothes_tolerance_parsing(tmp_path):
    assert _task(tmp_path, tmp_path, clothes="2")._clothes_tolerance() == 2
    assert _task(tmp_path, tmp_path, clothes=None)._clothes_tolerance() == 0


def test_default_options_keep_the_exact_path(tmp_path):
    chara_dir, coord_dir = tmp_path / "chara", tmp_path / "coord"
    chara_dir.mkdir()
    coord_dir.mkdir()
    (coord_dir / "a.png").write_bytes(b"png")
    with mock.patch.object(gc, "collect_chara_digests", return_value={chara_dir / "Alice.png": {"d"}}) as digests, \
         mock.patch.object(gc, "build_coord_cache", return_value={str(coord_dir / "a.png"): "d"}), \
         mock.patch.object(gc, "collect_chara_signatures", side_effect=AssertionError("fuzzy path used")):
        _task(chara_dir, coord_dir).run()
    assert digests.called and (coord_dir / "Alice" / "a.png").exists()


# --- end to end with real coordinate files -------------------------------------------------------------

def _write_coord(path: Path, outfit: dict) -> None:
    from kkloader.KoikatuCharaData import CoordinateEntry
    entry = CoordinateEntry()
    entry.image = cf.png_bytes()
    entry.coordinate_name = b"coord"
    entry.data = {**outfit, "enableMakeup": False, "makeup": {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    entry.save(str(path))


class _Block:
    def __init__(self, data):
        self.data = data


def _fake_chara(outfit_list, infos=()):
    return {"Coordinate": _Block(outfit_list), "KKEx": _Block({})}


def test_real_coordinate_files_end_to_end(tmp_path):
    coord_dir = tmp_path / "coord"
    base = _outfit(accessories=((5, 1), (6, 1), (7, 1)))
    _write_coord(coord_dir / "same.png", base)
    _write_coord(coord_dir / "one_swapped.png", _outfit(accessories=((5, 1), (6, 1), (9, 1))))
    _write_coord(coord_dir / "one_added.png", _outfit(accessories=((5, 1), (6, 1), (7, 1), (8, 1))))
    _write_coord(coord_dir / "reslotted.png", _outfit(accessories=((7, 1), (0, 120), (6, 1), (5, 1))))
    _write_coord(coord_dir / "two_off.png", _outfit(accessories=((5, 1), (9, 1), (10, 1))))
    _write_coord(coord_dir / "other_clothes.png", _outfit(top_id=2, accessories=((5, 1), (6, 1), (7, 1))))
    _write_coord(coord_dir / "other_clothes_and_acc.png", _outfit(top_id=2, accessories=((5, 1), (6, 1), (9, 1))))
    (coord_dir / "stray.png").write_bytes(cf.png_bytes())

    sigs = outfits.build_coord_sig_cache(coord_dir, use_cache=True)
    assert sigs[str(coord_dir / "stray.png")] is None
    assert sigs[str(coord_dir / "same.png")] == outfits.outfit_signature(base, [])

    chara_dir = tmp_path / "chara"
    chara_dir.mkdir()
    chara_sigs = {chara_dir / "Alice.png": outfits.chara_outfit_signatures(_fake_chara([base]))}
    with mock.patch.object(gc, "collect_chara_signatures", return_value=chara_sigs):
        _task(chara_dir, coord_dir, True).run()

    moved = sorted(p.name for p in (coord_dir / "Alice").iterdir())
    assert moved == ["one_added.png", "one_swapped.png", "reslotted.png", "same.png", "two_off.png"]
    for n in ("other_clothes.png", "other_clothes_and_acc.png", "stray.png"):
        assert (coord_dir / n).exists()

    # with a clothes tolerance of 1 the different-top variants join in
    with mock.patch.object(gc, "collect_chara_signatures", return_value=chara_sigs):
        _task(chara_dir, coord_dir, True, clothes=1).run()
    assert (coord_dir / "Alice" / "other_clothes.png").exists()
    assert (coord_dir / "Alice" / "other_clothes_and_acc.png").exists()

    # the signature cache followed the moves, so a second run re-parses nothing
    with mock.patch.object(outfits, "_coord_file_signature", side_effect=AssertionError("re-parsed")):
        again = outfits.build_coord_sig_cache(coord_dir, use_cache=True)
    assert str(coord_dir / "Alice" / "same.png") in again


# --- digests ignore serialisation noise (regression: a card whose coordinates never matched) ----------------

def _add_null_extended_data(obj):
    """What some cards carry: "ExtendedSaveData": null at every level of the outfit data."""
    if isinstance(obj, dict):
        obj["ExtendedSaveData"] = None
        for v in list(obj.values()):
            _add_null_extended_data(v)
    elif isinstance(obj, list):
        for v in obj:
            _add_null_extended_data(v)
    return obj


def _padded(outfit, slots=92):
    out = json.loads(json.dumps(outfit))
    out["accessory"]["parts"] += [{"type": 120, "id": 0, "parentKey": ""}] * (slots - len(out["accessory"]["parts"]))
    return out


def test_digest_ignores_null_extended_save_data_and_empty_slot_padding():
    base = _outfit(accessories=((5, 1), (0, 120), (6, 1)))
    digest = outfits.outfit_digest(base, [])
    assert outfits.outfit_digest(_add_null_extended_data(json.loads(json.dumps(base))), []) == digest
    assert outfits.outfit_digest(_padded(base), []) == digest
    assert outfits.outfit_digest(_add_null_extended_data(_padded(base)), []) == digest


def test_digest_still_sees_real_differences():
    base = _outfit(accessories=((5, 1), (6, 1)))
    with_extra = _padded(_outfit(accessories=((5, 1), (6, 1))))
    with_extra["accessory"]["parts"][50] = {"type": 121, "id": 9, "parentKey": ""}      # a real accessory far out
    kept = json.loads(json.dumps(base))
    kept["accessory"]["parts"].insert(0, {"type": 120, "id": 0, "parentKey": ""})       # empty slot *before* others
    real_ext = json.loads(json.dumps(base))
    real_ext["clothes"]["ExtendedSaveData"] = {"plugin": 1}                             # non-null data counts
    d = outfits.outfit_digest(base, [])
    assert len({d, outfits.outfit_digest(with_extra, []), outfits.outfit_digest(kept, []),
                outfits.outfit_digest(real_ext, [])}) == 4


def test_noise_does_not_change_signatures_either():
    base = _outfit(accessories=((5, 1), (6, 1)))
    assert outfits.outfit_signature(_add_null_extended_data(_padded(base)), []) == outfits.outfit_signature(base, [])


def test_colours_in_a_slot_with_nothing_worn_are_ignored():
    base = _outfit()
    base["clothes"]["parts"].append({"id": 0, "colorInfo": [{"baseColor": [1, 1, 1, 1]}], "hideOpt": [False]})
    leftover = json.loads(json.dumps(base))
    leftover["clothes"]["parts"][1]["colorInfo"] = [{"baseColor": [0.9, 0.5, 0.5, 1]}]
    worn = json.loads(json.dumps(base))
    worn["clothes"]["parts"][1]["id"] = 7                                    # same colour data, but now worn
    assert outfits.outfit_digest(base, []) == outfits.outfit_digest(leftover, [])
    assert outfits.outfit_signature(base, []) == outfits.outfit_signature(leftover, [])
    assert outfits.outfit_digest(base, []) != outfits.outfit_digest(worn, [])
    recoloured = json.loads(json.dumps(base))
    recoloured["clothes"]["parts"][0]["colorInfo"] = [{"baseColor": [0.1, 0.2, 0.3, 1]}]   # a worn item
    assert outfits.outfit_digest(base, []) != outfits.outfit_digest(recoloured, [])
