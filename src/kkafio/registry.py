# coding:utf-8
"""
registry.py — What a KKAFIO task *is*, declared once.

A task is its class in ``tasks/`` plus one `TaskSpec`: the spec
names the task, says where its class lives, and lists its command-line
options. Each option knows both how to be parsed *and* which key of the task's
config section it sets, so the subcommand, its ``--help``, its dispatch from
``kkafio_cli run`` and its config overrides all come from that one declaration.

    TaskSpec("UngroupChara", "ungroup-chara", "Move cards out of subfolders",
             "kkafio.tasks.ungroup_chara:UngroupChara",
             options=(Value("--input", "-i", key="InputPath", coerce=Path, ...),
                      Toggle("delete-empty", key="DeleteEmptyFolders", ...)))

The specs themselves live in `kkafio.task_specs`. This module holds only the
machinery and must stay cheap to import — `--help` builds every task's parser
without importing any task (task classes are imported lazily by `run_task`,
because they pull in heavy dependencies).

How CLI overrides work
----------------------
A CLI option that is given replaces the matching value in the task's config
section *before* the task is constructed, so the task sees it exactly as if the
GUI had configured it. Options that are not given change nothing.
"""

from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

def _dest_of(flags: Sequence[str], explicit: str | None = None) -> str:
    """The attribute argparse will store this option under."""
    if explicit:
        return explicit
    if not flags[0].startswith("-"):                    # positional
        return flags[0]
    long_flag = next((f for f in flags if f.startswith("--")), flags[0])
    return long_flag.lstrip("-").replace("-", "_")


class Option:
    """One command-line option: how to parse it, and what it overrides."""

    def add(self, parser: argparse.ArgumentParser) -> None:
        raise NotImplementedError

    def overrides(self, args: argparse.Namespace) -> dict[str, Any]:
        """{config key: value} for this option — empty if it wasn't given."""
        return {}


@dataclass(frozen=True, init=False)
class Value(Option):
    """A value-taking option or positional (also store_true flags, via ``action=``).

    key           config key it overrides; ``None`` for parse-only options
    coerce        applied to a given value before it goes into config (e.g. ``Path``)
    transform     applied first (e.g. read a file's text)
    ignore_blank  treat a blank/empty value as "not given". Off by default because
                  ``--input ""`` is, for most tasks, a real (if odd) override.
    kwargs        passed straight to ``add_argument`` (help, metavar, choices, ...)
    """

    flags: tuple[str, ...]
    key: str | None = None
    coerce: Callable[[Any], Any] | None = None
    transform: Callable[[Any], Any] | None = None
    ignore_blank: bool = False
    kwargs: dict[str, Any] = field(default_factory=dict)

    def __init__(self, *flags: str, key: str | None = None,
                 coerce: Callable[[Any], Any] | None = None,
                 transform: Callable[[Any], Any] | None = None,
                 ignore_blank: bool = False, **kwargs: Any):
        # frozen dataclass: assign through object.__setattr__
        object.__setattr__(self, "flags", flags)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "coerce", coerce)
        object.__setattr__(self, "transform", transform)
        object.__setattr__(self, "ignore_blank", ignore_blank)
        object.__setattr__(self, "kwargs", kwargs)

    @property
    def dest(self) -> str:
        return _dest_of(self.flags, self.kwargs.get("dest"))

    def add(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(*self.flags, **self.kwargs)

    def overrides(self, args: argparse.Namespace) -> dict[str, Any]:
        if self.key is None:
            return {}
        value = getattr(args, self.dest, None)
        if value is None or (self.ignore_blank and not value):
            return {}
        if self.transform is not None:
            value = self.transform(value)
        if self.coerce is not None:
            value = self.coerce(value)
        return {self.key: value}


@dataclass(frozen=True)
class Toggle(Option):
    """A ``--name`` / ``--no-name`` pair that overrides a boolean config key.

    Neither given -> ``None`` -> config untouched; otherwise ``True`` / ``False``.
    """

    name: str                    # "use-cache" -> --use-cache / --no-use-cache
    key: str
    help: str | None = None
    off_help: str | None = None

    @property
    def dest(self) -> str:
        return self.name.replace("-", "_")

    def add(self, parser: argparse.ArgumentParser) -> None:
        group = parser.add_mutually_exclusive_group()
        group.add_argument(f"--{self.name}", dest=self.dest, action="store_true",
                           default=None, help=self.help)
        group.add_argument(f"--no-{self.name}", dest=self.dest, action="store_false",
                           help=self.off_help)

    def overrides(self, args: argparse.Namespace) -> dict[str, Any]:
        value = getattr(args, self.dest, None)
        return {} if value is None else {self.key: bool(value)}


@dataclass(frozen=True)
class Custom(Option):
    """Escape hatch for options that don't fit `Value` / `Toggle`.

    ``add_fn(parser)`` declares the argparse arguments (keep it byte-for-byte
    what you want in ``--help``); ``overrides_fn(args)`` returns the
    {config key: value} it produces.
    """

    add_fn: Callable[[argparse.ArgumentParser], None]
    overrides_fn: Callable[[argparse.Namespace], dict[str, Any]] | None = None

    def add(self, parser: argparse.ArgumentParser) -> None:
        self.add_fn(parser)

    def overrides(self, args: argparse.Namespace) -> dict[str, Any]:
        return self.overrides_fn(args) if self.overrides_fn else {}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskSpec:
    """Everything the CLI needs to know about one task.

    name         the task's name everywhere else: interface.json, the MXU
                 config, the key of its config section, log/traceback labels
    command      its subcommand, e.g. ``ungroup-chara``
    target       ``"package.module:ClassName"`` — imported only when it runs
    options      command-line options, in ``--help`` order
    prepare      optional hook ``(args, overrides) -> bool | None`` run after the
                 options are collected and before the task is built; may edit
                 ``overrides``; returning ``False`` skips the run entirely
    after_run    optional hook ``(args) -> None`` run after a successful run
    """

    name: str
    command: str
    help: str
    target: str
    options: tuple[Option, ...] = ()
    description: str | None = None
    prepare: Callable[[argparse.Namespace, dict[str, Any]], bool | None] | None = None
    after_run: Callable[[argparse.Namespace], None] | None = None

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        for option in self.options:
            option.add(parser)

    def overrides_from(self, args: argparse.Namespace) -> dict[str, Any]:
        collected: dict[str, Any] = {}
        for option in self.options:
            collected.update(option.overrides(args))
        return collected

    def load_class(self) -> type:
        module_name, _, class_name = self.target.partition(":")
        return getattr(importlib.import_module(module_name), class_name)


def run_task(spec: TaskSpec, config, file_manager,
             overrides: dict[str, Any] | None = None, **run_kwargs: Any) -> None:
    """Apply `overrides` to the task's config section, build the task, run it.

    `run_kwargs` are passed to the task's ``run()`` (only InstallContents takes
    any: ``skip_extract``).
    """
    if overrides:
        config.config_data[spec.name].update(overrides)
    spec.load_class()(config, file_manager).run(**run_kwargs)
