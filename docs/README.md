# KKAFIO Developer Documentation

This folder documents KKAFIO's internals — how the pieces fit together, not
how to use them. If you're looking for user-facing task documentation, see
the [wiki](../wiki/Home.md) or the [README](../README.md) instead.

This is for anyone modifying KKAFIO itself: adding a task, adding an option,
understanding why a change to `interface.json` didn't do anything, or trying
to figure out where a given piece of state actually lives on disk.

| Doc | Covers |
|---|---|
| [01 — Project Structure](01-project-structure.md) | Repo layout: what's a "task", what's in `utils/`, and how the two repos (this one + the GUI) relate. |
| [02 — How It Works](02-how-it-works.md) | The full execution flow from clicking Start in the GUI to a task actually running, end to end. |
| [03 — interface.json](03-interface-json.md) | The schema that defines every task and option: structure, option types, quirks, and fields that look load-bearing but aren't. |
| [04 — The MXU GUI](04-mxu-gui.md) | What MXU is, what's forked vs. KKAFIO-specific in this GUI, and how the Rust/TypeScript side talks to the Python CLI. |
| [05 — Configuration](05-configuration.md) | Every config/cache/session file KKAFIO reads or writes: where it lives, its structure, and when it's used. |

## The two repositories

KKAFIO is actually two separate codebases that only communicate through a
JSON config file on disk and a subprocess boundary:

- **This repository** — the Python backend (`kkafio_cli.py`, `tasks/`,
  `utils/`) plus `interface.json`, which describes every task/option to the
  GUI. This is what actually does the work.
- **MXU-KKAFIO** (a separate repo, a fork of [MistEO/MXU](https://github.com/MistEO/MXU))
  — a Tauri + React desktop app that renders `interface.json` into a
  settings UI, lets the user configure tasks, writes their choices to disk,
  and spawns `kkafio_cli.exe run` as a subprocess when the user presses
  Start.

Neither repo imports code from the other. If you're debugging something and
you're not sure which side owns it, [02 — How It Works](02-how-it-works.md)
walks through exactly where the boundary is at each step.
