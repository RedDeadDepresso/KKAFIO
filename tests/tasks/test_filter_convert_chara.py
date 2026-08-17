"""Tests for tasks/filter_convert_chara.py"""
import pytest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from utils.classifier import CardType


def _make_task(input_path, convert_kks=False, convert_kk=False, extract=False):
    from tasks.filter_convert_chara import FilterConvertChara
    cfg = MagicMock()
    cfg.filter_convert_chara = {
        "InputPath": str(input_path),
        "ConvertKKS": convert_kks,
        "ConvertKK":  convert_kk,
        "ExtractArchive": extract,
        "Password": "Skip",
    }
    fm = MagicMock()
    task = FilterConvertChara.__new__(FilterConvertChara)
    task.config = cfg
    task.file_manager = fm
    task.convert_kks = convert_kks
    task.convert_kk  = convert_kk
    task.extract_archive = extract
    return task, fm


class TestFilterConvertChara:
    def test_kks_moved_to_folder(self, tmp_path):
        task, fm = _make_task(tmp_path)
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuCharaSun")

        with patch.object(task, "check_png", return_value=CardType.KKS):
            task.run()

        kks_folder = tmp_path / "_KKS_card_"
        assert kks_folder.exists()

    def test_kk_moved_to_folder(self, tmp_path):
        task, fm = _make_task(tmp_path)
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuChara")

        with patch.object(task, "check_png", return_value=CardType.KK):
            task.run()

        kk_folder = tmp_path / "_KK_card_"
        assert kk_folder.exists()

    def test_convert_kks_creates_output_folder(self, tmp_path):
        task, fm = _make_task(tmp_path, convert_kks=True)
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuCharaSun")

        with patch.object(task, "check_png", return_value=CardType.KKS):
            task.run()

        assert (tmp_path / "_KKS_to_KK_").exists()

    def test_convert_kk_creates_output_folder(self, tmp_path):
        task, fm = _make_task(tmp_path, convert_kk=True)
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuChara")

        with patch.object(task, "check_png", return_value=CardType.KK):
            task.run()

        assert (tmp_path / "_KK_to_KKS_").exists()

    def test_no_png_files(self, tmp_path):
        task, fm = _make_task(tmp_path)
        # Should not crash when there are no PNGs
        task.run()
        assert not (tmp_path / "_KKS_card_").exists()
        assert not (tmp_path / "_KK_card_").exists()

    def test_patch_kks_to_kk_modifies_header(self, tmp_path):
        from tasks.filter_convert_chara import FilterConvertChara
        task, fm = _make_task(tmp_path)
        src = tmp_path / "src.png"
        src.write_bytes(b"\x15\xe3\x80\x90KoiKatuCharaSun" + b"Parameter\xa7version\xa50.0.6\xa3sex")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        task._patch_kks_to_kk(src, out_dir)
        result = list(out_dir.glob("*.png"))
        assert len(result) == 1
        data = result[0].read_bytes()
        assert b"KoiKatuCharaSun" not in data
        assert b"KoiKatuChara" in data

    def test_patch_kk_to_kks_modifies_header(self, tmp_path):
        from tasks.filter_convert_chara import FilterConvertChara
        task, fm = _make_task(tmp_path)
        src = tmp_path / "src.png"
        src.write_bytes(b"\x12\xe3\x80\x90KoiKatuChara" + b"Parameter\xa7version\xa50.0.5\xa3sex")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        task._patch_kk_to_kks(src, out_dir)
        result = list(out_dir.glob("*.png"))
        assert len(result) == 1
        data = result[0].read_bytes()
        assert b"KoiKatuCharaSun" in data