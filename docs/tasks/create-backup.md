# Create Backup

- Automatically creates a `.7z` archive containing any combination of:
  - `UserData`
  - `Mods` (Sideloader Modpack folders are always excluded — they're re-downloadable and would bloat the archive)
  - `BepInEx`
- **None of the three are selected by default** — pick which folders to include from the checkboxes in the task settings.
- **Output Directory** defaults to `C:/KKAFIO/Backups`.
- **Filename** defaults to `koikatsu_backup`. If an archive with the same name already exists it will be overwritten.
