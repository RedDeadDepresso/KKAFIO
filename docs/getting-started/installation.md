# Installation

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
2. **Enable clipboard history (Win+V)** — turns on Windows clipboard history for your user, so the failed links / mod GUIDs that Download Contents and Download Missing Mods copy to the clipboard stay in the Win+V list instead of being replaced by your next copy.
3. **Register context menu** — see [Context Menu Integration](context-menu.md).
4. **Unregister context menu** — removes it again.
5. **Delete KKAFIO config and default task folders** — deletes `%APPDATA%\KKAFIO` (saved config, Telegram session, download history) and `C:\KKAFIO` (the four folders above). Asks for confirmation first; cannot be undone.
