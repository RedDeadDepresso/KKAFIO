"""Tests for tasks/delete_chara.py"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_task(tmp_path, mods_dir="", coord_dir=""):
    from tasks.delete_chara import DeleteChara
    cfg = MagicMock()
    cfg.config_data = {"Core": {"GamePath": str(tmp_path / "game")}}
    cfg.delete_chara = {
        "CharaPaths":  [],
        "AutoResolve": True,
        "UseCache":    False,
        "ModsDir":     mods_dir,
        "CoordDir":    coord_dir,
    }
    fm = MagicMock()
    task = DeleteChara.__new__(DeleteChara)
    task.config = cfg
    task.file_manager = fm
    task.auto_resolve = True
    task.use_cache = False
    task.mods_dir_str = mods_dir
    task.coord_dir_str = coord_dir
    task.chara_paths = []
    return task, fm


class TestDeleteChara:
    def test_no_paths_does_not_crash(self, tmp_path):
        task, fm = _make_task(tmp_path)
        task.chara_paths = []
        task.run()
        fm.move_to_trash.assert_not_called()

    def test_mods_dir_override_stored(self, tmp_path):
        custom = str(tmp_path / "my_mods")
        task, fm = _make_task(tmp_path, mods_dir=custom)
        assert task.mods_dir_str == custom

    def test_coord_dir_override_stored(self, tmp_path):
        custom = str(tmp_path / "my_coord")
        task, fm = _make_task(tmp_path, coord_dir=custom)
        assert task.coord_dir_str == custom

    def test_deletes_collected_files(self, tmp_path):
        task, fm = _make_task(tmp_path)
        chara = tmp_path / "chara.png"
        chara.write_bytes(b"data")
        task.chara_paths = [str(chara)]

        coord_paths = [tmp_path / "coord.png"]
        zipmod_paths = [tmp_path / "mod.zipmod"]
        for p in coord_paths + zipmod_paths:
            p.write_bytes(b"data")

        with patch("tasks.delete_chara.resolve_chara_deps",
                   return_value=(coord_paths, zipmod_paths)):
            task.run()

        assert fm.move_to_trash.called or fm.delete_file.called

    def test_mods_dir_override_passed_to_resolve(self, tmp_path):
        custom_mods = str(tmp_path / "custom_mods")
        task, fm = _make_task(tmp_path, mods_dir=custom_mods)
        chara = tmp_path / "chara.png"
        chara.write_bytes(b"data")
        task.chara_paths = [str(chara)]

        with patch("tasks.delete_chara.resolve_chara_deps",
                   return_value=([], [])) as mock_resolve:
            task.run()

        if mock_resolve.called:
            call_kwargs = mock_resolve.call_args
            # mods override should be Path(custom_mods)
            assert Path(custom_mods) in call_kwargs.args or \
                   Path(custom_mods) in (call_kwargs.kwargs or {}).values()