"""
Regenerate tests/golden/*.json from the CURRENT code.

    uv run python tests/generate_goldens.py

Only do this deliberately: the goldens are the record of what the CLI does, so
regenerating them after a change accepts that change as the new behaviour.
Review the diff of tests/golden/ before committing it.
"""

import json
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


def build_error_behavior() -> dict:
    import cli_process as cp
    out = {}
    for name in cp.scenarios():
        with tempfile.TemporaryDirectory() as tmp:
            out[name] = cp.run_scenario(h.World(Path(tmp)), name)
    return out


def build_pure_functions() -> dict:
    """Recorded outputs of pure helpers. Imports live in test_pure_functions / test_card_code."""
    import test_card_code as cc
    import test_pure_functions as pf
    from pure_corpus import msgpack_corpus

    links = ["https://t.me/c/1234567890/42", "t.me/c/123/9", "https://t.me/somechannel/77",
             "https://t.me/+InviteHash", "http://t.me/c/5/6/7", "not a link", "", "https://t.me/c/abc/1",
             "https://t.me/c/1234567890/42?single=1", "  https://t.me/c/12/34  "]
    search = ["https://t.me/somechannel", "https://t.me/somechannel/299", "https://t.me/c/123456/7",
              "t.me/somechannel/12", "@somechannel", "https://t.me/+abcDEF", "junk", "", "https://t.me/c/123456"]
    raws = ["", "https://t.me/a\nhttps://t.me/b/5", "https://t.me/a  # comment\n\n# only comment\nhttps://t.me/c/9/8",
            "  https://t.me/x/1  \r\nhttps://t.me/y", "garbage\nhttps://t.me/ok"]
    return {
        "outfit_digests": cc._digest_cases(),
        "msgpack_roundtrip": {k: pf.roundtrip(v).hex() for k, v in msgpack_corpus().items()},
        "tme_links": {x: pf._jsonable(pf.telegram_links.parse_tme_link(x)) for x in links},
        "chat_link_for_search": {x: pf._jsonable(pf.telegram_links._parse_chat_link_for_search(x)) for x in search},
        "chat_links": {x: pf._jsonable(pf.telegram_links.parse_chat_links(x)) for x in raws},
        "default_chat_links": pf._jsonable(pf.telegram_links.parse_chat_links(pf.telegram_links.DEFAULT_TELEGRAM_CHAT_LINKS)),
        "source_labels": {x: pf.telegram_links.telegram_source_label(x)
                          for x in ("No", "KoikatsuCards", "ChatLinks", "Both", "anything else")},
    }


if __name__ == "__main__":
    GOLDEN.mkdir(exist_ok=True)
    _dump("cli_help.json", build_help())
    _dump("cli_overrides.json", build_overrides())
    _dump("pipeline.json", build_pipeline())
    _dump("pure_functions.json", build_pure_functions())
    _dump("error_behavior.json", build_error_behavior())
    print("goldens written to", GOLDEN)
