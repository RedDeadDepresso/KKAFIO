# The MXU GUI

KKAFIO's GUI lives in a **separate repository**, `MXU-KKAFIO` — a fork of
[MistEO/MXU](https://github.com/MistEO/MXU), a generic Tauri + React desktop
shell originally built to run [MaaFramework](https://github.com/MaaXYZ/MaaFramework)
automation projects (mobile game automation bots). Nothing in *this*
repository imports or depends on that one; they only communicate through the
config file and subprocess boundary described in
[02 — How It Works](02-how-it-works.md).

## Why a MaaFramework GUI runs a file-management tool

MXU is schema-driven: point it at an `interface.json` describing tasks and
options, and it renders a full settings UI, task list, scheduler, and log
viewer without knowing anything about what the tasks actually do. KKAFIO
reuses that schema (see [03 — interface.json](03-interface-json.md)) to get
a polished GUI for free, at the cost of some fields in `interface.json`
existing only because the schema expects them, and some MaaFramework/MAA
terminology and concepts leaking through (`pipeline_override`, "agent",
"special tasks" for generic automation actions).

## What's KKAFIO-specific in the fork

Most of MXU is unmodified — the task list rendering, option editors,
scheduler, log panel, settings UI, and window chrome all work exactly the
same as upstream MXU. The fork adds a small, focused set of KKAFIO-specific
pieces:

| File (in MXU-KKAFIO) | Purpose |
|---|---|
| `src-tauri/src/commands/kkafio.rs` | Spawns/stops the `kkafio_cli.exe` subprocess, streams its output, runs `Run Game`/`Run Studio`. |
| `src-tauri/src/commands/state.rs` | The in-memory + on-disk runtime log buffer (`push_log`/`get_all_logs`), used for both live log display and `export_logs`. |
| `src/utils/useKkafioLogger.ts` | Listens for `kkafio-output` events from Rust and feeds parsed log lines into the app state. |
| `src/utils/llm_dialog` interactions | None on the GUI side — the Group Chara / Rename Chara Copy/Paste dialog is a **native OS dialog spawned by Python** (`utils/llm_dialog.py`), not a React component. The GUI has no code for it at all. |

Everything else — how an option of type `folder`/`switch`/`checkbox`/etc.
gets rendered and stored — is generic MXU code, unaware it's editing KKAFIO
settings specifically.

## How the Rust side finds and runs the CLI

`resolve_cli()` in `kkafio.rs` looks for, in order:

1. `kkafio_cli.exe` in the given working directory (a packaged release).
2. `kkafio_cli.py` in the same directory, in which case it falls back to
   invoking `python -u kkafio_cli.py run` instead.

This means the GUI works identically against a packaged `.exe` release or a
`uv run` source checkout, as long as the GUI's working directory is set to
wherever `kkafio_cli.py`/`.exe` lives.

`kkafio_start` (the Tauri command the Start button calls) always kills any
previously-running child process first — **only one KKAFIO run can be active
at a time**, globally, not per-instance. If you select a different config
instance and hit Start while a run is still going, the old one is killed.

## Config instances vs. the running process

The GUI can hold multiple named "instances" (separate configurations — e.g.
different game installs) in `mxu-KKAFIO.json`, but there is exactly one
running `kkafio_cli.exe` process at a time, chosen via `--instance N`. This
has a real consequence documented in the codebase: the GUI's task
**scheduler** only actually triggers a run if the scheduled instance is also
the currently *active* (displayed) instance in the UI — see the scheduler's
trigger callback in `Toolbar.tsx` for the exact gating logic. A scheduled
run on a background instance is silently skipped, not queued.

## Logs: three different places, one source

A single line of task output ends up in up to three places, all originating
from the same `logger.info(...)` call in Python:

1. **The GUI's live log panel** — via the `kkafio-output` stdout event →
   `useKkafioLogger.ts` → the Zustand store → rendered in `LogsPanel.tsx`.
2. **`localStorage`** — `runtimeLogPersistence.ts` mirrors the log list there
   purely so it survives a page refresh; it's not a durable log file.
3. **`debug/runtime-<instance-id>.log`** on disk — written by the Rust
   `push_log` command specifically so `export_logs` (Settings → Export Logs)
   has something to include. This is the only one of the three that's
   actually a persisted file; see [05 — Configuration](05-configuration.md).

## Extending the GUI vs. extending Python

A rule of thumb for "where does this change go":

- **New task, new option, new task behaviour** → Python only
  (`interface.json` + `tasks/`/`utils/`). The GUI needs zero changes; it
  renders whatever the schema says.
- **New kind of *option* the schema can't express yet** (a new `"type"`) →
  both sides: the GUI needs a new renderer in `OptionEditor.tsx` /
  `TaskItem.tsx` / a default-value case in `stores/helpers.ts`, and Python
  needs a matching branch in `_extract_opt()` (`utils/config.py`).
- **New button/action that isn't "run a task with these options"** (like
  `Run Game`/`Run Studio`, or the Copy/Paste dialog) → usually a dedicated
  Tauri command plus either a small UI addition (Run Game/Studio) or, if it
  needs to block for user input mid-task, a native dialog spawned from
  Python instead of touching the GUI at all (Group Chara/Rename Chara).
