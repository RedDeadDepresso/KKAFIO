# coding:utf-8
"""
task_specs.py — The declaration of every KKAFIO task (see `kkafio.registry`).

To add a task: write the class in ``tasks/``, add its `TaskSpec` here, add it to
interface.json, and run ``tools/generate_config.py``. The subcommand, its
options, its dispatch from ``run`` and its config overrides all follow from the
spec — there is no CLI code to write.

Order matters in two places: `TASK_SPECS` is the order subcommands appear in
``--help``, and within a spec the options are in ``--help`` order.

Keep this module light: it is imported on every invocation, so it must never
import a task class (specs name them by ``"module:Class"`` string instead).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from kkafio.registry import Custom, TaskSpec, Toggle, Value


# ---------------------------------------------------------------------------
# Small helpers used by several specs
# ---------------------------------------------------------------------------

def _read_if_file(value: str) -> str:
    """``--links`` accepts either the URLs themselves or a text file holding them."""
    path = Path(value)
    return path.read_text(encoding="utf-8") if path.is_file() else value


def _content_type_toggles(*flags: str) -> tuple[Toggle, ...]:
    """The ``--chara/--no-chara`` ... pairs shared by install/uninstall-contents."""
    return tuple(Toggle(flag, key=flag.capitalize()) for flag in flags)


def _coalesce_context_menu(command: str, args: argparse.Namespace, overrides: dict[str, Any],
                           *, default_output_dir: bool = False) -> bool:
    """Explorer runs the context-menu entry once per selected file; merge those
    simultaneous invocations into a single combined run.

    Returns ``False`` for every invocation but the one that owns the batch
    (those must not run anything); for the owner, replaces ``ContentPaths`` with
    the whole batch. With ``default_output_dir``, an unset output folder becomes
    the common parent of the selection (the cards are being combined into one
    archive, so each card's own folder is the wrong default).
    """
    paths = overrides.get("ContentPaths")
    if not (getattr(args, "context_menu", False) and paths and len(paths) == 1):
        return True
    from kkafio.system.context_menu_batch import coordinate_batch
    batch = coordinate_batch(command, Path(paths[0]))
    if batch is None:
        return False                      # a sibling invocation is handling this whole batch
    overrides["ContentPaths"] = [str(p) for p in batch]
    if default_output_dir and "OutputPath" not in overrides:
        overrides["OutputPath"] = os.path.commonpath([str(Path(p).parent) for p in batch])
    return True


def _pause_if_context_menu(args: argparse.Namespace) -> None:
    """Keep the console window open after a context-menu run so the log can be read."""
    if getattr(args, "context_menu", False):
        input("\nPress Enter to close...")


_CONTEXT_MENU_HELP = (
    "Internal flag set by the context menu (via kkafio_setup.bat): coalesces multiple "
    "simultaneous Explorer-selection invocations (one per selected file) "
    "into a single combined run instead of processing each file separately"
)


# ---------------------------------------------------------------------------
# Tasks that need a bit more than a flat list of options
# ---------------------------------------------------------------------------

def _download_missing_mods_prepare(args: argparse.Namespace, overrides: dict[str, Any]) -> None:
    # --mods-dir is the legacy single-directory flag: it sets both directories
    # unless the specific flag is also given.
    input_dir = args.input_mods_dir or args.mods_dir or None
    output_dir = args.output_mods_dir or args.mods_dir or None
    if input_dir is not None:
        overrides["InputModsDir"] = input_dir
    if output_dir is not None:
        overrides["OutputModsDir"] = output_dir
    if args.no_chara or args.no_scene or args.no_coord:
        overrides["ContentTypes"] = [
            name for name, skipped in (("Chara", args.no_chara),
                                       ("Scene", args.no_scene),
                                       ("Coord", args.no_coord))
            if not skipped
        ]


def _filter_duplicates_prepare(args: argparse.Namespace, overrides: dict[str, Any]) -> None:
    # --input is a shorthand: one folder for every content type, unless the
    # specific --*-dir flag is also given.
    if args.input:
        for key in ("CharaDir", "SceneDir", "CoordDir", "ModsDir", "OverlaysDir"):
            overrides.setdefault(key, args.input)


def _export_mods_guids(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--guids", metavar="TEXT", default=None,
                       help="GUIDs to export — one per line or comma-separated. Accepts a Download "
                            "Missing Mods report pasted directly (bullets like '!', '\u2717', '+', '~' and "
                            "trailing '(...)' notes are stripped automatically). "
                            "(default: ExportMods.Guids from config)")
    group.add_argument("--guids-file", metavar="FILE", default=None,
                       help="Read GUIDs from a text file instead of passing them inline "
                            "(e.g. a saved kkafio_missing_mods_report.txt)")


def _export_mods_guids_override(args: argparse.Namespace) -> dict[str, Any]:
    guids = args.guids
    if args.guids_file:
        guids = Path(args.guids_file).read_text(encoding="utf-8")
    return {} if guids is None else {"Guids": guids}


def _delete_unused_mods_exceptions(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--exceptions", metavar="TEXT", default=None,
                       help="Exception list, one entry per line: 'Folder\\\\' (ends in a slash) protects a "
                            "folder inside mods, a name ending in .zip/.zipmod protects that file, anything "
                            "else is a GUID. (default: DeleteUnusedMods.ExceptionList from config)")
    group.add_argument("--exceptions-file", metavar="FILE", default=None,
                       help="Read the exception list from a text file instead")


def _delete_unused_mods_exceptions_override(args: argparse.Namespace) -> dict[str, Any]:
    text = args.exceptions
    if args.exceptions_file:
        text = Path(args.exceptions_file).read_text(encoding="utf-8")
    return {} if text is None else {"ExceptionList": text}


def _create_backup_folders(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("folder selection (each pair overrides its config flag)")
    for flag in ("mods", "userdata", "bepinex"):
        group.add_argument(f"--{flag}", dest=flag, action="store_true", default=False)
        group.add_argument(f"--no-{flag}", dest=f"no_{flag}", action="store_true", default=False)


def _create_backup_folders_override(args: argparse.Namespace) -> dict[str, Any]:
    # Enabling wins over disabling if both are given; neither leaves config alone.
    out: dict[str, Any] = {}
    for flag, key in (("mods", "mods"), ("userdata", "UserData"), ("bepinex", "BepInEx")):
        if getattr(args, flag):
            out[key] = True
        elif getattr(args, f"no_{flag}"):
            out[key] = False
    return out


_DUPLICATE_ACTIONS = {"move-rename": "Move & Rename", "move": "Move", "delete": "Delete"}


# ---------------------------------------------------------------------------
# The specs, in --help order
# ---------------------------------------------------------------------------

TASK_SPECS: tuple[TaskSpec, ...] = (

    TaskSpec(
        name="InstallContents", command="install-contents",
        help="Copy cards / mods / overlays into the game",
        target="kkafio.tasks.install_contents:InstallContents",
        options=(
            # Blank --input means "use the configured folder" here (unlike most tasks).
            Value("--input", "-i", key="InputPath", coerce=Path, ignore_blank=True,
                  metavar="DIR", default=None,
                  help="Folder to scan (default: InstallContents.InputPath from config)"),
            Toggle("extract-archive", key="ExtractArchive"),
            *_content_type_toggles("chara", "mods", "coords", "scenes", "overlays"),
        ),
    ),

    TaskSpec(
        name="UninstallContents", command="uninstall-contents",
        help="Remove cards / mods from the game",
        target="kkafio.tasks.uninstall_contents:UninstallContents",
        options=(
            Value("--input", "-i", key="InputPath", coerce=Path, metavar="DIR", default=None,
                  help="Folder to scan (default: UninstallContents.InputPath from config)"),
            *_content_type_toggles("chara", "mods", "coords", "scenes", "overlays"),
        ),
    ),

    TaskSpec(
        name="FilterConvertKKS", command="filter-convert-kks",
        help="Convert KKS cards and scenes to KK and/or sort each type into its own folder",
        target="kkafio.tasks.filter_convert_kks:FilterConvertKKS",
        options=(
            Value("--input", "-i", key="InputPath", coerce=Path, metavar="DIR", default=None),
            Toggle("convert", key="Convert",
                   help="Produce a KK-compatible copy of each KKS card and scene, saved next to its original "
                        "(then sorted/kept by --kk-action, not --kks-action)"),
            Value("--kk-action", key="KKAction", choices=["Keep", "Move", "Delete"], default=None,
                  help="What to do with KK/KKSP cards found (including converted KKS card/scene copies). Default: Keep"),
            Value("--kks-action", key="KKSAction", choices=["Keep", "Move", "Delete"], default=None,
                  help="What to do with the original KKS cards and scenes found. Default: Keep"),
            Toggle("extract-archive", key="ExtractArchive"),
        ),
    ),

    TaskSpec(
        name="DownloadContents", command="download-contents",
        help="Download character cards from db.bepis.moe or koikatsucards.com",
        target="kkafio.tasks.download_contents:DownloadContents",
        options=(
            Value("--links", key="Links", transform=_read_if_file, ignore_blank=True,
                  default=None, metavar="URLS_OR_FILE",
                  help="Newline-separated URLs, or path to a .txt file containing them "
                       "(default: DownloadContents.Links from config)"),
            Value("--output-dir", key="OutputDir", default=None, metavar="DIR",
                  help="Directory to save downloaded cards (default: DownloadContents.OutputDir from config)"),
            Toggle("skip-downloaded", key="SkipDownloaded",
                   help="Skip already-downloaded URLs (overrides config)",
                   off_help="Re-download even if previously downloaded (overrides config)"),
        ),
    ),

    TaskSpec(
        name="DownloadMissingMods", command="download-missing-mods",
        help="Find mods referenced by chara cards but missing locally, then download them",
        target="kkafio.tasks.download_missing_mods:DownloadMissingMods",
        # InputModsDir / OutputModsDir / ContentTypes are combined from several flags in the hook.
        prepare=_download_missing_mods_prepare,
        options=(
            Value("--input-mods-dir", default=None, metavar="DIR",
                  help="Mods directory whose installed mods count as present (default: game mods dir from config)"),
            Value("--output-mods-dir", default=None, metavar="DIR",
                  help="Mods directory that missing mods are downloaded into; its installed mods "
                       "also count as present (default: game mods dir from config)"),
            Value("--mods-dir", default=None, metavar="DIR",
                  help="Legacy shorthand: use DIR as both --input-mods-dir and --output-mods-dir"),
            Value("--chara-dir", key="CharaDir", ignore_blank=True, default=None, metavar="DIR",
                  help="Override the chara directory to scan (default: game chara dirs from config)"),
            Value("--scene-dir", key="SceneDir", ignore_blank=True, default=None, metavar="DIR",
                  help="Override the Studio scene directory to scan (default: game scene dir from config, if Studio is installed)"),
            Value("--coord-dir", key="CoordDir", ignore_blank=True, default=None, metavar="DIR",
                  help="Override the coordinate directory to scan (default: game coordinate dir from config)"),
            Value("--no-chara", action="store_true", default=False,
                  help="Skip scanning character cards for referenced mod GUIDs"),
            Value("--no-scene", action="store_true", default=False,
                  help="Skip scanning Studio scenes for referenced mod GUIDs"),
            Value("--no-coord", action="store_true", default=False,
                  help="Skip scanning coordinate cards for referenced mod GUIDs"),
            Toggle("use-cache", key="UseCache",
                   help="Use mods and chara cache to skip scanning (default: on)"),
            Toggle("open-report", key="OpenReport",
                   help="Open the report when mods are still missing or a download failed (default: on)",
                   off_help="Never open the report automatically"),
            Value("--modpack-mode", key="SideloaderModpack", ignore_blank=True, default=None,
                  choices=["Skip", "OnlyUsed", "All"],
                  help="How to handle Sideloader Modpack mods: "
                       "Skip=ignore modpack entirely, "
                       "OnlyUsed=download missing mods used by chara (default), "
                       "All=download all missing modpack mods"),
            Value("--telegram-source", key="TelegramSource", default=None,
                  choices=["No", "KoikatsuCards", "ChatLinks", "Both"],
                  help="Where to look for mods not covered by BetterRepack: "
                       "No=don't use Telegram (default), "
                       "KoikatsuCards=look up each GUID on koikatsucards.com, "
                       "ChatLinks=search the chats in --telegram-chat-links directly, "
                       "Both=try koikatsucards.com first, then ChatLinks for anything it couldn't find"),
            Value("--telegram-chat-links", key="TelegramChatLinks", default=None, metavar="LINKS",
                  help="Newline-separated Telegram chat/channel/group links to search "
                       "(one per line; add a topic ID like .../299 to search only that "
                       "forum topic; a trailing '# comment' is ignored). Only used when "
                       "--telegram-source is ChatLinks or Both. Default: the two example "
                       "chats shipped in the config."),
        ),
    ),

    TaskSpec(
        name="ExportMods", command="export-mods",
        help="Find zipmods by GUID and copy them into an output folder",
        target="kkafio.tasks.export_mods:ExportMods",
        options=(
            Value("--output", "-o", key="OutputPath", metavar="DIR", default=None,
                  help="Output directory to copy exported zipmods into (default: ExportMods.OutputPath from config)"),
            Custom(_export_mods_guids, _export_mods_guids_override),
            Toggle("rename-to-guid", key="RenameToGuid",
                   help="Rename each exported zipmod to [guid].zipmod (default: on)",
                   off_help="Keep each exported zipmod's original filename"),
            Toggle("use-cache", key="UseCache",
                   help="Use the mods cache to skip re-scanning unchanged zipmods (default: on)"),
            Value("--mods-dir", key="ModsDir", default=None, metavar="DIR",
                  help="Override the mods directory to search (default: game mods dir from config)"),
        ),
    ),

    TaskSpec(
        name="DeleteUnusedMods", command="delete-unused-mods",
        help="Send zipmods not used by any chara, scene or coordinate card to the recycle bin",
        target="kkafio.tasks.delete_unused_mods:DeleteUnusedMods",
        options=(
            Custom(_delete_unused_mods_exceptions, _delete_unused_mods_exceptions_override),
            Toggle("use-cache", key="UseCache",
                   help="Use the mods/card caches to skip re-scanning unchanged files (default: on)",
                   off_help="Disable cache and do a full scan"),
            Value("--mods-dir", key="ModsDir", default=None, metavar="DIR",
                  help="Override the mods directory (default: game mods dir from config)"),
            Value("--chara-dir", key="CharaDir", default=None, metavar="DIR",
                  help="Custom chara directory (default: game's chara folders)"),
            Value("--scene-dir", key="SceneDir", default=None, metavar="DIR",
                  help="Custom scene directory (default: game's Studio scene folder)"),
            Value("--coord-dir", key="CoordDir", default=None, metavar="DIR",
                  help="Custom coordinate directory (default: game's coordinate folder)"),
        ),
    ),

    TaskSpec(
        name="CompressCardsTextures", command="compress-cards-textures",
        help="Recompress the textures inside chara/coordinate cards with KoiCardTexTool",
        target="kkafio.tasks.compress_cards_textures:CompressCardsTextures",
        options=(
            Value("--input", "-i", key="InputPath", metavar="DIR", default=None,
                  help="Folder to scan (default: CompressCardsTextures.InputPath from config)"),
            Value("--tool-path", key="KoiCardTexToolPath", metavar="DIR", default=None,
                  help="Folder containing (or where to install) KoiCardTexTool.exe "
                       "(default: the input folder itself)"),
            Toggle("delete-original", key="DeleteOriginalCards",
                   help="Send the original card to the Recycle Bin once a compressed "
                        "[zip] version exists alongside it (default: off)",
                   off_help="Keep both the original and the compressed [zip] version"),
        ),
    ),

    TaskSpec(
        name="DeleteCards", command="delete-cards",
        help="Send character cards, coordinate cards, or Studio scenes and their associated mods/coords to the recycle bin",
        target="kkafio.tasks.delete_cards:DeleteCards",
        prepare=lambda args, ov: _coalesce_context_menu("delete-cards", args, ov),
        after_run=_pause_if_context_menu,
        options=(
            Value("content", key="ContentPaths", ignore_blank=True, nargs="*", metavar="CONTENT",
                  help="Character/coordinate/scene PNG paths (default: DeleteCards.ContentPaths from config)"),
            Toggle("check-shared-mods", key="CheckSharedMods",
                   help="Before deleting a zipmod, verify no other installed character/scene/coordinate "
                        "still uses it (default: on)",
                   off_help="Skip the shared-mod check — faster, but may delete mods other characters still need"),
            Toggle("auto-resolve", key="AutoResolve",
                   help="Auto-resolve mods and coord dirs (overrides config)",
                   off_help="Use explicit --mods-dir / --coord-dir instead"),
            Toggle("use-cache", key="UseCache",
                   help="Cache mod/coord directory scans (overrides config)",
                   off_help="Disable cache and do a full scan (overrides config)"),
            Toggle("include-coordinates", key="IncludeCoordinates",
                   help="When deleting a character card, also delete its matching coordinate "
                        "cards and their mods (default: on)",
                   off_help="Only delete the character card itself, leave its coordinates alone"),
            Value("--mods-dir", key="ModsDir", default=None, metavar="DIR",
                  help="Mods directory (only used when --no-auto-resolve)"),
            Value("--chara-dir", key="CharaDir", default=None, metavar="DIR",
                  help="Custom chara directory for the shared-mod check (default: game's chara folders)"),
            Value("--scene-dir", key="SceneDir", default=None, metavar="DIR",
                  help="Custom scene directory for the shared-mod check (default: game's Studio scene folder)"),
            Value("--coord-dir", key="CoordDir", default=None, metavar="DIR",
                  help="Coordinate directory (used for coordinate matching when --no-auto-resolve, "
                       "and for the shared-mod check; default: game's coordinate folder)"),
            Value("--context-menu", action="store_true", default=False, help=_CONTEXT_MENU_HELP + "."),
        ),
    ),

    TaskSpec(
        name="ArchiveCards", command="archive-cards",
        help="Bundle character cards, coordinate cards, or Studio scenes with their zipmods and matching coordinates",
        target="kkafio.tasks.archive_cards:ArchiveCards",
        prepare=lambda args, ov: _coalesce_context_menu("archive-cards", args, ov, default_output_dir=True),
        after_run=_pause_if_context_menu,
        options=(
            Value("content", key="ContentPaths", ignore_blank=True, nargs="*", metavar="CONTENT",
                  help="Character/coordinate/scene PNG paths (default: ArchiveCards.ContentPaths from config)"),
            Value("--format", key="Format", choices=["7z", "zip", "copy"], default=None,
                  help="Archive format, or 'copy' to copy the files flat into "
                       "a destination folder instead of archiving them "
                       "(default: ArchiveCards.Format from config)"),
            Toggle("auto-resolve", key="AutoResolve",
                   help="Auto-resolve mods and coord dirs (overrides config)",
                   off_help="Disable auto-resolution (overrides config)"),
            Toggle("use-cache", key="UseCache",
                   help="Cache mod/coord directory scans (overrides config)",
                   off_help="Disable cache and do a full scan (overrides config)"),
            Toggle("include-modpack", key="IncludeModpack",
                   help="Include zipmods from Sideloader Modpack folders (overrides config)",
                   off_help="Exclude Sideloader Modpack zipmods (overrides config)"),
            Toggle("combined", key="CombinedArchive",
                   help="Put all cards in one archive (overrides config)",
                   off_help="One archive per card (overrides config)"),
            Toggle("include-coordinates", key="IncludeCoordinates",
                   help="When archiving a character card, also bundle its matching coordinate "
                        "cards and their mods (default: on)",
                   off_help="Only bundle the character card itself, leave its coordinates out"),
            Value("--mods-dir", key="ModsDir", default=None, metavar="DIR",
                  help="Mods directory override (only used when --no-auto-resolve)"),
            Value("--coord-dir", key="CoordDir", default=None, metavar="DIR",
                  help="Coordinate directory override"),
            Value("--output-dir", key="OutputPath", default=None, metavar="DIR",
                  help="Output directory (default: same folder as chara card/scene)"),
            Value("--context-menu", action="store_true", default=False,
                  help=_CONTEXT_MENU_HELP + ", and defaults --output-dir to the common parent "
                                            "folder of the selection."),
        ),
    ),

    TaskSpec(
        name="UngroupChara", command="ungroup-chara",
        help="Move character cards from subfolders back to the top-level folder",
        target="kkafio.tasks.ungroup_chara:UngroupChara",
        options=(
            Value("--input", "-i", key="InputPath", coerce=Path, metavar="DIR", default=None,
                  help="Folder to ungroup (default: UngroupChara.InputPath from config)"),
            Toggle("delete-empty", key="DeleteEmptyFolders",
                   help="Remove empty subfolders after moving (overrides config)",
                   off_help="Keep empty subfolders (overrides config)"),
        ),
    ),

    TaskSpec(
        name="RenameChara", command="rename-chara",
        help="Translate character card names to English using an LLM "
             "(shows a native Copy/Paste dialog when it runs)",
        target="kkafio.tasks.rename_chara:RenameChara",
        options=(
            Value("--chara-dir", "--input", "-i", key="CharaDir", ignore_blank=True, metavar="DIR",
                  default=None,
                  help="Custom chara directory (default: the game's female and male chara folders; "
                       "--input is an alias)"),
            Toggle("skip-already-renamed", key="SkipAlreadyRenamed"),
            Toggle("update-metadata", key="UpdateMetadata",
                   help="Write translated names into card metadata (default: on)"),
            Toggle("rename-files", key="RenameFiles",
                   help="Also rename the PNG file to match the translated name"),
        ),
    ),

    TaskSpec(
        name="GroupChara", command="group-chara",
        help="Move character cards into series subfolders using an LLM "
             "(shows a native Copy/Paste dialog when it runs)",
        target="kkafio.tasks.group_chara:GroupChara",
        options=(
            Value("--chara-dir", "--input", "-i", key="CharaDir", ignore_blank=True, metavar="DIR",
                  default=None,
                  help="Custom chara directory (default: the game's female and male chara folders; "
                       "pointing it at the game's chara folder also uses female/ and male/; "
                       "--input is an alias)"),
            Value("--include-subfolders", key="IncludeSubfolders", action="store_true", default=None,
                  help="Include character cards from subfolders when scanning (overrides config)"),
        ),
    ),

    TaskSpec(
        name="FilterDuplicateContents", command="filter-duplicate-contents",
        help="Find and handle duplicate PNG cards and zipmod files",
        description=(
            "Scans the game's chara, scene, coordinate, mods and overlays folders (or the custom "
            "directories given) recursively for duplicate PNG cards and zipmod files. "
            "Duplicates are identified by content (not filename). "
            "By default they are moved to a _duplicates_/ subfolder and renamed. "
            "With --action delete they are sent to the recycle bin instead."
        ),
        target="kkafio.tasks.filter_duplicate_contents:FilterDuplicateContents",
        prepare=_filter_duplicates_prepare,
        options=(
            # Shorthand used by the context menu: scan DIR for every content type.
            Value("--input", "-i", metavar="DIR", default=None,
                  help="Scan this one folder for every selected content type, instead of the "
                       "per-type directories (shorthand for setting all five --*-dir options)"),
            *_content_type_toggles("chara", "mods", "coords", "scenes", "overlays"),
            Value("--chara-dir", key="CharaDir", ignore_blank=True, default=None, metavar="DIR",
                  help="Custom chara directory (default: the game's female and male chara folders)"),
            Value("--scene-dir", key="SceneDir", ignore_blank=True, default=None, metavar="DIR",
                  help="Custom scene directory (default: the game's Studio scene folder)"),
            Value("--coord-dir", key="CoordDir", ignore_blank=True, default=None, metavar="DIR",
                  help="Custom coordinate directory (default: the game's coordinate folder)"),
            Value("--mods-dir", key="ModsDir", ignore_blank=True, default=None, metavar="DIR",
                  help="Custom mods directory (default: the game's mods folder)"),
            Value("--overlays-dir", key="OverlaysDir", ignore_blank=True, default=None, metavar="DIR",
                  help="Custom overlays directory (default: the game's Overlays folder)"),
            Toggle("fuzzy", key="FuzzyChara",
                   help="Enable fuzzy matching for chara cards (overrides config)",
                   off_help="Disable fuzzy matching (overrides config)"),
            Value("--keep", key="Keep", metavar="STRATEGY", default=None,
                  choices=['None', 'Newest', 'Oldest', 'Biggest file size', 'Smallest file size',
                           'Last alphabetically', 'First alphabetically'],
                  help="Which copy to keep as the original (overrides config)"),
            Value("--action", key="DuplicateAction", transform=_DUPLICATE_ACTIONS.__getitem__,
                  choices=["move-rename", "move", "delete"], default=None,
                  help="What to do with duplicates (overrides config). 'move-rename' (default) "
                       "moves duplicates to _duplicates_/ and renames them after the kept copy "
                       "(or the first duplicate found, if --keep is 'None'), "
                       "with a number suffix. 'move' moves them to _duplicates_/ keeping their "
                       "original filenames. 'delete' sends them straight to the recycle bin."),
            Toggle("use-cache", key="UseCache",
                   help="Cache file hashes (and phashes, for fuzzy matching) to speed up "
                        "repeat scans (overrides config)",
                   off_help="Disable cache and re-hash every file (overrides config)"),
        ),
    ),

    TaskSpec(
        name="CreateBackup", command="create-backup",
        help="Create a 7-Zip backup of game folders",
        target="kkafio.tasks.create_backup:CreateBackup",
        options=(
            Value("--output", "-o", key="OutputPath", coerce=Path, metavar="DIR", default=None),
            Value("--filename", "-f", key="Filename", metavar="NAME", default=None),
            Custom(_create_backup_folders, _create_backup_folders_override),
        ),
    ),
)

TASKS_BY_NAME: dict[str, TaskSpec] = {spec.name: spec for spec in TASK_SPECS}
