"""
Regenerate tests/golden/*.json from the CURRENT code.

    uv run python tests/generate_goldens.py

Only do this deliberately: the goldens are the record of what the CLI does, so
regenerating them after a change accepts that change as the new behaviour.
Review the diff of tests/golden/ before committing it.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import conftest  # noqa: F401,E402  (sandbox env must be set before kkafio is imported)
import cli_harness as h  # noqa: E402

GOLDEN = HERE / "golden"


def _dump(name: str, data) -> None:
    (GOLDEN / name).write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                               encoding="utf-8")


def build_help() -> dict:
    from kkafio import cli
    parser = cli.build_parser()
    out = {"": parser.format_help()}
    for cmd, sp in h._subparsers(parser).items():
        out[cmd] = sp.format_help()
    return out


def build_overrides() -> dict:
    from kkafio import cli
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        world = h.World(Path(tmp))
        cases = {**h.enumerate_cases(cli.build_parser()), **h.handwritten_cases(world)}
        for cid, argv in cases.items():
            out[cid] = {"argv": argv, "world": "default", "result": h.run_argv(world, argv)}
        for cid, (argv, kw) in h.handwritten_variants(world).items():
            out[cid] = {"argv": argv, "world": "default", "kwargs": kw,
                        "result": h.run_argv(world, argv, **kw)}
    with tempfile.TemporaryDirectory() as tmp:
        world = h.make_world("configured", Path(tmp))
        for cid, argv in h.configured_cases(cli.build_parser()).items():
            out[cid] = {"argv": argv, "world": "configured", "result": h.run_argv(world, argv)}
    return out


def build_pipeline() -> dict:
    out = {}
    for cid in h.pipeline_cases(Path(tempfile.gettempdir())):     # ids only; rebuilt per world below
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks, kw = h.pipeline_cases(root)[cid]
            world = h.World(root, tasks)
            out[cid] = {"kwargs": kw, "result": h.run_argv(world, ["run"], **kw)}
    return out


if __name__ == "__main__":
    GOLDEN.mkdir(exist_ok=True)
    _dump("cli_help.json", build_help())
    _dump("cli_overrides.json", build_overrides())
    _dump("pipeline.json", build_pipeline())
    print("goldens written to", GOLDEN)
