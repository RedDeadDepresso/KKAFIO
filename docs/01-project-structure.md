# Project Structure

## Repository layout

```
KKAFIO-dev/
├── kkafio_cli.py                    # Thin launcher (PyInstaller entry point; also `python kkafio_cli.py`)
├── interface.json                   # Schema describing every task/option for the GUI (see doc 03)
├── kkafio_setup.bat                 # Menu launcher: create default folders / register / unregister context menu / delete config+folders
│
├── scripts/                         # Scripts run by kkafio_setup.bat (also runnable standalone)
│   ├── create_default_task_folders.bat  # Creates C:\KKAFIO\{Backups,Downloads,Archived Cards,Exported Mods}
│   ├── register_context_menu.bat        # Thin wrapper (bypasses PowerShell's execution policy) that runs register_context_menu.ps1
│   ├── register_context_menu.ps1        # Adds right-click Explorer entries that call kkafio_cli (unregisters first, then prompts for language + task selection)
│   ├── unregister_context_menu.bat      # Removes them without registering new ones
│   └── delete_config_and_task_folders.bat # Deletes %APPDATA%\KKAFIO and C:\KKAFIO (with confirmation)
│
├── tools/                           # Standalone maintainer/setup scripts (not imported by kkafio_cli)
│   ├── build_modpack_index.py       # Regenerates assets/kkafio_modpack_index_*.json
│   ├── download_gui.py              # Downloads the MXU-KKAFIO GUI release as KKAFIO.exe
│   ├── generate_config.py           # Regenerates src/kkafio/core/config.py's interface.json-driven sections (see doc 03)
│   └── sync_i18n_keys.py            # Adds/removes assets/i18n/*.json keys to match interface.json (see doc 03)
│
├── assets/                          # Static data shipped with every release
│   ├── icon.png                     # App icon source (converted to kkafio.ico during CI build)
│   ├── kkafio_modpack_index_kk.json  # Pre-built Sideloader Modpack index for Koikatsu/Party
│   ├── kkafio_modpack_index_kks.json # Pre-built Sideloader Modpack index for Koikatsu Sunshine
│   ├── xkcd_colors.json             # Named colour list used by the coordinate colour-fingerprint matcher
│   └── i18n/                        # GUI translation strings, one file per language (see doc 03)
│       ├── en_us.json
│       ├── ja_jp.json
│       ├── ko_kr.json
│       ├── ru_ru.json
│       ├── zh_cn.json
│       └── zh_tw.json
│
├── src/kkafio/                     # The Python package (src layout; `uv sync` installs it editable)
│   ├── cli.py                       # CLI implementation — argparse, subcommands, task dispatch, main()
│   ├── registry.py                  # TaskSpec + option types + run_task() (see "How CLI options work")
│   ├── task_specs.py                # Declaration of every task: name, class, CLI options
│   ├── __main__.py                  # `python -m kkafio`
│   │
│   ├── tasks/                       # One module per task (see below)
│   │   ├── base_task.py             # BaseTask class + validate_input_path() helper
│   │   ├── content_resolver.py      # Shared PNG-dispatch mixin for Install/Uninstall Contents
│   │   ├── archive_cards.py
│   │   ├── compress_cards_textures.py
│   │   ├── create_backup.py
│   │   ├── delete_cards.py
│   │   ├── download_contents.py
│   │   ├── download_missing_mods.py
│   │   ├── export_mods.py
│   │   ├── filter_convert_kks.py
│   │   ├── filter_duplicate_contents.py
│   │   ├── group_chara.py
│   │   ├── install_contents.py
│   │   ├── rename_chara.py
│   │   ├── ungroup_cards.py
│   │   └── uninstall_contents.py
│   │
│   ├── core/                        # Configuration, logging, paths, file operations
│   │   ├── config.py                # Reads the GUI's JSON config, builds per-task config dicts (see doc 05)
│   │   ├── errors.py                # KKAFIOError / UserError / ConfigError / InputError / ToolNotFoundError / TaskFailedError
│   │   ├── paths.py                 # Shipped-file paths (APP_DIR / ASSETS_DIR, frozen vs. source) and the per-user config dir (CONFIG_DIR)
│   │   ├── logger.py                # Structured logger; the GUI parses its stdout format live
│   │   └── file_manager.py          # Copy/move/delete/archive/extract file operations, 7-Zip wrapper
│   │
│   ├── cards/                       # Card (PNG) domain logic
│   │   ├── classifier.py            # get_card_type() / is_male() / is_coordinate() — PNG card sniffing
│   │   ├── parsing.py               # Reading mod GUIDs out of card files (chara / scene / coordinate)
│   │   ├── png_guids.py             # Per-folder GUID collection + its caches
│   │   ├── mods.py                  # Zipmod scanning, mods cache, Sideloader Modpack index
│   │   ├── outfits.py               # Coordinate outfit digests + matching a chara's outfits to coordinates
│   │   ├── cache_io.py              # Atomic JSON writes / file fingerprints shared by the caches
│   │   ├── kks_scene.py             # Converting a KKS Studio scene to KK
│   │   ├── msgpack_min.py           # Byte-exact MessagePack codec used by the scene converter
│   │   ├── chara_key.py
│   │   └── scene_version.py
│   │
│   ├── services/                    # External accounts, credentials and mod sources
│   │   ├── kkd_session.py           # koikatsucards.com session cookie management
│   │   ├── telegram_config.py       # Telegram API credential storage
│   │   ├── telegram_links.py        # Parsing Telegram links / the chat list (pure string handling)
│   │   ├── telegram_mods.py         # Searching Telegram chats and downloading mods (teleget)
│   │   └── http_mod_sources.py      # BetterRepack downloads + the koikatsucards.com GUID index
│   │
│   └── system/                      # OS-facing helpers (mostly Windows)
│       ├── special_tasks.py         # Generic MXU automation primitives (sleep/notify/launch/power/etc.)
│       ├── subprocess_utils.py      # Windows-console-encoding-safe subprocess.run/Popen wrappers
│       ├── password_dialog.py       # Input dialog (archive passwords, session cookies)
│       ├── llm_dialog.py            # Copy/Paste dialog (Group Chara / Rename Chara)
│       └── context_menu_batch.py    # Coalesces multi-select Explorer context-menu invocations
│
├── docs/                            # You are here
└── wiki/                            # User-facing task documentation (mirrors GitHub Wiki pages)
```

## What is a "task"?

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
  [`llm_dialog.py`](../src/kkafio/system/llm_dialog.py)) needs to build a prompt, hand
  control to the user, and then process whatever they paste back — the
  class's `run()` just calls both in sequence.

Every task is declared once, as a `TaskSpec` in
[`task_specs.py`](../src/kkafio/task_specs.py) (machinery in
[`registry.py`](../src/kkafio/registry.py)). The spec names the task, points at
its class by `"module:Class"` string, and lists its command-line options; the
subcommand, its `--help`, its dispatch from `kkafio_cli run` and its config
overrides all follow from that. Nothing is auto-discovered from the `tasks/`
folder, so adding a task still means touching a few places — but the CLI is no
longer one of them:

1. `src/kkafio/tasks/your_task.py` — the actual implementation.
2. `src/kkafio/task_specs.py` — add a `TaskSpec` to `TASK_SPECS`.
3. `src/kkafio/core/config.py` — add to `_TASK_KEY`, `_TASK_DEFAULTS`, and a
   `elif task_name == "YourTask":` branch in `_build_task_config()`.
4. `interface.json` — declare the task and its options (see
   [03 — interface.json](03-interface-json.md)), then run
   `python tools/generate_config.py`.

`tests/test_registry.py` fails if the task names in these places drift apart,
and the characterization tests in `tests/` fail if a new subcommand has no
recorded behaviour — regenerate them deliberately with
`uv run python tests/generate_goldens.py` and review the diff.

### Raising errors

Raise the exception that says who can fix the problem (all in
[`core/errors.py`](../src/kkafio/core/errors.py)):

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

### How CLI options work

Each option in a spec is either a `Value` (takes an argument), a `Toggle` (a
`--x` / `--no-x` pair) or, for the odd ones, a `Custom`. An option that is
given replaces the matching key of the task's config section **before the task
is constructed**, so the task sees it exactly as if the GUI had configured it;
an option that isn't given changes nothing (the GUI's value, or the default,
stands). A few options need a small hook for logic that spans several flags —
e.g. `download-missing-mods --mods-dir` setting both of its directories, or
the context-menu batching of `delete-cards` / `archive-cards`.

Task classes are imported only when they run (the specs refer to them by
string), so `--help` and every parse stay cheap. That also means PyInstaller
can't discover them by scanning imports; the build uses
`--collect-submodules kkafio` for this reason.

## `tasks/` vs. the rest of the package

The rule of thumb: if a piece of logic is used by more than one task, or
isn't really "a task" (parsing a PNG's embedded mod GUIDs, matching
coordinate colours, wrapping subprocess calls), it lives outside `tasks/`,
in the subpackage that fits it:

| Subpackage | Put it here when it is… |
|---|---|
| `core/` | configuration, logging, paths, or generic file operations |
| `cards/` | about the contents of card PNGs (classification, GUIDs, outfit matching) |
| `services/` | tied to an external account, credential store or remote mod source (BetterRepack, Telegram, koikatsucards.com) |
| `system/` | an OS-facing helper (dialogs, job objects, subprocess quirks, MXU automation) |

Task modules should mostly be: read config → validate input → call into the
shared subpackages → log results.

`tasks/content_resolver.py` and `tasks/base_task.py` live under `tasks/`
rather than elsewhere — they're not generic utilities usable by anything,
they're specifically the shared internals of the task classes (a mixin and a
base class), so keeping them next to the classes that use them is clearer.

## Running from source

The package uses a `src/` layout. After `uv sync` it is installed in editable
mode, so any of these work:

```
uv run kkafio <command>            # console script (kkafio.cli:main)
uv run python -m kkafio <command>
uv run python kkafio_cli.py <command>   # the launcher; also works without installing
```

Imports are always `kkafio.<subpackage>.<module>` (for example
`from kkafio.core.logger import logger`). Shipped data files are found through
`kkafio.core.paths` — never by walking up from `__file__`.
