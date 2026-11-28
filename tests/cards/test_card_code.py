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
from urllib.parse import quote

import pytest

import card_factory as cf

# ---- where the code lives -----------------------------------------------------------
from kkafio.cards import cache_io
from kkafio.cards.mods import zipmods
from kkafio.indices import modpack
from kkafio.indices.modpack import load_modpack_index, url_to_rel_path
from kkafio.cards.chara import outfits
from kkafio.cards.mods import parsing
from kkafio.cards.mods import png_guids
# -------------------------------------------------------------------------------------

G1, G2, G3 = "mod.one", "mod.two", "mod.three"


def modpack_url(rel: str) -> str:
    """The download URL of a modpack file, as the shipped index stores it."""
    return "https://sideload.betterrepack.com/download/KKEC/" + quote(rel)


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

    def parse(path, payload=None):
        calls.append(path.name)
        return parsing.parse_chara_guids(path, payload)

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
    assert zipmods.guid_from_zipmod(cf.zipmod(tmp_path / "a.zipmod", "g.a")) == "g.a"
    assert zipmods.guid_from_zipmod(cf.zipmod(tmp_path / "b.zipmod", "g.b", manifest_name="sub/MANIFEST.XML")) == "g.b"
    assert zipmods.guid_from_zipmod(cf.zipmod(tmp_path / "c.zipmod", None)) is None
    (tmp_path / "d.zipmod").write_bytes(b"not a zip")
    assert zipmods.guid_from_zipmod(tmp_path / "d.zipmod") is None
    assert zipmods.guid_from_zipmod(tmp_path / "missing.zipmod") is None


def test_iter_mod_files_includes_plain_zip_with_manifest_only(tmp_path):
    cf.zipmod(tmp_path / "a.zipmod", "g.a")
    cf.zipmod(tmp_path / "deep" / "b.zipmod", "g.b")
    cf.zipmod(tmp_path / "plain_mod.zip", "g.c")
    cf.zipmod(tmp_path / "just_files.zip", None)
    found = sorted(p.name for p in zipmods.iter_mod_files(tmp_path))
    assert found == ["a.zipmod", "b.zipmod", "plain_mod.zip"]


def test_in_modpack_folder(tmp_path):
    md = tmp_path / "mods"
    assert zipmods.in_modpack_folder(md / "Sideloader Modpack - Foo" / "a.zipmod", md)
    assert zipmods.in_modpack_folder(md / "Sideloader Modpack" / "x" / "a.zipmod", md)
    assert not zipmods.in_modpack_folder(md / "a.zipmod", md)                       # directly in mods dir
    assert not zipmods.in_modpack_folder(md / "Other" / "Sideloader Modpack" / "a.zipmod", md)   # not first level
    assert not zipmods.in_modpack_folder(tmp_path / "elsewhere" / "a.zipmod", md)


def _mods_tree(root: Path):
    cf.zipmod(root / "own" / "a.zipmod", "local.a")
    cf.zipmod(root / "b.zipmod", "local.b")
    cf.zipmod(root / "Sideloader Modpack - X" / "c.zipmod", "pack.c")
    return root


def test_build_mods_cache_modpack_scopes(tmp_path):
    root = _mods_tree(tmp_path / "mods")
    without = zipmods.build_mods_cache(root, include_modpack=False, use_cache=False)
    with_pack = zipmods.build_mods_cache(root, include_modpack=True, use_cache=False)
    assert set(without) == {"local.a", "local.b"}
    assert set(with_pack) == {"local.a", "local.b", "pack.c"}
    assert Path(with_pack["local.a"]) == root / "own" / "a.zipmod"


def test_build_mods_cache_uses_and_writes_cache_file(tmp_path):
    root = _mods_tree(tmp_path / "mods")
    zipmods.build_mods_cache(root, include_modpack=True, use_cache=True)
    assert (root / zipmods.MODS_CACHE_FILE).exists()
    again = zipmods.build_mods_cache(root, include_modpack=True, use_cache=True)
    assert set(again) == {"local.a", "local.b", "pack.c"}
    zipmods.build_mods_cache(root, include_modpack=True, use_cache=False)        # must not crash on existing cache


@pytest.mark.parametrize("use_cache", [False, True])
def test_scan_mods_local_fallback(tmp_path, use_cache):
    root = _mods_tree(tmp_path / "mods")
    found = zipmods.scan_mods(root, {"local.a", "pack.c", "absent.guid"}, include_modpack=False, use_cache=use_cache)
    assert set(found) == {"local.a"}
    found = zipmods.scan_mods(root, {"local.a", "pack.c", "absent.guid"}, include_modpack=True, use_cache=use_cache)
    assert set(found) == {"local.a", "pack.c"}
    assert zipmods.scan_mods(tmp_path / "missing", {"x"}) == {}


def _resolution_setup(tmp_path):
    root = _mods_tree(tmp_path / "mods")
    cf.zipmod(root / "Elsewhere" / "d.zipmod", "pack.d")        # in the index, but not at its indexed path
    cf.zipmod(root / "Sideloader Modpack - X" / "f.zipmod", "pack.f")   # only its second URL is on disk
    index = {"pack.c": [modpack_url("Sideloader Modpack - X/c.zipmod")],
             "pack.d": [modpack_url("Sideloader Modpack - X/d.zipmod")],
             "pack.f": [modpack_url("Sideloader Modpack - X/old f.zipmod"),
                        modpack_url("Sideloader Modpack - X/f.zipmod")],
             "pack.gone": [modpack_url("Sideloader Modpack - X/gone.zipmod")]}
    return root, index


def test_resolve_guids_falls_back_to_local_scan_when_indexed_path_is_missing(tmp_path):
    root, index = _resolution_setup(tmp_path)
    local = zipmods.local_mods_map(root, include_modpack=True, use_cache=False)
    res = zipmods.resolve_guids({"local.a", "pack.c", "pack.d", "pack.f", "pack.gone", "absent"}, local, index, root, True)
    assert set(res.found) == {"local.a", "pack.c", "pack.d", "pack.f"}
    assert res.found["pack.d"] == root / "Elsewhere" / "d.zipmod"
    assert res.found["pack.f"] == root / "Sideloader Modpack - X" / "f.zipmod"      # a later URL of the GUID
    assert res.excluded == set() and res.missing == {"pack.gone", "absent"}


def test_resolve_guids_reports_modpack_guids_as_excluded_when_modpack_is_off(tmp_path):
    root, index = _resolution_setup(tmp_path)
    local = zipmods.local_mods_map(root, include_modpack=False, use_cache=False)
    res = zipmods.resolve_guids({"local.a", "pack.c", "pack.d", "pack.gone", "absent"}, local, index, root, False)
    assert set(res.found) == {"local.a"}
    assert res.excluded == {"pack.c", "pack.d", "pack.gone"} and res.missing == {"absent"}


def test_is_modpack_protected(tmp_path):
    md = tmp_path / "mods"
    index = {"pack.c": [modpack_url("Sideloader Modpack/c.zipmod")]}
    assert zipmods.is_modpack_protected("pack.c", None, md, index)                                    # by GUID
    assert zipmods.is_modpack_protected("x", md / "Sideloader Modpack" / "x.zipmod", md, index)       # by folder
    assert not zipmods.is_modpack_protected("x", md / "own" / "x.zipmod", md, index)
    assert not zipmods.is_modpack_protected("x", None, None, index)


class _Resp:
    def __init__(self, payload, status=200, etag=None):
        self._p, self.status_code = payload, status
        self.headers = {"etag": etag} if etag else {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Client:
    """Fake httpx client serving `index` with the given ETag; answers 304 to a matching If-None-Match."""
    def __init__(self, etag, index):
        self.etag, self.index, self.urls = etag, index, []

    def get(self, url, headers=None, **kw):
        self.urls.append(url)
        if headers and headers.get("If-None-Match") == self.etag:
            return _Resp(None, 304)
        return _Resp(self.index, etag=self.etag)


def _modpack_payload(n=3):
    return {f"pack.{i}": [{"version": "1.0", "betterrepack": [modpack_url(f"Sideloader Modpack/{i}.zipmod")]}]
            for i in range(n)}


def test_load_modpack_index_downloads_and_revalidates(tmp_path):
    from index_cache import age, read_meta
    c = _Client('"e1"', _modpack_payload())
    index = load_modpack_index(c, tmp_path)
    assert set(index) == {"pack.0", "pack.1", "pack.2"}
    assert c.urls == [modpack.MODPACK_INDEX_URL]
    assert read_meta(tmp_path / "modpack.json")["etag"] == '"e1"'

    again = _Client('"e2"', _modpack_payload(1))
    assert load_modpack_index(again, tmp_path) == index          # under an hour: no request
    assert again.urls == []

    age(tmp_path / "modpack.json", 2)
    same = _Client('"e1"', _modpack_payload(1))                               # same ETag -> 304, cached copy
    assert load_modpack_index(same, tmp_path) == index

    age(tmp_path / "modpack.json", 2)
    new = _Client('"e2"', _modpack_payload(1))                                # new ETag -> re-download
    assert set(load_modpack_index(new, tmp_path)) == {"pack.0"}


def test_load_modpack_index_falls_back_to_cache_or_none(tmp_path):
    from index_cache import age

    class Down:
        def get(self, *a, **k):
            raise OSError("offline")

    assert load_modpack_index(Down(), tmp_path) is None            # nothing cached
    load_modpack_index(_Client('"e1"', _modpack_payload()), tmp_path)
    age(tmp_path / "modpack.json", 2)
    assert len(load_modpack_index(Down(), tmp_path)) == 3          # offline: cached copy
    assert load_modpack_index(_Client('"x"', {"guids": [1]}), tmp_path / "other") is None  # wrong format
    old = {"guids": {"pack.0": [modpack_url("Sideloader Modpack/0.zipmod")]}}             # the older format
    assert load_modpack_index(_Client('"y"', old), tmp_path / "older") is None


def test_modpack_index_lists_the_urls_newest_version_first_and_knows_their_versions():
    new, old, other = (modpack_url(f"Sideloader Modpack/{n}.zipmod") for n in ("new", "old", "other"))
    raw = {"pack.a": [{"version": "2.0", "betterrepack": [new]},
                      {"version": "1.0", "betterrepack": [old, new]},
                      {"version": "unknown", "betterrepack": [other]},
                      {"version": "0.1", "betterrepack": []}],
           "pack.empty": [{"version": "1.0", "betterrepack": []}]}
    index = modpack._normalize_index(raw)
    assert index == {"pack.a": [new, old, other]}                  # flat, best first, no repeats
    assert index.versions == {new: "2.0", old: "1.0", other: "unknown"}


def test_url_to_rel_path():
    assert url_to_rel_path(modpack_url("Sideloader Modpack/xiongben/[x] A v1.0.zipmod")) == \
        "Sideloader Modpack/xiongben/[x] A v1.0.zipmod"
    assert url_to_rel_path(modpack_url("Sideloader Modpack/Alvarna#0602/a+b.zipmod")) == \
        "Sideloader Modpack/Alvarna#0602/a+b.zipmod"                                   # %23 is a '#', '+' stays
    base = "https://sideload.betterrepack.com/download/KKEC/"
    assert url_to_rel_path(base + "Sideloader%20Modpack/%E3%83%A2/a.zipmod") == "Sideloader Modpack/モ/a.zipmod"
    for bad in ("http://sideload.betterrepack.com/download/KKEC/a/b.zipmod",       # not https
                "https://evil.example/download/KKEC/a/b.zipmod",                    # another host
                "https://sideload.betterrepack.com/other/a/b.zipmod",              # outside the base
                base + "a.zipmod",                                                 # no folder
                base + "a/../../b.zipmod", base + "a/%2E%2E/b.zipmod",             # leaves the mods folder
                base + "a/b%5Cc.zipmod", base + "a//b.zipmod", base):
        assert url_to_rel_path(bad) is None, bad


def test_resolve_paths(tmp_path):
    game = tmp_path / "game"
    (game / "mods").mkdir(parents=True)
    (game / "UserData" / "coordinate").mkdir(parents=True)
    inside = game / "UserData" / "chara" / "female" / "c.png"
    outside = tmp_path / "loose" / "c.png"
    outside.parent.mkdir()
    assert zipmods.resolve_paths(inside, game, True, None, None) == (game / "mods", game / "UserData" / "coordinate")
    assert zipmods.resolve_paths(outside, game, True, None, None) == (outside.parent, outside.parent)
    assert zipmods.resolve_paths(outside, game, False, Path("M"), Path("C")) == (Path("M"), Path("C"))
    bare = tmp_path / "bare"
    bare.mkdir()
    assert zipmods.resolve_paths(bare / "x.png", bare, True, None, None) == (None, None)


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
    golden = json.loads((Path(__file__).resolve().parents[1] / "golden" / "pure_functions.json").read_text(encoding="utf-8"))
    assert _digest_cases() == golden["outfit_digests"]


# --- single-read / payload-only scanning ---------------------------------------------------

def test_payload_after_png_matches_whole_file_slice(tmp_path):
    png = cf.png_bytes()
    for name, data in (("card.png", cf.chara_card([G1])), ("plain.png", png),
                       ("tail.png", png + b"tail"), ("trunc.png", png[:-6]),
                       ("sig.png", b"\x89PNG\r\n\x1a\n")):
        p = cf.write(tmp_path / name, data)
        end = parsing._find_iend_end(data)
        expected = data[end:] if 0 <= end < len(data) else None
        assert parsing._payload_after_png(p) == expected, name


def test_parsers_accept_a_preread_payload_without_touching_the_file(tmp_path):
    p = cf.write(tmp_path / "c.png", cf.chara_card([G1, G2]))
    payload = parsing._payload_after_png(p)
    p.unlink()                                                  # a file read would now fail
    assert parsing.parse_chara_guids(p, payload=payload) == [G1, G2]


def test_collector_reads_each_file_once_and_only_past_the_png(tmp_path, monkeypatch):
    _populate_charas(tmp_path)
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self.name) or real(self))
    guids, _ = png_guids.collect_png_guids(
        [tmp_path], False, png_guids.CHARA_GUID_CACHE_FILE, "Chara",
        lambda raw: b"KoiKatuChara" in raw, parsing.parse_chara_guids)
    assert guids == {G1, G2} and reads == []                    # no whole-file read_bytes at all


def test_scan_caches_plain_zips_without_manifest_and_skips_reopening_them(tmp_path, monkeypatch):
    cf.zipmod(tmp_path / "a.zipmod", "g.a")
    cf.zipmod(tmp_path / "plain_mod.zip", "g.b")
    cf.zipmod(tmp_path / "just_files.zip", None)
    assert zipmods.build_mods_cache(tmp_path, use_cache=True) == {
        "g.a": str(tmp_path / "a.zipmod"), "g.b": str(tmp_path / "plain_mod.zip")}

    opened = []
    real = zipmods.guid_from_zipmod
    monkeypatch.setattr(zipmods, "guid_from_zipmod", lambda zp: opened.append(zp.name) or real(zp))
    assert set(zipmods.build_mods_cache(tmp_path, use_cache=True)) == {"g.a", "g.b"}
    assert opened == []                                         # all three served from the cache


# --- header-and-KKEx-only reading of chara / coordinate cards --------------------------------

import io
import struct


def _big_chara(guids, face=200_000, other=300_000, marker=cf.CHARA_MARKER, kkex_first=False, kk=None):
    """A chara card with a big face image and a big block *before* the KKEx block, like a real one."""
    kk = kk if kk is not None else cf.kkex(guids)
    blocks = [("Custom", b"c" * other), ("KKEx", kk)]
    if kkex_first:
        blocks.reverse()
    data, lst = b"", []
    for name, body in blocks:
        lst.append({"name": name, "pos": len(data), "size": len(body)})
        data += body
    header = cf.msgpack.packb({"lstInfo": lst})
    payload = (struct.pack("<i", 100) + cf._s(marker) + cf._s("0.0.0")
               + struct.pack("<i", face) + b"f" * face
               + struct.pack("<i", len(header)) + header + struct.pack("<q", len(data)) + data)
    return cf.png_bytes() + payload


def _big_coord(guids, blob=300_000, kk=None):
    kk = kk if kk is not None else cf.kkex(guids)
    payload = (struct.pack("<i", 100) + cf._s(cf.COORD_MARKER) + cf._s("1.0.0") + cf._s("name")
               + struct.pack("<i", blob) + b"b" * blob
               + cf._s("KKEx") + struct.pack("<i", 1) + struct.pack("<i", len(kk)) + kk)
    return cf.png_bytes() + payload


class _CountingRaw(io.FileIO):
    total = 0

    def readinto(self, b):
        n = super().readinto(b)
        type(self).total += n or 0
        return n


def _measure_reads(monkeypatch, fn):
    """Bytes the OS actually handed to the process while `fn` ran (not what Python returned)."""
    _CountingRaw.total = 0
    monkeypatch.setattr(parsing, "open",
                        lambda p, mode="rb", buffering=-1: io.BufferedReader(_CountingRaw(p, mode), max(buffering, 512)),
                        raising=False)
    try:
        return fn(), _CountingRaw.total
    finally:
        monkeypatch.setattr(parsing, "open", open, raising=False)


@pytest.mark.parametrize("kkex_first", [False, True])
def test_scan_chara_reads_only_header_and_kkex(tmp_path, monkeypatch, kkex_first):
    p = cf.write(tmp_path / "c.png", _big_chara([G2, G1], kkex_first=kkex_first))
    assert p.stat().st_size > 500_000
    guids, read = _measure_reads(monkeypatch, lambda: parsing.scan_chara_guids(p))
    assert guids == [G1, G2]
    assert read < 8_000, read                      # was: the whole ~500 kB payload


def test_scan_coord_reads_only_header_and_kkex(tmp_path, monkeypatch):
    p = cf.write(tmp_path / "k.png", _big_coord([G1, G3]))
    guids, read = _measure_reads(monkeypatch, lambda: parsing.scan_coord_guids(p))
    assert guids == [G1, G3]
    assert read < 8_000, read


def test_parse_chara_guids_without_payload_also_seeks(tmp_path, monkeypatch):
    p = cf.write(tmp_path / "c.png", _big_chara([G1]))
    guids, read = _measure_reads(monkeypatch, lambda: parsing.parse_chara_guids(p))
    assert guids == [G1] and read < 8_000


def test_scan_chara_claims_only_koikatu_chara_cards(tmp_path):
    scan = parsing.scan_chara_guids
    assert scan(cf.write(tmp_path / "a.png", cf.chara_card([G1]))) == [G1]
    assert scan(cf.write(tmp_path / "b.png", cf.chara_card([G1], marker="【KoiKatuCharaSun】"))) == [G1]
    assert scan(cf.write(tmp_path / "c.png", cf.chara_card([G1], with_kkex=False))) == []   # a chara, no mods
    for name, data in (("scene", cf.scene_card([[G1]])), ("coord", cf.coord_card([G1])),
                       ("plain", cf.png_bytes()), ("junk", b"not a png at all"),
                       ("eromake", cf.chara_card([G1], marker="【EroMakeChara】")),
                       ("unknown", cf.chara_card([G1], marker="【NotACard】"))):
        assert scan(cf.write(tmp_path / f"{name}.png", data)) is None, name


def test_scan_coord_claims_only_koikatu_coordinates(tmp_path):
    scan = parsing.scan_coord_guids
    assert scan(cf.write(tmp_path / "a.png", cf.coord_card([G1]))) == [G1]
    assert scan(cf.write(tmp_path / "b.png", cf.coord_card([]))) == []
    for name, data in (("scene", cf.scene_card([[G1]])), ("chara", cf.chara_card([G1])),
                       ("plain", cf.png_bytes()), ("junk", b"not a png at all"),
                       ("ais", cf.coord_card([G1], marker="【AIS_Clothes】"))):
        assert scan(cf.write(tmp_path / f"{name}.png", data)) is None, name


def test_scan_survives_truncated_cards(tmp_path):
    for data in (cf.chara_card([G1])[:-5], _big_chara([G1])[:300], cf.coord_card([G1])[:-5]):
        p = cf.write(tmp_path / "t.png", data)
        for scan in (parsing.scan_chara_guids, parsing.scan_coord_guids):
            try:
                assert scan(p) in (None, [])
            except Exception:
                pass                                   # the collector logs and skips such files


def test_seek_based_and_payload_based_parsing_agree(tmp_path):
    for name, data in (("c", _big_chara([G1, G2])), ("s", cf.chara_card([G3])), ("k", _big_coord([G2])),
                       ("e", cf.chara_card([G1], with_kkex=False)), ("x", cf.png_bytes())):
        p = cf.write(tmp_path / f"{name}.png", data)
        payload = parsing._payload_after_png(p)
        assert parsing.parse_chara_guids(p) == parsing.parse_chara_guids(p, payload=payload), name
        assert parsing.parse_coord_guids(p) == parsing.parse_coord_guids(p, payload=payload), name


def test_chara_and_coord_collectors_never_load_the_whole_payload(tmp_path, monkeypatch):
    cf.write(tmp_path / "chara" / "a.png", _big_chara([G1]))
    cf.write(tmp_path / "coord" / "k.png", _big_coord([G2]))
    monkeypatch.setattr(parsing, "_payload_after_png",
                        lambda p: (_ for _ in ()).throw(AssertionError("whole payload read")), raising=False)
    monkeypatch.setattr(png_guids, "_payload_after_png",
                        lambda p: (_ for _ in ()).throw(AssertionError("whole payload read")), raising=False)
    assert png_guids.collect_chara_guids([tmp_path / "chara"], use_cache=False) == {G1}
    assert png_guids.collect_coord_guids([tmp_path / "coord"], use_cache=False) == {G2}


# --- skipping other plugins' data inside KKEx ------------------------------------------------

def _noisy_kkex(guids, noise=400_000):
    """A KKEx map where UAR sits between two big non-UAR plugin entries (e.g. KSOX textures)."""
    infos = [cf.msgpack.packb({"ModID": g, "Slot": 1, "LocalSlot": 1000 + i, "Property": "x"})
             for i, g in enumerate(guids)]
    return cf.msgpack.packb({
        "KSOX": {1: {"tex": b"t" * noise, "names": ["a", "b"]}},
        cf.UAR: {1: {"info": infos}},
        "com.deathweasel.bepinex.materialeditor": [None, {"blob": b"m" * noise, "n": 5}],
    })


def test_scan_chara_skips_non_uar_plugin_data_inside_kkex(tmp_path, monkeypatch):
    p = cf.write(tmp_path / "c.png", _big_chara([G2, G1], kk=_noisy_kkex([G2, G1])))
    assert p.stat().st_size > 1_200_000
    guids, read = _measure_reads(monkeypatch, lambda: parsing.scan_chara_guids(p))
    assert guids == [G1, G2]
    assert read < 8_000, read              # the 800 kB of KSOX / Material Editor data is never read


def test_scan_coord_skips_non_uar_plugin_data_inside_kkex(tmp_path, monkeypatch):
    p = cf.write(tmp_path / "k.png", _big_coord([G1], kk=_noisy_kkex([G3])))
    guids, read = _measure_reads(monkeypatch, lambda: parsing.scan_coord_guids(p))
    assert guids == [G3]
    assert read < 8_000, read


def test_kkex_with_only_other_plugins_gives_no_guids(tmp_path):
    kk = cf.msgpack.packb({"KSOX": {1: {"tex": b"t" * 1000}}})
    assert parsing.scan_chara_guids(cf.write(tmp_path / "a.png", _big_chara([], kk=kk))) == []
    assert parsing.scan_coord_guids(cf.write(tmp_path / "b.png", _big_coord([], kk=kk))) == []


def test_uar_only_and_whole_kkex_give_the_same_guids(tmp_path):
    for kk in (_noisy_kkex([G1, G2], noise=50), cf.kkex([G3]), cf.kkex([G1], ext_id="EC.Core.Sideloader.UniversalAutoResolver"),
               cf.msgpack.packb({cf.UAR.encode(): {1: {"info": [cf.msgpack.packb({"ModID": G2})]}}})):
        raw = cf.write(tmp_path / "c.png", _big_chara([], face=10, other=10, kk=kk))
        payload = parsing._payload_after_png(raw)
        whole = parsing._chara_kkex_from_stream(io.BytesIO(payload), only_uar=False)[1]
        assert whole == kk
        assert sorted(set(parsing._extract_guids_from_kkex(whole))) == parsing.scan_chara_guids(raw)


def test_malformed_kkex_falls_back_to_reading_it_whole(tmp_path):
    # not a map / invalid type byte / value running past the block: reading whole gives the old result ([])
    for kk in (b"\x91\x01", b"\x81\xa1k\xc1", b"\x81\xa1k\xdb\xff\xff\xff\xff"):
        p = cf.write(tmp_path / "c.png", _big_chara([], face=10, other=10, kk=kk))
        assert parsing.scan_chara_guids(p) == []


def test_mp_skip_steps_over_every_msgpack_type_exactly():
    one = {   # raw encodings written by hand, so this doesn't lean on the msgpack library
        "nil": b"\xc0", "false": b"\xc2", "true": b"\xc3", "posfix": b"\x7f", "negfix": b"\xe0",
        "fixstr": b"\xa3abc", "str8": b"\xd9\x03abc", "str16": b"\xda\x00\x03abc", "str32": b"\xdb\x00\x00\x00\x03abc",
        "bin8": b"\xc4\x03abc", "bin16": b"\xc5\x00\x03abc", "bin32": b"\xc6\x00\x00\x00\x03abc",
        "ext8": b"\xc7\x03\x01abc", "ext16": b"\xc8\x00\x03\x01abc", "ext32": b"\xc9\x00\x00\x00\x03\x01abc",
        "f32": b"\xca" + bytes(4), "f64": b"\xcb" + bytes(8),
        "u8": b"\xcc\x01", "u16": b"\xcd" + bytes(2), "u32": b"\xce" + bytes(4), "u64": b"\xcf" + bytes(8),
        "i8": b"\xd0\x01", "i16": b"\xd1" + bytes(2), "i32": b"\xd2" + bytes(4), "i64": b"\xd3" + bytes(8),
        "fixext1": b"\xd4" + bytes(2), "fixext2": b"\xd5" + bytes(3), "fixext4": b"\xd6" + bytes(5),
        "fixext8": b"\xd7" + bytes(9), "fixext16": b"\xd8" + bytes(17),
        "fixarray": b"\x92\x01\xa1x", "array16": b"\xdc\x00\x02\x01\x02", "array32": b"\xdd\x00\x00\x00\x02\x01\x02",
        "fixmap": b"\x81\xa1k\x01", "map16": b"\xde\x00\x01\xa1k\x01", "map32": b"\xdf\x00\x00\x00\x01\xa1k\x01",
    }
    nested = b"\x82\xa1a\x92\x81\xa1b\xc4\x02xy\xc0\xa1c\xdc\x00\x01\x91\x05"
    for name, raw in {**one, "nested": nested}.items():
        s = io.BytesIO(raw + b"TAIL")
        parsing._mp_skip(s)
        assert s.tell() == len(raw), name
    with pytest.raises(ValueError):
        parsing._mp_skip(io.BytesIO(b"\xc1"))
    with pytest.raises(EOFError):
        parsing._mp_skip(io.BytesIO(b"\x92\x01"))


# --- streaming scene scan -----------------------------------------------------------------

def _scene_with(guid_lists, pad_before=0, pad_between=0, tail=True, ext_id=cf.UAR):
    blob = b"\x00" * pad_before
    for guids in guid_lists:
        infos = [cf.msgpack.packb({"ModID": g}) for g in guids]
        blob += b"\x01" + ext_id.encode() + cf.msgpack.packb({1: {"info": infos}}) + b"\x02" * pad_between
    if tail:
        blob += cf.SCENE_MARKER.encode("utf-8")
    return cf.png_bytes() + blob


@pytest.mark.parametrize("chunk", [200, 257, 1000])
def test_scene_stream_matches_in_memory_scan_at_every_chunk_boundary(tmp_path, monkeypatch, chunk):
    monkeypatch.setattr(parsing, "_SCENE_CHUNK", chunk)
    monkeypatch.setattr(parsing, "_SCENE_UNPACK_START", 64)       # lookahead = 128: small enough to test
    many = [f"mod.big.{i}" for i in range(60)]                    # ~1.5 kB value: bigger than the first window
    for pad in range(0, chunk + 5, 7):                            # slide every needle across a boundary
        data = _scene_with([[G1], many, [G2, G3]], pad_before=pad, pad_between=pad % 40)
        p = cf.write(tmp_path / "s.png", data)
        expected = parsing.parse_scene_guids(p, payload=parsing._payload_after_png(p))
        assert parsing.scan_scene_guids(p) == expected == sorted({G1, G2, G3, *many}), pad
        assert parsing.parse_scene_guids(p) == expected


def test_scene_stream_handles_both_ids_and_nested_needles(tmp_path, monkeypatch):
    monkeypatch.setattr(parsing, "_SCENE_CHUNK", 300)
    monkeypatch.setattr(parsing, "_SCENE_UNPACK_START", 64)
    data = _scene_with([[G1]], pad_before=100) + b"\x02EC.Core.Sideloader.UniversalAutoResolver" + cf.msgpack.packb(
        {1: {"info": [cf.msgpack.packb({"ModID": "ec.mod"})]}})
    p = cf.write(tmp_path / "s.png", data)
    assert parsing.scan_scene_guids(p) == sorted([G1, "ec.mod"])


def test_scene_stream_decodes_a_value_larger_than_the_buffer(tmp_path, monkeypatch):
    monkeypatch.setattr(parsing, "_SCENE_CHUNK", 256)
    monkeypatch.setattr(parsing, "_SCENE_UNPACK_START", 64)
    huge = [f"mod.{i:04d}" for i in range(2000)]                  # ~50 kB value, buffer is ~400 B
    p = cf.write(tmp_path / "s.png", _scene_with([huge], pad_before=33))
    assert parsing.scan_scene_guids(p) == sorted(huge)


def test_scene_stream_requires_the_kstudio_mark_even_across_a_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(parsing, "_SCENE_CHUNK", 200)
    monkeypatch.setattr(parsing, "_SCENE_UNPACK_START", 64)
    for pad in range(0, 300, 11):
        p = cf.write(tmp_path / "s.png", _scene_with([[G1]], pad_before=pad))
        assert parsing.scan_scene_guids(p) == [G1], pad
    assert parsing.scan_scene_guids(cf.write(tmp_path / "n.png", _scene_with([[G1]], tail=False))) is None
    assert parsing.scan_scene_guids(cf.write(tmp_path / "e.png", _scene_with([], pad_before=500))) == []
    for name, data in (("chara", cf.chara_card([G1])), ("coord", cf.coord_card([G1])),
                       ("plain", cf.png_bytes()), ("junk", b"not a png")):
        assert parsing.scan_scene_guids(cf.write(tmp_path / f"{name}.png", data)) is None, name


@pytest.mark.parametrize("tail_len", [0, 5, 190, 200, 201, 260, 1000, 5000])
def test_scene_mark_found_whether_or_not_the_tail_fits_the_probe(tmp_path, monkeypatch, tail_len):
    monkeypatch.setattr(parsing, "_SCENE_CHUNK", 200)
    monkeypatch.setattr(parsing, "_SCENE_UNPACK_START", 64)
    # the mark sits `tail_len` bytes before the end: inside the probe window, straddling it, or far outside
    data = _scene_with([[G1]], pad_before=300, tail=True) + b"\x07" * tail_len
    p = cf.write(tmp_path / "s.png", data)
    assert parsing.scan_scene_guids(p) == [G1]
    no_mark = cf.png_bytes() + b"\x00" * 700 + b"\x07" * tail_len
    assert parsing.scan_scene_guids(cf.write(tmp_path / "n.png", no_mark)) is None


def test_scene_scan_memory_is_bounded_by_the_chunk_not_the_scene(tmp_path):
    import tracemalloc
    pad = 24 * 1024 * 1024
    p = cf.write(tmp_path / "big.png", _scene_with([[G1], [G2]], pad_before=pad, pad_between=pad // 2))
    assert p.stat().st_size > 35_000_000
    tracemalloc.start()
    try:
        guids = parsing.scan_scene_guids(p)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert guids == [G1, G2]
    assert peak < 4_000_000, peak                  # one 1 MiB chunk + lookahead, not ~36 MB


def test_scene_scan_reads_each_byte_about_once(tmp_path, monkeypatch):
    p = cf.write(tmp_path / "s.png", _scene_with([[G1], [G2]], pad_before=3_000_000, pad_between=1_000_000))
    payload = p.stat().st_size - len(cf.png_bytes())
    guids, read = _measure_reads(monkeypatch, lambda: parsing.scan_scene_guids(p, mode="full"))
    assert guids == [G1, G2]
    assert payload <= read <= payload + parsing._SCENE_CHUNK + 4096, (read, payload)   # + the tail probe


def test_scene_collectors_use_the_streaming_scan(tmp_path, monkeypatch):
    cf.write(tmp_path / "scenes" / "s.png", _scene_with([[G1, G2]]))
    cf.write(tmp_path / "scenes" / "chara.png", cf.chara_card([G3]))
    boom = lambda p: (_ for _ in ()).throw(AssertionError("whole payload read"))
    monkeypatch.setattr(png_guids, "_payload_after_png", boom, raising=False)
    assert png_guids.collect_scene_guids([tmp_path / "scenes"], use_cache=False) == {G1, G2}
    by_file = png_guids.collect_scene_guids_by_file([tmp_path / "scenes"], use_cache=False)
    assert by_file == {str(tmp_path / "scenes" / "s.png"): sorted([G1, G2])}
