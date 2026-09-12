# Project Structure

## Repository layout

```
KKAFIO-dev/
├── kkafio_cli.py                    # CLI entry point — argparse, subcommands, task dispatch
├── interface.json                   # Schema describing every task/option for the GUI (see doc 03)
├── register_context_menu.bat        # Adds right-click Explorer entries that call kkafio_cli
├── unregister_context_menu.bat      # Removes them
│
├── tools/                           # Standalone maintainer/setup scripts (not imported by kkafio_cli)
│   ├── build_modpack_index.py       # Regenerates assets/kkafio_modpack_index_*.json
│   └── download_gui.py              # Downloads the MXU-KKAFIO GUI release as KKAFIO.exe
│
├── assets/                          # Static data shipped with every release
│   ├── logo.png                     # App icon source (converted to kkafio.ico during CI build)
│   ├── kkafio_modpack_index_kk.json  # Pre-built Sideloader Modpack index for Koikatsu/Party
│   ├── kkafio_modpack_index_kks.json # Pre-built Sideloader Modpack index for Koikatsu Sunshine
│   └── xkcd_colors.json             # Named colour list used by the coordinate colour-fingerprint matcher
│
├── tasks/                           # One module per task (see below)
│   ├── base_task.py                 # BaseTask class + validate_input_path() helper
│   ├── create_backup.py
│   ├── download_contents.py
│   ├── download_missing_mods.py
│   ├── filter_convert_kks.py
│   ├── filter_duplicate_contents.py
│   ├── group_chara.py
│   ├── install_contents.py
│   ├── rename_chara.py
│   ├── ungroup_chara.py
│   ├── uninstall_contents.py
│   ├── archive_cards.py
│   └── delete_cards.py
│
├── utils/                           # Shared infrastructure, not task-specific
│   ├── config.py                    # Reads the GUI's JSON config, builds per-task config dicts (see doc 05)
│   ├── classifier.py                # get_card_type() / is_male() / is_coordinate() — PNG card sniffing
│   ├── chara_ops.py                 # GUID parsing, mod scanning, coordinate matching + their caches
│   ├── file_manager.py              # Copy/move/delete/archive/extract file operations, 7-Zip wrapper
│   ├── subprocess_utils.py          # Windows-console-encoding-safe subprocess.run/Popen wrappers
│   ├── logger.py                    # Structured logger; the GUI parses its stdout format live
│   ├── special_tasks.py             # Generic MXU automation primitives (sleep/notify/launch/power/etc.)
│   ├── kkd_session.py                # koikatsucards.com session cookie management
│   ├── telegram_config.py           # Telegram API credential storage
│   ├── password_dialog.py           # Native Windows input dialog (archive passwords, session cookies)
│   ├── llm_dialog.py                # Native Windows Copy/Paste dialog (Group Chara / Rename Chara)
│   ├── content_resolver.py          # Shared PNG-dispatch mixin for Install/Uninstall Contents
│   └── constants.py                 # Config directory / file path constants
│
├── docs/                            # You are here
└── wiki/                            # User-facing task documentation (mirrors GitHub Wiki pages)
```

## What is a "task"?

Every entry in the `tasks/` folder maps 1:1 to a task declared in
`interface.json`, and to a key in `utils.config._TASK_KEY` /
`_TASK_DEFAULTS`. A task is either:

- **A class subclassing `BaseTask`** (most of them) — instantiated with
  `(config, file_manager)`, configured by reading `self.config.<task_attr>`
  in `__init__`, and run via `.run()`. `BaseTask` only provides logging
  helpers (`log_start`/`log_done`) and `validate_input_path()`.
- **A pair of module-level functions**, `export()` + `process()` — used by
  `group_chara.py` and `rename_chara.py` in addition to their `GroupChara`/
  `RenameChara` classes, because the LLM round-trip dialog (see
  [`llm_dialog.py`](../utils/llm_dialog.py)) needs to build a prompt, hand
  control to the user, and then process whatever they paste back — the
  class's `run()` just calls both in sequence.

Adding a new task means touching **all** of these, since nothing here is
auto-discovered:

1. `tasks/your_task.py` — the actual implementation.
2. `utils/config.py` — add to `_TASK_KEY`, `_TASK_DEFAULTS`, and a
   `elif task_name == "YourTask":` branch in `_build_task_config()`.
3. `interface.json` — declare the task and its options (see
   [03 — interface.json](03-interface-json.md)).
4. `kkafio_cli.py` — add a `run_your_task()` wrapper, a `cmd_your_task()`
   handler, and an `argparse` subcommand, and register it in the
   `kkafio_task_map` dispatch dict used by `kkafio_cli run`.

There's no plugin system or auto-registration — this is deliberate given the
small, fixed set of tasks, but it's the reason step 2–4 above are easy to
forget when adding something new.

## `utils/` vs `tasks/`

The rule of thumb: if a piece of logic is used by more than one task, or
isn't really "a task" (parsing a PNG's embedded mod GUIDs, matching
coordinate colours, wrapping subprocess calls), it lives in `utils/`. Task
modules should mostly be: read config → validate input → call into `utils/`
→ log results.

`tasks/content_resolver.py` and `tasks/base_task.py` are the two exceptions
that live under `tasks/` instead of `utils/` — they're not generic utilities
usable by anything, they're specifically the shared internals of the task
classes (a mixin and a base class), so keeping them next to the classes that
use them is clearer than filing them under `utils/`.
