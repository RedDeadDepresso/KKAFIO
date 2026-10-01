"""
Characterization tests for the card-reading code: GUID parsers, the per-folder
GUID caches, mod scanning and outfit digests.

Written before chara_ops.py was split into modules, against the old layout; the
test bodies were not touched by the split — only the import block below was updated
to say where each piece now lives.
"""

import json
import os
import time
from pathlib import Path

import pytest

import card_factory as cf

# ---- where the code lives -----------------------------------------------------------
from kkafio.cards import cache_io, mods, outfits, parsing, png_guids
# -------------------------------------------------------------------------------------

G1, G2, G3 = "mod.one", "mod.two", "mod.three"


def _card(tmp_path, name, data):
    return cf.write(tmp_path / name, data)


# --- parsers ---------------------------------------------------------------------------

def test_chara_guids_sorted_and_unique(tmp_path):
    p = _card(tmp_path, "c.png", cf.chara_card([G2, G1, G2]))
    assert parsing.parse_chara_guids(p) == [G1, G2]


@pytest.mark.parametrize("marker", sorted({"【KoiKatuChara】", "【KoiKatuCharaSP】", "【KoiKatuCharaSun】", "【EroMakeChara】"}))
def test_chara_known_markers_accepted(tmp_path, marker):
    p = _card(tmp_path, "c.png", cf.chara_card([G1], marker=marker))
    assert parsing.parse_chara_guids(p) == [G1]


def test_chara_unknown_marker_gives_nothing(tmp_path):
    p = _card(tmp_path, "c.png", cf.chara_card([G1], marker="【NotACard】"))
    assert parsing.parse_chara_guids(p) == []


def test_chara_without_kkex_block_gives_nothing(tmp_path):
    p = _card(tmp_path, "c.png", cf.chara_card([G1], with_kkex=False))
    assert parsing.parse_chara_guids(p) == []


def test_chara_ignores_non_uar_plugin_data(tmp_path):
    data = cf.png_bytes() + b""                      # plain PNG, nothing appended
    p = _card(tmp_path, "plain.png", data)
    assert parsing.parse_chara_guids(p) == []


def test_not_a_png_gives_nothing(tmp_path):
    p = _card(tmp_path, "x.png", b"this is not a png at all")
    assert parsing.parse_chara_guids(p) == []
    assert parsing.parse_scene_guids(p) == []
    assert parsing.parse_coord_guids(p) == []


def test_scene_guids_from_several_values_and_both_ext_ids(tmp_path):
    data = cf.scene_card([[G1], [G2, G3]])
    data += b"\x02" + b"EC.Core.Sideloader.UniversalAutoResolver" + cf.msgpack.packb(
        {1: {"info": [cf.msgpack.packb({"ModID": "ec.mod"})]}})
    p = _card(tmp_path, "s.png", data)
    assert parsing.parse_scene_guids(p) == sorted(["ec.mod", G1, G2, G3])


def test_scene_garbage_after_needle_is_skipped(tmp_path):
    data = cf.png_bytes() + cf.UAR.encode() + b"\xc1\xc1\xc1"    # 0xc1 is never valid msgpack
    p = _card(tmp_path, "s.png", data)
    assert parsing.parse_scene_guids(p) == []


@pytest.mark.parametrize("marker", ["【KoiKatuClothes】", "【AIS_Clothes】"])
def test_coord_guids(tmp_path, marker):
    p = _card(tmp_path, "k.png", cf.coord_card([G2, G1], marker=marker))
    assert parsing.parse_coord_guids(p) == [G1, G2]


def test_coord_rejects_wrong_product_number_and_marker(tmp_path):
    assert parsing.parse_coord_guids(_card(tmp_path, "a.png", cf.coord_card([G1], product_no=101))) == []
    assert parsing.parse_coord_guids(_card(tmp_path, "b.png", cf.coord_card([G1], marker="【Nope】"))) == []


def test_uar_resolve_infos_accepts_dict_and_list_forms():
    info = cf.msgpack.packb({"ModID": G1, "Slot": 5})
    as_dict = {cf.UAR: {1: {"info": [info]}}}
    as_list = {cf.UAR: [None, {"info": [info]}]}
    bytes_keys = {cf.UAR.encode(): {1: {"info": [info]}}}
    for kk in (as_dict, as_list, bytes_keys):
        assert parsing.uar_resolve_infos(kk) == [{"ModID": G1, "Slot": 5}]
    assert parsing.uar_resolve_infos(None) == []
    assert parsing.uar_resolve_infos({"some.other.plugin": {1: {"info": [info]}}}) == []


def test_find_iend_end(tmp_path):
    png = cf.png_bytes()
    assert parsing._find_iend_end(png) == len(png)
    assert parsing._find_iend_end(png + b"tail") == len(png)
    assert parsing._find_iend_end(b"\x89PNG\r\n\x1a\n") == -1


# --- per-folder GUID collection + caches --------------------------------------------------

def _populate_charas(root: Path):
    a = cf.write(root / "a.png", cf.chara_card([G1]))
    b = cf.write(root / "sub" / "b.png", cf.chara_card([G2]))
    cf.write(root / "not_a_card.png", cf.png_bytes())
    return a, b


def test_collect_chara_guids_and_by_file(tmp_path):
    a, b = _populate_charas(tmp_path)
    assert png_guids.collect_chara_guids([tmp_path], use_cache=False) == {G1, G2}
    by_file = png_guids.collect_chara_guids_by_file([tmp_path], use_cache=False)
    assert by_file == {str(a): [G1], str(b): [G2]}


def test_collect_scene_and_coord(tmp_path):
    cf.write(tmp_path / "scenes" / "s.png", cf.scene_card([[G1]]))
    cf.write(tmp_path / "coords" / "k.png", cf.coord_card([G3]))
    assert png_guids.collect_scene_guids([tmp_path / "scenes"], use_cache=False) == {G1}
    assert png_guids.collect_coord_guids([tmp_path / "coords"], use_cache=False) == {G3}
    # a chara collector must not claim scenes or coordinates, and vice versa
    assert png_guids.collect_chara_guids([tmp_path / "scenes", tmp_path / "coords"], use_cache=False) == set()


def test_collect_handles_empty_and_missing_dirs(tmp_path):
    assert png_guids.collect_chara_guids([], use_cache=True) == set()
    assert png_guids.collect_chara_guids([tmp_path / "nope"], use_cache=True) == set()


def _counting_collect(tmp_path, use_cache):
    calls = []

    def parse(path):
        calls.append(path.name)
        return parsing.parse_chara_guids(path)

    from kkafio.cards.classifier import CardType, get_card_type
    guids, by_file = png_guids.collect_png_guids(
        [tmp_path], use_cache, png_guids.CHARA_GUID_CACHE_FILE, "Chara",
        lambda raw: get_card_type(raw) in (CardType.KK, CardType.KKSP, CardType.KKS), parse)
    return guids, by_file, sorted(calls)


def test_cache_reuses_unchanged_files_and_reparses_changed_ones(tmp_path):
    a, b = _populate_charas(tmp_path)
    cache = tmp_path / png_guids.CHARA_GUID_CACHE_FILE

    _, _, calls = _counting_collect(tmp_path, use_cache=True)
    assert calls == ["a.png", "b.png"] and cache.exists()

    guids, _, calls = _counting_collect(tmp_path, use_cache=True)
    assert calls == [] and guids == {G1, G2}                    # fully served from cache

    cf.write(a, cf.chara_card([G3]))                            # change a.png
    os.utime(a, (time.time() + 10, time.time() + 10))
    guids, by_file, calls = _counting_collect(tmp_path, use_cache=True)
    assert calls == ["a.png"] and guids == {G3, G2}
    assert by_file[str(a)] == [G3]

    b.unlink()                                                  # deleted files drop out
    guids, _, _ = _counting_collect(tmp_path, use_cache=True)
    assert guids == {G3}


def test_no_cache_always_reparses_and_writes_nothing(tmp_path):
    _populate_charas(tmp_path)
    for _ in range(2):
        _, _, calls = _counting_collect(tmp_path, use_cache=False)
        assert calls == ["a.png", "b.png"]
    assert not (tmp_path / png_guids.CHARA_GUID_CACHE_FILE).exists()


def test_cache_for_another_folder_is_ignored(tmp_path):
    _populate_charas(tmp_path)
    _counting_collect(tmp_path, use_cache=True)
    cache = tmp_path / png_guids.CHARA_GUID_CACHE_FILE
    data = json.loads(cache.read_text(encoding="utf-8"))
    data["dir"] = "/somewhere/else"
    data.pop("dirs", None)
    cache.write_text(json.dumps(data), encoding="utf-8")
    _, _, calls = _counting_collect(tmp_path, use_cache=True)
    assert calls == ["a.png", "b.png"]


def test_corrupt_cache_is_ignored(tmp_path):
    _populate_charas(tmp_path)
    (tmp_path / png_guids.CHARA_GUID_CACHE_FILE).write_text("{not json", encoding="utf-8")
    guids, _, calls = _counting_collect(tmp_path, use_cache=True)
    assert guids == {G1, G2} and calls == ["a.png", "b.png"]


def testatomic_write_json_is_compact_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "out.json"
    cache_io.atomic_write_json(target, {"a": [1, 2], "b": "日本"})
    assert target.read_text(encoding="utf-8") == '{"a":[1,2],"b":"日本"}'       # compact, non-ASCII kept
    cache_io.atomic_write_json(target, {"a": 2})                               # overwrite in place
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_file_fingerprint_is_int_mtime_and_size(tmp_path):
    f = cf.write(tmp_path / "f.bin", b"12345")
    os.utime(f, (1_700_000_000.75, 1_700_000_000.75))
    assert cache_io.file_fp(f) == [1_700_000_000, 5]
    assert cache_io.file_fp(tmp_path / "missing") == [0, 0]


# --- zipmods -----------------------------------------------------------------------------------

def test_guid_from_zipmod(tmp_path):
    assert mods.guid_from_zipmod(cf.zipmod(tmp_path / "a.zipmod", "g.a")) == "g.a"
    assert mods.guid_from_zipmod(cf.zipmod(tmp_path / "b.zipmod", "g.b", manifest_name="sub/MANIFEST.XML")) == "g.b"
    assert mods.guid_from_zipmod(cf.zipmod(tmp_path / "c.zipmod", None)) is None
    (tmp_path / "d.zipmod").write_bytes(b"not a zip")
    assert mods.guid_from_zipmod(tmp_path / "d.zipmod") is None
    assert mods.guid_from_zipmod(tmp_path / "missing.zipmod") is None


def test_iter_mod_files_includes_plain_zip_with_manifest_only(tmp_path):
    cf.zipmod(tmp_path / "a.zipmod", "g.a")
    cf.zipmod(tmp_path / "deep" / "b.zipmod", "g.b")
    cf.zipmod(tmp_path / "plain_mod.zip", "g.c")
    cf.zipmod(tmp_path / "just_files.zip", None)
    found = sorted(p.name for p in mods.iter_mod_files(tmp_path))
    assert found == ["a.zipmod", "b.zipmod", "plain_mod.zip"]


def test_in_modpack_folder(tmp_path):
    md = tmp_path / "mods"
    assert mods.in_modpack_folder(md / "Sideloader Modpack - Foo" / "a.zipmod", md)
    assert mods.in_modpack_folder(md / "Sideloader Modpack" / "x" / "a.zipmod", md)
    assert not mods.in_modpack_folder(md / "a.zipmod", md)                       # directly in mods dir
    assert not mods.in_modpack_folder(md / "Other" / "Sideloader Modpack" / "a.zipmod", md)   # not first level
    assert not mods.in_modpack_folder(tmp_path / "elsewhere" / "a.zipmod", md)


def _mods_tree(root: Path):
    cf.zipmod(root / "own" / "a.zipmod", "local.a")
    cf.zipmod(root / "b.zipmod", "local.b")
    cf.zipmod(root / "Sideloader Modpack - X" / "c.zipmod", "pack.c")
    return root


def test_build_mods_cache_modpack_scopes(tmp_path):
    root = _mods_tree(tmp_path / "mods")
    without = mods.build_mods_cache(root, include_modpack=False, use_cache=False)
    with_pack = mods.build_mods_cache(root, include_modpack=True, use_cache=False)
    assert set(without) == {"local.a", "local.b"}
    assert set(with_pack) == {"local.a", "local.b", "pack.c"}
    assert Path(with_pack["local.a"]) == root / "own" / "a.zipmod"


def test_build_mods_cache_uses_and_writes_cache_file(tmp_path):
    root = _mods_tree(tmp_path / "mods")
    mods.build_mods_cache(root, include_modpack=True, use_cache=True)
    assert (root / mods.MODS_CACHE_FILE).exists()
    again = mods.build_mods_cache(root, include_modpack=True, use_cache=True)
    assert set(again) == {"local.a", "local.b", "pack.c"}
    mods.build_mods_cache(root, include_modpack=True, use_cache=False)        # must not crash on existing cache


@pytest.mark.parametrize("use_cache", [False, True])
def test_scan_mods_local_fallback(tmp_path, use_cache):
    root = _mods_tree(tmp_path / "mods")
    found = mods.scan_mods(root, {"local.a", "pack.c", "absent.guid"}, include_modpack=False, use_cache=use_cache)
    assert set(found) == {"local.a"}
    found = mods.scan_mods(root, {"local.a", "pack.c", "absent.guid"}, include_modpack=True, use_cache=use_cache)
    assert set(found) == {"local.a", "pack.c"}
    assert mods.scan_mods(tmp_path / "missing", {"x"}) == {}


def test_load_modpack_index_reads_bundled_index():
    index = mods.load_modpack_index("Koikatsu")
    assert isinstance(index, dict) and len(index) > 1000
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in list(index.items())[:50])


def test_resolve_paths(tmp_path):
    game = tmp_path / "game"
    (game / "mods").mkdir(parents=True)
    (game / "UserData" / "coordinate").mkdir(parents=True)
    inside = game / "UserData" / "chara" / "female" / "c.png"
    outside = tmp_path / "loose" / "c.png"
    outside.parent.mkdir()
    assert mods.resolve_paths(inside, game, True, None, None) == (game / "mods", game / "UserData" / "coordinate")
    assert mods.resolve_paths(outside, game, True, None, None) == (outside.parent, outside.parent)
    assert mods.resolve_paths(outside, game, False, Path("M"), Path("C")) == (Path("M"), Path("C"))
    bare = tmp_path / "bare"
    bare.mkdir()
    assert mods.resolve_paths(bare / "x.png", bare, True, None, None) == (None, None)


# --- outfit digests (their values are persisted in users' caches: they must not drift) -------------

def _outfit(top_id=1, version="0.0.1"):
    return {
        "clothes": {"version": version, "parts": [{"id": top_id, "colorInfo": [
            {"pattern": 2, "offset": [0.5, 0.5], "rotate": 0.5, "baseColor": [1, 0, 0, 1]}]}],
            "subPartsId": [0, 0, 0]},
        "accessory": {"version": "9.9.9", "parts": [{"id": 5}]},
    }


_INFOS = [{"Property": "ChaFileClothes.ClothesTop", "Slot": 1, "LocalSlot": 1001, "ModID": "mod.top"},
          {"Property": "accessory0.ChaFileAccessory.PartsInfo.id", "Slot": 5, "LocalSlot": 1005, "ModID": "mod.acc"}]


def _digest_cases():
    return {
        "plain": outfits.outfit_digest(_outfit(), []),
        "resolved_by_slot": outfits.outfit_digest(_outfit(top_id=1), _INFOS),
        "resolved_by_local_slot": outfits.outfit_digest(_outfit(top_id=1001), _INFOS),
        "different_item": outfits.outfit_digest(_outfit(top_id=2), _INFOS),
        "chara_slot_index": outfits.outfit_digest(_outfit(), _INFOS, 0),
    }


def test_outfit_digest_ignores_versions_and_equates_slot_spellings():
    d = _digest_cases()
    assert outfits.outfit_digest(_outfit(version="0.0.9"), []) == d["plain"]
    assert d["resolved_by_slot"] == d["resolved_by_local_slot"]
    assert d["different_item"] != d["resolved_by_slot"]
    assert all(len(v) == 32 for v in d.values())


def test_outfit_digest_values_are_stable():
    golden = json.loads((Path(__file__).parent / "golden" / "pure_functions.json").read_text(encoding="utf-8"))
    assert _digest_cases() == golden["outfit_digests"]
