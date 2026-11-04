"""Prompt Language: which prompt Group / Rename Characters send, and the hints language it implies."""

from kkafio.cards.chara import hint_language, prompt_language as pl


def test_choices_are_custom_plus_every_hint_language():
    assert pl.choices()[0] == pl.CUSTOM
    assert set(pl.languages()) == set(hint_language.available_languages())


def test_custom_uses_the_prompt_option_and_names_no_language():
    assert pl.resolve("custom", "my prompt", pl.GROUP, "T") == ("my prompt", None)
    assert pl.resolve("nope", "my prompt", pl.RENAME, "T") == ("my prompt", None)


def test_a_language_uses_its_builtin_prompt_and_ignores_the_option():
    for kind in (pl.GROUP, pl.RENAME):
        for code in pl.languages():
            prompt, lang = pl.resolve(code, "my prompt", kind, "T")
            assert lang == code and prompt == pl.builtin(kind, code) and "my prompt" not in prompt
            assert prompt.endswith("JSON to fill in:\n")
    assert "Japanese" in pl.builtin(pl.GROUP, "ja_jp") and "hangul" in pl.builtin(pl.RENAME, "ko_kr")


def test_english_is_the_default_prompt():
    assert pl.resolve("", "my prompt", pl.RENAME, "T")[1] == "en_us"

    assert pl.builtin(pl.GROUP, "en_us") == pl.GROUP_DEFAULT_PROMPT
    assert pl.builtin(pl.RENAME, "en_us") == pl.RENAME_DEFAULT_PROMPT


def test_hints_detect_follows_the_prompt_language_not_the_text():
    assert hint_language.resolve("detect", "answer in Korean", "T", "ja_jp") == "ja_jp"
    assert hint_language.resolve("detect", "answer in Korean", "T", None) == "ko_kr"   # custom prompt
    assert hint_language.resolve("ru_ru", "x", "T", "ja_jp") == "ru_ru"                # explicit wins
    assert hint_language.resolve("no", "x", "T", "ja_jp") is None
