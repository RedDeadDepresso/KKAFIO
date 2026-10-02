"""Compress Cards Textures: command line and original-deletion paths."""

from pathlib import Path
from unittest import mock

import card_factory as cf
from kkafio.tasks import compress_cards_textures as cct


class _FakeProc:
    stdout = mock.Mock(readline=lambda: "")
    returncode = 0

    def wait(self):
        return 0


def _task(tmp_path: Path, delete_original=False):
    cfg = mock.Mock()
    cfg.compress_cards_textures = {
        "InputPath": str(tmp_path / "Downloads"),
        "KoiCardTexToolPath": str(tmp_path / "tool"),
        "DeleteOriginalCards": delete_original,
    }
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / cct.KOICARDTEXTOOL_EXE).write_bytes(b"")
    return cct.CompressCardsTextures(cfg, None)


def test_command_uses_zip_output_folder_and_suffix(tmp_path):
    task = _task(tmp_path)
    with mock.patch.object(cct, "popen_text", return_value=_FakeProc()) as popen:
        task.run()
    cmd = popen.call_args.args[0]
    assert cmd == [str(tmp_path / "tool" / cct.KOICARDTEXTOOL_EXE), "batch",
                   str(tmp_path / "Downloads"), f"{tmp_path / 'Downloads'}[zip]", "suffix=[zip]"]


def test_merge_moves_files_back_and_removes_output_folder(tmp_path):
    task = _task(tmp_path)
    inp, out = tmp_path / "Downloads", tmp_path / "Downloads[zip]"
    cf.write(out / "A[zip].png", b"a")
    cf.write(out / "sub" / "deep" / "B[zip].png", b"b")
    cf.write(out / cct.OUT_MARKER, b"")
    cf.write(inp / "A[zip].png", b"old")             # stale copy gets overwritten

    assert task._merge_output_into_input(inp, out) is True

    assert not out.exists()
    assert (inp / "A[zip].png").read_bytes() == b"a"
    assert (inp / "sub" / "deep" / "B[zip].png").read_bytes() == b"b"
    assert not (inp / cct.OUT_MARKER).exists()       # marker must never reach the input


def test_merge_keeps_output_folder_if_a_move_fails(tmp_path):
    task = _task(tmp_path)
    inp, out = tmp_path / "Downloads", tmp_path / "Downloads[zip]"
    cf.write(out / "A[zip].png", b"a")
    with mock.patch.object(cct.shutil, "move", side_effect=OSError("locked")):
        assert task._merge_output_into_input(inp, out) is False
    assert (out / "A[zip].png").exists()


def test_run_merges_then_deletes_originals_in_order(tmp_path):
    task = _task(tmp_path, delete_original=True)
    inp, out = tmp_path / "Downloads", tmp_path / "Downloads[zip]"
    small = cf.chara_card(["a"])
    cf.write(inp / "sub" / "Card.png", small + b"x" * 500)
    cf.write(out / "sub" / "Card[zip].png", small)
    cf.write(out / cct.OUT_MARKER, b"")

    trashed = []
    with mock.patch.object(cct, "popen_text", return_value=_FakeProc()), \
         mock.patch.object(cct, "send2trash", lambda p: trashed.append(Path(p))):
        task.run()

    assert not out.exists()
    assert (inp / "sub" / "Card[zip].png").exists()
    assert trashed == [inp / "sub" / "Card.png"]


def test_delete_originals_off_keeps_originals(tmp_path):
    task = _task(tmp_path, delete_original=False)
    inp, out = tmp_path / "Downloads", tmp_path / "Downloads[zip]"
    small = cf.chara_card(["a"])
    cf.write(inp / "Card.png", small + b"x" * 500)
    cf.write(out / "Card[zip].png", small)
    trashed = []
    with mock.patch.object(cct, "popen_text", return_value=_FakeProc()), \
         mock.patch.object(cct, "send2trash", lambda p: trashed.append(Path(p))):
        task.run()
    assert trashed == []
    assert (inp / "Card.png").exists() and (inp / "Card[zip].png").exists()
