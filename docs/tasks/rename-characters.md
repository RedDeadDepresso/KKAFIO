# Rename Characters

- Translates character card names to English using an LLM. Works on KK, KKSP, and KKS character cards; a KKS card stays a KKS card when its metadata is updated.
- Workflow:
  1. Optionally set a **Custom Chara Directory** (blank = the game's female and male chara folders) and enable **Rename Characters**.
  2. Click **Start** — KKAFIO scans all PNG cards (recursively), builds a JSON mapping `{character_key: {lastname, firstname, nickname}}`, and opens a dialog showing the combined prompt + JSON.
  3. Click **Copy** in the dialog, paste into your LLM of choice. The LLM fills in the English name for each key.
  4. Copy the LLM's reply, click **Paste** in the same dialog — KKAFIO immediately writes the translated names into each card's internal metadata and/or renames the file, depending on the options below.
- **Update card metadata** (off by default): writes the translated names into the card file.
- **Rename PNG files** (on by default): also renames the file on disk to `Lastname_Firstname.png`. Files in subfolders stay in their subfolder.
- **Skip already renamed** (on by default): skips cards whose name is already in the local cache.
- Results are cached in `kkafio_rename_cache.json` inside each chara folder and reused across runs. This translation cache is always used.
- **Use Cache** _(on by default)_ — a separate cache of each card's identification key, `kkafio_chara_key_cache.json` (shared with Group Characters), so unchanged cards aren't re-parsed on every run; with *Rename PNG files* only, no card needs to be parsed at all once the cache is warm. It follows the changes Rename makes: a renamed card's entry moves to its new file name, and a card whose metadata was rewritten gets its new key. Saved when the run ends. Turn it off to read every card.
- **Prompt Language** _(`en_us` by default; CLI: `--prompt-language`)_ — which prompt is sent to the LLM. A language (`en_us`, `zh_cn`, `zh_tw`, `ja_jp`, `ko_kr`, `ru_ru`) sends the built-in prompt for that language, which asks for names in it; the prompt text is then ignored. `custom` sends the **Custom Prompt** text instead. When **Hints Language** is `detect`, the hints use the prompt language too (with `custom` the language is read from the prompt text, as before).
- **Resolve Locally** _(on by default; needs **Hints Language** other than `no`)_ — narrows the hints each name gets in the prompt. When the character indexes name exactly one character for a card, or one of several with the card's own birth month and day, only that character is listed as the hint instead of every name match. Nothing is named or renamed locally: the LLM still fills in the names. If any card behind a name can't be decided this way, that name keeps its full hint list. CLI: `--resolve-locally` / `--no-resolve-locally`.
- **Open LLM Website** _(on by default)_ and **LLM Website URL** _(`https://claude.ai/` by default)_ — open the LLM website in your browser right before the Copy/Paste dialog appears; same as in Group Characters (see above).
- The prompt is fully editable in the settings panel.
- **Recommended LLMs:** same as Group Characters (see above).
- **Warning:** Group Characters uses card metadata to extract character names. It is recommended to use **Rename Characters after Group Characters if Update card metadata is turned on**, as LLMs might not recognize the characters by their translated names.
- **Warning:** It is possible to modify the prompt to allow for transliteration, rather than limiting it to just the character's English name. However, the transliteration of Chinese characters can differ significantly from that of English characters. Transliterating Japanese characters tends to yield better results, although there may be exceptions.
