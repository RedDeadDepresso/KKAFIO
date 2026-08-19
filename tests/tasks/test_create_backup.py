"""Tests for tasks/create_backup.py"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_task(folders_enabled=None, output_path="/output", filename="backup"):
    from tasks.create_backup import CreateBackup
    if folders_enabled is None:
        folders_enabled = {"mods": True, "UserData": True, "BepInEx": False}
    cfg = MagicMock()
    cfg.create_backup = {
        "mods":       folders_enabled.get("mods", False),
        "UserData":   folders_enabled.get("UserData", False),
        "BepInEx":    folders_enabled.get("BepInEx", False),
        "OutputPath": Path(output_path),
        "Filename":   filename,
    }
    cfg.game_path = {
        "mods":    Path("/game/mods"),
        "UserData": Path("/game/UserData"),
        "BepInEx": Path("/game/BepInEx"),
    }
    fm = MagicMock()
    task = CreateBackup.__new__(CreateBackup)
    task.config = cfg
    task.file_manager = fm
    task.game_path = cfg.game_path
    task.folders = [cfg.game_path[k] for k, v in folders_enabled.items() if v]
    task.filename = filename
    task.output_path = Path(output_path)
    return task, fm


class TestCreateBackup:
    def test_calls_create_game_archive(self):
        task, fm = _make_task()
        task.run()
        fm.create_game_archive.assert_called_once()

    def test_output_path_includes_filename(self):
        task, fm = _make_task(output_path="/out", filename="mybackup")
        task.run()
        call_args = fm.create_game_archive.call_args[0]
        assert call_args[1] == Path("/out/mybackup")

    def test_only_enabled_folders_included(self):
        task, fm = _make_task({"mods": True, "UserData": False, "BepInEx": False})
        task.run()
        folders_arg = fm.create_game_archive.call_args[0][0]
        assert Path("/game/mods") in folders_arg
        assert Path("/game/UserData") not in folders_arg
        assert Path("/game/BepInEx") not in folders_arg

    def test_no_folders_enabled(self):
        task, fm = _make_task({"mods": False, "UserData": False, "BepInEx": False})
        task.run()
        folders_arg = fm.create_game_archive.call_args[0][0]
        assert folders_arg == []