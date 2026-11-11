# CLI Usage

`kkafio_cli` exposes every task as a subcommand. Arguments override config; omit them to use config defaults.

```
kkafio_cli run                                    # run all enabled tasks from config

kkafio_cli download-contents [--links URLS_OR_FILE] [--output-dir DIR]
                             [--skip-downloaded | --no-skip-downloaded]

kkafio_cli download-missing-mods [--installed-mods-dir DIR] [--downloaded-mods-dir DIR] [--chara-dir DIR] [--scene-dir DIR] [--coord-dir DIR] [--game-folders]
                                 [--no-chara] [--no-scene] [--no-coord]
                                 [--use-cache | --no-use-cache]
                                 [--open-report | --no-open-report]
                                 [--modpack-mode Skip|OnlyUsed|All]
                                 [--mod-sources [Drive|Mega|Telegram|Gofile|Catbox|MediaFire|Pixeldrain ...]]
                                 [--use-telegram-chat-links | --no-use-telegram-chat-links]
                                 [--telegram-chat-links LINKS]

kkafio_cli export-mods [--output DIR] [--guids TEXT | --guids-file FILE]
                        [--rename-to-guid | --no-rename-to-guid]
                        [--use-cache | --no-use-cache]
                        [--mods-dir DIR] [--game-folders]

kkafio_cli delete-unused-mods [--exceptions TEXT | --exceptions-file FILE]
                              [--use-cache | --no-use-cache]
                              [--mods-dir DIR] [--chara-dir DIR] [--scene-dir DIR] [--coord-dir DIR] [--game-folders]

kkafio_cli compress-card-textures [--input DIR] [--tool-path DIR] [--mode {auto,png,jpg,maintex}]
                                   [--max-size PX]
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
                             [--chara | --no-chara] [--mods | --no-mods] [--coords | --no-coords]
                             [--scenes | --no-scenes] [--overlays | --no-overlays]
                             [--chara-dir DIR] [--scene-dir DIR] [--coord-dir DIR]
                             [--mods-dir DIR] [--overlays-dir DIR] [--game-folders]
                             [--keep STRATEGY]
                             [--action move-rename|move|delete]
                             [--use-cache | --no-use-cache]

kkafio_cli install-contents   [--input DIR]
                           [--extract-archive | --no-extract-archive]

kkafio_cli uninstall-contents [--input DIR]

kkafio_cli rename-chara    [--chara-dir DIR] [--game-folders]
                           [--skip-already-renamed | --no-skip-already-renamed]
                           [--update-metadata | --no-update-metadata]
                           [--rename-files | --no-rename-files]
                           [--use-cache | --no-use-cache]
                           [--open-llm-website | --no-open-llm-website] [--llm-website-url URL]

kkafio_cli group-scenes    [--scene-dir DIR] [--game-folders] [--use-cache | --no-use-cache] [--include-subfolders]
                           [--subgroup-by-year | --no-subgroup-by-year]
kkafio_cli group-chara     [--chara-dir DIR] [--game-folders] [--include-subfolders] [--use-cache | --no-use-cache]
                           [--open-llm-website | --no-open-llm-website] [--llm-website-url URL]

kkafio_cli ungroup-cards   [--input DIR]
                           [--chara | --no-chara] [--scenes | --no-scenes] [--coords | --no-coords]
                           [--delete-empty | --no-delete-empty]

kkafio_cli review-similar-chara [--mode cover|name|filename]
                           [--check-shared-mods | --no-check-shared-mods]
                           [--include-coordinates | --no-include-coordinates]
                           [--auto-resolve | --no-auto-resolve]
                           [--use-cache | --no-use-cache]
                           [--mods-dir DIR] [--chara-dir DIR] [--scene-dir DIR] [--coord-dir DIR] [--game-folders]

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
                           [--mods-dir DIR] [--chara-dir DIR] [--scene-dir DIR] [--coord-dir DIR] [--game-folders]

# Global options (all commands):
kkafio_cli --config PATH --instance N|context-menu <command>
```

**Game folders instead of saved folders.** A task's folder options (`--chara-dir`, `--scene-dir`, `--coord-dir`, `--mods-dir`, ...) override the folders saved in the config; left out, the saved folder is used, and a folder that is blank there means the game's own. To use the game's own folders for one run even though the config has a folder saved, add **`--game-folders`**. It is accepted by every task whose folders can fall back to the game's: `download-missing-mods`, `export-mods`, `delete-unused-mods`, `filter-duplicate-contents`, `rename-chara`, `group-chara`, `group-scenes`, `group-coordinates`, `organize-library`, `review-similar-chara` and `delete-cards`. A folder named by another option still wins, so `--game-folders --chara-dir D:\cards` uses `D:\cards` for the characters and the game's folders for the rest. Nothing is written to the config. (The other tasks don't take it: `install-contents`, `uninstall-contents`, `ungroup-cards`, `compress-card-textures`, `filter-convert-kks`, `download-contents` and `create-backup` work on an input or output folder you give, and `archive-cards` has folder overrides that only apply with `--no-auto-resolve`, so none of them has a game folder to fall back to. For the same reason `delete-cards` and `review-similar-chara` leave `--mods-dir` alone.)
