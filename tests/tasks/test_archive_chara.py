"""Tests for tasks/archive_chara.py"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call


def _make_task(tmp_path, combined=True, include_modpack=False,
               output_path="", mods_dir="", coord_dir=""):
    from tasks.archive_chara import ArchiveChara
    cfg = MagicMock()
    cfg.config_data = {"Core": {"GamePath": str(tmp_path / "game")}}
    cfg.archive_chara = {
        "CharaPaths":     [],
        "Format":         "7z",
        "AutoResolve":    True,
        "UseCache":       False,
        "IncludeModpack": include_modpack,
        "CombinedArchive": combined,
        "ModsDir":        mods_dir,
        "CoordDir":       coord_dir,
        "OutputPath":     output_path,
    }
    fm = MagicMock()
    task = ArchiveChara.__new__(ArchiveChara)
    task.config = cfg
    task.file_manager = fm
    task.format = "7z"
    task.auto_resolve = True
    task.use_cache = False
    task.include_modpack = include_modpack
    task.combined_archive = combined
    task.mods_dir_str = mods_dir
    task.coord_dir_str = coord_dir
    task.output_dir_str = output_path
    task.chara_paths = []
    return task, fm


class TestArchiveChara:
    def test_no_chara_paths_does_not_crash(self, tmp_path):
        task, fm = _make_task(tmp_path)
        task.chara_paths = []
        task.run()
        fm.create_archive.assert_not_called()

    def test_output_path_used_when_provided(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        task, fm = _make_task(tmp_path, output_path=str(out))
        chara = tmp_path / "chara.png"
        chara.write_bytes(b"data")
        task.chara_paths = [str(chara)]

        with patch("tasks.archive_chara.resolve_chara_deps",
                   return_value=([], [])):
            task.run()

        if fm.create_archive.called:
            archive_path = fm.create_archive.call_args[0][1]
            assert str(out) in str(archive_path)

    def test_output_falls_back_to_chara_parent(self, tmp_path):
        task, fm = _make_task(tmp_path, output_path="")
        chara = tmp_path / "chara.png"
        chara.write_bytes(b"data")
        task.chara_paths = [str(chara)]

        with patch("tasks.archive_chara.resolve_chara_deps",
                   return_value=([], [])):
            task.run()

        if fm.create_archive.called:
            archive_path = fm.create_archive.call_args[0][1]
            assert str(tmp_path) in str(archive_path)

    def test_combined_archive_creates_one_archive(self, tmp_path):
        task, fm = _make_task(tmp_path, combined=True)
        charas = [str(tmp_path / f"c{i}.png") for i in range(3)]
        for p in charas:
            Path(p).write_bytes(b"data")
        task.chara_paths = charas

        with patch("tasks.archive_chara.resolve_chara_deps",
                   return_value=([], [])):
            task.run()

        assert fm.create_archive.call_count <= 1

    def test_mods_dir_override_used(self, tmp_path):
        custom_mods = str(tmp_path / "custom_mods")
        task, fm = _make_task(tmp_path, mods_dir=custom_mods)
        assert task.mods_dir_str == custom_mods

    def test_coord_dir_override_used(self, tmp_path):
        custom_coord = str(tmp_path / "custom_coord")
        task, fm = _make_task(tmp_path, coord_dir=custom_coord)
        assert task.coord_dir_str == custom_coord
