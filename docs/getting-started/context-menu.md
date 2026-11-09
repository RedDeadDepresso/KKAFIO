# Context Menu Integration

Run `kkafio_setup.bat` (in the KKAFIO install root) and choose **Register context menu** to add a **KKAFIO** submenu to the Windows Explorer right-click menu. Under the hood this runs `scripts\register_context_menu.bat`, a thin wrapper around `scripts\register_context_menu.ps1` — using the `.bat` avoids Windows' default PowerShell execution policy, which otherwise blocks `.ps1` scripts from running at all. It uses the selected file/folder as an argument; remaining settings are taken from the configuration tab you marked in the GUI (see below), or from the first tab if none is marked.

It first removes any existing KKAFIO menu entries, then asks you to pick a language (used for the menu labels and the script's own prompts) and which folder tasks to include and in what order (enter the numbers shown, e.g. `3 1 4 6`; leave blank for all tasks in the default order). "Organize Library (Game Folders)", "Run GUI" and the PNG entries below are always included. Re-run it any time to change your language or task selection — no need to run an "unregister" step first.

**On folders and folder backgrounds:**

| Entry                      | Action                                                                                                                                 |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Filter & Convert KKS Cards | `filter-convert-kks --input <folder>`                                                                                                  |
| Filter Duplicate Contents  | `filter-duplicate-contents --chara-dir <folder> --scene-dir <folder> --coord-dir <folder> --mods-dir <folder> --overlays-dir <folder>` |
| Download Missing Mods      | `download-missing-mods --chara-dir <folder> --scene-dir <folder> --coord-dir <folder> --downloaded-mods-dir <folder>`                      |
| Organize Library (This Folder) | `organize-library --chara-dir <folder> --scene-dir <folder> --coord-dir <folder> --downloaded-mods-dir <folder>`                         |
| Compress Card Textures    | `compress-card-textures --input <folder>`                                                                                             |
| Install Contents           | `install-contents --input <folder>`                                                                                                    |
| Uninstall Contents         | `uninstall-contents --input <folder>`                                                                                                  |
| Group Scenes               | `group-scenes --scene-dir <folder>`                                                                                                    |
| Group Characters           | `group-chara --chara-dir <folder>`                                                                                                     |
| Ungroup Cards              | `ungroup-cards --input <folder>`                                                                                                       |
| Rename Characters          | `rename-chara --chara-dir <folder>`                                                                                                    |
| Review Similar Characters  | `review-similar-chara --chara-dir <folder>`                                                                                            |
| Organize Library (Game Folders) | `organize-library --game-folders` (the game's own folders, whatever the config has saved)                                                                         |
| Run GUI                    | Opens GUI                                                                                                                              |

**On PNG files (single or multi-select):**

| Entry         | Action                                          |
| ------------- | ----------------------------------------------- |
| Archive Cards | `archive-cards <selected files> --context-menu` |
| Delete Cards  | `delete-cards <selected files> --context-menu`  |

**Choosing which config the context menu uses:** in the GUI, right-click a tab and choose **Use in Explorer Context Menu**. That tab shows a small pointer icon, and every context-menu entry now runs with its settings. Right-click it again and choose **Remove from Explorer Context Menu** to clear it. Only one tab can be marked at a time (marking another moves the mark), and if no tab is marked the first tab is used, as before. The choice is read every time you click an entry, so changing it in the GUI takes effect immediately with no re-registering. (Entries registered by an older version don't know about this yet — run **Register context menu** once more to update them.) Each entry runs `kkafio_cli --instance context-menu <command> ...`.

Run `kkafio_setup.bat` and choose **Unregister context menu** to remove all entries without registering new ones.

**Tip:** the **⚡ All Tasks** [preset](../presets.md) is an easy way to get every task showing up in the GUI at once, so you can configure all of them on your marked tab in one pass before registering the context menu.
