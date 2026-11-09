# Data formats

The files KKAFIO downloads, ships or generates for lookups, and what is inside them. Where the downloaded ones are kept on disk is also listed in [Configuration](../configuration.md).

## Downloaded indices

`%APPDATA%/KKAFIO/indices/` (under `CONFIG_DIR`, not inside `config/`) holds every index KKAFIO downloads.
Each index is stored as `<name>.json`, named after the module in `src/kkafio/indices/` that downloads it,
next to a `<name>.meta.json` file, `{"etag": "...", "last_checked": "<ISO 8601 datetime>"}`, with the ETag of the
copy on disk and when KKAFIO last confirmed it is current (e.g. `password.json` and `password.meta.json`):

| File | Module | Source | Used by | Format |
|---|---|---|---|---|
| `agv.json` | `agv.py` | [agv-character-scraper](https://github.com/RedDeadDepresso/agv-character-scraper) | Group / Rename Characters hints | [agv index](agv-index.md) |
| `gacha.json` | `gacha.py` | [gacha-character-scraper](https://github.com/RedDeadDepresso/gacha-character-scraper) | Group / Rename Characters hints | [gacha index](gacha-index.md) |
| `mod.json` | `mod.py` | [kk-mod-scraper](https://github.com/RedDeadDepresso/kk-mod-scraper) | Download Missing Mods | [mod index](mod-index.md) |
| `password.json` | `password.py` | [kk-password-scraper](https://github.com/RedDeadDepresso/kk-password-scraper) | archive extraction | — |
| `scene.json` | `scene.py` | [pepper-scene-hasher](https://github.com/RedDeadDepresso/pepper-scene-hasher) | Group Scenes | — |
| `modpack.json` | `modpack.py` | [kk-mod-scraper](https://github.com/RedDeadDepresso/kk-mod-scraper) | Archive Cards, Delete Cards, Download Missing Mods | [modpack index](modpack-index.md) |

All of them are refreshed the same way (`src/kkafio/indices/cache.py`):

1. If the cached copy was last checked less than 1 hour ago (`last_checked`), the server isn't asked at all.
2. Otherwise the cached copy is requested again with a conditional request (`If-None-Match` with the saved ETag).
3. A `304` means the cached copy is current: nothing is downloaded and `last_checked` is set to now.
4. Otherwise the new index is validated, then written to `<name>.json`, with its ETag and the current time in
   `<name>.meta.json`. A download that fails validation (e.g. an `agv.json` that isn't format version 1) leaves the
   cached copy and its `last_checked` alone.
5. If the server can't be reached (offline, rate-limited), the cached index is used if one exists; without one
   the task runs without that index.

All files are safe to delete; the next run downloads a fresh copy.

## Shipped data

| File | Purpose |
|---|---|
| `assets/indices/default_coordinate.json` | Default coordinates Group Coordinates leaves alone. Built by a tool, not shipped: see [Default coordinates](default-coordinates.md). |
| `assets/indices/author.json` | The known scene authors, used by Organize Library's Scene tab: see [Configuration](../configuration.md#files-shipped-with-the-app-not-runtime-config-but-config-adjacent). |
