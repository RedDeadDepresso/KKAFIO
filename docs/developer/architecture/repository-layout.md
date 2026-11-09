# Repository layout

```
KKAFIO-dev/
├── kkafio_cli.py                    # Thin launcher (PyInstaller entry point; also `python kkafio_cli.py`)
├── interface.json                   # Schema describing every task/option for the GUI (see the interface.json reference)
├── kkafio_setup.bat                 # Menu launcher: create default folders / enable clipboard history / register / unregister context menu / delete config+folders
│
├── scripts/                         # Scripts run by kkafio_setup.bat (also runnable standalone)
│   ├── create_default_task_folders.bat  # Creates C:\KKAFIO\{Backups,Downloads,Archived Cards,Exported Mods}
│   ├── enable_clipboard_history.bat     # Turns on Windows clipboard history (Win+V) for the current user
│   ├── register_context_menu.bat        # Thin wrapper (bypasses PowerShell's execution policy) that runs register_context_menu.ps1
│   ├── register_context_menu.ps1        # Adds right-click Explorer entries that call kkafio_cli (unregisters first, then prompts for language + task selection)
│   ├── unregister_context_menu.bat      # Removes them without registering new ones
│   └── delete_config_and_task_folders.bat # Deletes %APPDATA%\KKAFIO and C:\KKAFIO (with confirmation)
│
├── tools/                           # Standalone maintainer/setup scripts (not imported by kkafio_cli)
│   ├── build_default_coordinate.py     # Records a folder of default coordinates in assets/indices/default_coordinate.json (Group Coordinates leaves those coordinates alone)
│   ├── download_gui.py              # Downloads the MXU-KKAFIO GUI release as KKAFIO.exe
│   ├── generate_config.py           # Regenerates src/kkafio/core/config.py's interface.json-driven sections (see the interface.json reference)
│   └── sync_i18n_keys.py            # Adds/removes assets/i18n/*.json keys to match interface.json (see the interface.json reference)
│
├── assets/                          # Static data shipped with every release
│   ├── icon.png                     # App icon source (converted to kkafio.ico during CI build)
│   ├── indices/                     # Static indices and reference data
│   │   └── default_coordinate.json  # Optional, written by tools/build_default_coordinate.py: outfit digests of the default coordinates Group Coordinates skips
│   └── i18n/                        # GUI translation strings, one file per language (see the interface.json reference)
│       ├── runtime/                 # Same languages: text for Python's own dialogs, via core/i18n.py (see the interface.json reference)
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
│   │   ├── compress_card_textures.py
│   │   ├── create_backup.py
│   │   ├── delete_cards.py
│   │   ├── download_contents.py     # Download Contents: parses the links, picks the client + downloader per site (the sites live in services/content_sources/)
│   │   ├── download_missing_mods.py
│   │   ├── export_mods.py
│   │   ├── filter_convert_kks.py
│   │   ├── filter_duplicate_contents.py
│   │   ├── group_chara.py
│   │   ├── group_coordinates.py
│   │   ├── group_scenes.py
│   │   ├── install_contents.py
│   │   ├── organize_library.py
│   │   ├── rename_chara.py
│   │   ├── review_similar_chara.py
│   │   ├── ungroup_cards.py
│   │   └── uninstall_contents.py
│   │
│   ├── core/                        # Configuration, logging, paths, file operations, session_store.py (JSON state files under sessions/)
│   │   ├── config.py                # Reads the GUI's JSON config, builds per-task config dicts (see the Configuration reference)
│   │   ├── errors.py                # KKAFIOError / UserError / ConfigError / InputError / ToolNotFoundError / TaskFailedError
│   │   ├── paths.py                 # Shipped-file paths (APP_DIR / ASSETS_DIR, frozen vs. source) and the per-user config dir (CONFIG_DIR)
│   │   ├── folder_names.py          # safe_folder_name(): a series / character / author name made safe to create as a folder (Group Characters, Coordinates, Scenes)
│   │   ├── logger.py                # Structured logger; the GUI parses its stdout format live
│   │   └── file_manager.py          # Copy/move/delete/archive/extract file operations, 7-Zip wrapper
│   │
│   ├── cards/                       # Card (PNG) domain logic
│   │   ├── classifier.py            # get_card_type() / is_male() / is_coordinate() — PNG card sniffing
│   │   ├── cache_io.py              # Atomic JSON writes / file fingerprints shared by the caches
│   │   ├── mods/                    # Which zipmods a card needs, and which are installed
│   │   │   ├── parsing.py           # Reading mod GUIDs out of card files (chara / scene / coordinate)
│   │   │   ├── png_guids.py         # Per-folder GUID collection + its caches
│   │   │   ├── zipmods.py           # Zipmod scanning, mods cache, resolving a card's mods/coordinate folders
│   │   │   └── shared_mods.py       # "Is this zipmod still used by a remaining card?" check
│   │   ├── scenes/                  # Studio scene handling
│   │   │   ├── scene_version.py     # KK vs KKS scene detection
│   │   │   ├── kks_scene.py         # Converting a KKS Studio scene to KK
│   │   │   └── msgpack_min.py       # Byte-exact MessagePack codec used by the scene converter
│   │   └── chara/                   # Identifying and comparing characters
│   │       ├── chara_key.py         # "last | first | nick | personality | hair" key for LLM matching
│   │       ├── chara_key_cache.py   # per-card key cache for Group / Rename Characters
│   │       ├── hint_language.py     # Language of the character-index hints
│   │       ├── prompt_language.py   # Built-in LLM prompts per language (Prompt Language option)
│   │       ├── hair_color.py        # RGB -> hair colour name used in the character key
│   │       ├── similarity.py        # pHash + leader clustering for cover comparison
│   │       └── outfits.py           # Coordinate outfit digests + matching a chara's outfits to coordinates
│   │
│   ├── indices/                     # Lookup indices (downloaded on demand and cached in CONFIG_DIR, or shipped in assets/); "no help" when unavailable
│   │   ├── chara.py                 # Character lookup for Group / Rename Characters: gacha first, then AGV
│   │   ├── gacha.py                 # Gacha-game character index (format: reference/data-formats/gacha-index)
│   │   ├── agv.py                   # Anime / visual novel / game character + series index (Bangumi Archive)
│   │   ├── text.py                  # Text normalisation helpers for the two character indices
│   │   ├── scene.py                 # pepper-scene-index: scene hash -> author / year (Group Scenes)
│   │   ├── password.py              # kk-password-scraper index: archive filename -> passwords
│   │   ├── mod.py                   # kk-mod-scraper index: mod GUID -> Telegram message link (Download Missing Mods)
│   │   ├── modpack.py               # kk-mod-scraper index: Sideloader Modpack: mod GUID -> download URLs (the path inside the modpack is the URL's path)
│   │   └── author.py                # Known scene authors and their aliases (assets/indices/author.json), for the Scene library (Organize Library)
│   │
│   ├── services/                    # External accounts, credentials and mod sources
│   │   ├── content_sources/         # Where Download Contents gets files from (see the task's docstring for the link formats)
│   │   │   ├── net.py / history.py / transfer.py   # Shared: retry + HTTP client, download history, streaming downloads and paged listings
│   │   │   ├── card_size.py         # 320x180 / 252x352-ratio test that identifies KK cards and scenes
│   │   │   ├── bepis.py  gofile.py  catbox.py  mega.py  gdrive.py   # One module per site
│   │   │   ├── session_flow.py      # Shared "stored / env / ask for the cookie, validate, save" flow for the session.py files below
│   │   │   ├── koikatsucards/       # koikatsucards.com: downloader (__init__) + session.py (kkd_session cookie)
│   │   │   ├── pixiv/               # pixiv.net: downloader (__init__) + session.py (PHPSESSID cookie)
│   │   │   └── pawchive/            # parsing (links, posts) · access (throttle, session use) · files · posts · downloader · session.py (session cookie, t1 files only)
│   │   └── mod_sources/             # Where Download Missing Mods fetches mods from
│   │       ├── betterrepack.py      # BetterRepack downloads + the shared HTTP client for Download Missing Mods
│   │       ├── filehosts.py         # Downloads one mod from a Google Drive / Mega link of the mod index, verifying its manifest GUID
│   │       └── telegram/            # Telegram support for Download Missing Mods
│   │           ├── config.py        # Telegram API credential storage
│   │           ├── links.py         # Parsing Telegram links / the chat list (pure string handling)
│   │           └── mods.py          # Searching Telegram chats and downloading mods (teleget)
│   │
│   ├── dialogs/                     # CustomTkinter dialogs (run in-process, block the calling thread)
│   │   ├── common.py                # Shared window behaviour: always-on-top, foreground, centering, show-and-wait · format_size
│   │   ├── password_dialog.py       # Input dialog (archive passwords, session cookies)
│   │   ├── llm.py                   # Copy/Paste dialog (Group Chara / Rename Chara)
│   │   ├── library/                 # The Organize Library window and its tabs; imports run one way: organize_library -> dialogs/library/<tab> -> model.py, and <tab> -> tasks/group_*
│   │   │   ├── __init__.py          # Re-exports the public names (library_dialog, LibraryTab, CharaItem, ...)
│   │   │   ├── model.py             # What the window shows and is told, no GUI: CharaItem, LibraryModel, DialogText, LibraryTab, file-rename helpers · the trash and Archive queue views (cards queued with right-click > Archive / F11 are handed to Archive Cards when the window closes)
│   │   │   ├── window.py            # The window: draws the tabs, knows nothing about characters / coordinates / scenes; library_dialog() hosts them
│   │   │   ├── mods_panel.py        # The mod dependencies sidebar's data, no GUI: which mods a card uses (cached per file) and which are installed (ModsLookup, CardMods)
│   │   │   ├── mods_sidebar.py      # The collapsible mod dependencies sidebar itself (one canvas, card read on a worker thread)
│   │   │   ├── common.py            # What the three tabs share: folder checks, building items, ranking suggestions, the wording
│   │   │   ├── characters.py        # Character tab: scan, suggest, move into series folders
│   │   │   ├── coordinates.py       # Outfit tab: scan, suggest, move into character folders
│   │   │   └── scenes.py            # Scene tab: scan, suggest, move into author folders
│   │   └── similar_chara.py         # Review dialog (Review Similar Characters)
│   │
│   └── system/                      # OS-facing helpers (mostly Windows)
│       ├── special_tasks.py         # Generic MXU automation primitives (sleep/notify/launch/power/etc.)
│       ├── subprocess_utils.py      # Windows-console-encoding-safe subprocess.run/Popen wrappers
│       └── context_menu_batch.py    # Coalesces multi-select Explorer context-menu invocations
│
├── tests/                           # pytest suite (see Testing)
│   ├── conftest.py                  # Redirects the config dir and home into a temp sandbox before kkafio is imported
│   ├── helpers/                     # Synthetic card factory, CLI harness, golden generator
│   ├── golden/                      # Recorded CLI behaviour that tests/characterization replays
│   └── cards/ characterization/ core/ dialogs/ indices/ tasks/   # The tests, grouped like src/kkafio
│
├── .github/workflows/
│   ├── build.yml                    # Release build (see Releasing)
│   └── docs.yml                     # Publishes this documentation site
│
├── docs/                            # Documentation site source (Zensical): user guide + this developer guide
└── zensical.toml                    # Documentation site configuration
```

## `tasks/` vs. the rest of the package

The rule of thumb: if a piece of logic is used by more than one task, or
isn't really "a task" (parsing a PNG's embedded mod GUIDs, matching
coordinate colours, wrapping subprocess calls), it lives outside `tasks/`,
in the subpackage that fits it:

| Subpackage | Put it here when it is… |
|---|---|
| `core/` | configuration, logging, paths, or generic file operations |
| `cards/` | about the contents of card PNGs (classification, GUIDs, outfit matching) |
| `services/` | tied to an external account, credential store or remote mod source (site sessions, Telegram, BetterRepack, the Download Contents sites) |
| `indices/` | a lookup table: a remote index downloaded on demand and cached (characters, scenes, archive passwords, mods, the Sideloader Modpack), or a shipped one (scene authors) |
| `dialogs/` | a CustomTkinter window the user interacts with (prompts, libraries, review screens) |
| `system/` | an OS-facing helper (job objects, subprocess quirks, MXU automation, context-menu batching) |

Task modules should mostly be: read config → validate input → call into the
shared subpackages → log results.

`tasks/content_resolver.py` and `tasks/base_task.py` live under `tasks/`
rather than elsewhere — they're not generic utilities usable by anything,
they're specifically the shared internals of the task classes (a mixin and a
base class), so keeping them next to the classes that use them is clearer.
