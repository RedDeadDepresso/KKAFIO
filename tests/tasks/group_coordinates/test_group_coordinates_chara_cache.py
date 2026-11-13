"""The chara outfit cache lives in each scanned chara folder, not in the coordinate folder."""

import json
import sys
from types import SimpleNamespace
from unittest import mock

from kkafio.tasks import group_coordinates as gc


def _scan(chara_dirs, use_cache=True):
    fake_loader = SimpleNamespace(KoikatuCharaData=SimpleNamespace(load=lambda path: object()))
    with mock.patch.object(gc, "get_card_type", return_value=next(iter(gc.CHARA_CARD_TYPES))), \
         mock.patch.object(gc, "chara_outfit_digests", return_value={"d"}), \
         mock.patch.object(gc, "chara_outfit_signatures", return_value=[]), \
         mock.patch.object(gc, "chara_outfit_hairs", return_value=[]), \
         mock.patch.object(gc, "chara_outfit_head_mods", return_value={}), \
         mock.patch.dict(sys.modules, {"kkloader": fake_loader}):
        return gc._scan_charas(chara_dirs, use_cache, need_sigs=False)


def test_cache_written_per_chara_folder(tmp_path):
    female, male = tmp_path / "female", tmp_path / "male"
    for d, n in ((female, "A.png"), (male / "sub", "B.png")):
        d.mkdir(parents=True)
        (d / n).write_bytes(b"png")
    result = _scan([female, male])
    assert set(p.name for p in result) == {"A.png", "B.png"}

    fcache = json.loads((female / gc.CHARA_OUTFIT_CACHE_FILE).read_text(encoding="utf-8"))
    mcache = json.loads((male / gc.CHARA_OUTFIT_CACHE_FILE).read_text(encoding="utf-8"))
    assert fcache["dir"] == str(female) and list(fcache["files"]) == [str(female / "A.png")]
    assert mcache["dir"] == str(male) and list(mcache["files"]) == [str(male / "sub" / "B.png")]


def test_cache_reused_and_dir_mismatch_ignored(tmp_path):
    chara = tmp_path / "chara"
    chara.mkdir()
    (chara / "A.png").write_bytes(b"png")
    _scan([chara])

    assert str(chara / "A.png") in gc._load_outfit_cache(chara)
    path = chara / gc.CHARA_OUTFIT_CACHE_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["dir"] = str(tmp_path / "elsewhere")          # e.g. the folder was copied
    path.write_text(json.dumps(data), encoding="utf-8")
    assert gc._load_outfit_cache(chara) == {}


def test_nothing_written_into_coord_folder(tmp_path):
    chara, coord = tmp_path / "chara", tmp_path / "coord"
    chara.mkdir()
    coord.mkdir()
    (chara / "A.png").write_bytes(b"png")
    _scan([chara])
    assert not (coord / gc.CHARA_OUTFIT_CACHE_FILE).exists()
