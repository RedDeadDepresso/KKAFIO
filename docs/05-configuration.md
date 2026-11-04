# Configuration

KKAFIO has no database and no central "settings" object — every kind of
persistent state is its own small JSON file, written by whichever piece of
code needs it, in one of two places:

- **`%APPDATA%/KKAFIO/`** (or the OS equivalent — see below) for anything
  global: GUI state, credentials, sessions, history, logs.
- **Inside the user's own folders** (mods dir, chara dir, input folder,
  etc.) for anything that's a cache *about* that folder's contents — these
  travel with the folder, not with the KKAFIO install.

This doc lists every file KKAFIO reads or writes, grouped by where it lives.

## The config root directory

Defined once in `src/kkafio/core/paths.py`, `_get_config_dir()`:

| OS | Path |
|---|---|
| Windows | `%APPDATA%/KKAFIO` |
| macOS | `~/Library/Application Support/KKAFIO` |
| Linux | `$XDG_CONFIG_HOME/KKAFIO`, or `~/.config/KKAFIO` if unset |

This exact same path is computed independently on the Rust side
(`get_app_data_dir()` in the GUI) — the two implementations must be kept in
sync since several files under this directory are shared between the GUI
and Python (most importantly `config/mxu-KKAFIO.json`). The module creates
this directory on import if it doesn't already exist.

Referred to as `CONFIG_DIR` below.

---

## Global files (under `CONFIG_DIR`)

### `config/mxu-KKAFIO.json` — the GUI's config, and KKAFIO's actual input

The single most important file — this is the entire bridge between the GUI
and the Python backend. Written by the GUI's Rust `save_config` command,
read by Python's `kkafio.core.config.Config`. Its structure (this is a Python
docstring copied verbatim from `src/kkafio/core/config.py`, since it's the clearest
description of the format):

```jsonc
{
  "instances": [
    {
      "id": "...",
      "name": "My Config",
      "globalOptionValues": { "GamePath": {"type": "folder", "path": "..."} },
      "tasks": [
        {
          "taskName": "InstallContents",
          "enabled": true,
          "optionValues": {
            "InputPath":      {"type": "folder",   "path": "D:/cards"},
            "ExtractArchive": {"type": "switch",   "value": true}
            // ... one entry per option this task declares in interface.json
          }
        }
      ],
      "schedulePolicies": [ /* GUI-only, not read by Python */ ]
    }
  ]
}
```

The filename isn't hardcoded as `mxu.json` — it's derived from
`interface.json`'s top-level `"name"` field (`"KKAFIO"`) by the GUI's
`make_config_filename()`, producing `mxu-KKAFIO.json`. This lets multiple
MXU-based projects coexist under the same `%APPDATA%/KKAFIO` root without
their configs colliding, though in practice only KKAFIO's own name is ever
used here.

**When it's used:** written by the GUI on every config change and app
close; read once at the start of every `kkafio_cli run` (or any other
subcommand that needs config, via `--config`/`--instance`).

### `sessions/` — service sessions

All login sessions live in `%APPDATA%/KKAFIO/sessions/`, one `<service>.json` per
service (`koikatsucards.json`, `pixiv.json`, `pawchive.json`, `gofile.json`). Telegram is the exception:
it has its own folder, `sessions/telegram/`.

### `sessions/koikatsucards.json` — koikatsucards.com session

```json
{ "session": "<kkd_session cookie value>" }
```

Written/read by `src/kkafio/services/content_sources/koikatsucards/session.py`. Used by **Download Contents** when
downloading from koikatsucards.com (not by Download Missing Mods, which
uses the KKC mod index above). Validated against
`koikatsucards.com/api/session` before each use; if invalid, the user is
prompted to paste a fresh cookie via a native dialog
(`src/kkafio/dialogs/password_dialog.py`) and the file is overwritten.

### Gofile environment variables

**Download Contents** needs no stored configuration for `gofile.io` links (a guest
account is created per run; its token and the signature-rejection cooldown are kept in
`sessions/gofile.json` between runs). Optional environment variables: `GOFILE_TOKEN` (use a
real account's token) and `GOFILE_SALT` (override the website-token salt when gofile
rotates it; the symptom of a stale salt is an `error-notPremium` error).

### `sessions/pixiv.json` — pixiv.net session

```json
{ "session": "<PHPSESSID cookie value>" }
```

Written/read by `src/kkafio/services/content_sources/pixiv/session.py`. Used by **Download Contents**
for `pixiv.net` links. Lookup order: the `PIXIV_PHPSESSID` environment variable,
this file, then a native dialog. The value is only checked for its shape
(`<user id>_<token>`); there is no online validation. A missing session is not
an error — public works still download, only R-18 / restricted ones need it —
so cancelling the dialog continues without a login. The cookie is scoped to
`.pixiv.net` and never sent to the image host (`i.pximg.net`).

### `sessions/pawchive.json` — pawchive.pw session

```json
{ "session": "<session cookie value>" }
```

Written/read by `src/kkafio/services/content_sources/pawchive/session.py`. Used by **Download Contents**
for the oversized files Pawchive keeps in temporary storage (`t1.pawchive.pw`), which
are only served to a logged-in account. Unlike the other sessions it is asked for
lazily: only when such a file is about to be downloaded, and at most once per run.
Lookup order: the `PAWCHIVE_SESSION` environment variable, this file, then a native
dialog (after opening `pawchive.pw/account/login`). A stored or pasted value is
checked with `GET pawchive.pw/account`: a redirect or 401 means invalid/expired (a new
one is asked for); a network error or a 403 proves nothing, so the value is used as it
is. Cancelling the dialog continues without a login (the temporary files then fail).
The cookie is scoped to `.pawchive.pw` and never sent to other hosts.

### `sessions/telegram/telegram.json` — Telegram API credentials

```json
{ "api_id": 12345678, "api_hash": "abcdef0123456789..." }
```

Written/read by `src/kkafio/services/mod_sources/telegram/config.py`. Used by **Download Missing
Mods** when a Telegram source is enabled. Only `api_id`/`api_hash` are
ever actually written by `save()` in current usage — the module's docstring
also mentions a `"session"` key as part of the intended shape, but nothing
in the codebase currently populates it there; the real Telegram session
lives separately (see next entry).

### `sessions/telegram/kkafio.session` — Telethon session

A [Telethon](https://github.com/LonamiWebs/Telethon) SQLite session file
(not JSON), created and managed entirely by the Telethon library itself via
`TelegramClient(str(session_dir / "kkafio"), api_id, api_hash)`. This is
what actually keeps the user logged in to Telegram between runs — losing
this file means going through the phone-number/verification-code (/2FA)
dialogs again. Same directory, `sessions/telegram/`, is also where
`teleget9527`'s multi-connection downloader looks for the session by the
same `"kkafio"` base name.

> ⚠️ This file (and the API credentials above) grant full access to
> whatever Telegram account authorized them. Never share this directory.

### `indices/` — downloaded index caches

`%APPDATA%/KKAFIO/indices/` (under `CONFIG_DIR`, not inside `config/`) holds every index KKAFIO downloads.
Each index is stored as `<name>.json`, named after the module in `src/kkafio/indices/` that downloads it,
next to a `<name>.json.etag` file with the ETag of the copy on disk:

| File | Module | Source | Used by |
|---|---|---|---|
| `agv.json` | `agv.py` | [agv-character-scraper](https://github.com/RedDeadDepresso/agv-character-scraper) | Group / Rename Characters hints |
| `gacha.json` | `gacha.py` | [gacha-character-scraper](https://github.com/RedDeadDepresso/gacha-character-scraper) | Group / Rename Characters hints |
| `kkc_mod.json` | `kkc_mod.py` | [kkc-mod-scraper](https://github.com/RedDeadDepresso/kkc-mod-scraper) | Download Missing Mods |
| `password.json` | `password.py` | [kk-password-scraper](https://github.com/RedDeadDepresso/kk-password-scraper) | archive extraction |
| `scene.json` | `scene.py` | [pepper-scene-hasher](https://github.com/RedDeadDepresso/pepper-scene-hasher) | Group Scenes |

All of them are refreshed the same way (`src/kkafio/indices/cache.py`):

1. The cached copy is requested again with a conditional request (`If-None-Match` with the saved ETag).
2. A `304` means the cached copy is current and nothing is downloaded.
3. Otherwise the new index is validated, then written to `<name>.json` and its ETag to `<name>.json.etag`.
   A download that fails validation (e.g. an `agv.json` that isn't format version 1) leaves the cached copy alone.
4. If the server can't be reached (offline, rate-limited), the cached index is used if one exists; without one
   the task runs without that index.

Formats:

- `kkc_mod.json` maps mod GUIDs to Telegram message links, e.g.
  `{ "com.AgiShark.Casual_Moves_Part2": "https://t.me/KK_archive_modlibrary/25" }`. It is only read when at
  least one mod needs a Telegram download and the `KoikatsuCards` entry of Telegram Sources is checked.
- `agv.json` is the character / series index (format version 1; anime and visual novel / JRPG games from the
  Bangumi Archive) used for the `hints` that **Group Characters** and **Rename Characters** send to the LLM. It is
  refreshed when those tasks run with *Hints Language* set to anything but `no`. An entry of `chars` is
  `[names, series ids, score]`, optionally followed by `birthMonth` and `birthDay` (integers, month 1-12 and day
  1-31): `[names, series ids, score, 9, 28]`. Both may be left out, and `null` stands for an unknown one
  (`[names, series ids, score, null, 28]`). The format version stays 1.
- `gacha.json`: see `docs/06-gacha-index-format.md`.

All files are safe to delete; the next run downloads a fresh copy.

### `download_history.json` — Download Contents dedup

Lives directly under `CONFIG_DIR` (not inside `config/`):
`%APPDATA%/KKAFIO/download_history.json`. A flat history of previously
downloaded URLs/filenames, consulted when **Skip already downloaded** is
enabled on **Download Contents** so re-running the same links doesn't
re-download everything.

### `debug/*.log` and `debug/runtime-<instance-id>.log`

Written by the GUI, not Python. `debug/*.log` are the GUI's own internal
application logs. `debug/runtime-<instance-id>.log` is where the GUI mirrors
every log line it receives from the running `kkafio_cli` subprocess (see
[04 — The MXU GUI](04-mxu-gui.md#logs-three-different-places-one-source)),
specifically so **Settings → Export Logs** has an on-disk copy of task
output to bundle up, since the live log panel itself is only ever kept in
memory/`localStorage`.

**Verbose diagnostics.** Failures that are deliberately ignored (an unreadable
cache file, a card that could not be parsed, a best-effort cleanup) are logged
at `DEBUG` level. That level is shown when running from source and hidden in
packaged (frozen) builds. Set the environment variable `KKAFIO_DEBUG=1`
before launching a packaged build to see them (e.g. to find out why a
particular card's mods were not counted by Delete Cards), or
`KKAFIO_DEBUG=0` to silence them when running from source.

---

## Per-folder caches (live inside the user's own folders, not `CONFIG_DIR`)

These all follow the same pattern: an incremental cache keyed by file path +
mtime + size, so re-running a task against a folder that hasn't changed
reuses previous results instead of re-reading every file. Each is created
the first time the relevant task scans that folder, and silently
regenerates itself if the folder's contents don't match what's cached.

| File | Written inside | Used by | Caches |
|---|---|---|---|
| `kkafio_mods_cache.json` | the mods directory | mod GUID lookups (`src/kkafio/cards/mods/zipmods.py`, used by Download Missing Mods / Archive / Delete) | GUID → zipmod file path |
| `kkafio_coord_cache.json` | the coordinate directory | coordinate matching (`src/kkafio/cards/chara/outfits.py`, used by Archive/Delete Cards) | xxh3-128 digest of each coordinate card's clothes + accessory data, with modded item IDs normalised to `ModID:Slot` via the file's own UAR info (looked up against a chara card's outfits) |
| `kkafio_chara_guid_cache.json` | each scanned chara directory (one file per folder) | [Download Missing Mods](../wiki/Task-Download-Missing-Mods.md), Delete Cards' shared-mod check | per-chara-card referenced mod GUIDs |
| `kkafio_scene_guid_cache.json` | each scanned scene directory (one file per folder) | Download Missing Mods (scene scan), Delete Cards' shared-mod check | per-scene referenced mod GUIDs |
| `kkafio_coord_guid_cache.json` | each scanned coordinate directory (one file per folder) | Download Missing Mods (coordinate scan), Delete Cards' shared-mod check | per-coordinate-card referenced mod GUIDs |
| `kkafio_rename_cache.json` | the Rename Chara input folder | [Rename Chara](../wiki/Task-Rename-Chara.md) | which cards have already been renamed, and to what |

None of these are meant to be edited by hand, and all are safe to delete —
worst case, the next run does a full rescan and rebuilds them.

---

## Files shipped with the app (not runtime config, but config-adjacent)

| File | Purpose |
|---|---|
| `interface.json` | The task/option schema — see [03 — interface.json](03-interface-json.md). Read only by the GUI. |
| `assets/indices/kk_modpack.json` / `assets/indices/kks_modpack.json` | Pre-built Sideloader Modpack GUID → download URLs indexes, one per game type. Regenerated with `tools/build_modpack_index.py`; see [Modpack Index](#modpack-index) below. Read by `src/kkafio/indices/modpack.py`'s `load_modpack_index()`. |
| `assets/indices/default_coordinate.json` | Optional list of default coordinates (a costume pack's stock outfits, say) that Group Coordinates leaves alone. Not shipped: written by `tools/build_default_coordinate.py`; see [Default Coordinates](#default-coordinates) below. Read by `src/kkafio/cards/chara/outfits.py`'s `default_coordinate_digests()`. |
| `assets/indices/author.json` | The known scene authors, `{"version", "updated", "authors": [{"name", "alias": [...]}]}`. Manage Library's Scene tab suggests these names (matching the aliases too, and resolving an alias to the name) and groups scenes under `<scene folder>/<name>/`. Static; read by `src/kkafio/services/scene_authors.py`. |
| `assets/indices/xkcd_color.json` | Static named-colour reference list used by the coordinate colour-fingerprint matcher in `src/kkafio/cards/classifier.py`. Never modified at runtime. |

---

## Default Coordinates

Default coordinates say nothing about whose outfit a coordinate is, so Group Coordinates can skip
them per file: a coordinate card that is one of your default coordinates is not matched to any
character, not moved, and not counted when grouping by hair.

Put the default coordinates in a folder (any depth; other PNGs are skipped) and run:

```
python tools/build_default_coordinate.py "C:/KK/default coordinates"
```

This writes `assets/indices/default_coordinate.json` (replacing its previous content). Each
default coordinate is recorded by its outfit digest, the same one Group Coordinates compares, so
a copy that was renamed, moved to another folder or saved with a different picture is still
recognised. Options:

| Option | Effect |
|---|---|
| `--merge` | Add to the existing file instead of replacing it. |
| `--output PATH` | Write somewhere else. |

Delete the file to stop skipping anything. Nothing needs rebuilding: the lookup happens on every run.

---

## Modpack Index

KKAFIO ships with two pre-built modpack index files:

| File | Game |
|---|---|
| `assets/indices/kk_modpack.json` | Koikatsu / Koikatsu Party |
| `assets/indices/kks_modpack.json` | Koikatsu Sunshine |

Each maps a mod GUID to the URLs it can be downloaded from on the BetterRepack server, best first:

```json
{
 "generated": "2026-11-03T12:00:00",
 "game_type": "kk",
 "count": 4300,
 "guids": {"com.example.mod": ["https://sideload.betterrepack.com/download/KKEC/Sideloader%20Modpack/a/mod.zipmod"]}
}
```

The path of a mod inside the mods folder is not stored: it is the URL's path below
`/download/KKEC/`, percent-decoded (`Sideloader Modpack/a/mod.zipmod`).
`kkafio.indices.modpack.url_to_rel_path()` does this and refuses URLs that are not on the
BetterRepack server or that could leave the mods folder.

Archive Cards, Delete Cards, and Download Missing Mods use the index for
the configured game type to instantly identify which required mods are
covered by the Sideloader Modpack. If a GUID is not in the index, KKAFIO
falls back to scanning the local mods folder automatically. A file that
isn't in this format (for example one from an older release) is ignored with a
warning in the log.

To regenerate the indexes after the Sideloader Modpack is updated, run:

```
python tools/build_modpack_index.py                  # both games
python tools/build_modpack_index.py --game-type kks  # one game
```

The script crawls the server's directory listings, then reads the GUID and version of each
new or changed zipmod with HTTP Range requests (the end of the zip and `manifest.xml` only,
never the whole file). Nothing needs to be downloaded or installed locally.

- **Which mods:** the main `Sideloader Modpack` folder plus the game's own categories
  (`KK_CATEGORIES` / `KKS_CATEGORIES` in the script), Studio included. Bleeding Edge and
  EmotionCreators are not part of the BetterRepack game release and are neither crawled nor indexed.
- **URL order:** the main folder first, then the specialised categories; within those the
  highest manifest version, then the newest file date. Identical copies in several folders (same
  filename, date and size) are listed once, and at most `--max-urls` (default 4) are kept.
- **Incremental updates:** the date and size shown in the listing are remembered per URL in
  `tools/modpack_index_cache.json` (git-ignored, shared by both games). Unchanged files are not
  probed again, and a file that only moved or was renamed keeps its GUID. A second run takes
  seconds. Files that can't be indexed (ZIP64, no `manifest.xml`, ...) are remembered too.
- **Options:** `--full` ignores the cache and probes everything; `--retry-failed` probes the
  remembered failures again; `--output PATH` (with a single `--game-type`) writes elsewhere;
  `--cache PATH` moves the cache; `--workers N` sets the parallel requests (default 8, max 16).
- **Incomplete runs:** if a listing or some files could not be reached, the script still
  writes the indexes, prints a warning and exits with status 2. Run it again before shipping.

Copy the updated `.json` files next to `kkafio_cli.exe` or commit them to
the repository to ship them with the next release.

---

## Quick reference: "where does X live?"

- **Which tasks are enabled, and their settings** → `config/mxu-KKAFIO.json`
- **A password/cookie/API key you typed into a dialog** → somewhere under
  `CONFIG_DIR` (see the "Global files" section above for exactly which file)
- **"Have I already scanned this folder?"** → a `kkafio_*_cache.json` file
  inside that folder itself
- **"Have I already downloaded this card?"** →
  `%APPDATA%/KKAFIO/download_history.json`
- **Task output you can hand to a bug report** → **Settings → Export Logs**
  in the GUI, which bundles `debug/*.log` (see
  [04 — The MXU GUI](04-mxu-gui.md))
