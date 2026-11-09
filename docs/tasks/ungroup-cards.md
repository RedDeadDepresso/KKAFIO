# Ungroup Cards

- Reverse of Group Characters: moves cards from subfolders back to the top-level input folder.
- **Card Types** — pick which of Chara / Scenes / Coords to ungroup (all three selected by default). Only cards of the selected types are moved; anything else in the subfolders (overlays, zipmods, other files) is left where it is.
- If the input folder is the game's `UserData/chara` folder, cards are ungrouped inside `female/` and `male/` instead, so they never end up directly in `UserData/chara` where the game can't read them. (Pointing it at `female/` or `male/` directly works as usual.)
- **Optional:** Deletes empty subfolders after moving (on by default).
