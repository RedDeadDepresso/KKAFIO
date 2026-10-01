"""A failing single-task command must exit 1 and leave a traceback headed by the task's name."""

import pytest

import cli_harness as h


def _traceback_path():
    from kkafio.core.paths import CONFIG_DIR
    return CONFIG_DIR / "traceback.log"


@pytest.mark.parametrize("argv, task", [
    (["ungroup-chara"], "UngroupChara"),
    (["create-backup"], "CreateBackup"),
    (["install-contents"], "InstallContents"),
    (["filter-duplicate-contents"], "FilterDuplicateContents"),
])
def test_failure_writes_traceback_and_exits_1(argv, task, tmp_path):
    _traceback_path().unlink(missing_ok=True)
    result = h.run_argv(h.World(tmp_path), argv, fail_on=task)
    assert result["outcome"]["exit"] == 1
    text = _traceback_path().read_text(encoding="utf-8")
    assert f"[{task}]" in text and "RuntimeError: boom" in text


def test_stale_traceback_is_cleared_on_success(tmp_path):
    _traceback_path().parent.mkdir(parents=True, exist_ok=True)
    _traceback_path().write_text("stale", encoding="utf-8")
    result = h.run_argv(h.World(tmp_path), ["ungroup-chara"])
    assert result["outcome"]["exit"] is None
    assert not _traceback_path().exists()
