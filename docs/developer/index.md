# Developer guide

This section documents KKAFIO's internals: how the pieces fit together, not how to use them. If you're looking for
user-facing task documentation, see the [user guide](../index.md) instead.

It is for anyone modifying KKAFIO itself: adding a task, adding an option, understanding why a change to
`interface.json` didn't do anything, or working out where a piece of state lives on disk.

## Where to start

| If you want to… | Read |
|---|---|
| Get the code running and know what to run before pushing | [Development setup](guides/development-setup.md) |
| Add a task or an option | [Adding a task](guides/adding-a-task.md) |
| Follow a click on Start all the way to a task running | [How it works](architecture/how-it-works.md) |
| Find where something lives in the repository | [Repository layout](architecture/repository-layout.md) |
| Understand a change to `interface.json` that did nothing | [interface.json](reference/interface-json.md) |
| Find where a piece of state lives on disk | [Configuration](reference/configuration.md) |
| Work on the tests or the golden recordings | [Testing](guides/testing.md) |
| Publish a release | [Releasing](guides/releasing.md) |

## Architecture

How the code is organised and how the parts talk to each other.

| Page | Covers |
|---|---|
| [Repository layout](architecture/repository-layout.md) | What every folder and module is for, and which subpackage a new piece of logic belongs in. |
| [Anatomy of a task](architecture/tasks.md) | What a task is, how it is declared, how CLI options work, and how to raise errors. |
| [How it works](architecture/how-it-works.md) | The full flow from clicking Start in the GUI to a task running, end to end. |
| [The MXU GUI](architecture/mxu-gui.md) | What MXU is, what is KKAFIO-specific in the fork, and how the Rust/TypeScript side talks to the Python CLI. |

## Guides

| Page | Covers |
|---|---|
| [Development setup](guides/development-setup.md) | Requirements, running from source, the maintainer tools, pre-push checks and the documentation site. |
| [Adding a task](guides/adding-a-task.md) | The checklist for a new task, and for a new option on an existing one. |
| [Testing](guides/testing.md) | The test layout, the sandbox, and the golden-file characterization tests. |
| [Releasing](guides/releasing.md) | What the release workflow does and what ends up in the zip. |

## Reference

| Page | Covers |
|---|---|
| [interface.json](reference/interface-json.md) | The schema that defines every task and option: structure, option types, quirks, and fields that look load-bearing but aren't. |
| [Configuration](reference/configuration.md) | Every config, session, cache and report file KKAFIO reads or writes: where it lives and when it is used. |
| [Data formats](reference/data-formats/index.md) | The downloaded indices and how they are refreshed, plus the [mod](reference/data-formats/mod-index.md), [modpack](reference/data-formats/modpack-index.md), [AGV](reference/data-formats/agv-index.md) and [gacha](reference/data-formats/gacha-index.md) index formats and the [default coordinates](reference/data-formats/default-coordinates.md) file. |

## The two repositories

KKAFIO is actually two separate codebases that only communicate through a
JSON config file on disk and a subprocess boundary:

- **This repository** — the Python backend (`kkafio_cli.py` launcher and the
  `src/kkafio/` package) plus `interface.json`, which describes every task/option to the
  GUI. This is what actually does the work.
- **MXU-KKAFIO** (a separate repo, a fork of [MistEO/MXU](https://github.com/MistEO/MXU))
  — a Tauri + React desktop app that renders `interface.json` into a
  settings UI, lets the user configure tasks, writes their choices to disk,
  and spawns `kkafio_cli.exe run` as a subprocess when the user presses
  Start.

Neither repo imports code from the other. If you're debugging something and
you're not sure which side owns it, [How It Works](architecture/how-it-works.md)
walks through exactly where the boundary is at each step.
