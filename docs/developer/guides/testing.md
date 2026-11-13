# Testing

## Running the tests

```
uv run pytest                       # everything
uv run pytest tests/core -k registry   # a folder, filtered by name
```

`pyproject.toml` sets `testpaths = ["tests"]`, `pythonpath = ["src", "tests/helpers"]` and `-v --tb=short`. That is why
tests can `import kkafio...` without installing it and import the helpers as top-level modules
(`import cli_harness as h`).

The tests that open real windows skip themselves when `tkinter`, `customtkinter` or `PIL.ImageTk` can't be imported,
so a machine without a display toolkit still runs the rest.

## Layout

| Folder | What is in it |
|---|---|
| `tests/cards/`, `core/`, `indices/`, `tasks/` | Ordinary tests, grouped like `src/kkafio`. `tasks/` is split further by task: `download_contents/`, `group_chara/`, `group_coordinates/`, `group_scenes/` and `library/`. |
| `tests/dialogs/` | Tests of the dialogs, including the Organize Library window in `tests/dialogs/library/`. |
| `tests/characterization/` | Tests that replay the recordings in `tests/golden/` (see below). |
| `tests/golden/` | The recordings, as JSON. |
| `tests/helpers/` | `card_factory.py`, `cli_harness.py`, `cli_process.py`, `generate_goldens.py`, `index_cache.py`, `pure_corpus.py`. |
| `tests/data/` | Static fixture files. |

## The sandbox

`tests/conftest.py` runs before any `kkafio` module is imported. It points `HOME`, `USERPROFILE`, `XDG_CONFIG_HOME` and
`APPDATA` at a throw-away directory, so the config directory and the default task folders are created there and your
real KKAFIO state is never touched. It also sets `COLUMNS=100`, so `--help` output wraps the same everywhere, and
deletes the Organize Library's saved settings before every test so one test can't affect another.

## Synthetic cards

`tests/helpers/card_factory.py` builds tiny character, scene and coordinate PNGs and zipmods in memory, so no game
files are needed. `tests/helpers/index_cache.py` has helpers for the tests of the downloaded-index cache, such as
making a cached index look as if it was last checked hours ago.

## Characterization (golden) tests

These record what the CLI **does** rather than assert what it should do. They were written before the CLI was moved
onto the task registry and generated from the old code, so they describe the behaviour the refactor had to keep.

| Recording | Holds |
|---|---|
| `cli_help.json` | The `--help` text of the top-level command and of every subcommand. |
| `cli_overrides.json` | For every CLI invocation (one generated per option, plus hand-written cases): which task class it builds and what state its config is in when `run()` is called. |
| `pipeline.json` | What `run` does with a set of enabled tasks. |
| `error_behavior.json` | Exit code, error-level output and traceback file for failure scenarios, recorded by running the real CLI as a subprocess (`cli_process.py`). |
| `pure_functions.json` | Outputs of pure helpers: outfit digests, the MessagePack codec, the Telegram link parsers. |

When one of these tests fails, you either broke something or changed behaviour on purpose. If it was on purpose,
regenerate the recordings and **review the diff before committing it**:

```
uv run python tests/helpers/generate_goldens.py
git diff tests/golden/
```

Regenerating accepts the current behaviour as the new truth, so don't do it just to turn a red test green.

The harness keeps its own hand-written map from task name to class (`TASK_CLASSES` in `cli_harness.py`) instead of
using the registry, so that it is an independent check on it. A new task has to be added there; see
[Adding a task](adding-a-task.md#5-register-it-with-the-tests).

## Tests that enforce project rules

| Test | Rule |
|---|---|
| `tests/core/test_registry.py` | The task names in the registry, `interface.json`, `config._TASK_KEY`, `config._TASK_DEFAULTS` and the harness agree; names and commands are unique; each spec points at the expected class. |
| `tests/core/test_errors.py` | Library code never calls `sys.exit()`; only `cli.py` decides to exit. |
| `tests/core/test_i18n.py` | Every language has every runtime key, placeholders are not renamed, and every `t()` call names a key that exists. |
| `tests/characterization/test_error_behavior.py` | Every failure scenario has a recording. |
