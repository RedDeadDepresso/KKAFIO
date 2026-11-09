# Adding a task

A task is a class in `src/kkafio/tasks/`, a `TaskSpec` that declares its command line, and an entry in
`interface.json` that the GUI draws. Nothing is discovered automatically, so these have to agree with each other.
[Anatomy of a task](../architecture/tasks.md) explains the pieces; this page is the checklist.

## 1. Write the task

Create `src/kkafio/tasks/your_task.py` with a class that subclasses `BaseTask`: it is built with
`(config, file_manager)`, reads its settings from `self.config.<task_attr>` in `__init__`, and does its work in
`run()`. Keep it thin (read config, validate input, call shared code, log results) and put anything another task
could use in the subpackage that fits it: see [Repository layout](../architecture/repository-layout.md#tasks-vs-the-rest-of-the-package).

Raise the exception that says who can fix the problem (`ConfigError` / `InputError` / `ToolNotFoundError` for the user,
`TaskFailedError` for a failed job) and never exit the process: see
[Raising errors](../architecture/tasks.md#raising-errors).

## 2. Declare the command line

Add a `TaskSpec` to `TASK_SPECS` in `src/kkafio/task_specs.py`, in the order you want in `--help`:

```python
TaskSpec(
    name="YourTask", command="your-task",
    help="One line for --help",
    target="kkafio.tasks.your_task:YourTask",
    options=(
        Value("--input", "-i", key="InputPath", coerce=Path, metavar="DIR", default=None,
              help="Folder to scan (default: YourTask.InputPath from config)"),
        Toggle("use-cache", key="UseCache", help="Reuse the cache", off_help="Rescan everything"),
    ),
),
```

`name` is the task's name everywhere else (`interface.json`, the saved GUI config, log labels). Each option's `key` is
the task's **config key**, not the GUI option's ID; the two often differ. Step 4 generates
`_TASK_DEFAULTS["YourTask"]` in `core/config.py`, which lists every key. A given option replaces that key before the
task is built, so the task sees it as if the GUI had set it. See [How CLI options work](../architecture/tasks.md#how-cli-options-work).

## 3. Declare it for the GUI

In `interface.json`:

- Add the task's options under `"option"` (or reuse existing ones such as `InputPath`). Give each a `type`, a
  `default` (or `default_case`), `label` / `description` written as `$dotted.key` references, and a
  `pipeline_override` that names the config key it ends up in. The generator reads that name.
- Add the task under `"task"`: `name` (exactly the `TaskSpec` name), `label`, `entry`, `default_check`,
  `description`, `group`, and the **ordered** `option` list of option IDs it shows.
- Optionally add it to a `"preset"`.

Copy a similar existing task and change it. The fields are explained in [interface.json](../reference/interface-json.md).

## 4. Run the generators

```
python tools/generate_config.py
python tools/sync_i18n_keys.py
```

`generate_config.py` rewrites `src/kkafio/core/config.py` (task keys, defaults, the per-task option mapping, default
folders and accessor properties). Read any warning it prints: it means an option's config key could not be worked
out. Never edit that file by hand; see the [warning](../reference/interface-json.md#pipeline_override-doesnt-do-what-it-looks-like-it-does).

`sync_i18n_keys.py` adds the new `$dotted.key` entries to every `assets/i18n/*.json` file, seeded with the English
text, and reports them as needing a real translation. Translate them in all six languages. Text that Python
draws itself (dialogs) belongs in `assets/i18n/runtime/` instead; see
[interface.json](../reference/interface-json.md#text-drawn-by-python-itself-assetsi18nruntime).

## 5. Register it with the tests

- Add the task to `TASK_CLASSES` in `tests/helpers/cli_harness.py`. That map is hand-written on purpose, as an
  independent check on the registry, and `test_task_names_agree_everywhere` fails until the registry,
  `interface.json`, `core/config.py` and the harness all list the same tasks.
- Regenerate the recordings and read the diff (the top-level `--help` now lists your command, and your task gets
  its own entries):

  ```
  uv run python tests/helpers/generate_goldens.py
  git diff tests/golden/
  ```

- Write tests for the task itself. `tests/helpers/card_factory.py` builds small synthetic cards, scenes and zipmods so
  you don't need game files. See [Testing](testing.md).

## 6. Optional: the Explorer context menu

To offer the task in the right-click menu, add an entry to the `$FolderTasks` catalog in
`scripts/register_context_menu.ps1` (`Id`, `Cli`, and an `ArgsTemplate` in which `{P}` stands for the clicked path)
and a label for it in each language of `$Labels` in the same file.

## 7. Document it

- A page in `docs/tasks/`, listed in `zensical.toml` and in the table in `docs/tasks/index.md`.
- A row in the README's task table.
- An entry in `CHANGELOG.md`.

## 8. Check everything

```
uv run pytest
python tools/generate_config.py --check
python tools/sync_i18n_keys.py --check
```

## Adding an option to an existing task

The same steps apply, in a smaller form:

1. Declare the option in `interface.json` and add its ID to the task's `option` list.
2. Add a matching `Value` / `Toggle` to the task's `TaskSpec` if it should also exist on the command line.
3. Run both generators and translate the new keys.
4. Read the option in the task through `self.config.<task_attr>`.
5. Regenerate the goldens (the task's `--help` changed) and review the diff.
6. Update the task's page in `docs/tasks/` and the changelog.

A new option **type** is a bigger change: it needs a renderer in the GUI and a branch in `_extract_opt()` (see
[The MXU GUI](../architecture/mxu-gui.md#extending-the-gui-vs-extending-python)).
