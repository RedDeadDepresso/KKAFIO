"""One read + parse per file across the passes of a run (scan_session), and loading a card
from bytes the caller already read."""

import sys
from types import SimpleNamespace
from unittest import mock

from kkafio.cards import scan_session
from kkafio.cards.chara import outfits
from kkafio.tasks import group_coordinates as gc


# --- scan_session itself ----------------------------------------------------------------------

def test_memoized_computes_once_inside_a_session_and_every_time_outside():
    calls = []

    def compute():
        calls.append(1)
        return None                                   # "not this kind of file" is remembered too

    with scan_session.scan_session():
        assert scan_session.active()
        assert [scan_session.memoized("k", compute) for _ in range(3)] == [None] * 3
    assert len(calls) == 1

    assert not scan_session.active()
    for _ in range(2):
        scan_session.memoized("k", compute)
    assert len(calls) == 3                            # nothing outlives the session


def test_nested_session_keeps_the_outer_memo():
    with scan_session.scan_session():
        scan_session.memoized("k", lambda: 1)
        with scan_session.scan_session():
            assert scan_session.memoized("k", lambda: 2) == 1
        assert scan_session.active()
    assert not scan_session.active()


# --- coordinate passes ---------------------------------------------------------------------------

def _patched_outfits(reads):
    def fake_read(path):
        reads.append(path.name)
        return {"o": path.name}, []

    return (mock.patch.object(outfits, "_read_coord_outfit", fake_read),
            mock.patch.object(outfits, "outfit_digest", lambda o, i: "d:" + o["o"]),
            mock.patch.object(outfits, "outfit_signature", lambda o, i: (("c",), ("a",))),
            mock.patch.object(outfits, "outfit_hair", lambda o, i: ("h",)),
            mock.patch.object(outfits, "outfit_head_mods", lambda o, i: ("m",)))


def _run_all_four_passes(coord_dir):
    return (outfits.build_coord_cache(coord_dir, use_cache=False),
            outfits.build_coord_hair_cache(coord_dir, use_cache=False),
            outfits.build_coord_sig_cache(coord_dir, use_cache=False),
            outfits.build_coord_mod_cache(coord_dir, use_cache=False))


def test_coordinate_passes_share_one_read_per_file_in_a_session(tmp_path):
    for n in "abc":
        (tmp_path / f"{n}.png").write_bytes(b"x")
    reads = []
    p1, p2, p3, p4, p5 = _patched_outfits(reads)
    with p1, p2, p3, p4, p5:
        outside = _run_all_four_passes(tmp_path)
        assert len(reads) == 12                       # no session: 4 passes x 3 files, as before
        reads.clear()
        with scan_session.scan_session():
            inside = _run_all_four_passes(tmp_path)
        assert len(reads) == 3                        # session: each file read once
    assert inside == outside
    digests, hairs, sigs, mods = inside
    key = str(tmp_path / "a.png")
    assert digests[key] == "d:a.png" and hairs[key] == ("h",)
    assert sigs[key] == (("c",), ("a",)) and mods[key] == ("m",)


def test_a_field_that_fails_is_none_without_losing_the_others(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    reads = []
    p1, p2, p3, p4, p5 = _patched_outfits(reads)

    def boom(o, i):
        raise ValueError("bad hair")

    with p1, p2, p3, mock.patch.object(outfits, "outfit_hair", boom), p5:
        with scan_session.scan_session():
            assert outfits._coord_file_hair(tmp_path / "a.png") is None
            assert outfits._coord_file_digest(tmp_path / "a.png") == "d:a.png"
    assert reads == ["a.png"]


# --- chara pass: a card is parsed once per session even with the cache off -------------------------

def test_chara_passes_share_one_parse_per_card_in_a_session(tmp_path):
    (tmp_path / "A.png").write_bytes(b"png")
    loads = []
    fake_loader = SimpleNamespace(KoikatuCharaData=SimpleNamespace(
        load=lambda raw: loads.append(type(raw)) or object()))
    with mock.patch.object(gc, "get_card_type", return_value=next(iter(gc.CHARA_CARD_TYPES))), \
         mock.patch.object(gc, "chara_outfit_digests", return_value={"d"}), \
         mock.patch.object(gc, "chara_outfit_signatures", return_value=[]), \
         mock.patch.object(gc, "chara_outfit_hairs", return_value=[]), \
         mock.patch.object(gc, "chara_outfit_head_mods", return_value={}), \
         mock.patch.dict(sys.modules, {"kkloader": fake_loader}):
        for _ in range(3):
            gc._scan_charas([tmp_path], False, need_sigs=False)
        assert len(loads) == 3                        # no session: parsed again every pass
        loads.clear()
        with scan_session.scan_session():
            for _ in range(3):
                assert gc._scan_charas([tmp_path], False, need_sigs=False)
        assert loads == [bytes]                       # one parse, from the bytes already read


# --- the chara-key readers read a card file once -------------------------------------------------------

def test_group_chara_read_card_reads_each_file_once(tmp_path, monkeypatch):
    from pathlib import Path
    from chara_fakes import fake_cards, write_cards
    from kkafio.cards.chara.chara_info_cache import CharaInfoCache
    from kkafio.tasks import group_chara

    cards = write_cards(tmp_path, a="Rin", b="Rin")
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self.name) or real(self))
    with fake_cards():
        cache = CharaInfoCache([tmp_path], False, "TEST")
        for png in cards.values():
            _, key, _ = group_chara.read_card(png, cache)
            assert key and not key.startswith("__error__")
    assert sorted(reads) == ["a.png", "b.png"]        # classified and loaded from the same bytes


# --- review similar characters: a cold scan reads each card once ----------------------------------------

def test_review_similar_cold_scan_reads_each_card_once(tmp_path, monkeypatch):
    import io
    from pathlib import Path
    from PIL import Image
    from kkafio.cards.chara.similarity import png_preview_of_bytes, read_png_preview
    from kkafio.tasks import review_similar_chara as rsc

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 30, 30)).save(buf, "PNG")
    card = buf.getvalue() + b"\x00" + "【KoiKatuChara】".encode() + b"\x00" * 64
    for n in "ab":
        (tmp_path / f"{n}.png").write_bytes(card)

    assert png_preview_of_bytes(card) == read_png_preview(_write(tmp_path.parent / (tmp_path.name + "_c.png"), card))
    assert png_preview_of_bytes(b"not a png") is None and png_preview_of_bytes(card[:30]) is None

    opened = []
    real_read, real_open = Path.read_bytes, Path.open
    inside_read = []                                    # read_bytes() may call open() itself: count once

    def spy_read(self):
        opened.append(self.name)
        inside_read.append(1)
        try:
            return real_read(self)
        finally:
            inside_read.pop()

    def spy_open(self, *a, **k):
        if not inside_read:
            opened.append(self.name)
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", spy_read)
    monkeypatch.setattr(Path, "open", spy_open)
    cards = rsc._Scanner(rsc.MODE_COVER, use_cache=False).scan([tmp_path])
    assert sorted(c.path.name for c in cards) == ["a.png", "b.png"] and all(c.phash for c in cards)
    assert sorted(n for n in opened if n in ("a.png", "b.png")) == ["a.png", "b.png"]   # once each


def _write(path, data):
    path.write_bytes(data)
    return path
