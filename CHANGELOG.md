# Changelog

All notable changes to KKAFIO are documented in this file.

## [2.1.0]

### Added

- **Compress Cards Textures** — new task to shrink the embedded texture data in character and coordinate cards.
- **Download Missing Mods** — new task to scan cards for zipmods they need but you don't have, and fetch them from BetterRepack and/or Telegram.
- **Export Mods** — new task to find specific mods by GUID and copy them out into a folder, ready to share.
- **i18n support** for Japanese, Korean, Simplified Chinese, Traditional Chinese, and Russian, covering both the GUI and the context menu.
- **Filter & Convert KKS Cards** can now convert Koikatsu Sunshine **Studio scenes** to Koikatsu format, in addition to character cards. Scenes are transcoded to the KK layout (KKS-only fields removed, embedded KKS characters down-converted, background path and Timeline owner fixed) and Text objects, which KK doesn't support, are removed. Ported from [KoikatsuSceneConverter](https://github.com/maguro-alternative/KoikatsuSceneConverter).

### Fixed

- Auto-update.
- Downloading content from koikatsucards.com.
- Filter & Convert KKS Cards no longer treats a KKS scene that contains characters as a KKS character card, and now also finds scenes with no characters.
- Re-running Filter & Convert KKS Cards with **Move** on a folder that already holds `KKS2KK_*` copies no longer fails.
- Studio scenes are now recognised by their `KStudio` marker, and checked before character markers. Previously a scene containing characters could be misclassified as a character card, and the old `sceneInfo` marker did not match scenes at all.

### Changed

- Improved cache logic across tasks that scan cards for GUIDs, so repeat runs skip re-parsing files that haven't changed.
- Renamed **Archive Characters** to **Archive Cards** and **Delete Characters** to **Delete Cards**, and added support for Studio scenes and coordinate cards to both.
- Replaced the old presets with new ones: **Download, Filter & Install**, **Organize Installed Contents**, and **Export Installed Contents**.
- Renamed **Filter & Convert KKS Characters** to **Filter & Convert KKS Cards**.
- **Install Contents** and **Uninstall Contents** now check the scene version before acting on a scene and skip KKS scenes when the game is Koikatsu / Koikatsu Party, the same way KKS character cards are already skipped. Sunshine installs are unaffected.
