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

Defined once in `utils/constants.py`, `_get_config_dir()`:

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
read by Python's `utils.config.Config`. Its structure (this is a Python
docstring copied verbatim from `utils/config.py`, since it's the clearest
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

### `config/kkd_session.json` — koikatsucards.com session

```json
{ "session": "<kkd_session cookie value>" }
```

Written/read by `utils/kkd_session.py`. Used by **Download Contents** when
downloading from koikatsucards.com. Validated against
`koikatsucards.com/api/session` before each use; if invalid, the user is
prompted to paste a fresh cookie via a native dialog
(`utils/password_dialog.py`) and the file is overwritten.

### `config/telegram.json` — Telegram API credentials

```json
{ "api_id": 12345678, "api_hash": "abcdef0123456789..." }
```

Written/read by `utils/telegram_config.py`. Used by **Download Missing
Mods** when the Telegram fallback is enabled. Only `api_id`/`api_hash` are
ever actually written by `save()` in current usage — the module's docstring
also mentions a `"session"` key as part of the intended shape, but nothing
in the codebase currently populates it there; the real Telegram session
lives separately (see next entry).

### `config/tg_session/kkafio.session` — Telethon session

A [Telethon](https://github.com/LonamiWebs/Telethon) SQLite session file
(not JSON), created and managed entirely by the Telethon library itself via
`TelegramClient(str(session_dir / "kkafio"), api_id, api_hash)`. This is
what actually keeps the user logged in to Telegram between runs — losing
this file means going through the phone-number/verification-code (/2FA)
dialogs again. Same directory, `tg_session/`, is also where
`teleget9527`'s multi-connection downloader looks for the session by the
same `"kkafio"` base name.

> ⚠️ This file (and the API credentials above) grant full access to
> whatever Telegram account authorized them. Never share this directory.

### `7zip.json` — cached 7-Zip path (transient)

```json
{ "ArchivePath": "D:/backups/koikatsu_backup.7z", "PID": 12345 }
```

Written by `FileManager.write_backup_info()` right before starting a 7-Zip
archive process, and deleted again as soon as that process finishes
(`self.backup_info_path.unlink(missing_ok=True)` in
`create_game_archive()`). In normal operation this file exists only for the
duration of a single Create Backup run — if you find it lingering, it means
a previous backup was killed mid-archive rather than finishing cleanly.

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

---

## Per-folder caches (live inside the user's own folders, not `CONFIG_DIR`)

These all follow the same pattern: an incremental cache keyed by file path +
mtime + size, so re-running a task against a folder that hasn't changed
reuses previous results instead of re-reading every file. Each is created
the first time the relevant task scans that folder, and silently
regenerates itself if the folder's contents don't match what's cached.

| File | Written inside | Used by | Caches |
|---|---|---|---|
| `kkafio_mods_cache.json` | the mods directory | mod GUID lookups (`utils/chara_ops.py`, used by Download Missing Mods / Archive / Delete) | GUID → zipmod file path |
| `kkafio_coord_cache.json` | the coordinate directory | coordinate matching (`utils/chara_ops.py`, used by Archive/Delete Cards) | colour fingerprints per coordinate file |
| `kkafio_chara_guid_cache.json` | the first configured chara directory | [Download Missing Mods](../wiki/Task-Download-Missing-Mods.md), Delete Cards' shared-mod check | per-chara-card referenced mod GUIDs |
| `kkafio_scene_guid_cache.json` | the first configured scene directory | Download Missing Mods (scene scan), Delete Cards' shared-mod check | per-scene referenced mod GUIDs |
| `kkafio_coord_guid_cache.json` | the first configured coordinate directory | Download Missing Mods (coordinate scan), Delete Cards' shared-mod check | per-coordinate-card referenced mod GUIDs |
| `kkafio_rename_cache.json` | the Rename Chara input folder | [Rename Chara](../wiki/Task-Rename-Chara.md) | which cards have already been renamed, and to what |

None of these are meant to be edited by hand, and all are safe to delete —
worst case, the next run does a full rescan and rebuilds them.

---

## Files shipped with the app (not runtime config, but config-adjacent)

| File | Purpose |
|---|---|
| `interface.json` | The task/option schema — see [03 — interface.json](03-interface-json.md). Read only by the GUI. |
| `assets/kkafio_modpack_index_kk.json` / `assets/kkafio_modpack_index_kks.json` | Pre-built Sideloader Modpack GUID → file indexes, one per game type. Regenerated with `tools/build_modpack_index.py`; see [Modpack Index](../wiki/Modpack-Index.md). Read by `utils/chara_ops.py`'s `load_modpack_index()`. |
| `assets/xkcd_colors.json` | Static named-colour reference list used by the coordinate colour-fingerprint matcher in `utils/chara_ops.py`. Never modified at runtime. |

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
