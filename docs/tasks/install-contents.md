# Install Contents

- Given a folder containing chara cards, coordinate cards, scenes, overlays, and zipmod files, copies them into their respective game directories.
- **Content Types to Install** — multi-select: Chara / Mods / Coords / Scenes / Overlays. All five selected by default; deselect any type you don't want copied in.
- Respects the configured **Game Type**: Koikatsu Sunshine installs KK, KKSP, and KKS cards. Koikatsu / Koikatsu Party installs KK and KKSP cards only — KKS cards are skipped with a log message. The same applies to Studio scenes: a KKS scene (scene version 1.1.0.0 or newer) is skipped when the game is Koikatsu / Koikatsu Party, because KK can't load it; KK scenes are installed everywhere. Run **Filter & Convert KKS Cards** first to convert KKS scenes to KK.
- Scene cards (Studio) are installed only if the Studio `scene` folder is present.
- **File Conflicts** _(`Rename` by default)_: `Skip` leaves an existing file in place and doesn't install the new one over it; `Replace` overwrites it; `Rename` installs the new file alongside the existing one under a new name — unless the existing file is identical (same content, compared with xxhash), in which case it is skipped instead of creating a duplicate.
- **Extract Archives** _(on by default)_: extracts ZIP / RAR / 7z archives automatically. Each archive is extracted next to itself into a folder named after it and that folder is left in place; an archive whose folder already exists is not extracted again.
- **Archive Password** _(`Skip` by default)_: `Skip` ignores password-protected archives; `Request Password` prompts you for one when an archive needs it. Has a separate value from Filter & Convert KKS Cards.
- If both Filter & Convert KKS Cards and Install Contents are enabled with the same input folder, archive extraction runs in the filter step only to avoid double-extracting.
