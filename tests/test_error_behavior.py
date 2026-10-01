"""What users get when things go wrong: exit codes, error output, and traceback files.

Replays tests/golden/error_behavior.json (see cli_process.py).
"""

import json
import sys
from pathlib import Path

import pytest

import cli_harness as h
import cli_process as cp

GOLDEN = json.loads((Path(__file__).parent / "golden" / "error_behavior.json").read_text(encoding="utf-8"))

_WINDOWS_FINDS_7ZIP_ANYWAY = {"7-Zip missing (backup)"}   # discovered via the registry / Program Files there


def test_every_scenario_is_recorded():
    assert set(cp.scenarios()) == set(GOLDEN)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_failure_behavior_is_unchanged(name, tmp_path):
    if sys.platform == "win32" and name in _WINDOWS_FINDS_7ZIP_ANYWAY:
        pytest.skip("7-Zip is found without PATH on Windows")
    assert cp.run_scenario(h.World(tmp_path), name) == GOLDEN[name]
