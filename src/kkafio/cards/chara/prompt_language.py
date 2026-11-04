"""
prompt_language.py — The LLM prompt of Group Characters / Rename Characters, per language.

The "Prompt Language" option picks which prompt is sent:

    "custom"   the task's own prompt option (``Prompt`` — the user's text)
    <code>     the built-in prompt written for that language (``en_us`` — the default — ``ja_jp`` ...), which asks
               the LLM for series titles / names in that language; the ``Prompt`` option is ignored

The language also decides the hints language when that is left on "detect" (see
`hint_language.resolve`): the language asked of the LLM is the one the hints should be in.

The instructions themselves stay in English on purpose (LLMs follow English instructions best);
only the language of the answer changes.
"""

from __future__ import annotations

from kkafio.core.logger import logger

CUSTOM = "custom"
DEFAULT_PROMPT_LANGUAGE = "en_us"

GROUP = "group"
RENAME = "rename"

# code -> (language name, how the answer is to be written)
_LANGUAGES: dict[str, tuple[str, str]] = {
    "ja_jp": ("Japanese", "in Japanese, using kanji and kana"),
    "ko_kr": ("Korean", "in Korean, using hangul"),
    "ru_ru": ("Russian", "in Russian, using Cyrillic"),
    "zh_cn": ("Simplified Chinese", "in Chinese, using simplified Chinese characters"),
    "zh_tw": ("Traditional Chinese", "in Chinese, using traditional Chinese characters"),
}

_HEADER = """\
You will receive a JSON object whose keys identify Koikatsu character card files.
Each key has the format:  lastname | firstname | nickname | personality | hair_color
"""

# ---------------------------------------------------------------------------
# English — identical to the default of the tasks' ``Prompt`` option
# ---------------------------------------------------------------------------

_GROUP_EN = """\
You will receive a JSON object whose keys identify Koikatsu character card files.
Each key has the format:  lastname | firstname | nickname | personality | hair_color

Your task: for every key, write the English name of the anime/game series the character \
is from.

A value is either an empty string "" or an object {"series": "", "hints": [...]}. "hints" \
lists up to 5 candidate characters from a character database, written \
"Last, First (Series)", most popular first. They are only name matches and can be wrong: \
use the one that fits the personality and hair colour, and ignore the list if none does.

Rules:
- Use the official title of the series.
- If a character appears in multiple series, use the one they are most associated with.
- Some characters may be VTubers (virtual YouTubers). Use "VTubers" as the series for them.
- If you use a hint whose series is not in English, translate it into English.
- Use the personality and hair colour as additional hints to identify the character.
- If you do not recognise a character and can search the web, look it up online first.
- If you are still not sure or the character is an original creation, leave the value as an empty string "".
- Return ONLY a JSON object that maps every key to the series name as a plain string \
(do not repeat "hints") — no explanation, no markdown code fences, no extra text \
before or after.

JSON to fill in:
"""

_RENAME_EN = """\
You will receive a JSON object whose keys identify Koikatsu character card files.
Each key has the format:  lastname | firstname | nickname | personality | hair_color

Your task: for every key fill in "lastname", "firstname", and "nickname" with the
character's English name.

Rules:
- Use the English name the character is commonly known by (the romanisation used
  in official releases), not a literal reading of the card.
- If you do not recognise the character or are not confident, search the web for
  it if you can. If you still do not know, fall back to your best transliteration
  of the name written on the card.
- Always fill in all three fields; never leave them empty.
- firstname = given name, lastname = family name, regardless of the order the
  name is written in the source material.
- "nickname" can be a common short form or the same as firstname.
- Use the personality and hair colour as additional hints to identify the character.
- An entry may have a "hints" list: up to 5 candidate characters from a character
  database, written "Last, First (Series)", most popular first. They are only name
  matches and can be wrong: use the one that fits the personality and hair colour (take
  its name from it), and ignore the list if none does. Do not repeat "hints" in
  your answer.
- Some characters may be VTubers (virtual YouTubers); their hints are written with the series
  "VTubers". Use the name from the hint.
- If you use a hint whose name is not in English, translate it into English.
- Return ONLY the completed JSON object — no explanation, no markdown fences,
  no extra text before or after.

JSON to fill in:
"""


# ---------------------------------------------------------------------------
# The other languages
# ---------------------------------------------------------------------------

def _group(language: str, how: str) -> str:
    return _HEADER + f"""
Your task: for every key, write the {language} title of the anime/game series the character \
is from, written {how}.

A value is either an empty string "" or an object {{"series": "", "hints": [...]}}. "hints" \
lists up to 5 candidate characters from a character database, written \
"Last, First (Series)", most popular first. They are only name matches and can be wrong: \
use the one that fits the personality and hair colour, and ignore the list if none does.

Rules:
- Use the official {language} title of the series (the one used in official {language} releases). \
If it has none, translate or transliterate its original title into {language}.
- If a character appears in multiple series, use the one they are most associated with.
- Some characters may be VTubers (virtual YouTubers). Use "VTubers" as the series for them \
(exactly this English word, whatever the language).
- If you use a hint whose series is not in {language}, translate it into {language}.
- Use the personality and hair colour as additional hints to identify the character.
- If you do not recognise a character and can search the web, look it up online first.
- If you are still not sure or the character is an original creation, leave the value as an empty string "".
- Return ONLY a JSON object that maps every key to the series name as a plain string \
(do not repeat "hints") — no explanation, no markdown code fences, no extra text \
before or after.

JSON to fill in:
"""


def _rename(language: str, how: str) -> str:
    return _HEADER + f"""
Your task: for every key fill in "lastname", "firstname", and "nickname" with the
character's {language} name, written {how}.

Rules:
- Use the {language} name the character is commonly known by (the one used in official
  {language} releases), not a literal reading of the card.
- If you do not recognise the character or are not confident, search the web for
  it if you can. If you still do not know, fall back to your best transliteration
  into {language} of the name written on the card.
- Always fill in all three fields; never leave them empty.
- firstname = given name, lastname = family name, regardless of the order the
  name is written in the source material.
- "nickname" can be a common short form or the same as firstname.
- Use the personality and hair colour as additional hints to identify the character.
- An entry may have a "hints" list: up to 5 candidate characters from a character
  database, written "Last, First (Series)", most popular first. They are only name
  matches and can be wrong: use the one that fits the personality and hair colour (take
  its name from it), and ignore the list if none does. Do not repeat "hints" in
  your answer.
- Some characters may be VTubers (virtual YouTubers); their hints are written with the series
  "VTubers". Use the name from the hint.
- If you use a hint whose name is not in {language}, translate it into {language}.
- Return ONLY the completed JSON object — no explanation, no markdown fences,
  no extra text before or after.

JSON to fill in:
"""


_PROMPTS: dict[str, dict[str, str]] = {
    GROUP: {"en_us": _GROUP_EN, **{c: _group(n, h) for c, (n, h) in _LANGUAGES.items()}},
    RENAME: {"en_us": _RENAME_EN, **{c: _rename(n, h) for c, (n, h) in _LANGUAGES.items()}},
}

# The task's default prompt is the English one (what the ``Prompt`` option ships with).
GROUP_DEFAULT_PROMPT = _GROUP_EN
RENAME_DEFAULT_PROMPT = _RENAME_EN


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def languages() -> list[str]:
    """The languages that have a built-in prompt (English first)."""
    return list(_PROMPTS[GROUP])


def choices() -> list[str]:
    """Every value of the setting: ``custom`` then the languages."""
    return [CUSTOM, *languages()]


def _normalise(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def language_of(setting: object) -> str | None:
    """The language a setting asks for, or None for ``custom`` (and for an unknown value)."""
    value = _normalise(setting) or DEFAULT_PROMPT_LANGUAGE
    return value if value in _PROMPTS[GROUP] else None


def builtin(kind: str, language: str) -> str:
    """The built-in prompt of `kind` (``GROUP`` / ``RENAME``) for a language code."""
    return _PROMPTS[kind][language]


def resolve(setting: object, custom_prompt: str, kind: str, tag: str) -> tuple[str, str | None]:
    """The prompt to send, and the language it asks for (None when it is the custom prompt).

    `setting` is the Prompt Language option's value: ``custom`` uses `custom_prompt`; a language
    code uses that language's built-in prompt, ignoring `custom_prompt`. An empty value falls
    back to the default, English.
    """
    value = _normalise(setting) or DEFAULT_PROMPT_LANGUAGE
    if value == CUSTOM:
        logger.info(tag, "Prompt language: custom — using the Prompt option.")
        return custom_prompt, None
    if value not in _PROMPTS[kind]:
        logger.warning(tag, f"Unknown prompt language '{setting}' — using the Prompt option instead.")
        return custom_prompt, None
    logger.info(tag, f"Prompt language: {value} — using the built-in prompt (the Prompt option is ignored).")
    return _PROMPTS[kind][value], value
