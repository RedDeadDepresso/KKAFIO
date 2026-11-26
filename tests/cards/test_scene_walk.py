"""The scene walker (`parsing.walk_scene`) and the modes of `parsing.scan_scene_guids`.

Scenes here are built in the real binary layout (tests/helpers/scene_layout.py); the old
needle-in-a-blob scenes of test_card_code.py stay valid because the walker refuses them and the
full scan takes over, which these tests also pin down.
"""

import os
from pathlib import Path

import pytest

import card_factory as cf
import scene_layout as sl
from kkafio.cards.mods import parsing

G1, G2, G3, G4, G5 = ("mod.one", "mod.two", "mod.three", "mod.four", "mod.five")
VERSIONS = ["1.0.4.2", "1.1.2.1"]


@pytest.fixture(autouse=True)
def _fresh_stats(monkeypatch):
    monkeypatch.delenv(parsing.SCENE_SCAN_ENV, raising=False)
    parsing.scene_scan_stats.reset()
    yield
    parsing.scene_scan_stats.reset()


def _write(tmp_path, data: bytes, name: str = "s.png") -> Path:
    return cf.write(tmp_path / name, data)


def _rich_scene(v: str, **kw) -> bytes:
    """Every object kind, nesting (a character inside a folder inside an accessory slot), two
    characters, a character with no KKEx, and mod GUIDs in the tail."""
    inner = sl.character(v, [G3])
    return sl.scene(v, [
        sl.item(v, bones=2), sl.folder([sl.item(v), sl.folder([sl.character(v, [G1, G2])])]),
        sl.light(), sl.camera(), sl.route(v, 3, kids=[sl.item(v)]), sl.text(),
        sl.character(v, None, marker="【KoiKatuCharaSun】"),
        sl.character(v, [G2], accessory_children=[sl.item(v), sl.folder([inner])]),
        sl.item(v, kids=[sl.item(v)]),
    ], tail_guids=[G4], **kw)


@pytest.mark.parametrize("v", VERSIONS)
def test_walker_finds_what_the_full_scan_finds(tmp_path, v):
    p = _write(tmp_path, _rich_scene(v))
    walk = parsing.walk_scene(p)
    assert walk.guids == parsing._scan_scene_full(p) == sorted([G1, G2, G3, G4])
    assert walk.version == tuple(int(x) for x in v.split("."))
    assert (walk.chars, walk.items) == (4, 6)
    assert walk.kinds == {0: 4, 1: 6, 2: 1, 3: 3, 4: 1, 5: 1, 7: 1}
    assert parsing.scan_scene_guids(p) == walk.guids
    assert parsing.scene_scan_stats.walked == 1 and parsing.scene_scan_stats.fell_back == 0


@pytest.mark.parametrize("v", VERSIONS)
def test_scene_without_mods_gives_an_empty_list_not_none(tmp_path, v):
    p = _write(tmp_path, sl.scene(v, [sl.item(v), sl.character(v, None)]))
    assert parsing.scan_scene_guids(p) == []
    assert parsing.scene_scan_stats.walked == 1


def test_walker_seeks_over_big_plugin_data(tmp_path, monkeypatch):
    v = "1.1.2.1"
    data = sl.scene(v, [sl.character(v, [G1], other_plugin_bytes=3_000_000),
                        sl.character(v, [G2], other_plugin_bytes=2_000_000)],
                    tail_guids=[G3], tail_plugin_bytes=4_000_000)
    p = _write(tmp_path, data)
    assert p.stat().st_size > 9_000_000
    from test_card_code import _measure_reads
    guids, read = _measure_reads(monkeypatch, lambda: parsing.scan_scene_guids(p))
    assert guids == sorted([G1, G2, G3])
    assert read < 100_000, read                  # the full scan reads all ~9 MB
    assert parsing.scene_scan_stats.walked == 1


@pytest.mark.parametrize("v", VERSIONS)
def test_every_byte_flip_and_truncation_is_caught_or_harmless(tmp_path, monkeypatch, v):
    """Corrupt each byte of a real-layout scene in turn: the walker either refuses (so the full
    scan runs) or returns exactly what the full scan returns. Never a different answer. (This is
    the test that found a damaged KKEx map header being read as "no mods".)"""
    monkeypatch.setattr(parsing, "_SCENE_CHUNK", 8192)        # the scene is ~5 kB: don't allocate 1 MB per scan
    monkeypatch.setattr(parsing, "_SCENE_UNPACK_START", 1024)
    stride = 1 if v == "1.0.4.2" else 3                       # the layouts share almost all structure
    good = _rich_scene(v)
    png_end = len(cf.png_bytes())
    p = tmp_path / "m.png"
    refused = 0
    for i in range(png_end, len(good), stride):
        data = bytearray(good)
        data[i] ^= 0xFF
        p.write_bytes(bytes(data))
        full = parsing._scan_scene_full(p)
        try:
            got = parsing.walk_scene(p).guids
        except Exception:
            refused += 1
            continue
        assert got == (full or []), f"byte {i - png_end} flipped"
    assert refused > 50                          # structure bytes really are detected
    for cut in range(png_end + 1, len(good), 3):
        p.write_bytes(good[:cut])
        with pytest.raises(Exception):
            parsing.walk_scene(p)


def _flip_first_kkex_key_header(v: str) -> bytes:
    """A scene whose first character's KKEx has its map-key header (0xd9, str8) damaged: the key
    text is still there for the full scan to find, but the map no longer reads as a map."""
    data = bytearray(sl.scene(v, [sl.character(v, [G1])], tail_guids=[G2]))
    at = data.find(cf.UAR.encode()) - 2
    assert data[at] == 0xD9
    data[at] ^= 0xFF
    return bytes(data)


@pytest.mark.parametrize("name, build, expected", [
    ("newer version", lambda: sl.scene("1.1.2.1", [sl.character("1.1.2.1", [G1])], tail_guids=[G2])
        .replace(b"\x071.1.2.1", b"\x071.9.0.0", 1), [G1, G2]),
    ("unknown object kind", lambda: sl.scene("1.0.4.2", [sl.character("1.0.4.2", [G1]),
                                                         sl.obj(9, sl.base() + b"\x00" * 8)], tail_guids=[G2]), None),
    ("trailing bytes after the tail", lambda: sl.scene("1.0.4.2", [sl.character("1.0.4.2", [G1])],
                                                       tail_guids=[G2], tail_extra=b"\x00" * 7), [G1, G2]),
    ("no tail mark", lambda: sl.scene("1.0.4.2", [sl.character("1.0.4.2", [G1])], tail_marker=False), None),
    ("damaged map framing in an embedded KKEx", lambda: _flip_first_kkex_key_header("1.0.4.2"), None),
])
def test_walker_refuses_what_it_does_not_know_and_the_full_scan_takes_over(tmp_path, name, build, expected):
    p = _write(tmp_path, build())
    with pytest.raises(parsing.SceneWalkError):
        parsing.walk_scene(p)
    got = parsing.scan_scene_guids(p)
    assert got == parsing._scan_scene_full(p)
    if expected is not None:
        assert got == sorted(expected)
    assert parsing.scene_scan_stats.fell_back == 1 and parsing.scene_scan_stats.walked == 0


def test_a_garbled_key_text_is_not_a_refusal_both_scans_just_find_nothing(tmp_path):
    """Damage to the extension id's own text hides the entry from both scans alike."""
    data = sl.scene("1.0.4.2", [sl.character("1.0.4.2", [G1])], tail_guids=[G2]).replace(
        cf.UAR.encode(), b"\xc1" * len(cf.UAR), 1)
    p = _write(tmp_path, data)
    assert parsing.walk_scene(p).guids == parsing._scan_scene_full(p) == [G2]


def test_not_a_scene_still_gives_none(tmp_path):
    for name, data in (("chara", cf.chara_card([G1])), ("coord", cf.coord_card([G1])),
                       ("plain", cf.png_bytes()), ("junk", b"not a png")):
        assert parsing.scan_scene_guids(_write(tmp_path, data, f"{name}.png")) is None, name


def test_old_needle_scenes_still_work_through_the_fallback(tmp_path):
    p = _write(tmp_path, cf.scene_card([[G1], [G2, G3]]))
    assert parsing.scan_scene_guids(p) == sorted([G1, G2, G3])
    assert parsing.scene_scan_stats.fell_back == 1


# --- modes ---------------------------------------------------------------------------------

def test_full_mode_never_walks(tmp_path, monkeypatch):
    p = _write(tmp_path, _rich_scene("1.0.4.2"))
    monkeypatch.setattr(parsing, "walk_scene", lambda path: (_ for _ in ()).throw(AssertionError("walked")))
    monkeypatch.setenv(parsing.SCENE_SCAN_ENV, "full")
    assert parsing.scan_scene_guids(p) == sorted([G1, G2, G3, G4])
    assert parsing.scan_scene_guids(p, mode="full") == sorted([G1, G2, G3, G4])


def test_unknown_mode_warns_once_and_walks(tmp_path, monkeypatch):
    p = _write(tmp_path, _rich_scene("1.0.4.2"))
    monkeypatch.setenv(parsing.SCENE_SCAN_ENV, "bogus-mode")
    monkeypatch.setattr(parsing, "_warned_modes", set())
    warned = []
    monkeypatch.setattr(parsing.logger, "warning", lambda cat, msg: warned.append(msg))
    assert parsing.scan_scene_guids(p) == sorted([G1, G2, G3, G4])
    assert parsing.scan_scene_guids(p) == sorted([G1, G2, G3, G4])
    assert len(warned) == 1 and "bogus-mode" in warned[0]
    assert parsing.scene_scan_stats.walked == 2


@pytest.mark.parametrize("v", VERSIONS)
def test_verify_agrees_on_a_scene_the_walker_follows(tmp_path, monkeypatch, v):
    p = _write(tmp_path, _rich_scene(v))
    monkeypatch.setenv(parsing.SCENE_SCAN_ENV, "verify")
    assert parsing.scan_scene_guids(p) == sorted([G1, G2, G3, G4])
    st = parsing.scene_scan_stats
    assert (st.verified, st.mismatched, st.fell_back) == (1, 0, 0)
    cmp = parsing.compare_scene_scans(p)
    assert cmp.agree and cmp.describe() == "agree" and not cmp.uncovered


def test_verify_flags_a_uar_entry_outside_every_kkex_and_keeps_the_guid(tmp_path, monkeypatch):
    """An extension id in a place the walker never looks (here: inside an item's colour string).
    The walker alone would miss G5; verify reports it and returns the union."""
    v = "1.0.4.2"
    hidden = cf.UAR.encode() + parsing.msgpack.packb({1: {"info": [parsing.msgpack.packb({"ModID": G5})]}})
    p = _write(tmp_path, sl.scene(v, [sl.item(v, color_blob=hidden), sl.character(v, [G1])], tail_guids=[G2]))
    assert parsing.walk_scene(p).guids == [G1, G2]                       # what walk mode alone would return
    monkeypatch.setenv(parsing.SCENE_SCAN_ENV, "verify")
    warned = []
    monkeypatch.setattr(parsing.logger, "warning", lambda cat, msg: warned.append(msg))
    assert parsing.scan_scene_guids(p) == sorted([G1, G2, G5])
    assert parsing.scene_scan_stats.mismatched == 1 and len(warned) == 1
    cmp = parsing.compare_scene_scans(p)
    assert not cmp.agree and len(cmp.uncovered) == 1 and G5 in cmp.describe() + str(cmp.full)


def test_verify_reports_a_fallback_without_calling_it_a_mismatch(tmp_path, monkeypatch):
    p = _write(tmp_path, cf.scene_card([[G1]]))
    monkeypatch.setenv(parsing.SCENE_SCAN_ENV, "verify")
    assert parsing.scan_scene_guids(p) == [G1]
    st = parsing.scene_scan_stats
    assert (st.fell_back, st.mismatched, st.verified) == (1, 0, 0)


def test_summary_line_counts_and_resets(monkeypatch):
    lines = []
    monkeypatch.setattr(parsing.logger, "info", lambda cat, msg: lines.append(msg))
    parsing.log_scene_scan_summary()
    assert lines == []                                                   # nothing scanned, nothing said
    st = parsing.scene_scan_stats
    st.walked, st.bytes_read, st.payload_size, st.fell_back = 3, 3_000_000, 30_000_000, 1
    parsing.log_scene_scan_summary()
    assert lines == ["Scene scan: 3 walked (read 3.0 of 30.0 MB), 1 fell back to the full scan"]
    assert st.walked == 0


# --- real scenes (optional) ----------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("KKAFIO_TEST_SCENES"), reason="set KKAFIO_TEST_SCENES to a folder of real scenes")
def test_real_scenes_walk_matches_full_scan():
    folder = Path(os.environ["KKAFIO_TEST_SCENES"])
    scenes = sorted(folder.rglob("*.png"))
    assert scenes, f"no .png files in {folder}"
    for p in scenes:
        cmp = parsing.compare_scene_scans(p)
        assert not cmp.walk or cmp.agree, f"{p.name}: {cmp.describe()}"
