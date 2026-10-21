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


def test_existing_zip_cards_are_set_aside_while_the_tool_runs_and_restored_after(tmp_path):
    task = _task(tmp_path)
    inp = tmp_path / "Downloads"
    cf.write(inp / "Old[zip].png", b"old")
    cf.write(inp / "sub" / "Deep[zip].png", b"deep")
    cf.write(inp / "Plain.png", b"plain")
    seen = {}

    def tool(cmd, **kw):
        seen["during"] = sorted(p.relative_to(inp).as_posix() for p in inp.rglob("*") if p.is_file())
        return _FakeProc()

    with mock.patch.object(cct, "popen_text", side_effect=tool):
        task.run()

    assert seen["during"] == ["Old[zip].png.bak", "Plain.png", "sub/Deep[zip].png.bak"]
    assert (inp / "Old[zip].png").read_bytes() == b"old"
    assert (inp / "sub" / "Deep[zip].png").read_bytes() == b"deep"
    assert not list(inp.rglob("*.bak"))


def test_zip_cards_are_restored_even_when_the_tool_fails(tmp_path):
    task = _task(tmp_path)
    inp = tmp_path / "Downloads"
    cf.write(inp / "Old[zip].png", b"old")

    class Failing(_FakeProc):
        returncode = 2

    with mock.patch.object(cct, "popen_text", return_value=Failing()):
        try:
            task.run()
            raise AssertionError("expected the task to fail")
        except cct.TaskFailedError:
            pass
    assert (inp / "Old[zip].png").read_bytes() == b"old" and not list(inp.rglob("*.bak"))


def test_a_fresh_compressed_copy_replaces_the_set_aside_one(tmp_path):
    task = _task(tmp_path)
    inp, out = tmp_path / "Downloads", tmp_path / "Downloads[zip]"
    cf.write(inp / "A[zip].png", b"stale")
    cf.write(out / "A[zip].png", b"fresh")
    with mock.patch.object(cct, "popen_text", return_value=_FakeProc()):
        task.run()
    assert (inp / "A[zip].png").read_bytes() == b"fresh" and not list(inp.rglob("*.bak"))


def test_original_is_deleted_even_if_the_compressed_card_is_not_smaller(tmp_path):
    task = _task(tmp_path, delete_original=True)
    inp = tmp_path / "Downloads"
    card = cf.chara_card(["a"])
    cf.write(inp / "Card.png", card)
    cf.write(inp / "Card[zip].png", card + b"x" * 500)               # bigger than the original
    trashed = []
    with mock.patch.object(cct, "send2trash", lambda p: trashed.append(Path(p))):
        task._delete_originals(inp)
    assert trashed == [inp / "Card.png"]


def test_original_is_kept_when_the_compressed_card_is_invalid(tmp_path):
    task = _task(tmp_path, delete_original=True)
    inp = tmp_path / "Downloads"
    cf.write(inp / "Card.png", cf.chara_card(["a"]))
    cf.write(inp / "Card[zip].png", b"truncated")
    trashed = []
    with mock.patch.object(cct, "send2trash", lambda p: trashed.append(Path(p))):
        task._delete_originals(inp)
    assert trashed == []
