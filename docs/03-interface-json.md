# interface.json

`interface.json` is the single source of truth for what the GUI renders:
every task, every option, how they're grouped, and the built-in presets. It
follows the same schema shape as [MaaFramework's ProjectInterface
format](https://github.com/MaaXYZ/MaaFramework), because the GUI (MXU) was
originally built to run MaaFramework automation projects, not KKAFIO. Large
parts of the schema exist purely because the GUI expects them, not because
KKAFIO uses them — this doc calls those out explicitly so you don't go
looking for code that reads them.

## Top-level structure

```jsonc
{
  "interface_version": ...,
  "name": "KKAFIO",              // used to derive the config filename — see doc 05
  "label": ..., "title": ..., "version": "2.0.1",
  "github": ..., "description": ..., "icon": ...,

  "agent":      { ... },          // NOT used to launch kkafio_cli — see below
  "controller": [ ... ],          // NOT used by KKAFIO at all — see below
  "resource":   [ ... ],          // NOT used by KKAFIO at all — see below

  "option":  { "OptionId": { ... }, ... },   // every option, keyed by ID
  "group":   [ { "name": ..., "label": ..., "default_expand": ... }, ... ],
  "task":    [ { "name": "TaskName", "option": ["OptionId", ...], ... }, ... ],
  "preset":  [ { "name": ..., "task": [{"name": "TaskName"}, ...] }, ... ]
}
```

### `agent` / `controller` / `resource` — present but unused

These three top-level keys exist because the GUI's schema loader expects a
complete MaaFramework `ProjectInterface`. KKAFIO doesn't use any of them:

- `controller` normally describes how to attach to a game window for
  automated input — irrelevant here, KKAFIO only does file I/O.
- `resource` normally points at MaaFramework pipeline/resource folders.
- `agent`'s `child_exec`/`child_args` look like they might be what launches
  the CLI, but they aren't — the actual subprocess launch is a bespoke Rust
  function, `resolve_cli()` in `kkafio.rs`, which looks for
  `kkafio_cli.exe` next to the GUI executable (falling back to
  `python kkafio_cli.py`) and ignores this field entirely. See
  [04 — The MXU GUI](04-mxu-gui.md).

If you're trying to trace "how does the GUI know what to run", the answer
is not in `interface.json` — it's in `kkafio.rs`.

## `option` entries

Every option the GUI can render is declared once under `"option"`, keyed by
an ID that's referenced by name from one or more tasks' `"option"` arrays
(the same option can be shared across multiple tasks — e.g. `InputPath` is
reused by most tasks rather than each task declaring its own copy).

```jsonc
"InputPath": {
  "type": "folder",
  "name": "input_path",              // camelCase form used in some GUI code paths
  "label": "Input Directory",
  "description": "...",
  "default": "",
  "pipeline_override": { "InputPath": "{input_path}" }   // see the warning below
}
```

### Option types

| `type` | Stored shape (`OptionValue`) | Rendered as |
|---|---|---|
| `folder` | `{type: "folder", path: string}` | Folder picker |
| `file_list` | `{type: "file_list", paths: string[]}` | Multi-file picker (e.g. Content Paths) |
| `textarea` | `{type: "textarea", text: string}` | Multi-line text box (prompts, links) |
| `switch` | `{type: "switch", value: boolean}` | Single on/off toggle |
| `checkbox` | `{type: "checkbox", caseNames: string[]}` | Row of multi-select tabs/chips — e.g. `BackupFolders` |
| `select` | `{type: "select", caseName: string}` | Single-choice dropdown |
| `input` | `{type: "input", values: {[key]: string}}` | Free-text field(s) |

Every one of these is read on the Python side by `_extract_opt()` in
`utils/config.py`, which has one `if t == "...":` branch per type. **If you
add a new option `type`, you must add a matching branch there too**, or
Python will silently see `None` for every value of that option.

### ⚠️ `pipeline_override` doesn't do what it looks like it does

Every option declares a `pipeline_override`, e.g.:

```jsonc
"GroupCharaPrompt": {
  "type": "textarea",
  ...
  "pipeline_override": { "Prompt": "{prompt}" }
}
```

In a real MaaFramework project, this is what actually wires an option's
value into the automation pipeline. **KKAFIO does not use this mechanism at
all.** Python reads option values straight out of the raw, unresolved
`optionValues` dict using the option's **own ID** (`"GroupCharaPrompt"`),
not whatever `pipeline_override` says it maps to (`"Prompt"`):

```python
# utils/config.py, inside _build_task_config()
v = _extract_opt(opt_values, "GroupCharaPrompt")   # the option's ID
if v is not None: cfg["Prompt"] = v                # KKAFIO's own internal key
```

So `pipeline_override` in this codebase is decorative — kept because it's
part of the schema shape the GUI expects and because it doubles as
documentation of "this option ends up as config key X", but changing or
deleting it has **no effect on Python's behaviour**. If an option's value
isn't reaching the task that uses it, the bug is almost always a mismatch
between the option's ID and the string literal passed to `_extract_opt()`
in `utils/config.py` — check there, not `pipeline_override`.

## `task` entries

```jsonc
{
  "name": "GroupChara",                     // must match utils.config._TASK_KEY
  "label": "🗂️ Group Characters",
  "entry": "GroupChara",
  "default_check": false,
  "description": "...",
  "group": ["card-management"],             // which collapsible section(s) it appears under
  "option": ["InputPath", "GroupCharaIncludeSubfolders", "GroupCharaPrompt"]
}
```

`"option"` is an **ordered** list of option IDs — this controls both
rendering order in the GUI and which options are actually sent for this
task (an option not listed here won't show up even though it's declared
under the top-level `"option"` dict).

`"name"` must exactly match a key in `_TASK_KEY` / `_TASK_DEFAULTS` in
`utils/config.py`, and the `elif task_name == "...":` string in
`_build_task_config()`. Nothing enforces this at schema level — a typo here
means the task silently never runs (Python only reads task names it
recognizes; see step 5 in [02 — How It Works](02-how-it-works.md)).

## `group` entries

Purely a GUI concern — collapsible sections in the task list
(`card-management`, `duplicate-handling`, `backup`). A task can belong to
multiple groups; Python never sees this field.

## `preset` entries

Named bundles of tasks that can be enabled all at once from the GUI (⚡ All
Tasks, 📥 Install & Filter, etc.). Each preset just lists task names in the
order they should run — enabling a preset flips those tasks' `enabled` flag
and sets their order in the GUI's state; it's resolved into the same
`task_order` list described in doc 02 like any other manually-enabled set of
tasks. Presets are a GUI-side convenience; there's no separate "preset" concept
on the Python side at all.
