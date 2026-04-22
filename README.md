# KKAFIO: Koikatsu Auto File I/O
<p align="center">
<img width="720" alt="KKAFIO preview" src="https://github.com/user-attachments/assets/5acc50f1-baf3-476f-b625-fc0405bf4e2f">
</p>

## Features

**1. Create Backup**
- Automatically creates a `.7z` archive containing:
  - `UserData`
  - `Mods` (excluding Sideloader Modpack)
  - `BepInEx`
- If an archive with the same name already exists it will be overwritten.

**2. Filter & Convert KKS**
- Functions similarly to [FlYiNGPoTAToChiP's KK_SunshineCardFilter](https://github.com/FlYiNGPoTAToChiP/KK_SunshineCardFilter).
- Given a folder, the task:
  - Finds all KKS (Koikatsu Sunshine) cards and moves them into `_KKS_card_`
  - **Optional:** If conversion is enabled, converts KKS cards to KK format and stores them in `_KKS_to_KK_`
  - **Optional:** Extracts ZIP / RAR / 7z archives before filtering
- Has a separate archive password setting from Install Chara.

**3. Filter Duplicates**
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

**4. Install Chara**
- Given a folder containing chara cards, coordinate cards, overlays, and zipmod files, copies them into their respective game directories.
- Extracts ZIP / RAR / 7z archives automatically (configurable).
- If both Filter & Convert KKS and Install Chara are enabled with the same input folder, archive extraction runs in the KKS filter step only to avoid double-extracting.

**5. Remove Chara**
- Reverse of Install Chara: given the same folder, deletes the matching files from the game directories.
- **Note:** Only use this if you selected **Rename** or **Replace** under file conflicts when installing.

**6. Group Chara**
- Groups character cards into subfolders named after their series, using an LLM.
- Workflow:
  1. Select an input folder, customise the prompt if desired, and click **Copy**.  
     KKAFIO scans the folder, builds a JSON mapping `{character_key: ""}`, merges it with the prompt, and copies the result to the clipboard.
  2. Paste into your LLM of choice. The LLM fills in the series name for each key.
  3. Copy the LLM response and click **Paste** in KKAFIO to save it.
  4. Enable **Group Chara**, click **Start** — KKAFIO moves each card into `<input>/<series>/`.
- **Include subfolders** option lets you export already-sorted cards too (off by default to skip them).

**7. Ungroup Chara**
- Reverse of Group Chara: moves all cards from subfolders back to the top-level input folder.
- **Optional:** Deletes empty subfolders after moving (on by default).

**8. Archive Chara**
- Given a list of character cards, bundles each card with its matching coordinate files and required zipmods into a single archive.
- Coordinates are matched by colour fingerprint (not filename), so cards from different mod setups are handled correctly.
- Zipmods are found by GUID — Sideloader Modpack mods are excluded by default.
- **Auto-resolve**: if the card lives inside the game folder, mods and coordinate directories are inferred automatically. Otherwise the card's own folder is used.
- Output format: **7z** (default) or **zip**.
- **Combined archive** option puts all cards into one archive (default), or creates one archive per card.

**9. Delete Chara**
- Given a list of character cards, sends each card together with its matching coordinates and required zipmods to the recycle bin.
- Uses the same path resolution and coordinate matching as Archive Chara.
- Never touches Sideloader Modpack mods.

## Context menu integration
Run `register_context_menu.bat` to add an **KKAFIO** submenu to the Windows Explorer right-click menu.  
No Administrator rights required — entries are written to `HKEY_CURRENT_USER`.

**On folders and folder backgrounds:**
| Entry | Action |
|---|---|
| Install Chara | `install-chara --input <folder>` |
| Remove Chara | `remove-chara --input <folder>` |
| Filter / Convert KKS | `fc-kks --input <folder>` |
| Filter Duplicates | `filter-duplicates --input <folder>` |
| Group Chara | `group-chara --input <folder>` |
| Ungroup Chara | `ungroup-chara --input <folder>` |
| Run GUI | Launch KKAFIO.exe |

**On PNG files (single or multi-select):**
| Entry | Action |
|---|---|
| Archive Chara | `archive-chara <selected files>` |
| Delete Chara | `delete-chara <selected files>` |

Run `unregister_context_menu.bat` to remove all entries.

## CLI usage
`kkafio_cli` exposes every task as a subcommand. Arguments override config; omit them to use config defaults.

```
kkafio_cli run                                    # run all enabled tasks from config

kkafio_cli install-chara [--input DIR] [--extract-archive | --no-extract-archive]
kkafio_cli remove-chara  [--input DIR]
kkafio_cli fc-kks        [--input DIR] [--convert | --no-convert]
                         [--extract-archive | --no-extract-archive]
kkafio_cli filter-duplicates [--input DIR] [--fuzzy | --no-fuzzy]
                             [--keep STRATEGY] [--delete | --no-delete]
kkafio_cli group-chara   [--input DIR] [--export] [--include-subfolders]
                         [--response JSON_OR_FILE]
kkafio_cli ungroup-chara [--input DIR] [--delete-empty | --no-delete-empty]
kkafio_cli archive-chara [CHARA ...] [--format 7z|zip]
                         [--auto-resolve | --no-auto-resolve]
                         [--include-modpack | --no-include-modpack]
                         [--combined | --no-combined]
                         [--mods-dir DIR] [--coord-dir DIR] [--output-dir DIR]
kkafio_cli delete-chara  [CHARA ...] [--auto-resolve | --no-auto-resolve]
                         [--mods-dir DIR] [--coord-dir DIR]
kkafio_cli create-backup [--output DIR] [--filename NAME]
                         [--mods | --no-mods] [--userdata | --no-userdata]
                         [--bepinex | --no-bepinex]

# Global option (all commands):
kkafio_cli --config PATH <command>                # use a custom config.json
```

## Requirements
- 7-Zip installed and on PATH.
- If running from source: [uv](https://docs.astral.sh/uv/getting-started/installation/) installed.
- For fuzzy duplicate matching: `pip install pillow imagehash`.

## Installation and Usage
Download the latest release, extract it, and run `KKAFIO.exe`.

To run from source:
1. Clone or download this repository.
2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
3. Run `uv sync` in the repository folder.
4. Run `uv run KKAFIO.py` and configure settings to your preference.
5. Press **Start**.

> **Note:** You may need to run as Administrator if Koikatsu is installed in `C:\Program Files (x86)`. Open Command Prompt as Administrator, `cd` to the KKAFIO folder and run `uv run KKAFIO.py`.

## Known Issues
- Any `.png` that cannot be classified as a chara card or coordinate is treated as an overlay. Files in the wrong category can be found in `UserData/Overlays` — sort by date to identify and remove them.

## Acknowledgements
- [zhiyiYo](https://github.com/zhiyiYo) for [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets).
- [Kiramei](https://github.com/Kiramei) for the logger. Original [here](https://github.com/Kiramei/blue_archive_auto_script/blob/master/core/utils.py).
- [FlYiNGPoTAToChiP](https://github.com/FlYiNGPoTAToChiP) for KK_SunshineCardFilter and the chara/coordinate distinction method.
- [Evaanxd](https://www.patreon.com/user?u=3125561) and [GaryuX](https://www.patreon.com/GaryuX) for the [Ryuko Matoi card and image](https://www.pixiv.net/en/artworks/77738576).