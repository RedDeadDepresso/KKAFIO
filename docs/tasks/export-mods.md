# Export Mods

- Find specific mods by GUID and copy them out into a folder:
- **Output Directory** defaults to `C:/KKAFIO/Exported Mods`.
- **GUIDs** — paste one GUID per line, or a section straight out of a Download Missing Mods report (e.g. the "Failed mods" list). A leading report bullet (`!`, `✗`, `+`, `~`) is removed; everything from a `#` onward is a comment and is ignored, and every other line is used as the GUID exactly as written, so delete any description or `(...)` note lines first.
- **Rename to GUID** _(on by default)_: renames each exported file to `<guid>.zipmod`, so it's obvious which file is which. Turn off to keep each file's original filename. Recommended to leave on, especially if you're uploading the exported mod to Telegram — Download Missing Mods finds mods there by matching the GUID in the filename, so a file named by its GUID is one it can find accurately, while an original filename may not be.
- Searches both the regular mods folder and any Sideloader Modpack subfolder inside it.
- **Custom Mods Directory** — blank by default (uses the game's default mods folder).
- **Use Cache** _(on by default)_ — reuses the same incremental mods cache as the other tasks.
