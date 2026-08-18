# KKAFIO: Koikatsu Auto File I/O

<p align="center">
<img width="720" alt="KKAFIO preview" src="https://github.com/user-attachments/assets/5acc50f1-baf3-476f-b625-fc0405bf4e2f">
</p>

## Features

**1. Download Chara**

- Downloads character cards from [db.bepis.moe](https://db.bepis.moe) and [koikatsucards.com](https://koikatsucards.com).
- Enter one URL per line in the Download Links field. Supports individual card pages and listing pages.
- **Pagination formats** (use `|` as separator):

  | Format                                      | Behaviour                        |
  | ------------------------------------------- | -------------------------------- |
  | `https://db.bepis.moe/user/cards`           | Single listing page or card page |
  | `https://db.bepis.moe/user/cards \| all`    | All pages until empty            |
  | `https://db.bepis.moe/user/cards \| 1 \| 5` | Pages 1 through 5                |
  | `https://db.bepis.moe/user/cards \| 5 \| 1` | Pages 5 down to 1 (reverse)      |

- Lines starting with `#` are treated as comments and ignored.
- **Skip already downloaded** (on by default) uses a history file at `%APPDATA%/KKAFIO/download_history.json` to avoid re-downloading files.
- **koikatsucards.com session cookie** — downloading from koikatsucards.com requires logging in and copying the `kkd_session` cookie value from your browser's DevTools (Application → Cookies). The cookie expires every 7 days and must be updated when it does.

**2. Create Backup**

- Automatically creates a `.7z` archive containing:
  - `UserData`
  - `Mods` (excluding Sideloader Modpack)
  - `BepInEx`
- If an archive with the same name already exists it will be overwritten.

**3. Filter & Convert Chara**

- Functions similarly to [FlYiNGPoTAToChiP's KK_SunshineCardFilter](https://github.com/FlYiNGPoTAToChiP/KK_SunshineCardFilter).
- Given a folder, the task:
  - Finds all **KKS** (Koikatsu Sunshine) cards and moves them into `_KKS_card_/`
  - Finds all **KK / KKSP** cards and moves them into `_KK_card_/`
- Two independent conversion options:
  - **Convert KKS → KK**: produces KK-compatible copies in `_KKS_to_KK_/`
  - **Convert KK → KKS**: produces KKS-compatible copies in `_KK_to_KKS_/`
- **Optional:** Extracts ZIP / RAR / 7z archives before filtering.
- Has a separate archive password setting from Install Contents.

**4. Filter Duplicate Contents**

- Given a folder, scans recursively for duplicate `.png` cards and `.zipmod` files.
- Duplicates are detected by **content** (not filename):
  - PNG cards are fingerprinted using the character data payload embedded after the PNG IEND chunk, so two cards with different preview images are still caught as duplicates.
  - **Optional fuzzy matching** uses perceptual image hashing to detect updated cards with the same preview pose. Requires `pillow` and `imagehash`.
- Duplicates are moved into `_duplicates_/<category>/` subfolders:
  - `chara/` — KK / KKSP character cards
  - `coordinate/` — coordinate cards
  - `overlays/` — unclassified PNGs
  - `mods/` — zipmod files
- **Keep strategy** controls which copy of a duplicate set is kept in place: Newest, Oldest, Biggest file size (default), Smallest file size, Last alphabetically, First alphabetically, or None (move all copies).
- **Optional:** Send duplicates directly to the recycle bin instead of moving them.

**5. Install Contents**

- Given a folder containing chara cards, coordinate cards, overlays, and zipmod files, copies them into their respective game directories.
- Respects the configured **Game Type**: Koikatsu Sunshine installs KKS cards; Koikatsu / Koikatsu Party installs KK and KKSP cards. Cards of the wrong type are skipped with a log message.
- Scene cards (Studio) are installed only if the Studio `scene` folder is present.
- Extracts ZIP / RAR / 7z archives automatically (configurable).
- If both Filter & Convert Chara and Install Contents are enabled with the same input folder, archive extraction runs in the filter step only to avoid double-extracting.

**6. Uninstall Contents**

- Reverse of Install Contents: given the same folder, deletes the matching files from the game directories.
- **Note:** Only use this if you selected **Rename** or **Replace** under file conflicts when installing.
- **Warning:** Uninstall Contents does not check whether a zipmod or coordinate file is shared with other characters before deleting it. Removing a zipmod used by multiple cards will break all of them. Only use this task when you are certain the files being removed are exclusive to the cards you are deleting. Files can still be recovered from the Recycle Bin.

**7. Rename Chara**

- Translates character card names to English using an LLM.
- Workflow:
  1. Select an input folder and click **Copy**.  
     KKAFIO scans all PNG cards (recursively), builds a JSON mapping `{character_key: {lastname, firstname, nickname}}`, merges it with the prompt, and copies the result to the clipboard.
  2. Paste into your LLM of choice. The LLM fills in the English name for each key.
  3. Copy the LLM response and click **Paste** in KKAFIO to save it.
  4. Enable **Rename Chara**, click **Start** — KKAFIO writes the translated names into each card's internal metadata (`Parameter.lastname / firstname / nickname`).
- **Update card metadata** (on by default): writes the translated names into the card file.
- **Rename PNG files** (off by default): also renames the file on disk to `Lastname_Firstname.png`. Files in subfolders stay in their subfolder.
- **Skip already renamed** (on by default): skips cards whose name is already in the local cache.
- Results are cached in `kkafio_rename_cache.json` inside the input folder and reused across runs.
- The prompt is fully editable in the settings panel.
- **Recommended LLMs:** same as Group Chara (see below).

**8. Group Chara**

- Groups character cards into subfolders named after their series, using an LLM.
- Workflow:
  1. Select an input folder, customise the prompt if desired, and click **Copy**.  
     KKAFIO scans the folder, builds a JSON mapping `{character_key: ""}`, merges it with the prompt, and copies the result to the clipboard.
  2. Paste into your LLM of choice. The LLM fills in the series name for each key.
  3. Copy the LLM response and click **Paste** in KKAFIO to save it.
  4. Enable **Group Chara**, click **Start** — KKAFIO moves each card into `<input>/<series>/`.
- **Include subfolders** option lets you export already-sorted cards too (off by default to skip them).
- **Recommended LLMs:**
  - [DeepSeek](https://chat.deepseek.com) — highly recommended: large context window, excels at identifying characters from Chinese gacha games (Genshin Impact, Honkai Star Rail, Arknights). Enable **Expert** and **Smart Search** for better identification of obscure characters.
  - [Claude](https://claude.ai) — strong general-purpose identification, particularly good for Japanese anime and game characters.

**9. Ungroup Chara**

- Reverse of Group Chara: moves all cards from subfolders back to the top-level input folder.
- **Optional:** Deletes empty subfolders after moving (on by default).

**10. Archive Chara**

- Given a list of character cards, bundles each card with its matching coordinate files and required zipmods into a single archive.
- Coordinates are matched by colour fingerprint (not filename), so cards from different mod setups are handled correctly.
- Zipmods are found by GUID. Sideloader Modpack mods are excluded by default (see [Modpack Index](#modpack-index) below).
- **Auto-resolve**: if the card lives inside the game folder, mods and coordinate directories are inferred automatically. Override with **Custom Mods Directory** and **Custom Coordinate Directory** if needed.
- Output format: **7z** (default) or **zip**.
- **Combined archive** option puts all cards into one archive (default), or creates one archive per card.

**11. Delete Chara**

- Given a list of character cards, sends each card together with its matching coordinates and required zipmods to the recycle bin.
- Uses the same path resolution and coordinate matching as Archive Chara.
- Never touches Sideloader Modpack mods.
- **Warning:** Delete Chara does not check whether a zipmod or coordinate file is shared with other characters before deleting it. Removing a zipmod used by multiple cards will break all of them. Only use this task when you are certain the files being removed are exclusive to the cards you are deleting. Files can still be recovered from the Recycle Bin.

## Game Type

Configure the game type in the instance settings at the top of the task list:

| Game Type          | Card type installed | Cards skipped |
| ------------------ | ------------------- | ------------- |
| Koikatsu (default) | KK, KKSP            | KKS           |
| Koikatsu Party     | KK, KKSP            | KKS           |
| Koikatsu Sunshine  | KKS                 | KK, KKSP      |

The game type also determines which executable is launched by the **Run Game** button and affects scene card installation (Studio must be installed separately).

## Modpack Index

KKAFIO ships with `kkafio_modpack_index.json` — a pre-built index of all GUIDs in the Sideloader Modpack. Archive Chara and Delete Chara use this index to instantly identify which required mods are already covered by the modpack (and therefore don't need to be bundled or deleted).

If a GUID is not in the index, KKAFIO falls back to scanning the local mods folder automatically.

To regenerate the index after updating the Sideloader Modpack, run:

```
python build_modpack_index.py "C:/KK Party/mods"
```

The updated `kkafio_modpack_index.json` is written to the mods folder. Copy it next to `kkafio_cli.exe` or commit it to the repository to ship it with the next release.

## Context Menu Integration

Run `register_context_menu.bat` to add a **KKAFIO** submenu to the Windows Explorer right-click menu. It uses the selected file/folder as an argument; remaining settings are taken from the first configuration instance.

**On folders and folder backgrounds:**

| Entry                     | Action                                       |
| ------------------------- | -------------------------------------------- |
| Install Contents          | `install-contents --input <folder>`          |
| Uninstall Contents        | `uninstall-contents --input <folder>`        |
| Filter / Convert Chara    | `filter-convert-chara --input <folder>`      |
| Filter Duplicate Contents | `filter-duplicate-contents --input <folder>` |
| Rename Chara (export)     | `rename-chara --export --input <folder>`     |
| Group Chara (export)      | `group-chara --export --input <folder>`      |
| Ungroup Chara             | `ungroup-chara --input <folder>`             |

**On PNG files (single or multi-select):**

| Entry         | Action                           |
| ------------- | -------------------------------- |
| Archive Chara | `archive-chara <selected files>` |
| Delete Chara  | `delete-chara <selected files>`  |

Run `unregister_context_menu.bat` to remove all entries.

## CLI Usage

`kkafio_cli` exposes every task as a subcommand. Arguments override config; omit them to use config defaults.

```
kkafio_cli run                                    # run all enabled tasks from config

kkafio_cli download-chara [--links URLS_OR_FILE] [--output-dir DIR]
                          [--skip-downloaded | --no-skip-downloaded]
                          [--kkd-session COOKIE]

kkafio_cli create-backup  [--output DIR] [--filename NAME]
                          [--mods | --no-mods]
                          [--userdata | --no-userdata]
                          [--bepinex | --no-bepinex]

kkafio_cli filter-convert-chara [--input DIR]
                                [--convert-kks | --no-convert-kks]
                                [--convert-kk  | --no-convert-kk]
                                [--extract-archive | --no-extract-archive]

kkafio_cli filter-duplicate-contents [--input DIR]
                             [--fuzzy | --no-fuzzy]
                             [--keep STRATEGY]
                             [--delete | --no-delete]

kkafio_cli install-contents   [--input DIR]
                           [--extract-archive | --no-extract-archive]

kkafio_cli uninstall-contents [--input DIR]

kkafio_cli rename-chara    [--input DIR] [--export]
                           [--response JSON_OR_FILE]
                           [--skip-already-renamed | --no-skip-already-renamed]
                           [--update-metadata | --no-update-metadata]
                           [--rename-files | --no-rename-files]

kkafio_cli group-chara     [--input DIR] [--export] [--include-subfolders]
                           [--response JSON_OR_FILE]

kkafio_cli ungroup-chara   [--input DIR]
                           [--delete-empty | --no-delete-empty]

kkafio_cli archive-chara   [CHARA ...] [--output-dir DIR]
                           [--format 7z|zip]
                           [--combined | --no-combined]
                           [--include-modpack | --no-include-modpack]
                           [--auto-resolve | --no-auto-resolve]
                           [--use-cache | --no-use-cache]
                           [--mods-dir DIR] [--coord-dir DIR]

kkafio_cli delete-chara    [CHARA ...]
                           [--auto-resolve | --no-auto-resolve]
                           [--use-cache | --no-use-cache]
                           [--mods-dir DIR] [--coord-dir DIR]

# Global options (all commands):
kkafio_cli --config PATH --instance N <command>
```

## Requirements

- 7-Zip installed and on PATH.
- If running from source: [uv](https://docs.astral.sh/uv/getting-started/installation/) installed.

## Installation and Usage

Download the latest release, extract it, and run `KKAFIO.exe`.

To run from source:

1. Clone or download this repository.
2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
3. Run `uv sync` in the repository folder.
3. Run `uv run download_gui.py` to download the GUI.
4. Open KKAFIO.exe and configure settings to your preference.
5. Press **Start**.

## Known Issues

- Any `.png` that cannot be classified as a chara card or coordinate is treated as an overlay. Files in the wrong category can be found in `UserData/Overlays` — sort by date to identify and remove them.
- Studio scene cards are skipped if Studio is not installed (the `UserData/Studio/scene` folder does not exist).

## Acknowledgements

- [Kiramei](https://github.com/Kiramei) for the logger. Original [here](https://github.com/Kiramei/blue_archive_auto_script/blob/master/core/utils.py).
- [FlYiNGPoTAToChiP](https://github.com/FlYiNGPoTAToChiP) for KK_SunshineCardFilter and the chara/coordinate distinction method.
- [Evaanxd](https://www.patreon.com/user?u=3125561) and [GaryuX](https://www.patreon.com/GaryuX) for the [Ryuko Matoi card and image](https://www.pixiv.net/en/artworks/77738576).
