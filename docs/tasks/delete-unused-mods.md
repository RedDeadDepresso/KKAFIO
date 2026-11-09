# Delete Unused Mods

- Sends every zipmod that no character card, scene, or coordinate card uses to the recycle bin. It works out the unused mods as every GUID found in the mods folder, minus every GUID referenced by the chara, scene, and coordinate folders.
- Never touches Sideloader Modpack mods.
- **Exception List** — mods that must never be deleted, one entry per line. A line ending in `\` is a folder inside the mods folder, a line ending in `.zip` or `.zipmod` is a filename (matched case-insensitively), and anything else is a mod GUID. Blank lines and lines starting with `#` are ignored. Defaults to `BetterPenetration\` and `Clo\`.
- **Use Cache** _(on by default)_ — reuses the same incremental mods cache and chara/scene/coordinate GUID caches as Download Missing Mods and Delete Cards.
- **Custom Mods/Chara/Scene/Coordinate Directory** — all blank by default (uses the game's mods, chara, Studio scene, and coordinate folders).
- Refuses to run if none of the chara, scene, or coordinate folders can be found, since every mod would otherwise look unused. Deleted files can still be recovered from the Recycle Bin.
- If two mod files share the same GUID, only one of them is considered, so an unused duplicate copy may be left behind.
