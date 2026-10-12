# Gacha index format

`gacha_index.json` is built by the separate gacha-index repo (it scrapes popular gacha
characters' wiki pages) and published on GitHub Pages at
`https://reddeaddepresso.github.io/kkafio-gacha-index/gacha_index.json`
(`INDEX_URL` in `src/kkafio/cards/gacha_index.py`).

KKAFIO downloads it when **Group Characters** / **Rename Characters** run with
*Use Character Index* on, caches it as `<config>/config/kkafio_gacha_index.json`, and
revalidates with an ETag (a 304 costs no download). Offline, the cached copy is used.

```json
{
  "version": 1,
  "series": [
    {"title": "Genshin Impact", "aliases": ["Genshin", "原神"]}
  ],
  "chars": [
    {"name": "Kamisato, Ayaka", "series": [0], "score": 90,
     "aliases": ["神里綾華", "Ayaka"]}
  ]
}
```

- `series[].title` is the folder name Group Characters will use; `aliases` are other
  names an LLM might answer with (snapped to `title`).
- `chars[].name` is `"Last, First"`, or a single word for a mononym (`"Ganyu"`). The
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
