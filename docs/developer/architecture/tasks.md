# Anatomy of a task

Every entry in the `src/kkafio/tasks/` folder maps 1:1 to a task declared in
`interface.json`, and to a key in `kkafio.core.config._TASK_KEY` /
`_TASK_DEFAULTS`. A task is either:

- **A class subclassing `BaseTask`** (most of them) — instantiated with
  `(config, file_manager)`, configured by reading `self.config.<task_attr>`
  in `__init__`, and run via `.run()`. `BaseTask` only provides logging
  helpers (`log_start`/`log_done`) and `validate_input_path()`.
- **A pair of module-level functions**, `export()` + `process()` — used by
  `group_chara.py` and `rename_chara.py` in addition to their `GroupChara`/
  `RenameChara` classes, because the LLM round-trip dialog (see
  [`llm.py`](https://github.com/RedDeadDepresso/KKAFIO/blob/dev/src/kkafio/dialogs/llm.py)) needs to build a prompt, hand
  control to the user, and then process whatever they paste back — the
  class's `run()` just calls both in sequence.

Every task is declared once, as a `TaskSpec` in
[`task_specs.py`](https://github.com/RedDeadDepresso/KKAFIO/blob/dev/src/kkafio/task_specs.py) (machinery in
[`registry.py`](https://github.com/RedDeadDepresso/KKAFIO/blob/dev/src/kkafio/registry.py)). The spec names the task, points at
its class by `"module:Class"` string, and lists its command-line options; the
subcommand, its `--help`, its dispatch from `kkafio_cli run` and its config
overrides all follow from that. Nothing is auto-discovered from the `tasks/`
folder, so adding a task still means touching a few places: see [Adding a task](../guides/adding-a-task.md) for the checklist.

## Raising errors

Raise the exception that says who can fix the problem (all in
[`core/errors.py`](https://github.com/RedDeadDepresso/KKAFIO/blob/dev/src/kkafio/core/errors.py)):

- The **user** can (a wrong setting, a folder that doesn't exist, nothing to
  process, a missing tool): raise `ConfigError` / `InputError` /
  `ToolNotFoundError` with `tag=` your log category. **Don't also log it** —
  the CLI prints the message once, as `ERROR | <tag> | <message>`, and exits 1
  without writing a traceback.
- The work **failed** (an external tool errored, a file couldn't be written):
  raise `TaskFailedError`. It is reported with a traceback.
- Anything else is a bug and will be reported with a traceback automatically.

Library code never exits the process; only `cli.py` does (a test enforces
this).

## How CLI options work

Each option in a spec is either a `Value` (takes an argument), a `Toggle` (a
`--x` / `--no-x` pair) or, for the odd ones, a `Custom`. An option that is
given replaces the matching key of the task's config section **before the task
is constructed**, so the task sees it exactly as if the GUI had configured it;
an option that isn't given changes nothing (the GUI's value, or the default,
stands). A few options need a small hook for logic that spans several flags —
e.g. `download-missing-mods --mods-dir` setting both of its directories, or
the context-menu batching of `delete-cards` / `archive-cards`.

`GameFolders` (`--game-folders`) is the one option that acts last instead of
first: after the hook has run, `TaskSpec.finish_overrides` blanks every folder
setting the task lists in it that no other flag (nor the hook) set, and a
blank folder means "the game's own folder" to the task. Add it to a task only
if that is true of every key you list; it is why `archive-cards`, whose
overrides mean "none" when blank, doesn't have it.

Task classes are imported only when they run (the specs refer to them by
string), so `--help` and every parse stay cheap. That also means PyInstaller
can't discover them by scanning imports; the build uses
`--collect-submodules kkafio` for this reason.
