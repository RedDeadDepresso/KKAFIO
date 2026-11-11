# Presets

Presets are one-click bundles that check a fixed set of tasks and preconfigure their settings for you — pick one from the preset dropdown instead of ticking tasks and filling in each one's options by hand. Every task, checked or not, keeps whatever settings the preset put in it, so you can still enable, disable, or edit any of them afterward; a preset only sets the starting point.

## 📥 Download, Filter & Install

Checks Download Contents → Filter & Convert KKS Cards → Filter Duplicate Contents → Download Missing Mods → Compress Card Textures → Install Contents. The full download-to-install pipeline: pull down new cards, convert any KKS ones and remove duplicates, grab whatever mods they need, shrink their textures, then install everything into the game.

This is the fastest end-to-end workflow for adding a large batch of new cards. All work happens in a temporary staging folder; the game directories are only touched at the final Install step.

```
📁 C:/KKAFIO/Downloads   ← the default staging folder
```

The staging folder path itself is preconfigured too — every task in this preset defaults to `C:/KKAFIO/Downloads` as its input/output. Point them elsewhere if you'd rather stage somewhere else, as long as you change all of them to the same folder.

### Step 1 — Download cards _(optional)_

The preset enables **Download Contents**, which pulls cards from db.bepis.moe or koikatsucards.com straight into the staging folder. Disable it if you'd rather copy cards you already have into the staging folder yourself.

### Step 2 — Filter & Convert _(optional)_

The preset enables **Filter & Convert KKS Cards** with the staging folder as input, and:

- **Extract Archives** is on — this unpacks any ZIP/RAR/7z files in the staging folder before the rest of the pipeline runs.
- **KK/KKSP Cards** and **KKS Cards** are left at their default, **Keep**. **Move** sorts cards into `_KK_card_/`/`_KKS_card_/` subfolders, which breaks **Uninstall Contents**' ability to accurately find and remove a character's files later — Uninstall Contents matches by the file's location in your input folder, and cards buried in a subfolder won't line up with what actually got installed. Leave this at Keep unless you specifically want the sorted subfolders (or deletion) for another reason.
- **Convert KKS → KK** is left off by default. **If you're on Koikatsu Sunshine, leave it off** — Sunshine already loads KK, KKSP, and KKS cards natively (see [Game Type](getting-started/game-type.md)), so converting KKS cards to KK-compatible copies just creates redundant duplicate cards you'll then have to deduplicate again in the next step. **If you're on Koikatsu / Koikatsu Party, turn it on and set KKS Cards to Delete** — KK/KKP can't load KKS cards at all, so converting them and removing the untranslatable originals is what actually makes them usable, rather than leaving unusable duplicate files sitting in the staging folder.
- You can still leave this task enabled purely for its archive-extraction step, even with Convert and both actions left at their defaults. In fact, **keep this task enabled even if you don't need any of its filtering/converting** as long as you have archives to extract or want to run other tasks (Steps 3–5) before Install Contents — those later tasks read from the staging folder, so extraction needs to happen first, and disabling this task skips that.

### Step 3 — Deduplicate _(optional)_

The preset enables **Filter Duplicate Contents** with the staging folder as input, and already turns on **Delete Duplicates**, sending duplicates straight to the recycle bin instead of moving them to `_duplicates_/`.

### Step 4 — Download missing mods _(optional)_

The preset enables **Download Missing Mods**, configured with:

- **Sideloader Modpack** → `Skip` _(mods in the staging folder are local, not modpack mods)_
- **Custom Chara Directory** → your staging folder
- **Custom Scene Directory** → your staging folder too, if you're staging Studio scenes
- **Custom Coordinate Directory** → your staging folder too, if you're staging coordinate cards
- **Installed Mods Directory** → blank (the game's mods folder, so mods you already have installed aren't downloaded again)
- **Downloaded Mods Directory** → your staging folder
- **Content Types to Scan** → deselect any type you aren't staging (e.g. uncheck Scenes/Coordinates if the staging folder only has chara cards) — this also means KKAFIO won't require that type's directory to be resolvable
- **Mod Sources** → check all of them (Google Drive, Mega, Telegram, Gofile, Catbox, MediaFire, Pixeldrain) and leave **Use Telegram Chat Links** on, for maximum coverage

These paths default to the staging folder already; adjust them only if you're scanning content types the staging folder doesn't have, or want to point elsewhere. KKAFIO then scans the selected content types in the staging folder, finds which mods they reference, and downloads any that are missing from both your game's mods folder and the staging folder into the staging folder alongside them.

### Step 5 — Compress textures _(optional)_

The preset enables **Compress Card Textures** with the staging folder as **Input Directory**, and already turns on **Delete Original Cards**, so only the compressed copies get installed rather than both. This recompresses the textures inside your newly-staged chara/coordinate cards before they're installed, so the smaller `[zip]` versions are what end up in your game folders.

Turning on Delete Duplicates, Delete (KKS Cards), and Delete Original Cards throughout this preset is deliberate: it keeps leftover and duplicate copies from piling up in the staging folder, avoids file conflicts at Install, and means compute-heavy steps like Compress Card Textures aren't wasting time compressing textures on cards that are just going to be removed anyway.

### Step 6 — Install

The preset enables **Install Contents** with the staging folder as input. KKAFIO copies everything — cards, coordinates, scenes, overlays, and zipmods — into the correct game directories.

### Step 7 — Remove content you don't want _(optional)_

Not every card you staged and installed is one you'll want to keep. Neither task is checked in this preset by default — add whichever one you want to it, or just use them straight from the [context menu](getting-started/context-menu.md) instead:

- **Delete Cards** — go to the game's `chara`/`scene`/`coordinate` folder, sort by date modified descending so your newly-installed cards are at the top, and select the ones you don't like. This is the recommended option, since Delete Cards can check for shared mods before deleting, so you don't accidentally strip a mod something else still needs.
- **Uninstall Contents** — set its input to the staging folder and it reverts everything Install Contents just did, or go into the staging folder, group the cards you don't want into a subfolder, and set that subfolder as the input path to undo just those. This is quicker to set up when you want to revert a whole batch, but it doesn't check for shared mods first.

To get to either folder quickly — the game folder for Delete Cards via the context menu, or the staging folder for grouping unwanted content before running Uninstall Contents — use the **folder component**'s dropdown on the right of any Directory field in the GUI, which has a **Show in Explorer** option that opens it directly.

### Step 8 — Clean up the staging folder _(optional)_

Once you're happy with what got installed, that same folder component dropdown also has a **Clear Contents** option — pick it on the staging folder's Directory field to send everything left in the staging folder to the recycle bin, ready for the next batch.

## 🗂️ Organize Installed Contents

Checks Filter Duplicate Contents → Group Characters → Rename Characters. Delete Cards and Download Missing Mods are left unchecked. For tidying up cards you already have installed: clear out duplicates, sort characters into folders, and rename them.

### Step 1 — Deduplicate _(optional)_

The preset enables **Filter Duplicate Contents** with every custom directory left blank, so it checks your game's own chara, scene, coordinate, mods and overlays folders.

**Tip — limiting what gets checked:** untick any of **Content Types** (Chara / Mods / Coords / Scenes / Overlays) to leave that part of your game alone, or set a custom directory to check somewhere else instead.

### Step 2 — Group characters _(optional)_

The preset enables **Group Characters** with the game's female and male chara folders (the default), sorting characters into per-series folders inside each of them.

### Step 3 — Rename characters _(optional)_

The preset enables **Rename Characters** with the game's female and male chara folders (the default), giving each card a readable filename.

### Step 4 — Remove content you don't want _(optional)_

**Delete Cards** is left unchecked on purpose — once everything's deduplicated, grouped, and renamed by Steps 1–3, copies of the same character sit together and are much easier to pick through. Add it to this preset, or just run it from the [context menu](getting-started/context-menu.md), and Delete Cards will check for shared mods before deleting, so you don't accidentally strip a mod something else still needs.

### Step 5 — Check for missing mods _(optional)_

**Download Missing Mods** is also left unchecked by this preset, but its settings are still preconfigured — enable it and it's already set up for checking your installed cards:

- **Sideloader Modpack** is set to `Only Used`.
- **Custom Chara Directory**, **Custom Scene Directory**, **Custom Coordinate Directory**, **Installed Mods Directory**, and **Downloaded Mods Directory** are left blank (uses game defaults).

All **Mod Sources** (Google Drive, Mega, Telegram, Gofile, Catbox, MediaFire, Pixeldrain) and **Use Telegram Chat Links** are already on for maximum coverage. Just enable **Download Missing Mods** and click **Start** to verify that all mods required by your currently installed cards are present. No staging folder needed.

KKAFIO scans your installed chara cards, scenes, and coordinates, finds any missing mod GUIDs, downloads missing Sideloader Modpack mods from BetterRepack, and (if any **Mod Sources** are checked) downloads any remaining mods from Google Drive, Mega or Telegram.

## 📤 Export Installed Contents

Archive Cards and Export Mods are both left unchecked — they serve different purposes, so enable whichever one matches what you're doing rather than running both: Archive Cards bundles selected cards together with their required mods, while Export Mods pulls any additional mods out by GUID. Use this preset for packaging up your installed content to share with someone else.

## ⚡ All Tasks

Doesn't check everything for you to run in one go — it's mostly used to quickly set up every task's settings on a tab in the GUI, most commonly so they're all configured and ready before [registering the context menu](getting-started/context-menu.md). Most workflows above are a better fit for everyday use.

If you're setting this up for the context menu, right-click its tab in the GUI and choose **Use in Explorer Context Menu** — this makes the tab easier to keep track of, and means the context menu reads settings from that tab specifically rather than falling back to whichever tab happens to be first.

If you're only setting this up for the context menu, you can remove **Create Backup**, **Download Contents**, **Export Mods**, and **Delete Unused Mods** — none of the four appear in the [context menu](getting-started/context-menu.md), so there's nothing there that reads their settings.
