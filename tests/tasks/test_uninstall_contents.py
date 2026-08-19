"""Tests for tasks/uninstall_contents.py"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from utils.classifier import CardType


def _make_task(game_type="Koikatsu", has_studio=True):
    from tasks.uninstall_contents import UninstallContents
    from utils.config import GameType as GT
    cfg = MagicMock()
    cfg.config_data = {"Core": {"GameType": game_type}}
    cfg.uninstall_contents = {"InputPath": "/input"}
    game_path = {
        "charaMale":   Path("/game/chara/male"),
        "charaFemale": Path("/game/chara/female"),
        "coordinate":  Path("/game/coordinate"),
        "Overlays":    Path("/game/Overlays"),
        "mods":        Path("/game/mods"),
    }
    if has_studio:
        game_path["scene"] = Path("/game/scene")
    cfg.game_path = game_path
    fm = MagicMock()
    task = UninstallContents.__new__(UninstallContents)
    task.config = cfg
    task.file_manager = fm
    task.game_path = game_path
    task.game_type = game_type
    task.is_sunshine = game_type == GT.KOIKATSU_SUNSHINE.value
    task.input_path = Path("/input")
    return task, fm


class TestUninstallContentsKK:
    def test_kk_female_removed(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.KK), \
             patch("utils.classifier.is_male", return_value=False):
            task.resolve_png(Path("card.png"))
        fm.find_and_remove.assert_called_once()
        assert "charaFemale" in str(fm.find_and_remove.call_args[0][2])

    def test_kk_male_removed(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.KK), \
             patch("utils.classifier.is_male", return_value=True):
            task.resolve_png(Path("card.png"))
        assert "charaMale" in str(fm.find_and_remove.call_args[0][2])

    def test_kks_skipped_for_kk(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.KKS):
            task.resolve_png(Path("card.png"))
        fm.find_and_remove.assert_not_called()

    def test_coordinate_removed(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.UNKNOWN), \
             patch("utils.classifier.is_coordinate", return_value=True):
            task.resolve_png(Path("card.png"))
        assert "coordinate" in str(fm.find_and_remove.call_args[0][2])

    def test_overlay_removed(self):
        task, fm = _make_task("Koikatsu")
        with patch("utils.classifier.get_card_type", return_value=CardType.UNKNOWN), \
             patch("utils.classifier.is_coordinate", return_value=False):
            task.resolve_png(Path("card.png"))
        assert "Overlays" in str(fm.find_and_remove.call_args[0][2])

    def test_scene_removed_with_studio(self):
        task, fm = _make_task("Koikatsu", has_studio=True)
        with patch("utils.classifier.get_card_type", return_value=CardType.SCENE):
            task.resolve_png(Path("card.png"))
        fm.find_and_remove.assert_called_once()
        assert "scene" in str(fm.find_and_remove.call_args[0][2])


class TestUninstallContentsKKS:
    def test_kks_removed_for_sunshine(self):
        task, fm = _make_task("KoikatsuSunshine")
        with patch("utils.classifier.get_card_type", return_value=CardType.KKS), \
             patch("utils.classifier.is_male", return_value=False):
            task.resolve_png(Path("card.png"))
        fm.find_and_remove.assert_called_once()

    def test_kk_skipped_for_sunshine(self):
        task, fm = _make_task("KoikatsuSunshine")
        with patch("utils.classifier.get_card_type", return_value=CardType.KK):
            task.resolve_png(Path("card.png"))
        fm.find_and_remove.assert_not_called()

    def test_kksp_skipped_for_sunshine(self):
        task, fm = _make_task("KoikatsuSunshine")
        with patch("utils.classifier.get_card_type", return_value=CardType.KKSP):
            task.resolve_png(Path("card.png"))
        fm.find_and_remove.assert_not_called()