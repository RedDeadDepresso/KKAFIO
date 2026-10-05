# Changelog

All notable changes to KKAFIO are documented in this file.

## [2.1.0]

### Added

- **Review Similar Characters** — new task (`review-similar-chara`) to find characters that were probably saved more than once and choose which to remove in a dialog. Match by similar cover image, same first & last name, or similar filename (ignoring trailing numbers like `_1`, `-05`; grouped per folder). Each card has a lock that engages when you make a choice (and can be toggled by hand); an Auto-select dropdown keeps one card per unlocked group (newest, oldest, biggest, smallest, first/last alphabetically) and ticks the rest. Trashing is done by Delete Cards, with this task's own copy of its options.
- **Group Scenes** — new task to sort Studio scenes into per-author folders using the pepper-scene-index. The index is re-downloaded only when the repository's latest commit changes, and scenes are hashed with the same cache Filter Duplicate Contents uses. Options: Custom Scene Directory, Use Cache, Include Subfolders. Also added to the Explorer context menu, above Group Characters.
- **Compress Cards Textures** — new task to shrink the embedded texture data in character and coordinate cards.
- **Download Missing Mods** — new task to scan cards for zipmods they need but you don't have, and fetch them from BetterRepack and/or Telegram.
- **Delete Unused Mods** — new task to send zipmods that no chara, scene or coordinate card uses to the Recycle Bin. Sideloader Modpack mods are always kept, and an exception list protects folders, filenames or GUIDs.
- **Export Mods** — new task to find specific mods by GUID and copy them out into a folder, ready to share.
- **i18n support** for Japanese, Korean, Simplified Chinese, Traditional Chinese, and Russian, covering both the GUI and the context menu.
- **Filter & Convert KKS Cards** can now convert Koikatsu Sunshine **Studio scenes** to Koikatsu format, in addition to character cards. Scenes are transcoded to the KK layout (KKS-only fields removed, embedded KKS characters down-converted, background path and Timeline owner fixed) and Text objects, which KK doesn't support, are removed. Ported from [KoikatsuSceneConverter](https://github.com/maguro-alternative/KoikatsuSceneConverter).

### Fixed

- Auto-update.
- Downloading content from koikatsucards.com.
- Filter & Convert KKS Cards no longer treats a KKS scene that contains characters as a KKS character card, and now also finds scenes with no characters.
- Re-running Filter & Convert KKS Cards with **Move** on a folder that already holds `KKS2KK_*` copies no longer fails.
- Studio scenes are now recognised by their `KStudio` marker, and checked before character markers. Previously a scene containing characters could be misclassified as a character card, and the old `sceneInfo` marker did not match scenes at all.

### Removed

- **Filter Duplicate Contents**: the *Fuzzy Character Matching* option (`--fuzzy`) and the *Biggest file size* / *Smallest file size* keep strategies. Similar-cover detection moved to Review Similar Characters, which has both size strategies in its Auto-select. The default keep strategy is now **Oldest**; a saved size strategy falls back to it. `kkafio_duplicate_fuzzy_cache.json` is no longer used and can be deleted.

### Changed

- **Install / Uninstall Contents** now only look at `.png`, `.zipmod`, `.zip`, `.rar` and `.7z` files (extensions are matched case-insensitively). Other files in the folder are ignored instead of being reported as "Cannot classify".
- **Ungroup Characters** is now **Ungroup Cards** (`ungroup-cards`) and has a **Card Types** option (Chara / Scenes / Coords) so it can ungroup character cards, Studio scenes and coordinate cards. It now moves only cards of the selected types — zipmods and other files in subfolders are left alone.
- **Filter Duplicate Contents** no longer has an input folder. It now has **Content Types** (like Install Contents) and custom chara, scene, coordinate, mods and overlays directories that default to the game's folders. `--input DIR` still works on the command line (and in the context menu) as a shorthand for scanning one folder for every type.
- Improved cache logic across tasks that scan cards for GUIDs, so repeat runs skip re-parsing files that haven't changed.
- Renamed **Archive Characters** to **Archive Cards** and **Delete Characters** to **Delete Cards**, and added support for Studio scenes and coordinate cards to both.
- Replaced the old presets with new ones: **Download, Filter & Install**, **Organize Installed Contents**, and **Export Installed Contents**.
- Renamed **Filter & Convert KKS Characters** to **Filter & Convert KKS Cards**.
- **Install Contents** and **Uninstall Contents** now check the scene version before acting on a scene and skip KKS scenes when the game is Koikatsu / Koikatsu Party, the same way KKS character cards are already skipped. Sunshine installs are unaffected.
