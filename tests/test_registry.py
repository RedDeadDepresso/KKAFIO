"""The places that name a task must agree with each other (and with the registry)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import cli_harness as h
from kkafio.core.config import _TASK_DEFAULTS, _TASK_KEY
from kkafio.registry import Toggle, Value
from kkafio.task_specs import TASK_SPECS, TASKS_BY_NAME

ROOT = Path(__file__).resolve().parents[1]


def test_task_names_agree_everywhere():
    interface = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    names = {spec.name for spec in TASK_SPECS}
    assert names == {t["name"] for t in interface["task"]}, "registry vs interface.json"
    assert names == set(_TASK_KEY), "registry vs config._TASK_KEY"
    assert names == set(_TASK_DEFAULTS), "registry vs config._TASK_DEFAULTS"
    assert names == set(h.TASK_CLASSES), "registry vs the test harness's own map"


def test_names_and_commands_are_unique():
    assert len(TASKS_BY_NAME) == len(TASK_SPECS)
    assert len({spec.command for spec in TASK_SPECS}) == len(TASK_SPECS)


@pytest.mark.parametrize("spec", TASK_SPECS, ids=lambda s: s.name)
def test_spec_points_at_the_expected_class(spec):
    cls = spec.load_class()
    assert cls.__name__ == spec.name
    assert (cls.__module__, cls.__name__) == h.TASK_CLASSES[spec.name]
    assert callable(getattr(cls, "run", None))


@pytest.mark.parametrize("spec", TASK_SPECS, ids=lambda s: s.name)
def test_override_keys_are_real_config_keys(spec):
    declared = {o.key for o in spec.options if isinstance(o, (Value, Toggle)) and o.key}
    assert declared <= set(_TASK_DEFAULTS[spec.name]), declared - set(_TASK_DEFAULTS[spec.name])


@pytest.mark.parametrize("spec", TASK_SPECS, ids=lambda s: s.name)
def test_option_dests_exist_on_the_parsed_namespace(spec):
    from kkafio import cli
    args = cli.build_parser().parse_args([spec.command])
    for option in spec.options:
        if isinstance(option, (Value, Toggle)):
            assert hasattr(args, option.dest), option


def test_building_the_parser_imports_no_task_module():
    """Task modules pull in heavy dependencies; only running a task may import them."""
    code = (
        "import sys\n"
        "from kkafio import cli\n"
        "cli.build_parser().format_help()\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('kkafio.tasks.'))\n"
        "print(loaded)\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT, capture_output=True, text=True)
