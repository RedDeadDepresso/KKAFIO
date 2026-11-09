# AGV character index

Published by [agv-character-scraper](https://github.com/RedDeadDepresso/agv-character-scraper) and cached as `indices/agv.json` (refreshed like every [downloaded index](index.md#downloaded-indices)). The gacha-game counterpart is the [gacha index](gacha-index.md).

It is the character / series index (format version 1; anime and visual novel / JRPG games from the
Bangumi Archive) used for the `hints` that **Group Characters** and **Rename Characters** send to the LLM. It is
refreshed when those tasks run with *Hints Language* set to anything but `no`. An entry of `chars` is
`[names, series ids, score]`, optionally followed by `birthMonth` and `birthDay` (integers, month 1-12 and day
1-31): `[names, series ids, score, 9, 28]`. Both may be left out, and `null` stands for an unknown one
(`[names, series ids, score, null, 28]`). The format version stays 1.
