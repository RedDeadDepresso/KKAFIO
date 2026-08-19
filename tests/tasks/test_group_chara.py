"""Tests for tasks/group_chara.py"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from utils.classifier import CardType


class TestGroupCharaExport:
    def test_returns_empty_when_no_pngs(self, tmp_path):
        from tasks.group_chara import export
        assert export(tmp_path) == ""

    def test_returns_json_for_kk_card(self, tmp_path):
        from tasks.group_chara import export
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuChara")
        mock_kc = MagicMock()
        mock_kc._repr_name.return_value = "TestChar"
        mock_kc.__getitem__ = lambda s, k: {"Custom": {"hair": {"parts": []}},
                                             "Parameter": {"personality": 0}}[k]
        with patch("tasks.group_chara.KoikatuCharaData") as MockKC, \
             patch("utils.classifier.get_card_type", return_value=CardType.KK):
            MockKC.load.return_value = mock_kc
            result = export(tmp_path)
        assert result
        json_start = result.find("{")
        data = json.loads(result[json_start:])
        assert isinstance(data, dict)
        assert all(v == "" for v in data.values())

    def test_skips_non_kk_cards(self, tmp_path):
        from tasks.group_chara import export
        png = tmp_path / "kks.png"
        png.write_bytes(b"KoiKatuCharaSun")
        with patch("utils.classifier.get_card_type", return_value=CardType.KKS):
            result = export(tmp_path)
        assert result == ""

    def test_top_level_only_by_default(self, tmp_path):
        from tasks.group_chara import export
        sub = tmp_path / "Series"
        sub.mkdir()
        (sub / "card.png").write_bytes(b"KoiKatuChara")
        with patch("utils.classifier.get_card_type", return_value=CardType.KK):
            result = export(tmp_path, include_subfolders=False)
        assert result == ""

    def test_recursive_when_include_subfolders(self, tmp_path):
        from tasks.group_chara import export
        sub = tmp_path / "Series"
        sub.mkdir()
        png = sub / "card.png"
        png.write_bytes(b"KoiKatuChara")
        mock_kc = MagicMock()
        mock_kc._repr_name.return_value = "TestChar"
        mock_kc.__getitem__ = lambda s, k: {"Custom": {"hair": {"parts": []}},
                                             "Parameter": {"personality": 0}}[k]
        with patch("tasks.group_chara.KoikatuCharaData") as MockKC, \
             patch("utils.classifier.get_card_type", return_value=CardType.KK):
            MockKC.load.return_value = mock_kc
            result = export(tmp_path, include_subfolders=True)
        assert result != ""


class TestGroupCharaProcess:
    def test_moves_card_to_series_folder(self, tmp_path):
        from tasks.group_chara import process
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuChara")
        key = "TestChar | Yandere | hair_rgb(0, 0, 0)"
        mapping = json.dumps({key: "MySeries"})
        mock_kc = MagicMock()
        mock_kc._repr_name.return_value = "TestChar"
        mock_kc.__getitem__ = lambda s, k: {"Custom": {"hair": {"parts": []}},
                                             "Parameter": {"personality": 24}}[k]
        with patch("tasks.group_chara.KoikatuCharaData") as MockKC, \
             patch("utils.classifier.get_card_type", return_value=CardType.KK), \
             patch("tasks.group_chara._make_key", return_value=key):
            MockKC.load.return_value = mock_kc
            process(tmp_path, mapping)
        assert (tmp_path / "MySeries" / "card.png").exists()
        assert not png.exists()

    def test_strips_markdown_fences(self, tmp_path):
        from tasks.group_chara import process
        process(tmp_path, "```json\n{}\n```")  # should not crash

    def test_invalid_json_does_not_crash(self, tmp_path):
        from tasks.group_chara import process
        process(tmp_path, "not json")

    def test_skips_cards_in_subfolders(self, tmp_path):
        from tasks.group_chara import process
        sub = tmp_path / "Existing"
        sub.mkdir()
        png = sub / "card.png"
        png.write_bytes(b"KoiKatuChara")
        key = "TestChar | Yandere | hair_rgb(0, 0, 0)"
        mapping = json.dumps({key: "NewSeries"})
        with patch("tasks.group_chara._make_key", return_value=key), \
             patch("utils.classifier.get_card_type", return_value=CardType.KK):
            process(tmp_path, mapping)
        # File should remain in its existing subfolder
        assert png.exists()

    def test_handles_filename_collision(self, tmp_path):
        from tasks.group_chara import process
        key = "Char | Yandere | hair_rgb(0, 0, 0)"
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        # Pre-existing file causes collision
        (series_dir / "card.png").write_bytes(b"existing")
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuChara")
        mapping = json.dumps({key: "Series"})
        with patch("tasks.group_chara._make_key", return_value=key), \
             patch("utils.classifier.get_card_type", return_value=CardType.KK), \
             patch("tasks.group_chara.KoikatuCharaData"):
            process(tmp_path, mapping)
        files = list(series_dir.glob("*.png"))
        assert len(files) == 2

    def test_strips_illegal_chars_from_folder_name(self, tmp_path):
        from tasks.group_chara import _safe_folder_name
        assert "/" not in _safe_folder_name("Series/Name")
        assert ":" not in _safe_folder_name("Series: Name")

    def test_empty_series_values_skipped(self, tmp_path):
        from tasks.group_chara import process
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuChara")
        mapping = json.dumps({"key": ""})
        with patch("utils.classifier.get_card_type", return_value=CardType.KK):
            process(tmp_path, mapping)
        assert not (tmp_path / "card.png").is_dir()
