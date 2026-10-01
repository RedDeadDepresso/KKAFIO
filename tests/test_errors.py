"""The exception hierarchy (kkafio.core.errors) and how it is raised and reported."""


import pytest

import cli_harness as h
import cli_process as cp
from kkafio.core.errors import (ConfigError, InputError, KKAFIOError, TaskFailedError,
                                ToolNotFoundError, UserError)


def _traceback_path():
    from kkafio.core.paths import CONFIG_DIR
    return CONFIG_DIR / "traceback.log"


# --- the hierarchy ---------------------------------------------------------------------------

def test_hierarchy():
    for cls in (ConfigError, InputError, ToolNotFoundError):
        assert issubclass(cls, UserError) and issubclass(cls, KKAFIOError)
    assert issubclass(TaskFailedError, KKAFIOError)
    assert not issubclass(TaskFailedError, UserError), "a failed operation is not something the user fixes"
    assert issubclass(UserError, Exception)


def test_user_error_carries_message_and_tag():
    e = InputError("nothing to do", tag="UNGRP")
    assert str(e) == "nothing to do" and e.tag == "UNGRP"
    assert UserError("x").tag == "CLI"                  # default tag


# --- how the CLI reports them ------------------------------------------------------------------

@pytest.mark.parametrize("make", [lambda: InputError("no folder", tag="UNGRP"),
                                  lambda: ToolNotFoundError("no 7-Zip", tag="7-Zip"),
                                  lambda: ConfigError("bad setting", tag="SCRIPT")])
def test_user_error_in_a_task_exits_1_without_a_traceback(make, tmp_path):
    _traceback_path().unlink(missing_ok=True)
    result = h.run_argv(h.World(tmp_path), ["ungroup-chara"], fail_on="UngroupChara", fail_with=make)
    assert result["outcome"]["exit"] == 1
    assert not _traceback_path().exists()


def test_task_failure_keeps_its_traceback(tmp_path):
    _traceback_path().unlink(missing_ok=True)
    result = h.run_argv(h.World(tmp_path), ["ungroup-chara"], fail_on="UngroupChara",
                        fail_with=lambda: TaskFailedError("7-Zip failed"))
    assert result["outcome"]["exit"] == 1
    text = _traceback_path().read_text(encoding="utf-8")
    assert "[UngroupChara]" in text and "TaskFailedError: 7-Zip failed" in text


def test_user_error_stops_the_run_pipeline(tmp_path):
    tasks, kwargs = h.pipeline_cases(tmp_path)["task raises -> exit 1, rest skipped"]
    result = h.run_argv(h.World(tmp_path, tasks), ["run"], fail_on="UngroupChara",
                        fail_with=lambda: InputError("no folder", tag="UNGRP"))
    assert result["outcome"]["exit"] == 1
    assert [e["task"] for e in result["events"] if e["kind"] == "task"] == ["UngroupChara"]


# --- where they are raised -----------------------------------------------------------------------

def test_validate_input_path(tmp_path):
    from pathlib import Path
    from kkafio.tasks.base_task import validate_input_path
    for unset in (Path(""), Path(".")):
        with pytest.raises(InputError, match="not set") as exc:
            validate_input_path("UNGRP", unset)
        assert exc.value.tag == "UNGRP"
    with pytest.raises(InputError, match="does not exist") as exc:
        validate_input_path("INSTALL", tmp_path / "missing")
    assert exc.value.tag == "INSTALL" and str(tmp_path / "missing") in str(exc.value)
    validate_input_path("X", tmp_path)                                  # exists: fine
    default = tmp_path / "default_dir"                                  # missing default folder is created
    validate_input_path("X", default, default_path=default)
    assert default.is_dir()
    with pytest.raises(InputError):                                     # ...but only if it IS the default
        validate_input_path("X", tmp_path / "other", default_path=default)


@pytest.mark.parametrize("kind, message", [
    ("missing", "not found"),
    ("badjson", "Invalid JSON"),
    ("noinstances", "no instances"),
    ("nogame", "GamePath is not set"),
    ("badgame", "Game path not valid"),
    ("badtask", "Path invalid for task UngroupChara"),
])
def test_config_problems_raise_config_error(kind, message, tmp_path):
    from kkafio.core.config import Config
    path = cp._write_config(h.World(tmp_path), kind)
    with pytest.raises(ConfigError, match=message) as exc:
        Config(str(path), instance_index=0)
    assert exc.value.tag == "SCRIPT"


def test_instance_index_out_of_range_raises_config_error(tmp_path):
    from kkafio.core.config import Config
    path = cp._write_config(h.World(tmp_path), "ok")
    with pytest.raises(ConfigError, match="out of range"):
        Config(str(path), instance_index=5)


def test_valid_config_loads(tmp_path):
    from kkafio.core.config import Config
    assert Config(str(cp._write_config(h.World(tmp_path), "ok")), instance_index=0)


def test_no_library_code_exits_the_process():
    """Library code raises; only the CLI decides to exit (config.py used to call sys.exit)."""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "kkafio"
    offenders = []
    for f in src.rglob("*.py"):
        if f.name == "cli.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and ast.unparse(n.func) in {"sys.exit", "exit", "quit", "os._exit"}:
                offenders.append(f"{f.relative_to(src)}:{n.lineno}")
    assert not offenders, offenders
