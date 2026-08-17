"""Tests for tasks/install_contents.py"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from utils.classifier import CardType


def _make_config(game_type="Koikatsu", has_studio=True):
    """Build a minimal config mock."""
    base = MagicMock()
    cfg = MagicMock()
    cfg.config_data = {"Core": {"GameType": game_type, "GamePath": "/game"}}
    cfg.install_contents = {
        "InputPath": "/input",
        "ExtractArchive": True,
        "FileConflicts": "Skip",
        "Password": "Skip",
    }
    game_path = {
        "base":        Path("/game"),
        "charaMale":   Path("/game/UserData/chara/male"),
        "charaFemale": Path("/game/UserData/chara/female"),
        "coordinate":  Path("/game/UserData/coordinate"),
        "Overlays":    Path("/game/UserData/Overlays"),
        "mods":        Path("/game/mods"),
        "BepInEx":     Path("/game/BepInEx"),
        "UserData":    Path("/game/UserData"),
    }
    if has_studio:
        game_path["scene"] = Path("/game/UserData/Studio/scene")
    cfg.game_path = game_path
    return cfg


def _make_task(game_type="Koikatsu", has_studio=True):
    from tasks.install_contents import InstallContents
    config = _make_config(game_type, has_studio)
    fm = MagicMock()
    task = InstallContents.__new__(InstallContents)
    task.config = config
    task.file_manager = fm
    task.game_path = config.game_path
    task.extract_archive = True
    task.game_type = game_type
    from utils.config import GameType
    task.is_sunshine = game_type == GameType.KOIKATSU_SUNSHINE.value
    return task, fm


class TestResolvePngKK:
    def test_kk_female_installed(self):
        task, fm = _make_task("Koikatsu")
        image_bytes = b"KoiKatuChara\x00sex\x01"
        with patch("utils.classifier.get_card_type", return_value=CardType.KK), \
             patch("utils.classifier.is_male", return_value=False):
            task.resolve_png_bytes(Path("test.png"), image_bytes)
        fm.copy_and_paste.assert_called_once()
        args = fm.copy_and_paste.call_args[0]
        assert "charaFemale" in str(args[2])

    def test_kk_male_installed(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.KK), \
             patch("utils.classifier.is_male", return_value=True):
            task.resolve_png_bytes(Path("test.png"), b"")
        args = fm.copy_and_paste.call_args[0]
        assert "charaMale" in str(args[2])

    def test_kks_skipped_for_kk(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.KKS):
            task.resolve_png_bytes(Path("test.png"), b"")
        fm.copy_and_paste.assert_not_called()

    def test_coordinate_installed(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.UNKNOWN), \
             patch("utils.classifier.is_coordinate", return_value=True):
            task.resolve_png_bytes(Path("test.png"), b"")
        args = fm.copy_and_paste.call_args[0]
        assert "coordinate" in str(args[2])

    def test_overlay_installed(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.UNKNOWN), \
             patch("utils.classifier.is_coordinate", return_value=False):
            task.resolve_png_bytes(Path("test.png"), b"")
        args = fm.copy_and_paste.call_args[0]
        assert "Overlays" in str(args[2])

    def test_scene_installed_when_studio_present(self):
        task, fm = _make_task("Koikatsu", has_studio=True)
        with patch("utils.classifier.get_card_type", return_value=CardType.SCENE):
            task.resolve_png_bytes(Path("test.png"), b"")
        fm.copy_and_paste.assert_called_once()
        args = fm.copy_and_paste.call_args[0]
        assert "scene" in str(args[2])

    def test_scene_skipped_when_no_studio(self):
        task, fm = _make_task("Koikatsu", has_studio=False)
        with patch("utils.classifier.get_card_type", return_value=CardType.SCENE):
            task.resolve_png_bytes(Path("test.png"), b"")
        fm.copy_and_paste.assert_not_called()


class TestResolvePngKKS:
    def test_kks_female_installed_for_sunshine(self):
        task, fm = _make_task("KoikatsuSunshine")
        with patch("utils.classifier.get_card_type", return_value=CardType.KKS), \
             patch("utils.classifier.is_male", return_value=False):
            task.resolve_png_bytes(Path("test.png"), b"")
        args = fm.copy_and_paste.call_args[0]
        assert "charaFemale" in str(args[2])

    def test_kk_skipped_for_sunshine(self):
        task, fm = _make_task("KoikatsuSunshine")
        with patch("utils.classifier.get_card_type", return_value=CardType.KK):
            task.resolve_png_bytes(Path("test.png"), b"")
        fm.copy_and_paste.assert_not_called()

    def test_kksp_skipped_for_sunshine(self):
        task, fm = _make_task("KoikatsuSunshine")
        with patch("utils.classifier.get_card_type", return_value=CardType.KKSP):
            task.resolve_png_bytes(Path("test.png"), b"")
        fm.copy_and_paste.assert_not_called()

    def test_scene_skipped_when_no_studio_sunshine(self):
        task, fm = _make_task("KoikatsuSunshine", has_studio=False)
        with patch("utils.classifier.get_card_type", return_value=CardType.SCENE):
            task.resolve_png_bytes(Path("test.png"), b"")
        fm.copy_and_paste.assert_not_called()