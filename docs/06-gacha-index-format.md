# Gacha index format

`gacha_character_index.json` is built by the separate
[gacha-character-scraper](https://github.com/RedDeadDepresso/gacha-character-scraper) repo (it scrapes popular gacha
characters' wiki pages) and published on GitHub Pages at
`https://reddeaddepresso.github.io/gacha-character-scraper/gacha_character_index.json`
(`INDEX_URL` in `src/kkafio/cards/gacha_index.py`).

KKAFIO downloads it when **Group Characters** / **Rename Characters** run with
*Hints Language* set to anything but `no`, caches it as `<config>/config/kkafio_gacha_index.json`, and
revalidates with an ETag (a 304 costs no download). Offline, the cached copy is used.

```json
{
  "version": 1,
  "series": [
    {"title": {"en_us": "Genshin Impact", "ja_jp": "原神", "ko_kr": "원신"},
     "aliases": ["Genshin"]}
  ],
  "chars": [
    {"name": {"en_us": "Kamisato, Ayaka", "ja_jp": "神里, 綾華"}, "series": [0], "score": 90,
     "aliases": ["神里綾華", "Ayaka"]}
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
- `aliases` should include the native-script name (cards usually carry Japanese names).
  Note that an alias of just the given name makes a one-field card ambiguous, not
  confident, which is intentional.
- A one-field card name is only treated as confident for a mononym entry.

Priority (`kkafio.cards.char_index.lookup`): gacha confident, MAL confident, gacha
ambiguous, MAL ambiguous. Series names are snapped gacha-first, then MAL.
