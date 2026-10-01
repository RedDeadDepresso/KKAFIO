"""
Characterization harness for the KKAFIO CLI.

Rather than asserting what the code *should* do, this records what each CLI
invocation *does* — which task class it builds, what state that task is in when
`run()` is called, and with which arguments — and compares it with a stored
"golden" recording. It was written before the CLI was refactored onto the task
registry, and generated from the pre-registry code, so the goldens describe the
old behaviour.

The harness deliberately knows nothing about the registry: the mapping from task
name to class below is hand-written, so it is an independent check on it.

One normalization is applied on purpose. InstallContents used to receive
``--input`` as a ``run(folder_path=...)`` argument, and now receives it through
its config section like every other task. The folder the run actually uses is
the same either way, so for tasks with an ``input_path`` attribute the harness
records that *effective* folder (``folder_path`` if given, else ``input_path``)
and drops the two raw fields it is derived from. Likewise a ``skip_extract=False``
passed to ``run()`` is dropped, since that is the parameter's default.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
from collections import defaultdict
from pathlib import Path
from unittest import mock

# Independent of the registry on purpose (see module docstring).
TASK_CLASSES: dict[str, tuple[str, str]] = {
    "CreateBackup":            ("kkafio.tasks.create_backup", "CreateBackup"),
    "DownloadContents":        ("kkafio.tasks.download_contents", "DownloadContents"),
    "FilterConvertKKS":        ("kkafio.tasks.filter_convert_kks", "FilterConvertKKS"),
    "FilterDuplicateContents": ("kkafio.tasks.filter_duplicate_contents", "FilterDuplicateContents"),
    "DownloadMissingMods":     ("kkafio.tasks.download_missing_mods", "DownloadMissingMods"),
    "CompressCardsTextures":   ("kkafio.tasks.compress_cards_textures", "CompressCardsTextures"),
    "InstallContents":         ("kkafio.tasks.install_contents", "InstallContents"),
    "UninstallContents":       ("kkafio.tasks.uninstall_contents", "UninstallContents"),
    "GroupChara":              ("kkafio.tasks.group_chara", "GroupChara"),
    "UngroupChara":            ("kkafio.tasks.ungroup_chara", "UngroupChara"),
    "RenameChara":             ("kkafio.tasks.rename_chara", "RenameChara"),
    "ArchiveCards":            ("kkafio.tasks.archive_cards", "ArchiveCards"),
    "DeleteCards":             ("kkafio.tasks.delete_cards", "DeleteCards"),
    "ExportMods":              ("kkafio.tasks.export_mods", "ExportMods"),
}

# Attributes that are constant noise for our purposes (derived from the sandbox).
_SKIP_ATTRS = {"config", "file_manager", "game_path", "game_type", "is_sunshine", "prompt"}

# Tasks whose run() reads this key straight from the config section rather than
# from an attribute — so the section value at run() time is part of the behaviour.
_SECTION_READS = {
    "UngroupChara": ["InputPath"],
    "FilterConvertKKS": ["InputPath"],
    "FilterDuplicateContents": ["InputPath"],
}

CONFIG_FLAG_SKIP = {"--context-menu"}  # internal flag; exercised by hand-written cases


# ---------------------------------------------------------------------------
# Sandbox world
# ---------------------------------------------------------------------------

class World:
    """A fake Koikatsu install + an MXU config pointing at it."""

    def __init__(self, root: Path, tasks: list[dict] | None = None):
        self.root = Path(root)
        self.game = self.root / "game"
        for rel in ("UserData/chara/male", "UserData/chara/female", "UserData/coordinate",
                    "BepInEx", "mods"):
            (self.game / rel).mkdir(parents=True, exist_ok=True)
        self.links_file = self.root / "links.txt"
        self.links_file.write_text("https://example.com/a\nhttps://example.com/b\n", encoding="utf-8")
        self.guids_file = self.root / "guids.txt"
        self.guids_file.write_text("guid.one\nguid.two\n", encoding="utf-8")
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps({"instances": [{
            "name": "main",
            "globalOptionValues": {"GamePath": {"type": "folder", "path": str(self.game)}},
            "tasks": tasks or [],
        }]}), encoding="utf-8")


def opt_select(name: str) -> dict:
    return {"type": "select", "caseName": name}


def opt_checkbox(*names: str) -> dict:
    return {"type": "checkbox", "caseNames": list(names)}


def opt_text(text: str) -> dict:
    return {"type": "textarea", "text": text}


def opt_input(value: str) -> dict:
    return {"type": "input", "values": {"value": value}}


def opt_files(*paths: str) -> dict:
    return {"type": "file_list", "paths": list(paths)}


def opt_folder(path) -> dict:
    return {"type": "folder", "path": str(path)}


def opt_switch(value: bool) -> dict:
    return {"type": "switch", "value": value}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _scrub(text: str, root: str) -> str:
    """Make a string machine- and OS-independent: placeholders for the sandbox locations, '/' separators.

    Order matters: the config directory lives under HOME, which lives under the temp area.
    """
    from kkafio.core.constants import CONFIG_DIR
    text = text.replace(str(CONFIG_DIR), "<CONFIG_DIR>")
    home = os.environ.get("HOME", "")
    if home:
        text = text.replace(home, "<HOME>")
    return text.replace(root, "<ROOT>").replace("\\", "/")


def norm(obj, root: str):
    """JSON-safe, root-independent, type-preserving (Path vs str) view of `obj`."""
    if isinstance(obj, Path):
        return {"__path__": _scrub(str(obj), root)}
    if isinstance(obj, str):
        s = _scrub(obj, root)
        if len(s) > 200:
            return f"<str len={len(s)} sha1={hashlib.sha1(s.encode()).hexdigest()[:12]}>"
        return s
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, dict):
        return {str(k): norm(v, root) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [norm(v, root) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((norm(v, root) for v in obj), key=repr)
    return f"<{type(obj).__name__}>"


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self, root: Path, *, fail_on: str | None = None, fail_with=None, special_ok: bool = True,
                 batch: str | None = "list"):
        self.root = str(root)
        self.events: list[dict] = []
        self.fail_on = fail_on
        self.fail_with = fail_with      # callable -> exception to raise (default RuntimeError('boom'))
        self.special_ok = special_ok
        self.batch = batch            # "list" | None (follower) | "raise"

    # ---- task.run() stand-in ------------------------------------------------
    def make_fake_run(self, task_name: str):
        rec = self

        def fake_run(instance, *args, **kwargs):
            attrs = {k: v for k, v in vars(instance).items() if k not in _SKIP_ATTRS}
            run_kwargs = dict(kwargs)
            if args:                       # positional args -> name them if we can
                run_kwargs["__positional__"] = list(args)
            if run_kwargs.get("skip_extract") is False:   # spelled-out default == absent
                run_kwargs.pop("skip_extract")
            if "input_path" in attrs:      # see module docstring: effective input folder
                effective = run_kwargs.pop("folder_path", None)
                if effective is None:
                    effective = attrs["input_path"]
                attrs.pop("input_path")
                attrs["__effective_input_path__"] = effective
            section = instance.config.config_data[task_name]
            event = {
                "kind": "task",
                "task": task_name,
                "class": type(instance).__name__,
                "enabled": section.get("Enable"),
                "attrs": norm(attrs, rec.root),
                "run_kwargs": norm(run_kwargs, rec.root),
            }
            reads = _SECTION_READS.get(task_name)
            if reads:
                event["section_reads"] = {k: norm(section.get(k), rec.root) for k in reads}
            rec.events.append(event)
            if rec.fail_on == task_name:
                raise (rec.fail_with() if rec.fail_with else RuntimeError("boom"))
        return fake_run

    # ---- other side effects -------------------------------------------------
    def fake_input(self, *a, **k):
        self.events.append({"kind": "input"})
        return ""

    def fake_batch(self, command, path):
        self.events.append({"kind": "batch", "command": command, "path": norm(Path(path), self.root)})
        if self.batch is None:
            return None
        if self.batch == "raise":
            raise RuntimeError("batch failure")
        return [Path(self.root) / "sel" / "a.png", Path(self.root) / "sel" / "b.png"]

    def fake_special(self, task_name, params, stop):
        self.events.append({"kind": "special", "task": task_name, "params": norm(params, self.root)})
        return self.special_ok


def run_argv(world: World, argv: list[str], **recorder_kw) -> dict:
    """Parse `argv` (after a --config flag) with the real parser, dispatch it, record everything."""
    from kkafio import cli

    rec = Recorder(world.root, **recorder_kw)
    outcome: dict = {"exit": None, "error": None}
    stderr = io.StringIO()
    with contextlib.ExitStack() as stack:
        for name, (mod, cls) in TASK_CLASSES.items():
            klass = getattr(importlib.import_module(mod), cls)
            stack.enter_context(mock.patch.object(klass, "run", rec.make_fake_run(name)))
        stack.enter_context(mock.patch("builtins.input", rec.fake_input))
        stack.enter_context(mock.patch("kkafio.system.context_menu_batch.coordinate_batch", rec.fake_batch))
        stack.enter_context(mock.patch("kkafio.system.special_tasks.run_special_task", rec.fake_special))
        stack.enter_context(contextlib.redirect_stderr(stderr))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        try:
            parser = cli.build_parser()
            argv = [a.replace("{ROOT}", str(world.root)) for a in argv]
            args = parser.parse_args(["--config", str(world.config_path)] + argv)
            args.func(args)
        except SystemExit as e:
            outcome["exit"] = e.code
        except BaseException as e:            # noqa: BLE001 - we want to record anything
            outcome["error"] = type(e).__name__
    return {"outcome": outcome, "events": rec.events}


# ---------------------------------------------------------------------------
# Case enumeration (driven by the parser itself)
# ---------------------------------------------------------------------------

def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return dict(a.choices)
    raise AssertionError("no subparsers")


def _sample(action: argparse.Action) -> str:
    return f"S_{action.dest}"


def enumerate_cases(parser: argparse.ArgumentParser) -> dict[str, list[str]]:
    """{case_id: argv} covering every option of every task command, individually and combined."""
    cases: dict[str, list[str]] = {}
    for cmd, sp in _subparsers(parser).items():
        if cmd in ("list-instances", "run"):
            continue
        cases[f"{cmd}"] = [cmd]
        actions = [a for a in sp._actions if not isinstance(a, argparse._HelpAction)]
        for a in actions:
            if a.option_strings:
                if a.option_strings[0] in CONFIG_FLAG_SKIP:
                    continue
                if a.nargs == 0:                                   # store_true / store_false
                    for opt in a.option_strings:
                        cases[f"{cmd} {opt}"] = [cmd, opt]
                else:
                    values = list(a.choices) if a.choices else [_sample(a)]
                    for v in values:
                        cases[f"{cmd} {a.option_strings[0]}={v}"] = [cmd, a.option_strings[0], v]
                    for alias in a.option_strings[1:]:
                        cases[f"{cmd} {alias}={_sample(a)}"] = [cmd, alias, _sample(a)]
                    if not a.choices:                              # blank value: "" is not None
                        cases[f"{cmd} {a.option_strings[0]}=<blank>"] = [cmd, a.option_strings[0], ""]
            else:                                                   # positional (nargs="*")
                cases[f"{cmd} <one>"] = [cmd, "a.png"]
                cases[f"{cmd} <two>"] = [cmd, "a.png", "b.png"]

        # everything at once, twice: "true" forms and "false" forms (skipping conflicts)
        groups = [g._group_actions for g in sp._mutually_exclusive_groups]
        by_dest: dict[str, list[argparse.Action]] = defaultdict(list)
        for a in actions:
            if a.option_strings and a.option_strings[0] not in CONFIG_FLAG_SKIP:
                by_dest[a.dest].append(a)
        for label, pick in (("all-first", 0), ("all-last", -1)):
            argv, chosen_groups = [cmd], {}
            for dest, acts in by_dest.items():
                a = acts[pick]
                blocked = False
                for gi, members in enumerate(groups):
                    if a in members:
                        if gi in chosen_groups and chosen_groups[gi] != dest:
                            blocked = True
                        else:
                            chosen_groups[gi] = dest
                if blocked:
                    continue
                if a.nargs == 0:
                    argv.append(a.option_strings[0])
                else:
                    v = (list(a.choices)[pick] if a.choices else _sample(a))
                    argv += [a.option_strings[0], v]
            positional = [a for a in actions if not a.option_strings]
            if positional:
                argv += ["a.png", "b.png"]
            cases[f"{cmd} [{label}]"] = argv
    return cases


def handwritten_cases(world: World) -> dict[str, list[str]]:
    """Quirks the generic enumeration can't reach (file reads, fallbacks, context menu)."""
    lf, gf = "{ROOT}/links.txt", "{ROOT}/guids.txt"     # substituted by run_argv
    return {
        "download-contents --links=<file>": ["download-contents", "--links", lf],
        "export-mods --guids-file": ["export-mods", "--guids-file", gf],
        "export-mods --guids=<inline>": ["export-mods", "--guids", "a,b"],
        "export-mods --guids=<blank>": ["export-mods", "--guids", ""],
        "dmm --mods-dir only": ["download-missing-mods", "--mods-dir", "M"],
        "dmm --mods-dir + --input-mods-dir": ["download-missing-mods", "--mods-dir", "M", "--input-mods-dir", "I"],
        "dmm --mods-dir + --output-mods-dir": ["download-missing-mods", "--mods-dir", "M", "--output-mods-dir", "O"],
        "dmm --no-chara": ["download-missing-mods", "--no-chara"],
        "dmm --no-scene --no-coord": ["download-missing-mods", "--no-scene", "--no-coord"],
        "dmm --no-chara --no-scene --no-coord": ["download-missing-mods", "--no-chara", "--no-scene", "--no-coord"],
        "dmm --chara-dir=<blank>": ["download-missing-mods", "--chara-dir", ""],
        "dmm --telegram-chat-links=<blank>": ["download-missing-mods", "--telegram-chat-links", ""],
        "create-backup --mods --no-mods": ["create-backup", "--mods", "--no-mods"],
        "create-backup --mods --userdata --bepinex": ["create-backup", "--mods", "--userdata", "--bepinex"],
        "create-backup --no-mods --no-userdata --no-bepinex": ["create-backup", "--no-mods", "--no-userdata", "--no-bepinex"],
        "delete-cards --context-menu <one>": ["delete-cards", "--context-menu", "a.png"],
        "delete-cards --context-menu <two>": ["delete-cards", "--context-menu", "a.png", "b.png"],
        "delete-cards --context-menu <none>": ["delete-cards", "--context-menu"],
        "archive-cards --context-menu <one>": ["archive-cards", "--context-menu", "a.png"],
        "archive-cards --context-menu <one> --output-dir": ["archive-cards", "--context-menu", "a.png", "--output-dir", "OUT"],
        "archive-cards --context-menu <two>": ["archive-cards", "--context-menu", "a.png", "b.png"],
        "filter-convert-kks --convert --kks-action=Move": ["filter-convert-kks", "--convert", "--kks-action", "Move"],
        "install-contents --input + toggles": ["install-contents", "-i", "IN", "--no-mods", "--no-extract-archive", "--chara"],
    }


def handwritten_variants(world: World) -> dict[str, tuple[list[str], dict]]:
    """Cases that also change how the fakes behave: (argv, run_argv kwargs)."""
    return {
        "delete-cards --context-menu <one> [follower]": (["delete-cards", "--context-menu", "a.png"], {"batch": None}),
        "archive-cards --context-menu <one> [follower]": (["archive-cards", "--context-menu", "a.png"], {"batch": None}),
        "delete-cards --context-menu <one> [batch error]": (["delete-cards", "--context-menu", "a.png"], {"batch": "raise"}),
        "ungroup-chara [task fails]": (["ungroup-chara"], {"fail_on": "UngroupChara"}),
        "create-backup [task fails]": (["create-backup"], {"fail_on": "CreateBackup"}),
    }


# ---------------------------------------------------------------------------
# Pipeline ("run") cases
# ---------------------------------------------------------------------------

def _task(name, enabled=True, **opts) -> dict:
    return {"taskName": name, "enabled": enabled, "optionValues": opts}


def _special(name, **values) -> dict:
    return {"taskName": name, "enabled": True,
            "optionValues": {f"{name.removesuffix('__')}_OPTION__": {"type": "input", "values": values}}}


def configured_tasks() -> list[dict]:
    """Every task carrying NON-default values, as if the user had set them in the GUI.

    (The tasks are left disabled, like a task you only ever run from the context
    menu.) With the default world an unset CLI option and an explicit one are
    often indistinguishable; here every setting has a distinctive value, so
    "option not given -> config untouched" is observable.
    """
    f, sw = opt_folder, opt_switch
    def t(name, **opts): return _task(name, enabled=False, **opts)              # noqa: E704
    return [
        t("CreateBackup", BackupOutputPath=f("/cfg/backups"), BackupFilename=opt_input("cfg_backup"),
          BackupFolders=opt_checkbox("Mods", "BepInEx")),
        t("DownloadContents", DownloadOutputDir=f("/cfg/dl"), DownloadLinks=opt_text("cfg-link"),
          SkipDownloaded=sw(False)),
        t("FilterConvertKKS", DownloadsInputPath=f("/cfg/in"), Convert=sw(True), KKAction=opt_select("Move"),
          KKSAction=opt_select("Delete"), ExtractArchive=sw(False)),
        t("FilterDuplicateContents", DownloadsInputPath=f("/cfg/in"), UseCache=sw(False),
          FuzzyMatching=sw(True), KeepStrategy=opt_select("Newest"), DuplicateAction=opt_select("Delete")),
        t("DownloadMissingMods", ContentTypes=opt_checkbox("Chara"), SideloaderModpack=opt_select("All"),
          TelegramSource=opt_select("Both"), TelegramChatLinks=opt_text("cfg-chat"), UseCache=sw(False),
          InputModsDir=f("/cfg/in-mods"), OutputModsDir=f("/cfg/out-mods"), CharaDir=f("/cfg/chara"),
          SceneDir=f("/cfg/scene"), CoordDir=f("/cfg/coord")),
        t("CompressCardsTextures", DownloadsInputPath=f("/cfg/in"), KoiCardTexToolPath=f("/cfg/tool"),
          DeleteOriginalCards=sw(True)),
        t("InstallContents", DownloadsInputPath=f("/cfg/in"), InstallContentTypes=opt_checkbox("Mods"),
          ExtractArchive=sw(False)),
        t("UninstallContents", DownloadsInputPath=f("/cfg/in"), InstallContentTypes=opt_checkbox("Chara")),
        t("GroupChara", InputPath=f("/cfg/in"), GroupCharaIncludeSubfolders=sw(True)),
        t("UngroupChara", InputPath=f("/cfg/in"), DeleteEmptyFolders=sw(False)),
        t("RenameChara", InputPath=f("/cfg/in"), SkipAlreadyRenamed=sw(False), UpdateMetadata=sw(True),
          RenameFiles=sw(False)),
        t("ArchiveCards", ArchiveOutputPath=f("/cfg/archive"), ContentPaths=opt_files("cfg1.png", "cfg2.png"),
          CombinedArchive=sw(False), ArchiveFormat=opt_select("zip"), IncludeModpack=sw(True),
          IncludeCoordinates=sw(False), UseCache=sw(False), AutoResolve=sw(False),
          ModsDir=f("/cfg/mods"), CoordDir=f("/cfg/coords")),
        t("DeleteCards", ContentPaths=opt_files("cfg1.png", "cfg2.png"), CheckSharedMods=sw(False),
          IncludeCoordinates=sw(False), UseCache=sw(False), AutoResolve=sw(False), ModsDir=f("/cfg/mods"),
          CharaDir=f("/cfg/chara"), SceneDir=f("/cfg/scene"), CoordDir=f("/cfg/coords")),
        t("ExportMods", ExportOutputPath=f("/cfg/export"), Guids=opt_text("cfg.guid"),
          RenameToGuid=sw(False), UseCache=sw(False), ModsDir=f("/cfg/mods")),
    ]


def configured_cases(parser: argparse.ArgumentParser) -> dict[str, list[str]]:
    """Per command: no options, all "true" forms, all "false" forms, and each blank value —
    run against `configured_tasks`."""
    return {f"configured: {cid}": argv for cid, argv in enumerate_cases(parser).items()
            if " " not in cid or cid.endswith(("[all-first]", "[all-last]", "=<blank>"))}


def make_world(kind: str, root: Path) -> World:
    return World(root, configured_tasks() if kind == "configured" else None)


def pipeline_cases(root: Path) -> dict[str, tuple[list[dict], dict]]:
    """{case_id: (mxu task list, run_argv kwargs)} for `kkafio_cli run`."""
    same = root / "shared"
    other = root / "other"
    same.mkdir(exist_ok=True)
    other.mkdir(exist_ok=True)
    f = opt_folder
    work = root / "work"
    work.mkdir(exist_ok=True)
    # Every task gets explicit, existing folders: otherwise validation would create the platform's
    # default folders (C:\\KKAFIO\\... on Windows), which would touch the real disk and differ by OS.
    path_option = {"CreateBackup": "BackupOutputPath", "ArchiveCards": "ArchiveOutputPath",
                   "ExportMods": "ExportOutputPath", "FilterConvertKKS": "DownloadsInputPath",
                   "FilterDuplicateContents": "DownloadsInputPath", "CompressCardsTextures": "DownloadsInputPath",
                   "InstallContents": "DownloadsInputPath", "UninstallContents": "DownloadsInputPath",
                   "GroupChara": "InputPath", "UngroupChara": "InputPath", "RenameChara": "InputPath"}
    def _task(name, enabled=True, **opts):                                                  # noqa: E306
        if name in path_option and path_option[name] not in opts:
            opts[path_option[name]] = opt_folder(work)
        return {"taskName": name, "enabled": enabled, "optionValues": opts}
    fc = lambda **kw: _task("FilterConvertKKS", DownloadsInputPath=f(same), **kw)          # noqa: E731
    ic = lambda path=same, **kw: _task("InstallContents", DownloadsInputPath=f(path), **kw)  # noqa: E731
    all_names = list(TASK_CLASSES)
    return {
        "every task, interface order": ([_task(n) for n in all_names], {}),
        "every task, reversed": ([_task(n) for n in reversed(all_names)], {}),
        "disabled tasks are skipped": ([_task("CreateBackup", enabled=False), _task("UngroupChara")], {}),
        "unknown task is ignored": ([_task("NoSuchTask"), _task("UngroupChara")], {}),
        "FC before IC, same folder -> skip extract": ([fc(), ic()], {}),
        "IC before FC, same folder -> extract": ([ic(), fc()], {}),
        "FC before IC, different folder -> extract": ([fc(), ic(other)], {}),
        "FC before IC, same folder, FC no extract": ([fc(ExtractArchive=opt_switch(False)), ic()], {}),
        "FC before IC, same folder, IC no extract": ([fc(), ic(ExtractArchive=opt_switch(False))], {}),
        "FC disabled, IC enabled, same folder": ([_task("FilterConvertKKS", enabled=False, DownloadsInputPath=f(same)), ic()], {}),
        "special task then tasks": ([_special("__MXU_SLEEP__", sleep_time="3"), _task("UngroupChara")], {}),
        "special task fails -> exit 1, rest skipped": ([_special("__MXU_SLEEP__", sleep_time="3"), _task("UngroupChara")], {"special_ok": False}),
        "task raises -> exit 1, rest skipped": ([_task("UngroupChara"), _task("GroupChara")], {"fail_on": "UngroupChara"}),
        "no tasks at all": ([], {}),
    }
