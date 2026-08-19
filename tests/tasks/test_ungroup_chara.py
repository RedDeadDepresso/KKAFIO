"""Tests for tasks/ungroup_chara.py"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock


def _make_task(folder, delete_empty=True):
    from tasks.ungroup_chara import UngroupChara
    cfg = MagicMock()
    cfg.ungroup_chara = {"InputPath": str(folder), "DeleteEmptyFolders": delete_empty}
    task = UngroupChara.__new__(UngroupChara)
    task.config = cfg
    task.file_manager = MagicMock()
    task.delete_empty = delete_empty
    return task


class TestUngroupChara:
    def test_moves_png_from_subfolder(self, tmp_path):
        task = _make_task(tmp_path)
        sub = tmp_path / "Series"
        sub.mkdir()
        (sub / "card.png").write_bytes(b"data")
        task.run(tmp_path)
        assert (tmp_path / "card.png").exists()
        assert not (sub / "card.png").exists()

    def test_moves_zipmod_from_subfolder(self, tmp_path):
        task = _make_task(tmp_path)
        sub = tmp_path / "Mods"
        sub.mkdir()
        (sub / "mod.zipmod").write_bytes(b"data")
        task.run(tmp_path)
        assert (tmp_path / "mod.zipmod").exists()

    def test_deletes_empty_folder(self, tmp_path):
        task = _make_task(tmp_path, delete_empty=True)
        sub = tmp_path / "Empty"
        sub.mkdir()
        (sub / "card.png").write_bytes(b"data")
        task.run(tmp_path)
        assert not sub.exists()

    def test_keeps_non_empty_folder(self, tmp_path):
        task = _make_task(tmp_path, delete_empty=True)
        sub = tmp_path / "Series"
        sub.mkdir()
        (sub / "card.png").write_bytes(b"data")
        (sub / "other.txt").write_bytes(b"txt")  # non-movable file stays
        task.run(tmp_path)
        assert sub.exists()

    def test_does_not_delete_when_flag_off(self, tmp_path):
        task = _make_task(tmp_path, delete_empty=False)
        sub = tmp_path / "Series"
        sub.mkdir()
        (sub / "card.png").write_bytes(b"data")
        task.run(tmp_path)
        assert sub.exists()

    def test_handles_filename_collision(self, tmp_path):
        task = _make_task(tmp_path)
        sub = tmp_path / "Series"
        sub.mkdir()
        (sub / "card.png").write_bytes(b"sub")
        (tmp_path / "card.png").write_bytes(b"root")  # collision
        task.run(tmp_path)
        pngs = list(tmp_path.glob("card*.png"))
        assert len(pngs) == 2

    def test_no_files_does_not_crash(self, tmp_path):
        task = _make_task(tmp_path)
        task.run(tmp_path)

    def test_ignores_top_level_files(self, tmp_path):
        task = _make_task(tmp_path)
        (tmp_path / "root.png").write_bytes(b"data")
        task.run(tmp_path)
        # File at root level should be untouched
        assert (tmp_path / "root.png").exists()

    def test_nested_subfolders(self, tmp_path):
        task = _make_task(tmp_path, delete_empty=True)
        deep = tmp_path / "A" / "B"
        deep.mkdir(parents=True)
        (deep / "card.png").write_bytes(b"data")
        task.run(tmp_path)
        assert (tmp_path / "card.png").exists()
        assert not deep.exists()
        assert not (tmp_path / "A").exists()