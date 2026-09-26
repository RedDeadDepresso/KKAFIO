# KKAFIO: Koikatsu Auto File I/O

<img width="1280" height="764" alt="KKAFIO preview" src="assets/preview.png" />

## Table of Contents

- [Requirements](#requirements)
- [Installation and Usage](#installation-and-usage)
- [Game Type](#game-type)
- [Context Menu Integration](#context-menu-integration)
- [Languages](#languages)
- [Features](#features)
- [Presets](#presets)
- [CLI Usage](#cli-usage)
- [Known Issues](#known-issues)
- [Acknowledgements](#acknowledgements)

## Requirements

- 7-Zip installed.
- If running from source: [uv](https://docs.astral.sh/uv/getting-started/installation/) installed.

## Installation and Usage

Download the latest release, extract it, and run `KKAFIO.exe`.

To run from source:

1. Clone or download this repository.
2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
3. Run `uv sync` in the repository folder.
4. Run `uv run tools/download_gui.py` to download the GUI.
5. Open KKAFIO.exe and configure settings to your preference.
6. Press **Start**.

Run `kkafio_setup.bat` (in the install root) any time for a few optional setup utilities:

1. **Create default task folders** — creates `C:\KKAFIO\Backups`, `Downloads`, `Archived Cards`, and `Exported Mods` ahead of time (these are also created automatically on first use of a task left at its default path).
2. **Register context menu** — see [Context Menu Integration](#context-menu-integration).
3. **Unregister context menu** — removes it again.
4. **Delete KKAFIO config and default task folders** — deletes `%APPDATA%\KKAFIO` (saved config, Telegram session, download history) and `C:\KKAFIO` (the four folders above). Asks for confirmation first; cannot be undone.

## Game Type

Configure the game type in the instance settings at the top of the task list:

| Game Type          | Card type installed | Cards skipped |
| ------------------ | ------------------- | ------------- |
| Koikatsu (default) | KK, KKSP            | KKS           |
| Koikatsu Party     | KK, KKSP            | KKS           |
| Koikatsu Sunshine  | KK, KKSP, KKS       | _(none)_      |

The game type also determines which executable is launched by the **Run Game** button, which modpack index file is used, and affects character card installation.

**Game Path** sits alongside Game Type in the same instance settings. If left empty on an instance, KKAFIO falls back to the **Game Path** set on another instance (the first one it finds with a path set), so you don't need to re-enter it on every tab — only set it explicitly on instances where it needs to point somewhere different.

## Context Menu Integration

Run `kkafio_setup.bat` (in the KKAFIO install root) and choose **Register context menu** to add a **KKAFIO** submenu to the Windows Explorer right-click menu. Under the hood this runs `scripts\register_context_menu.bat`, a thin wrapper around `scripts\register_context_menu.ps1` — using the `.bat` avoids Windows' default PowerShell execution policy, which otherwise blocks `.ps1` scripts from running at all. It uses the selected file/folder as an argument; remaining settings are taken from the configuration tab you marked in the GUI (see below), or from the first tab if none is marked.

It first removes any existing KKAFIO menu entries, then asks you to pick a language (used for the menu labels and the script's own prompts) and which folder tasks to include and in what order (enter the numbers shown, e.g. `3 1 4 6`; leave blank for all tasks in the default order). "Run GUI" and the PNG entries below are always included. Re-run it any time to change your language or task selection — no need to run an "unregister" step first.

**On folders and folder backgrounds:**

| Entry                           | Action                                                                                                     |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Filter & Convert KKS Characters | `filter-convert-kks --input <folder>`                                                                      |
| Filter Duplicate Contents       | `filter-duplicate-contents --input <folder>`                                                               |
| Download Missing Mods           | `download-missing-mods --chara-dir <folder> --scene-dir <folder> --coord-dir <folder> --mods-dir <folder>` |
| Compress Cards Textures         | `compress-cards-textures --input <folder>`                                                                 |
| Install Contents                | `install-contents --input <folder>`                                                                        |
| Uninstall Contents              | `uninstall-contents --input <folder>`                                                                      |
| Group Characters                | `group-chara --input <folder>`                                                                             |
| Ungroup Characters              | `ungroup-chara --input <folder>`                                                                           |
| Rename Characters               | `rename-chara --input <folder>`                                                                            |
| Run GUI                         | Opens GUI                                                                                                  |

**On PNG files (single or multi-select):**

| Entry         | Action                                          |
| ------------- | ----------------------------------------------- |
| Archive Cards | `archive-cards <selected files> --context-menu` |
| Delete Cards  | `delete-cards <selected files> --context-menu`  |

**Choosing which config the context menu uses:** in the GUI, right-click a tab and choose **Use in Explorer Context Menu**. That tab shows a small pointer icon, and every context-menu entry now runs with its settings. Right-click it again and choose **Remove from Explorer Context Menu** to clear it. Only one tab can be marked at a time (marking another moves the mark), and if no tab is marked the first tab is used, as before. The choice is read every time you click an entry, so changing it in the GUI takes effect immediately with no re-registering. (Entries registered by an older version don't know about this yet — run **Register context menu** once more to update them.) Each entry runs `kkafio_cli --instance context-menu <command> ...`.

Run `kkafio_setup.bat` and choose **Unregister context menu** to remove all entries without registering new ones.

**Tip:** the **⚡ All Tasks** [preset](#presets) is an easy way to get every task showing up in the GUI at once, so you can configure all of them on your marked tab in one pass before registering the context menu.

## Languages

The GUI (task names, option labels, dialogs, etc.) and the [right-click context menu](#context-menu-integration) are available in English, Simplified Chinese, Traditional Chinese, Japanese, Korean, and Russian. Pick a language in the GUI's settings, or in the language prompt shown when registering the context menu via `kkafio_setup.bat`.

Everything else — CLI output, log files, and error messages — is English-only, and intentionally so: keeping logs in one language makes them far easier to search for, share when reporting a bug, and debug against the source.

## Features

**1. Create Backup**

- Automatically creates a `.7z` archive containing any combination of:
  - `UserData`
  - `Mods` (Sideloader Modpack folders are always excluded — they're re-downloadable and would bloat the archive)
  - `BepInEx`
- **None of the three are selected by default** — pick which folders to include from the checkboxes in the task settings.
- **Output Directory** defaults to `C:/KKAFIO/Backups`.
- **Filename** defaults to `koikatsu_backup`. If an archive with the same name already exists it will be overwritten.

**2. Download Contents**

- Downloads character cards from [db.bepis.moe](https://db.bepis.moe) and [koikatsucards.com](https://koikatsucards.com).
- **Output Directory** defaults to `C:/KKAFIO/Downloads`.
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
- **koikatsucards.com session cookie** — downloading from koikatsucards.com requires a valid session. KKAFIO manages this automatically:
  - On first use (or when the session expires), KKAFIO opens [koikatsucards.com/login](https://koikatsucards.com/login) in your browser and shows a dialog asking you to paste the `kkd_session` cookie value from DevTools (F12 → Application → Cookies → koikatsucards.com).
  - The session is validated against `koikatsucards.com/api/session` before use. If it is expired, you are prompted for a new one automatically.
  - The session is stored in `%APPDATA%/KKAFIO/config/kkd_session.json`. It does not need to be entered in the GUI settings.

**3. Filter & Convert KKS Characters**

- Functions similarly to [FlYiNGPoTAToChiP's KK_SunshineCardFilter](https://github.com/FlYiNGPoTAToChiP/KK_SunshineCardFilter).
- Given a folder, the task:
  - **Convert KKS → KK** _(off by default)_: produces a KK-compatible copy of each **KKS** (Koikatsu Sunshine) card, saved next to its original. The copy is then treated as a KK card by **KK/KKSP Cards** below (not by **KKS Cards**).
  - **KK/KKSP Cards** _(Keep by default)_: what to do with every **KK / KKSP** card found — including any KKS card just converted above. **Keep** leaves them where they are; **Move** moves them into `_KK_card_/`; **Delete** sends them to the Recycle Bin.
  - **KKS Cards** _(Keep by default)_: the same three choices, applied to the original **KKS** cards (never to their converted copies — those are covered by **KK/KKSP Cards**).
- **Optional:** **Extract Archives** _(on by default)_: extracts ZIP / RAR / 7z archives before filtering — this still works with both actions left at **Keep**, so the task is also useful purely as an archive-extraction step.
- **Archive Password** _(`Skip` by default)_: `Skip` ignores password-protected archives; `Request Password` prompts you for one when an archive needs it.
- An archive whose extraction folder (named after the archive, next to it) already exists is not extracted again.
- Has a separate archive password setting from Install Contents.

**4. Filter Duplicate Contents**

- Given a folder, scans recursively for duplicate `.png` cards and `.zipmod` files.
- Duplicates are detected by **content** (not filename):
  - PNG cards are fingerprinted using the character data payload embedded after the PNG IEND chunk, so two cards with different preview images are still caught as duplicates.
  - **Optional fuzzy matching** _(off by default)_ uses perceptual image hashing to detect updated cards with the same preview pose.
- Duplicates are moved into `_duplicates_/<category>/` subfolders:
  - `chara/` — KK / KKSP / KKS character cards
  - `coordinate/` — coordinate cards
  - `scene/` — Studio scene files
  - `overlays/` — unclassified PNGs
  - `mods/` — zipmod files
- **Keep strategy** controls which copy of a duplicate set is kept in place: Newest, Oldest, Biggest file size (default), Smallest file size, Last alphabetically, First alphabetically, or None (move all copies).
- **Use Cache** (on by default) — remembers each file's content hash (and perceptual hash, for fuzzy chara matching) keyed by its mtime + size, so a repeat scan only re-hashes files that are new or have changed. Uses three separate cache files in the scanned folder:
  - `kkafio_duplicate_png_cache.json` — xxHash + category for every PNG
  - `kkafio_duplicate_fuzzy_cache.json` — perceptual hash for chara cards only (kept separate since it's only computed when **Fuzzy Matching** is on, and is much more expensive than the plain xxHash hash)
  - `kkafio_duplicate_mods_cache.json` — xxHash for every zipmod
- **Duplicate Action** controls what happens to the copies that aren't kept:
  - **Move & Rename** _(default)_: moves duplicates into `_duplicates_/<category>/` and renames them so it's obvious which card they're a copy of.
    - If **Keep strategy** is **None** (all copies moved, none kept in place), the first duplicate found in each set keeps its own name, and every other duplicate in that set is renamed after it with a number — e.g. `bar.png`, `bar_1.png`, `bar_2.png`.
    - If **Keep strategy** is anything else, every moved duplicate is renamed to the name of the copy that stayed in the source, plus a number — e.g. the kept `foo.png` leaves duplicates named `foo_1.png`, `foo_2.png`.
  - **Move**: moves duplicates into `_duplicates_/<category>/` keeping their original filenames.
  - **Delete**: sends duplicates straight to the recycle bin instead of moving them.

**5. Download Missing Mods**

Finds all mods referenced by installed character cards, Studio scenes, and/or coordinate cards that are not present in the local mods directory, then downloads them automatically.

See the **📥 Download, Filter & Install** preset for the staging-folder configuration, or the **🗂️ Organize Installed Contents** preset for checking mods on cards you already have installed.

- **Step 1 — Mods cache:** Scans the mods directory and builds a cache of all installed mod GUIDs.
- **Step 2 — Content scan:** Recursively scans whichever content types are selected in **Content Types to Scan** (Characters / Scenes / Coordinates — all three selected by default), collecting every mod GUID they reference. Each type's results are cached separately for subsequent runs. Deselecting a type also skips requiring its directory to be resolvable — e.g. if Characters is unchecked, the task no longer needs a valid chara directory to run.
- **Step 3 — Missing = referenced − installed.**
- **Step 4 — Download:**
  - **BetterRepack** — Mods found in `assets/data/kkafio_modpack_index_kk.json` / `assets/data/kkafio_modpack_index_kks.json` are downloaded from [sideload.betterrepack.com](https://sideload.betterrepack.com), preserving the Sideloader Modpack folder structure.
  - **Telegram** (if **Telegram Source** is not `No`) — mods not covered by BetterRepack are downloaded from Telegram, via whichever source(s) are selected:
    - `koikatsucards.com` — looks up each GUID on [koikatsucards.com/mod_library](https://koikatsucards.com/mod_library) and downloads the linked message using your Telegram account, via [Telethon](https://github.com/LonamiWebs/Telethon) and [teleget9527](https://pypi.org/project/teleget9527/) for maximum parallel speed.
    - `Telegram Chat Links` — searches each chat/channel/group listed in **Telegram Chat Links** directly (Telegram's own server-side document search — no scraping or scanning message history), moving on to the next chat if one has no match, and downloads the first result whose filename ends in `.zipmod`.
    - `koikatsucards.com + Telegram Chat Links` — tries koikatsucards.com first, then falls back to Telegram Chat Links for anything koikatsucards.com couldn't find (not found there, or the download itself failed).
  - If BetterRepack fails for a mod and a Telegram source is enabled, KKAFIO automatically retries it via Telegram.
- **Report:** a `kkafio_missing_mods_report.txt` file is written to the mods directory every time this task runs, listing what was found missing, downloaded, and still unresolved. This happens even with **Sideloader Modpack** set to `Skip` and **Telegram Source** set to `No` — the task still scans for and reports missing mods, it just won't download anything in that case.
- **Sideloader Modpack mode:**
  - `Skip` _(default)_ — ignores all modpack GUIDs; only downloads non-modpack mods.
  - `Only Used` — downloads missing modpack mods that are actually referenced by installed cards.
  - `All` — downloads every GUID in the modpack index not installed locally, even if no card uses it.
- **Content Types to Scan** — multi-select: Characters / Scenes / Coordinates. All three selected by default.
- **Telegram Source** _(`No` by default)_ — `No` / `koikatsucards.com` / `Telegram Chat Links` / `koikatsucards.com + Telegram Chat Links`, as described above.
- **Custom Chara Directory / Custom Scene Directory / Custom Coordinate Directory / Custom Mods Directory** — blank by default (uses the game's default directories). Set when using a staging folder workflow (see below). The scene directory only applies if Studio is installed; if left blank and no default `scene` folder exists, scene scanning is skipped (same for coordinates if no default coordinate folder exists).
- **Use Cache** _(on by default)_ — caches the mods list and the GUID scan for each selected content type. Each cache is invalidated automatically when its folder changes.

**Telegram Chat Links** — one link per line, used when **Telegram Source** is `Telegram Chat Links` or the combined option. You must already be a member of each chat. Add a topic ID (e.g. `.../299`) to search only that forum topic instead of the whole chat; a trailing `# comment` is ignored. Chats are tried in the order listed, moving to the next one if a chat has no match. Defaults to:

```
# You need to be a member of all these chats if you want to search mods within them
https://t.me/c/2549022984
https://t.me/KK_archive_modlibrary
https://t.me/KKDOC
https://t.me/koikatu_card_download
https://t.me/kknowcc
```

**Telegram setup:**

1. Go to [my.telegram.org/apps](https://my.telegram.org/apps), log in, and create an app to get an **API ID** and **API Hash**. Enter these in the GUI's settings.
2. Set **Telegram Source** to anything other than `No` in the GUI's settings.
3. On first use, KKAFIO opens [my.telegram.org](https://my.telegram.org) in your browser and shows dialogs for your phone number and verification code (and 2FA password if enabled). The session is saved to `%APPDATA%/KKAFIO/config/tg_session/kkafio.session` and reused automatically.

> ⚠️ **Security notice:** Telegram API credentials and the session file give full access to your Telegram account. **We strongly recommend using a secondary/dedicated Telegram account** rather than your personal account. The session file is stored locally and never uploaded anywhere, but treat it like a password. Never share `%APPDATA%/KKAFIO/config/tg_session/` with anyone.

**6. Compress Cards Textures**

- Recompress the textures embedded inside chara/coordinate cards with [KoiCardTexTool](https://github.com/EeEeX4/koikatsu-card-texture-tool), shrinking file size dramatically (often -50% to -80%) with minimal quality loss:
- **KoiCardTexTool Path** — folder containing (or where to install) `KoiCardTexTool.exe`. Defaults to `C:/KoiCardTexTool`. If the exe isn't found there (checked recursively, in case the release zip nests it in a subfolder), KKAFIO downloads and extracts the latest release automatically before running it.
- Runs `KoiCardTexTool.exe batch <input> <input>` — the same folder is used for both input and output, so compressed copies land right alongside the originals, named `CardA[zip].png` for an original `CardA.png`. Its output is streamed live into the log, the same way 7-Zip's output is.
- **Delete Original Cards** _(off by default)_: after compressing, finds every card whose filename ends in `[zip]`, and if the matching original (with `[zip]` removed from the name) still exists alongside it, sends the original to the Recycle Bin.

**7. Install Contents**

- Given a folder containing chara cards, coordinate cards, scenes, overlays, and zipmod files, copies them into their respective game directories.
- **Content Types to Install** — multi-select: Chara / Mods / Coords / Scenes / Overlays. All five selected by default; deselect any type you don't want copied in.
- Respects the configured **Game Type**: Koikatsu Sunshine installs KK, KKSP, and KKS cards. Koikatsu / Koikatsu Party installs KK and KKSP cards only — KKS cards are skipped with a log message.
- Scene cards (Studio) are installed only if the Studio `scene` folder is present.
- **File Conflicts** _(`Skip` by default)_: `Skip` leaves an existing file in place and doesn't install the new one over it; `Replace` overwrites it; `Rename` installs the new file alongside the existing one under a new name.
- **Extract Archives** _(on by default)_: extracts ZIP / RAR / 7z archives automatically. Each archive is extracted next to itself into a folder named after it and that folder is left in place; an archive whose folder already exists is not extracted again.
- **Archive Password** _(`Skip` by default)_: `Skip` ignores password-protected archives; `Request Password` prompts you for one when an archive needs it. Has a separate value from Filter & Convert KKS Characters.
- If both Filter & Convert KKS Characters and Install Contents are enabled with the same input folder, archive extraction runs in the filter step only to avoid double-extracting.

**8. Uninstall Contents**

- Reverse of Install Contents: given the same folder, deletes the matching files from the game directories.
- **Content Types to Uninstall** — the same Chara / Mods / Coords / Scenes / Overlays multi-select as Install Contents, all five selected by default; deselect any type you don't want removed.
- **Note:** Only use this if you selected **Rename** or **Replace** under file conflicts when installing.
- **Warning:** Uninstall Contents does not check whether a zipmod is shared with other characters before deleting it. Removing a zipmod used by multiple cards will break all of them. Character cards work independently from coordinate cards, so removing a coordinate does not affect the character card itself. Only use this task when you are certain the files being removed are exclusive to the cards you are deleting. Files can still be recovered from the Recycle Bin.

**9. Group Characters**

- Groups character cards into subfolders named after their series, using an LLM.
- Workflow:
  1. Select an input folder, customise the prompt if desired, and enable **Group Characters**.
  2. Click **Start** — KKAFIO scans the folder, builds a JSON mapping `{character_key: ""}`, and opens a dialog showing the combined prompt + JSON.
  3. Click **Copy** in the dialog, paste into your LLM of choice. The LLM fills in the series name for each key.
  4. Copy the LLM's reply, click **Paste** in the same dialog — KKAFIO immediately moves each card into `<input>/<series>/`.
- **Include subfolders** option lets you export already-sorted cards too (off by default to skip them).
- **Recommended LLMs:**
  - [DeepSeek](https://chat.deepseek.com) — highly recommended: large context window, excels at identifying characters from Chinese gacha games (Genshin Impact, Honkai Star Rail, Arknights). Enable **Expert** for better identification of obscure characters.
  - [Claude](https://claude.ai) — strong general-purpose identification, particularly good for Japanese anime and game characters.

**10. Ungroup Characters**

- Reverse of Group Characters: moves all cards from subfolders back to the top-level input folder.
- **Optional:** Deletes empty subfolders after moving (on by default).

**11. Rename Characters**

- Translates character card names to English using an LLM.
- Workflow:
  1. Select an input folder and enable **Rename Characters**.
  2. Click **Start** — KKAFIO scans all PNG cards (recursively), builds a JSON mapping `{character_key: {lastname, firstname, nickname}}`, and opens a dialog showing the combined prompt + JSON.
  3. Click **Copy** in the dialog, paste into your LLM of choice. The LLM fills in the English name for each key.
  4. Copy the LLM's reply, click **Paste** in the same dialog — KKAFIO immediately writes the translated names into each card's internal metadata and/or renames the file, depending on the options below.
- **Update card metadata** (off by default): writes the translated names into the card file.
- **Rename PNG files** (on by default): also renames the file on disk to `Lastname_Firstname.png`. Files in subfolders stay in their subfolder.
- **Skip already renamed** (on by default): skips cards whose name is already in the local cache.
- Results are cached in `kkafio_rename_cache.json` inside the input folder and reused across runs.
- The prompt is fully editable in the settings panel.
- **Recommended LLMs:** same as Group Characters (see above).
- **Warning:** Group Characters uses card metadata to extract character names. It is recommended to use **Rename Characters after Group Characters if Update card metadata is turned on**, as LLMs might not recognize the characters by their translated names.
- **Warning:** It is possible to modify the prompt to allow for transliteration, rather than limiting it to just the character's English name. However, the transliteration of Chinese characters can differ significantly from that of English characters. Transliterating Japanese characters tends to yield better results, although there may be exceptions.

**12. Archive Cards**

- Given a list of character cards, coordinate cards, and/or Studio scene files, bundles each one with its required zipmods into a single archive.
- **Include Coordinates** _(on by default)_: when a selected file is a character card, also bundles the coordinate cards it uses (matched by colour fingerprint, not filename) along with their mods. Disable to archive the character card by itself. This option has no effect on coordinate cards or scenes selected directly — a coordinate card is always archived with just its own mods, and scenes never have coordinates.
- **Include Modpack** _(off by default)_: Sideloader Modpack mods are excluded from the archive by default (see [Modpack Index](docs/05-configuration.md#modpack-index) in the configuration docs); turn this on to bundle them in too.
- Zipmods are found by GUID.
- **Auto-resolve** _(on by default)_: if the card/scene lives inside the game folder, mods and coordinate directories are inferred automatically. Override with **Custom Mods Directory** and **Custom Coordinate Directory** if needed (both blank by default).
- **Output Directory** defaults to `C:/KKAFIO/Archived Cards`.
- **Output format** _(`7z` by default)_: `7z`, `zip`, or `Copy`. `Copy` doesn't create an archive at all — it copies the card, its coordinates, its mods, and the generated `README.txt` straight into a destination folder (named the same way an archive would be), flattened to a single level exactly like the contents of a 7z/zip archive. If that folder already exists, it's deleted and recreated from scratch first.
- **Combined archive** _(on by default)_: puts all selected files into one archive (or, with `Copy`, one folder). Turn off to create one archive (or folder) per file. When more than one card ends up combined together (any output format), each card gets its own subfolder inside the bundle named after it, containing just that card's own card file, coordinates, and mods — so opening the bundle shows `<Character Name>/<their files>` for each card rather than everything dumped flat at the top level. A mod shared by several cards is duplicated into each of their subfolders. `README.txt` stays at the top level of the bundle. A combined bundle of a single card stays flat, same as before.
- **Use Cache** _(on by default)_: reuses the same incremental GUID caches as Download Missing Mods and Delete Cards.

**13. Delete Cards**

- Given a list of character cards, coordinate cards, and/or Studio scene files, sends each one together with its required zipmods to the recycle bin.
- **Include Coordinates** _(on by default)_: when a selected file is a character card, also deletes the coordinate cards it uses (and their mods). Disable to delete only the character card. This option has no effect on coordinate cards or scenes selected directly.
- Uses the same path resolution and coordinate matching as Archive Cards, including **Auto-resolve** _(on by default)_ and blank-by-default **Custom Mods/Chara/Scene/Coordinate Directory** overrides.
- Never touches Sideloader Modpack mods.
- **Check for Shared Mods** _(on by default)_: before deleting a zipmod, scans every character card in the game's chara folders (or **Custom Chara Directory**, if set), every scene in the Studio scene folder (or **Custom Scene Directory**, if set), and every coordinate card in the game's coordinate folder (or **Custom Coordinate Directory**, if set) to confirm no other character, scene, or coordinate still references it. Any zipmod still in use elsewhere is kept instead of deleted, and logged as such. This scan reuses the same incremental GUID caches as Download Missing Mods (`kkafio_chara_guid_cache.json`, `kkafio_scene_guid_cache.json`, `kkafio_coord_guid_cache.json`) when **Use Cache** _(on by default)_ is on, so repeat runs skip re-parsing cards that haven't changed. Turning **Use Cache** off, or turning **Check for Shared Mods** off entirely, skips the scan (faster, especially with a large card collection), but files can still be recovered from the Recycle Bin if needed.

**14. Export Mods**

- Find specific mods by GUID and copy them out into a folder:
- **Output Directory** defaults to `C:/KKAFIO/Exported Mods`.
- **GUIDs** — paste one GUID per line, or a section straight out of a Download Missing Mods report (e.g. the "Unresolvable mods" list). A leading report bullet (`!`, `✗`, `+`, `~`) is removed; everything from a `#` onward is a comment and is ignored, and every other line is used as the GUID exactly as written, so delete any description or `(...)` note lines first.
- **Rename to GUID** _(on by default)_: renames each exported file to `<guid>.zipmod`, so it's obvious which file is which. Turn off to keep each file's original filename. Recommended to leave on, especially if you're uploading the exported mod to Telegram — Download Missing Mods finds mods there by matching the GUID in the filename, so a file named by its GUID is one it can find accurately, while an original filename may not be.
- Searches both the regular mods folder and any Sideloader Modpack subfolder inside it.
- **Custom Mods Directory** — blank by default (uses the game's default mods folder).
- **Use Cache** _(on by default)_ — reuses the same incremental mods cache as the other tasks.

---

## Presets

Presets are one-click bundles that check a fixed set of tasks and preconfigure their settings for you — pick one from the preset dropdown instead of ticking tasks and filling in each one's options by hand. Every task, checked or not, keeps whatever settings the preset put in it, so you can still enable, disable, or edit any of them afterward; a preset only sets the starting point.

**📥 Download, Filter & Install**

Checks Download Contents → Filter & Convert KKS Characters → Filter Duplicate Contents → Download Missing Mods → Compress Cards Textures → Install Contents. The full download-to-install pipeline: pull down new cards, convert any KKS ones and remove duplicates, grab whatever mods they need, shrink their textures, then install everything into the game.

This is the fastest end-to-end workflow for adding a large batch of new cards. All work happens in a temporary staging folder; the game directories are only touched at the final Install step.

```
📁 C:/KKAFIO/Downloads   ← the default staging folder
```

The staging folder path itself is preconfigured too — every task in this preset defaults to `C:/KKAFIO/Downloads` as its input/output. Point them elsewhere if you'd rather stage somewhere else, as long as you change all of them to the same folder.

**Step 1 — Download cards** _(optional)_

The preset enables **Download Contents**, which pulls cards from db.bepis.moe or koikatsucards.com straight into the staging folder. Disable it if you'd rather copy cards you already have into the staging folder yourself.

**Step 2 — Filter & Convert** _(optional)_

The preset enables **Filter & Convert KKS Characters** with the staging folder as input, and:

- **Extract Archives** is on — this unpacks any ZIP/RAR/7z files in the staging folder before the rest of the pipeline runs.
- **KK/KKSP Cards** and **KKS Cards** are left at their default, **Keep**. **Move** sorts cards into `_KK_card_/`/`_KKS_card_/` subfolders, which breaks **Uninstall Contents**' ability to accurately find and remove a character's files later — Uninstall Contents matches by the file's location in your input folder, and cards buried in a subfolder won't line up with what actually got installed. Leave this at Keep unless you specifically want the sorted subfolders (or deletion) for another reason.
- **Convert KKS → KK** is left off by default. **If you're on Koikatsu Sunshine, leave it off** — Sunshine already loads KK, KKSP, and KKS cards natively (see [Game Type](#game-type)), so converting KKS cards to KK-compatible copies just creates redundant duplicate cards you'll then have to deduplicate again in the next step. **If you're on Koikatsu / Koikatsu Party, turn it on and set KKS Cards to Delete** — KK/KKP can't load KKS cards at all, so converting them and removing the untranslatable originals is what actually makes them usable, rather than leaving unusable duplicate files sitting in the staging folder.
- You can still leave this task enabled purely for its archive-extraction step, even with Convert and both actions left at their defaults. In fact, **keep this task enabled even if you don't need any of its filtering/converting** as long as you have archives to extract or want to run other tasks (Steps 3–5) before Install Contents — those later tasks read from the staging folder, so extraction needs to happen first, and disabling this task skips that.

**Step 3 — Deduplicate** _(optional)_

The preset enables **Filter Duplicate Contents** with the staging folder as input, and already turns on **Delete Duplicates**, sending duplicates straight to the recycle bin instead of moving them to `_duplicates_/`.

**Step 4 — Download missing mods** _(optional)_

The preset enables **Download Missing Mods**, configured with:

- **Sideloader Modpack** → `Skip` _(mods in the staging folder are local, not modpack mods)_
- **Custom Chara Directory** → your staging folder
- **Custom Scene Directory** → your staging folder too, if you're staging Studio scenes
- **Custom Coordinate Directory** → your staging folder too, if you're staging coordinate cards
- **Custom Mods Directory** → your staging folder
- **Content Types to Scan** → deselect any type you aren't staging (e.g. uncheck Scenes/Coordinates if the staging folder only has chara cards) — this also means KKAFIO won't require that type's directory to be resolvable
- **Telegram Source** → `Both` (koikatsucards.com + Telegram Chat Links), for maximum coverage

These paths default to the staging folder already; adjust them only if you're scanning content types the staging folder doesn't have, or want to point elsewhere. KKAFIO then scans the selected content types in the staging folder, finds which mods they reference, and downloads any missing ones into the staging folder alongside them.

**Step 5 — Compress textures** _(optional)_

The preset enables **Compress Cards Textures** with the staging folder as **Input Directory**, and already turns on **Delete Original Cards**, so only the compressed copies get installed rather than both. This recompresses the textures inside your newly-staged chara/coordinate cards before they're installed, so the smaller `[zip]` versions are what end up in your game folders.

Turning on Delete Duplicates, Delete (KKS Cards), and Delete Original Cards throughout this preset is deliberate: it keeps leftover and duplicate copies from piling up in the staging folder, avoids file conflicts at Install, and means compute-heavy steps like Compress Cards Textures aren't wasting time compressing textures on cards that are just going to be removed anyway.

**Step 6 — Install**

The preset enables **Install Contents** with the staging folder as input. KKAFIO copies everything — cards, coordinates, scenes, overlays, and zipmods — into the correct game directories.

**Step 7 — Remove content you don't want** _(optional)_

Not every card you staged and installed is one you'll want to keep. Neither task is checked in this preset by default — add whichever one you want to it, or just use them straight from the [context menu](#context-menu-integration) instead:

- **Delete Cards** — go to the game's `chara`/`scene`/`coordinate` folder, sort by date modified descending so your newly-installed cards are at the top, and select the ones you don't like. This is the recommended option, since Delete Cards can check for shared mods before deleting, so you don't accidentally strip a mod something else still needs.
- **Uninstall Contents** — set its input to the staging folder and it reverts everything Install Contents just did, or go into the staging folder, group the cards you don't want into a subfolder, and set that subfolder as the input path to undo just those. This is quicker to set up when you want to revert a whole batch, but it doesn't check for shared mods first.

To get to either folder quickly — the game folder for Delete Cards via the context menu, or the staging folder for grouping unwanted content before running Uninstall Contents — use the **folder component**'s dropdown on the right of any Directory field in the GUI, which has a **Show in Explorer** option that opens it directly.

**Step 8 — Clean up the staging folder** _(optional)_

Once you're happy with what got installed, that same folder component dropdown also has a **Clear Contents** option — pick it on the staging folder's Directory field to send everything left in the staging folder to the recycle bin, ready for the next batch.

---

**🗂️ Organize Installed Contents**

Checks Filter Duplicate Contents → Group Characters → Rename Characters. Delete Cards and Download Missing Mods are left unchecked. For tidying up cards you already have installed: clear out duplicates, sort characters into folders, and rename them.

**Step 1 — Deduplicate** _(optional)_

The preset enables **Filter Duplicate Contents** with **Input Directory** set to your game's `UserData\chara\female` or `UserData\chara\male` folder, depending on which characters you're organizing.

**Tip — organizing other content types:** Filter Duplicate Contents isn't limited to chara cards. Right-click the task and choose to duplicate it, then point the copy's **Input Directory** at your game's `mods`, Studio `scene`, or `coordinate` folder instead to deduplicate those too. Right-click the duplicated task again to rename it to something like **Filter Duplicate Mods** so it's easy to tell apart from the original.

**Step 2 — Group characters** _(optional)_

The preset enables **Group Characters**, with **Input Directory** set the same as Step 1, sorting characters into per-character folders.

**Step 3 — Rename characters** _(optional)_

The preset enables **Rename Characters**, with **Input Directory** set the same as Step 1, giving each card a readable filename.

**Step 4 — Remove content you don't want** _(optional)_

**Delete Cards** is left unchecked on purpose — once everything's deduplicated, grouped, and renamed by Steps 1–3, copies of the same character sit together and are much easier to pick through. Add it to this preset, or just run it from the [context menu](#context-menu-integration), and Delete Cards will check for shared mods before deleting, so you don't accidentally strip a mod something else still needs.

**Step 5 — Check for missing mods** _(optional)_

**Download Missing Mods** is also left unchecked by this preset, but its settings are still preconfigured — enable it and it's already set up for checking your installed cards:

- **Sideloader Modpack** is set to `Only Used`.
- **Custom Chara Directory**, **Custom Scene Directory**, **Custom Coordinate Directory**, and **Custom Mods Directory** are left blank (uses game defaults).

**Telegram Source** is already set to `Both` (koikatsucards.com + Telegram Chat Links) for maximum coverage. Just enable **Download Missing Mods** and click **Start** to verify that all mods required by your currently installed cards are present. No staging folder needed.

KKAFIO scans your installed chara cards, scenes, and coordinates, finds any missing mod GUIDs, downloads missing Sideloader Modpack mods from BetterRepack, and (if **Telegram Source** is not `No`) downloads any remaining mods via Telegram.

---

**📤 Export Installed Contents**

Archive Cards and Export Mods are both left unchecked — they serve different purposes, so enable whichever one matches what you're doing rather than running both: Archive Cards bundles selected cards together with their required mods, while Export Mods pulls any additional mods out by GUID. Use this preset for packaging up your installed content to share with someone else.

---

**⚡ All Tasks**

Doesn't check everything for you to run in one go — it's mostly used to quickly set up every task's settings on a tab in the GUI, most commonly so they're all configured and ready before [registering the context menu](#context-menu-integration). Most workflows above are a better fit for everyday use.

If you're setting this up for the context menu, right-click its tab in the GUI and choose **Use in Explorer Context Menu** — this makes the tab easier to keep track of, and means the context menu reads settings from that tab specifically rather than falling back to whichever tab happens to be first.

If you're only setting this up for the context menu, you can remove **Create Backup**, **Download Contents**, and **Export Mods** — none of the three appear in the [context menu](#context-menu-integration), so there's nothing there that reads their settings.

---

## CLI Usage

`kkafio_cli` exposes every task as a subcommand. Arguments override config; omit them to use config defaults.

```
kkafio_cli run                                    # run all enabled tasks from config

kkafio_cli download-contents [--links URLS_OR_FILE] [--output-dir DIR]
                             [--skip-downloaded | --no-skip-downloaded]

kkafio_cli download-missing-mods [--mods-dir DIR] [--chara-dir DIR] [--scene-dir DIR] [--coord-dir DIR]
                                 [--no-chara] [--no-scene] [--no-coord]
                                 [--use-cache | --no-use-cache]
                                 [--modpack-mode Skip|OnlyUsed|All]
                                 [--telegram-source No|KoikatsuCards|ChatLinks|Both]
                                 [--telegram-chat-links LINKS]

kkafio_cli export-mods [--output DIR] [--guids TEXT | --guids-file FILE]
                        [--rename-to-guid | --no-rename-to-guid]
                        [--use-cache | --no-use-cache]
                        [--mods-dir DIR]

kkafio_cli compress-cards-textures [--input DIR] [--tool-path DIR]
                                   [--delete-original | --no-delete-original]

kkafio_cli create-backup  [--output DIR] [--filename NAME]
                          [--mods | --no-mods]
                          [--userdata | --no-userdata]
                          [--bepinex | --no-bepinex]

kkafio_cli filter-convert-kks [--input DIR]
                                [--filter | --no-filter]
                                [--convert | --no-convert]
                                [--extract-archive | --no-extract-archive]

kkafio_cli filter-duplicate-contents [--input DIR]
                             [--fuzzy | --no-fuzzy]
                             [--keep STRATEGY]
                             [--action move-rename|move|delete]
                             [--use-cache | --no-use-cache]

kkafio_cli install-contents   [--input DIR]
                           [--extract-archive | --no-extract-archive]

kkafio_cli uninstall-contents [--input DIR]

kkafio_cli rename-chara    [--input DIR]
                           [--skip-already-renamed | --no-skip-already-renamed]
                           [--update-metadata | --no-update-metadata]
                           [--rename-files | --no-rename-files]

kkafio_cli group-chara     [--input DIR] [--include-subfolders]

kkafio_cli ungroup-chara   [--input DIR]
                           [--delete-empty | --no-delete-empty]

kkafio_cli archive-cards  [CONTENT ...] [--output-dir DIR]
                           [--format 7z|zip|copy]
                           [--combined | --no-combined]
                           [--include-modpack | --no-include-modpack]
                           [--include-coordinates | --no-include-coordinates]
                           [--auto-resolve | --no-auto-resolve]
                           [--use-cache | --no-use-cache]
                           [--mods-dir DIR] [--coord-dir DIR]

kkafio_cli delete-cards  [CONTENT ...]
                           [--check-shared-mods | --no-check-shared-mods]
                           [--include-coordinates | --no-include-coordinates]
                           [--auto-resolve | --no-auto-resolve]
                           [--use-cache | --no-use-cache]
                           [--mods-dir DIR] [--chara-dir DIR] [--scene-dir DIR] [--coord-dir DIR]

# Global options (all commands):
kkafio_cli --config PATH --instance N|context-menu <command>
```

## Known Issues

- Any `.png` that cannot be classified as a chara card or coordinate is treated as an overlay. Files in the wrong category can be found in `UserData/Overlays` — sort by date to identify and remove them.
- Studio scene cards are skipped if Studio is not installed (the `UserData/Studio/scene` folder does not exist).

## Acknowledgements

- [MistEO](https://github.com/MistEO) for the [GUI](https://github.com/MistEO/MXU).
- [great-majority](https://github.com/great-majority) for [KoikatuCharaLoader](https://github.com/great-majority/KoikatuCharaLoader), a deserializer and serializer for character and scene data from Koikatu.
- [xwc9527](https://github.com/xwc9527/telebackup) for [TeleBackup](https://github.com/xwc9527/telebackup), High-Speed Telegram Download Engine.
- [galact-byte](https://github.com/galact-byte) for caching logic taken from [KKTools](https://github.com/galact-byte/KKTools).
- [EeEeX4](github.com/EeEeX4/koikatsu-card-texture-tool) for [KoiCardTexTool](github.com/EeEeX4/koikatsu-card-texture-tool), the program used to compress cards textures.
- [FlYiNGPoTAToChiP](https://github.com/FlYiNGPoTAToChiP) for KK_SunshineCardFilter and the chara/coordinate distinction method.
- [Evaanxd](https://www.patreon.com/user?u=3125561) and [GaryuX](https://www.patreon.com/GaryuX) for the [Ryuko Matoi card and image](https://www.pixiv.net/en/artworks/77738576).
