"""
Replays the recordings in tests/golden/ against the current CLI.

If one of these fails you changed what the CLI does. If that was the point,
regenerate deliberately (`uv run python tests/generate_goldens.py`) and review
the golden diff; if not, you found a regression.
"""

import json
from pathlib import Path

import pytest

import cli_harness as h

GOLDEN = Path(__file__).parent / "golden"


def _load(name):
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


HELP = _load("cli_help.json")
OVERRIDES = _load("cli_overrides.json")
PIPELINE = _load("pipeline.json")


# --- --help text --------------------------------------------------------------

@pytest.mark.parametrize("command", sorted(HELP), ids=lambda c: c or "<top-level>")
def test_help_text_is_unchanged(command):
    from kkafio import cli
    parser = cli.build_parser()
    actual = parser.format_help() if command == "" else h._subparsers(parser)[command].format_help()
    assert actual == HELP[command]


# --- what each invocation does to its task --------------------------------------

def test_cli_surface_matches_goldens(tmp_path):
    """A new/removed/renamed option must come with regenerated goldens."""
    from kkafio import cli
    world = h.World(tmp_path)
    parser = cli.build_parser()
    current = {**h.enumerate_cases(parser), **h.handwritten_cases(world), **h.configured_cases(parser)}
    recorded = {cid: c["argv"] for cid, c in OVERRIDES.items() if "kwargs" not in c}
    assert current == recorded


@pytest.mark.parametrize("case_id", sorted(OVERRIDES))
def test_override_behavior_is_unchanged(case_id, tmp_path):
    case = OVERRIDES[case_id]
    world = h.make_world(case["world"], tmp_path)
    actual = h.run_argv(world, case["argv"], **case.get("kwargs", {}))
    assert actual == case["result"]


# --- the `run` pipeline -----------------------------------------------------------

@pytest.mark.parametrize("case_id", sorted(PIPELINE))
def test_pipeline_behavior_is_unchanged(case_id, tmp_path):
    case = PIPELINE[case_id]
    tasks, kwargs = h.pipeline_cases(tmp_path)[case_id]
    world = h.World(tmp_path, tasks)
    assert kwargs == case["kwargs"]
    assert h.run_argv(world, ["run"], **kwargs) == case["result"]
