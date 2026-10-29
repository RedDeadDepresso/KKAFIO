# Gacha index format

`gacha_character_index.json` is built by the separate
[gacha-character-scraper](https://github.com/RedDeadDepresso/gacha-character-scraper) repo (it scrapes popular gacha
characters' wiki pages) and published on GitHub Pages at
`https://reddeaddepresso.github.io/gacha-character-scraper/gacha_character_index.json`
(`INDEX_URL` in `src/kkafio/indices/gacha.py`).

KKAFIO downloads it when **Group Characters** / **Rename Characters** run with
*Hints Language* set to anything but `no`, caches it as `<config>/indices/gacha.json`, and
revalidates with an ETag (a 304 costs no download). Offline, the cached copy is used. (The anime, games, vtubers index it is
merged with is downloaded separately from agv-character-scraper, see `docs/05-configuration.md`.)

```json
{
  "version": 1,
  "series": [
    {"title": {"en_us": "Genshin Impact", "ja_jp": "原神", "ko_kr": "원신"},
     "aliases": ["Genshin"]}
  ],
  "chars": [
    {"name": {"en_us": "Kamisato, Ayaka", "ja_jp": "神里, 綾華"}, "series": [0], "score": 90,
     "aliases": ["神里綾華", "Ayaka"], "birthMonth": 9, "birthDay": 28}
  ]
}
```

`title` and `name` are objects keyed by the language codes of `assets/i18n/` (`en_us`,
`zh_cn`, `zh_tw`, `ja_jp`, `ko_kr`, `ru_ru`). Which one is used is the **Hints Language**
option of Group / Rename Characters (`--hints-language`: `no`, `detect` or a code; `detect`
reads the language from the prompt). A language an entry lacks falls back to `en_us`.
Every language's text and every alias is searchable whatever the hint language.

- `series[].title` (in the hint language) is the folder name Group Characters will use; `aliases` are other
  names an LLM might answer with (snapped to `title`).
- `chars[].name` is `"Last, First"` per language, or a single word for a mononym (`"Ganyu"`). The
  order matters for Rename Characters (it fills last/first name from it); lookups match
  either order.
- `chars[].series` indexes into `series`, most associated first. `score` is a popularity
  proxy used to rank candidates.
- `birthMonth` and `birthDay` are optional integers (month 1-12, day 1-31) on a `chars` entry; leave them out when
  unknown. A value that isn't an integer is ignored. The format version stays 1.
- `aliases` should include the native-script name (cards usually carry Japanese names).

## How it is used

Group Characters' **Resolve Locally** compares a card's `birthMonth` / `birthDay` with these fields to pick one of
several name matches (see the README); the hints sent to the LLM don't contain them.

Nothing is resolved locally. Every card sent to the LLM gets a `hints` list: up to 5
characters whose name matches the card (`kkafio.indices.chara.lookup`), written
`"Last, First (Series)"` in the hint language. The two indexes' scores aren't comparable,
so the lists are merged, not sorted: an index whose best candidate dominates its others
(5x the runner-up's `score`, or the only one) goes first, gacha before the anime, games, vtubers index, then the rest
alternate gacha / anime, games, vtubers, each most popular first. The same name + series from both is
listed once. Group Characters sends `{"key": {"series": "", "hints": [...]}}`, Rename
Characters adds `"hints"` next to `lastname` / `firstname` / `nickname`; the LLM answers
without them. Series names in the answer are then snapped to the canonical title, gacha
titles first, then the anime, games, vtubers index's series.
