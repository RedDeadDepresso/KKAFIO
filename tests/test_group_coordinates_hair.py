"""Group Coordinates, Group By Hair: ungrouped coordinates are grouped by their hair accessory."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import card_factory as cf
from kkafio.cards import outfits
from kkafio.tasks import group_coordinates as gc


def _outfit(hair=None, top=1, hair_parent="a_n_headtop", hair_type=122, extra=()):
    """An outfit wearing a hair accessory (item id `hair`, if any) plus `extra` accessory parts."""
    parts = []
    if hair is not None:
        parts.append({"type": hair_type, "id": hair, "parentKey": hair_parent,
                      "addMove": [[[0, 0, 0], [0, 0, 0], [1, 1, 1]]] * 2, "color": [[1, 1, 1, 1]]})
    parts.extend(extra)
    return {"clothes": {"version": "0.0.1", "parts": [{"id": top, "colorInfo": []}], "subPartsId": [0, 0, 0]},
            "accessory": {"version": "0.0.0", "parts": parts}}


def _write_coord(path: Path, outfit: dict) -> None:
    from kkloader.KoikatuCharaData import CoordinateEntry
    entry = CoordinateEntry()
    entry.image = cf.png_bytes()
    entry.coordinate_name = b"coord"
    entry.data = {**outfit, "enableMakeup": False, "makeup": {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    entry.save(str(path))


def _task(chara_dir, coord_dir, hair=True, use_cache=True, sub=False):
    cfg = {"UseCache": use_cache, "IncludeSubfolders": sub, "GroupByHair": hair,
           "CharaDir": str(chara_dir), "CoordDir": str(coord_dir)}
    config = SimpleNamespace(group_coordinates=cfg, game_path={
        "charaFemale": chara_dir, "charaMale": chara_dir, "coordinate": coord_dir})
    return gc.GroupCoordinates(config, None)


def _layout(coord_dir: Path) -> dict[str, str]:
    """{file name: top-level folder or ''} of every card under coord_dir."""
    return {p.name: (p.relative_to(coord_dir).parts[0] if len(p.relative_to(coord_dir).parts) > 1 else "")
            for p in coord_dir.rglob("*.png")}


# --- hair of an outfit ---------------------------------------------------------------------------------

def test_hair_is_the_hair_category_accessory_on_the_head_top():
    extra = ({"type": 122, "id": 9, "parentKey": "a_n_waist_f"},        # a tail in the hair category
             {"type": 121, "id": 7, "parentKey": "a_n_headtop"},        # a hat
             {"type": 120, "id": 0, "parentKey": "a_n_none"})           # empty slot
    assert outfits.outfit_hair(_outfit(hair=5, extra=extra), []) == ("5",)
    assert outfits.outfit_hair(_outfit(), []) == ()
    assert outfits.outfit_hair(_outfit(hair=5, hair_parent="a_n_waist"), []) == ()


def test_hair_keeps_slot_order_main_hair_first():
    ornament = {"type": 122, "id": 34, "parentKey": "a_n_headtop"}
    extra = ({"type": 124, "id": 3, "parentKey": "a_n_bust"}, ornament, {"type": 122, "id": 34, "parentKey": "a_n_headtop"})
    assert outfits.outfit_hair(_outfit(hair=5, extra=extra), []) == ("5", "34")           # duplicates collapse
    reordered = _outfit(hair=5, extra=extra)
    reordered["accessory"]["parts"].reverse()
    assert outfits.outfit_hair(reordered, []) == ("34", "5")


def test_hair_ignores_colour_position_and_slot():
    a = _outfit(hair=5)
    b = _outfit(hair=5)
    b["accessory"]["parts"][0]["color"] = [[0.1, 0.2, 0.3, 1]]
    b["accessory"]["parts"][0]["addMove"] = [[[9, 9, 9], [0, 0, 0], [2, 2, 2]]] * 2
    c = _outfit(hair=5, extra=({"type": 124, "id": 3, "parentKey": "a_n_bust"},))
    c["accessory"]["parts"].reverse()                                # the hair in another slot
    assert outfits.outfit_hair(a, []) == outfits.outfit_hair(b, []) == outfits.outfit_hair(c, []) == ("5",)
    assert outfits.outfit_hair(_outfit(hair=6), []) != outfits.outfit_hair(a, [])


def test_hair_items_are_resolved_like_the_digest_resolves_them():
    infos = [{"Property": "accessory0.ChaFileAccessory.PartsInfo.id", "Slot": 5, "LocalSlot": 1005, "ModID": "mod.hair"}]
    assert outfits.outfit_hair(_outfit(hair=5), infos) == outfits.outfit_hair(_outfit(hair=1005), infos) == ("mod.hair:5",)


# --- same hair --------------------------------------------------------------------------------------

def test_same_hair_ignores_extra_ornaments_but_not_different_hairstyles():
    same = gc.same_hair
    assert same(("H",), ("H",))
    assert same(("H", "34"), ("H",)) and same(("H",), ("H", "34"))             # an extra ornament
    assert same(("H", "34"), ("H", "39"))                                      # different ornaments
    assert same(("34", "H"), ("H", "34"))                                      # ornament listed first in one
    assert not same(("H", "34"), ("G", "34"))                                  # different hairstyles, shared ornament
    assert not same(("34",), ("H", "34"))                                      # ornament only vs. hairstyle + it
    assert not same(("34", "H"), ("H",))                                       # main hair is the ornament
    assert not same((), ("H",)) and not same((), ())


# --- planning ------------------------------------------------------------------------------------------

def test_next_unknown_number(tmp_path):
    assert gc.next_unknown_number(tmp_path) == 1
    for name in ("UNKNOWN_3", "unknown_7", "UNKNOWN_x", "Alice"):
        (tmp_path / name).mkdir()
    (tmp_path / "UNKNOWN_9").write_bytes(b"a file, not a folder")
    assert gc.next_unknown_number(tmp_path) == 8


def _plan(tmp_path, layout, first=1):
    """layout: {relative path: hair tuple or None}"""
    return gc.plan_hair_groups({tmp_path / rel: hair for rel, hair in layout.items()}, tmp_path, first)


def test_plan_moves_into_the_one_folder_with_that_hair(tmp_path):
    plan = _plan(tmp_path, {"Alice/a.png": ("H1",), "Alice/Summer/b.png": ("H1",), "c.png": ("H1",),
                            "Bob/d.png": ("H2",), "e.png": ("H2",)})
    assert plan["moves"] == {tmp_path / "c.png": "Alice", tmp_path / "e.png": "Bob"}
    assert not plan["ambiguous"]


def test_plan_leaves_hair_used_in_several_folders_alone(tmp_path):
    plan = _plan(tmp_path, {"Alice/a.png": ("H",), "Bob/b.png": ("H",), "c.png": ("H",), "d.png": ("H",)})
    assert plan["moves"] == {}
    assert plan["ambiguous"] == {tmp_path / "c.png": ["Alice", "Bob"], tmp_path / "d.png": ["Alice", "Bob"]}


def test_plan_groups_ungrouped_coordinates_that_share_hair(tmp_path):
    plan = _plan(tmp_path, {"a.png": ("X",), "b.png": ("Y",), "c.png": ("X",), "d.png": ("Y",), "e.png": ("Z",),
                            "f.png": (), "g.png": None, "Alice/h.png": ("Q",)}, first=4)
    assert plan["moves"] == {tmp_path / "a.png": "UNKNOWN_4", tmp_path / "c.png": "UNKNOWN_4",
                             tmp_path / "b.png": "UNKNOWN_5", tmp_path / "d.png": "UNKNOWN_5"}
    assert plan["lone"] == {tmp_path / "e.png": ("Z",)}               # unique hair
    assert plan["no_hair"] == [tmp_path / "f.png"]
    assert plan["grouped"] == 1


def test_a_real_folder_wins_over_an_unknown_folder_with_the_same_hair(tmp_path):
    plan = _plan(tmp_path, {"Alice/a.png": ("H",), "UNKNOWN_1/u.png": ("H",), "c.png": ("H",)})
    assert plan["moves"] == {tmp_path / "c.png": "Alice"} and not plan["ambiguous"]
    only_unknown = _plan(tmp_path, {"UNKNOWN_2/u.png": ("H",), "c.png": ("H",)})
    assert only_unknown["moves"] == {tmp_path / "c.png": "UNKNOWN_2"}
    several = _plan(tmp_path, {"Alice/a.png": ("H",), "Bob/b.png": ("H",), "UNKNOWN_1/u.png": ("H",), "c.png": ("H",)})
    assert several["ambiguous"] == {tmp_path / "c.png": ["Alice", "Bob"]}       # UNKNOWN_1 isn't listed or counted


def test_plan_groups_coordinates_whose_hair_differs_only_by_ornaments(tmp_path):
    """The Altina case: three coordinates with just the hairstyle, two that add a vanilla ornament."""
    plan = _plan(tmp_path, {"kimono.png": ("A",), "swim.png": ("A",), "lailai.png": ("A",),
                            "maid.png": ("A", "34"), "bunny.png": ("A", "34")})
    assert set(plan["moves"].values()) == {"UNKNOWN_1"} and len(plan["moves"]) == 5
    assert not plan["lone"]


def test_plan_ornament_variants_follow_the_folder_with_the_hairstyle(tmp_path):
    plan = _plan(tmp_path, {"Altina/kimono.png": ("A",), "Bob/b.png": ("B", "34"), "Cid/c.png": ("34",),
                            "maid.png": ("A", "34")})
    assert plan["moves"] == {tmp_path / "maid.png": "Altina"} and not plan["ambiguous"]


def test_plan_does_not_chain_unrelated_hair_through_a_shared_ornament(tmp_path):
    plan = _plan(tmp_path, {"a1.png": ("A", "34"), "a2.png": ("A",), "b1.png": ("B", "34"), "b2.png": ("B",),
                            "ribbon.png": ("34",)})
    groups = {}
    for coord, folder in plan["moves"].items():
        groups.setdefault(folder, set()).add(coord.name)
    assert sorted(map(sorted, groups.values())) == [["a1.png", "a2.png"], ["b1.png", "b2.png"]]
    assert list(plan["lone"]) == [tmp_path / "ribbon.png"]


def test_plan_ignores_folders_coordinates_without_hair(tmp_path):
    plan = _plan(tmp_path, {"Alice/a.png": (), "b.png": ("H",), "c.png": ("H",)})
    assert set(plan["moves"].values()) == {"UNKNOWN_1"}


# --- the task, with real coordinate files -----------------------------------------------------------------

def _run(tmp_path, files, hair=True, chara_digests=None, **kw):
    coord_dir, chara_dir = tmp_path / "coord", tmp_path / "chara"
    chara_dir.mkdir(exist_ok=True)
    coord_dir.mkdir(exist_ok=True)
    for rel, outfit in files.items():
        _write_coord(coord_dir / rel, outfit)
    with mock.patch.object(gc, "collect_chara_digests", return_value=chara_digests or {}):
        _task(chara_dir, coord_dir, hair=hair, **kw).run()
    return coord_dir


FILES = {
    "Alice/a1.png": _outfit(hair=11, top=1), "Alice/Summer/a2.png": _outfit(hair=11, top=2),
    "Bob/b1.png": _outfit(hair=22, top=3),
    "joins_alice.png": _outfit(hair=11, top=4),                     # Alice's hair
    "joins_bob.png": _outfit(hair=22, top=5),                       # Bob's hair
    "new1.png": _outfit(hair=33, top=6), "new2.png": _outfit(hair=33, top=7),      # share hair, no folder
    "lone.png": _outfit(hair=44, top=8),                            # unique hair
    "bald.png": _outfit(top=9),                                     # no hair accessory
    "stray.png": None,
}


def test_group_by_hair_end_to_end(tmp_path):
    files = {k: v for k, v in FILES.items() if v is not None}
    coord_dir = _run(tmp_path, files)
    (coord_dir / "stray.png").write_bytes(cf.png_bytes())
    # run again: nothing changes (and the stray PNG is still not touched)
    _task(tmp_path / "chara", coord_dir).run()
    assert _layout(coord_dir) == {
        "a1.png": "Alice", "a2.png": "Alice", "b1.png": "Bob",
        "joins_alice.png": "Alice", "joins_bob.png": "Bob",
        "new1.png": "UNKNOWN_1", "new2.png": "UNKNOWN_1",
        "lone.png": "", "bald.png": "", "stray.png": ""}


def test_hair_grouping_is_off_by_default(tmp_path):
    files = {k: v for k, v in FILES.items() if v is not None}
    coord_dir = _run(tmp_path, files, hair=False)
    assert _layout(coord_dir)["joins_alice.png"] == "" and _layout(coord_dir)["new1.png"] == ""


def test_later_runs_add_to_existing_unknown_folders_and_number_new_ones_after_them(tmp_path):
    coord_dir = _run(tmp_path, {"n1.png": _outfit(hair=33, top=1), "n2.png": _outfit(hair=33, top=2)})
    for name, outfit in {"n3.png": _outfit(hair=33, top=3),                       # joins UNKNOWN_1
                         "m1.png": _outfit(hair=55, top=4), "m2.png": _outfit(hair=55, top=5)}.items():
        _write_coord(coord_dir / name, outfit)
    _task(tmp_path / "chara", coord_dir).run()
    assert _layout(coord_dir) == {"n1.png": "UNKNOWN_1", "n2.png": "UNKNOWN_1", "n3.png": "UNKNOWN_1",
                                  "m1.png": "UNKNOWN_2", "m2.png": "UNKNOWN_2"}


def test_coordinates_with_extra_hair_ornaments_group_with_the_plain_ones(tmp_path):
    ornament = ({"type": 122, "id": 34, "parentKey": "a_n_headtop"},)
    coord_dir = _run(tmp_path, {"plain1.png": _outfit(hair=11, top=1), "plain2.png": _outfit(hair=11, top=2),
                                "maid.png": _outfit(hair=11, top=3, extra=ornament),
                                "bunny.png": _outfit(hair=11, top=4, extra=ornament),
                                "other.png": _outfit(hair=22, top=5, extra=ornament)})
    assert _layout(coord_dir) == {"plain1.png": "UNKNOWN_1", "plain2.png": "UNKNOWN_1", "maid.png": "UNKNOWN_1",
                                  "bunny.png": "UNKNOWN_1", "other.png": ""}


def test_hair_pass_runs_after_outfit_matching(tmp_path):
    """A coordinate matching a character's outfit goes to that character's folder first; then a
    hair-mate of it (a different outfit) follows into the same folder."""
    matched = _outfit(hair=77, top=1)
    chara = {tmp_path / "chara" / "Dana.png": {outfits.outfit_digest(matched, [])}}
    coord_dir = _run(tmp_path, {"matched.png": matched, "hairmate.png": _outfit(hair=77, top=2),
                                "other.png": _outfit(hair=88, top=3)}, chara_digests=chara)
    assert _layout(coord_dir) == {"matched.png": "Dana", "hairmate.png": "Dana", "other.png": ""}


def test_name_collisions_get_a_number(tmp_path):
    coord_dir = _run(tmp_path, {"Alice/same.png": _outfit(hair=1, top=1)})
    _write_coord(coord_dir / "same.png", _outfit(hair=1, top=2))
    _task(tmp_path / "chara", coord_dir).run()
    assert sorted(p.name for p in (coord_dir / "Alice").iterdir()) == ["same.png", "same_1.png"]


def test_hair_cache_follows_moves_and_skips_reparsing(tmp_path):
    coord_dir = _run(tmp_path, {"Alice/a.png": _outfit(hair=1, top=1), "b.png": _outfit(hair=1, top=2)})
    cache = json.loads((coord_dir / outfits.HAIR_CACHE_FILE).read_text())
    assert str(coord_dir / "Alice" / "b.png") in cache["hairs"] and str(coord_dir / "b.png") not in cache["hairs"]
    with mock.patch.object(outfits, "_coord_file_hair", side_effect=AssertionError("re-parsed")):
        _task(tmp_path / "chara", coord_dir).run()


def test_group_by_hair_without_the_cache(tmp_path):
    coord_dir = _run(tmp_path, {"Alice/a.png": _outfit(hair=1, top=1), "b.png": _outfit(hair=1, top=2)},
                     use_cache=False)
    assert _layout(coord_dir) == {"a.png": "Alice", "b.png": "Alice"}
    assert not (coord_dir / outfits.HAIR_CACHE_FILE).exists()


# --- matching characters by hair first ---------------------------------------------------------------------

def test_hair_matcher_finds_the_characters_wearing_that_hair():
    charas = {Path("Bob.png"): [("B",), ("B2", "34")], Path("Alice.png"): [("A",)], Path("Cid.png"): [("A", "34")]}
    matches = gc.hair_matcher(charas)
    assert matches(("A",)) == ["Alice", "Cid"]                 # same main hair, filename order
    assert matches(("B", "34")) == ["Bob"]                       # an extra ornament doesn't matter
    assert matches(("34",)) == []                                # an ornament alone isn't a hairstyle of anyone's
    assert matches(("Z",)) == [] and matches(()) == []


def _chara_hairs(tmp_path, **hairs):
    return {tmp_path / "chara" / f"{name}.png": value for name, value in hairs.items()}


def _run_hair_first(tmp_path, files, chara_hairs, chara_digests=None, **kw):
    coord_dir, chara_dir = tmp_path / "coord", tmp_path / "chara"
    chara_dir.mkdir(exist_ok=True)
    coord_dir.mkdir(exist_ok=True)
    for rel, outfit in files.items():
        _write_coord(coord_dir / rel, outfit)
    with mock.patch.object(gc, "collect_chara_hairs", return_value=chara_hairs), \
            mock.patch.object(gc, "collect_chara_digests", return_value=chara_digests or {}):
        _task(chara_dir, coord_dir, hair=False, **kw).run()
    return coord_dir


def test_coordinates_move_to_the_character_wearing_their_hair(tmp_path):
    coord_dir = _run_hair_first(
        tmp_path, {"a.png": _outfit(hair=11, top=1), "b.png": _outfit(hair=22, top=2),
                   "nohair.png": _outfit(top=3), "unknown.png": _outfit(hair=99, top=4)},
        _chara_hairs(tmp_path, Alice=[("11",)], Bob=[("22",), ("33",)]))
    assert _layout(coord_dir) == {"a.png": "Alice", "b.png": "Bob", "nohair.png": "", "unknown.png": ""}


def test_hair_shared_by_several_characters_is_left_for_outfit_matching(tmp_path):
    clothes_match = _outfit(hair=11, top=1)
    chara = {tmp_path / "chara" / "Bob.png": {outfits.outfit_digest(clothes_match, [])}}
    coord_dir = _run_hair_first(
        tmp_path, {"decided_by_clothes.png": clothes_match, "undecided.png": _outfit(hair=11, top=2)},
        _chara_hairs(tmp_path, Alice=[("11",)], Bob=[("11",)]), chara_digests=chara)
    assert _layout(coord_dir) == {"decided_by_clothes.png": "Bob", "undecided.png": ""}


def test_hair_pass_wins_over_an_outfit_match_for_another_character(tmp_path):
    coord = _outfit(hair=11, top=1)                                  # Bob's outfit, but Alice's hair
    chara = {tmp_path / "chara" / "Bob.png": {outfits.outfit_digest(coord, [])}}
    coord_dir = _run_hair_first(tmp_path, {"c.png": coord}, _chara_hairs(tmp_path, Alice=[("11",)], Bob=[("22",)]),
                                chara_digests=chara)
    assert _layout(coord_dir) == {"c.png": "Alice"}


def test_outfit_pass_still_groups_what_the_hair_pass_left(tmp_path):
    clothes_match = _outfit(hair=77, top=1)
    chara = {tmp_path / "chara" / "Dana.png": {outfits.outfit_digest(clothes_match, [])}}
    coord_dir = _run_hair_first(
        tmp_path, {"hair.png": _outfit(hair=11, top=2), "clothes.png": clothes_match},
        _chara_hairs(tmp_path, Alice=[("11",)], Dana=[("55",)]), chara_digests=chara)
    assert _layout(coord_dir) == {"hair.png": "Alice", "clothes.png": "Dana"}


def test_coordinate_already_in_its_hair_characters_folder_is_not_moved(tmp_path):
    coord_dir = _run_hair_first(tmp_path, {"Alice/a.png": _outfit(hair=11, top=1)},
                                _chara_hairs(tmp_path, Alice=[("11",)]))
    assert _layout(coord_dir) == {"a.png": "Alice"}
