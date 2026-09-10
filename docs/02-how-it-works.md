# How It Works

This traces the full path from "user clicks Start in the GUI" to "a task
actually runs and moves a file", crossing the process boundary between the
GUI (Rust/TypeScript, a separate repo) and this Python codebase.

## 1. The GUI renders `interface.json` into a settings UI

On startup, the GUI's Rust backend (`AppConfigState::load_interface`) reads
`interface.json` (shipped next to `kkafio_cli.exe`) and hands it to the
frontend. React components (`TaskItem.tsx`, `OptionEditor.tsx`) render each
declared task and option generically, based on each option's `"type"` field
(`folder`, `switch`, `checkbox`, `file_list`, `textarea`, `select`, ...). See
[03 — interface.json](03-interface-json.md) for the schema itself and
[04 — The MXU GUI](04-mxu-gui.md) for how the rendering actually works.

Nothing here is KKAFIO-specific code — the GUI has no idea what
"InstallContents" means. It just draws whatever `interface.json` describes.

## 2. The user configures tasks; the GUI holds this in memory

As the user toggles tasks, sets folder paths, edits the LLM prompt, etc.,
the GUI keeps this as in-memory state (a Zustand store, `appStore.ts`) keyed
by option ID exactly as declared in `interface.json` — e.g. `InputPath`,
`GroupCharaPrompt`, `BackupFolders`. This is **not** the same shape the
Python side eventually reads (more on that in step 4).

## 3. The GUI writes its state to `mxu-KKAFIO.json`

Whenever config changes (and on app close), the GUI calls the Rust
`save_config` command, which serializes all instances/tasks/option values to
`%APPDATA%/KKAFIO/config/mxu-KKAFIO.json`. This file is the **only** channel
between the GUI and the Python backend — there's no IPC, no shared memory,
just this one JSON file. See [05 — Configuration](05-configuration.md) for
its exact structure and location on each OS.

## 4. The user clicks Start

The Rust `kkafio_start` command:

1. Finds `kkafio_cli.exe` (or falls back to `python kkafio_cli.py` for a
   source checkout) next to wherever the GUI itself is running.
2. Spawns it as `kkafio_cli.exe --instance N run` (the instance index
   is only added if the config has more than one instance) — detached, with
   `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1` forced so text output is
   predictable regardless of the user's system codepage.
3. Streams the subprocess's stdout/stderr back to the frontend as events,
   which `useKkafioLogger.ts` turns into the log lines you see in the GUI.

**The `run` subcommand doesn't take any config as an argument.** It reads
`%APPDATA%/KKAFIO/config/mxu-KKAFIO.json` directly — the same file the GUI
just wrote in step 3. This is why "the GUI is stale but the CLI ran
correctly" is possible: the GUI's in-memory state and what's actually on
disk in `mxu-KKAFIO.json` can, in principle, diverge if `save_config` hasn't
fired yet.

## 5. Python loads and translates the config

`kkafio_cli.py`'s `cmd_run()` constructs a `utils.config.Config` instance
with the config file path and instance index. `Config.__init__` → `.read()`:

1. Parses the JSON, picks `instances[instance_index]`.
2. Calls the static method `Config._translate(inst, mxu)`, which:
   - Resolves `GamePath` / `GameType` from this instance's
     `globalOptionValues`, falling back to other instances' `GamePath` if
     this one hasn't set it, then falling back to a legacy per-task
     `GamePath` optionValue if even that's missing.
   - Starts every task's config dict from `_TASK_DEFAULTS` (all disabled).
   - Walks `inst["tasks"]`, and for each one whose `taskName` is a real
     KKAFIO task, calls `_build_task_config(task_name, enabled, optionValues)`
     — a big `if/elif` chain, one branch per task, that reads specific
     option IDs out of the raw `optionValues` dict via `_extract_opt()` and
     writes them into a flat config dict under KKAFIO's own internal key
     names (which often don't match the option IDs — see doc 03's
     "`pipeline_override` doesn't do what it looks like it does" section).
   - Also collects **task order**: for each *enabled* task (KKAFIO task or
     "special task", see below), appends `{"name": ..., "params": ...}` to
     `task_order` in the order they appear in the GUI's task list. This is
     what makes "drag to reorder tasks" in the GUI actually affect run
     order.
3. Calls `Config.validate()`, which resolves `GamePath` into the actual
   per-folder paths every task uses (`self.game_path["mods"]`,
   `self.game_path["charaMale"]`, etc. — see `Config.validate_gamepath()`),
   and exposes each task's config dict as a friendly attribute
   (`self.install_contents`, `self.group_chara`, ...) via
   `Config.validate_tasks()`.

If anything required is missing or a path doesn't exist, `Config` logs an
error and the process exits non-zero — the GUI sees this as a failed run via
the process's exit code and whatever was logged to stdout before the
failure.

### Special tasks

`interface.json`'s task list includes a handful of generic automation
primitives inherited from the MXU/MAA schema — Sleep, Wait Until, Notify,
Webhook, Launch Program, Kill Process, Power Action — that aren't KKAFIO
tasks at all. `utils/special_tasks.py`'s `is_special_task()` recognizes
these by their `__MXU_..._OPTION__`-prefixed option IDs and handles them
separately from `_TASK_KEY`, but they still participate in the same
`task_order` list, so they can be interleaved with real KKAFIO tasks (e.g.
"wait 5 seconds, then Install Contents, then Notify").

## 6. `cmd_run()` executes tasks in order

With `config` built, `cmd_run()` walks `config.task_order` and, for each
entry, either runs the matching special-task handler or looks up the task
name in `kkafio_task_map` (a dict of `lambda: run_x(config, file_manager)`
closures, one per task, defined near the top of `kkafio_cli.py`) and calls
it. Each `run_x()` wrapper just instantiates the task class (or calls the
module-level function pair) and calls `.run()`.

Every task writes its own log lines via `utils.logger.logger`, in a fixed
`STATUS | CATEGORY | message` format that both a human reading the console
and the GUI's log parser can consume. This is the entirety of the "API"
between a running task and the GUI — there's no structured result object,
just parsed log text and the process's final exit code.

## 7. Some tasks pause for human input

`GroupChara`/`RenameChara`'s `.run()` calls `utils.llm_dialog.llm_dialog()`,
which shells out to `powershell.exe` to show a native WinForms
Copy/Paste dialog and **blocks** until the user closes it or clicks Paste.
Archive-password and session-cookie prompts work the same way via
`utils.password_dialog.password_dialog()`. From the GUI's perspective this
just looks like the subprocess going quiet for a while — there's no special
handling on the GUI side for "a task is waiting on a dialog"; the dialog
itself is a separate OS-level window, not something rendered inside MXU.

## Summary diagram

```
┌─────────────────────────┐        writes         ┌──────────────────────────────┐
│  MXU-KKAFIO GUI (Rust/   │ ─────────────────────▶ │ %APPDATA%/KKAFIO/config/     │
│  TypeScript, separate    │                        │   mxu-KKAFIO.json             │
│  repo)                   │ ◀───────────────────── │ (only channel between them)  │
└───────────┬──────────────┘   streams stdout/      └───────────────┬──────────────┘
            │                  stderr as log events                 │ reads
            │ spawns subprocess                                     ▼
            │ kkafio_cli.exe --instance N run          ┌──────────────────────────┐
            └──────────────────────────────────────────▶ kkafio_cli.py            │
                                                        │  utils/config.py         │
                                                        │  tasks/*.py              │
                                                        │  utils/*.py              │
                                                        └──────────────────────────┘
```
