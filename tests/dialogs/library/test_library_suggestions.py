"""The library's folder suggestion settings (what the folder box offers per kind of card)."""

import json

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)

from kkafio.core.session_store import SessionStore
from kkafio.dialogs.library.common import Suggester
from kkafio.dialogs.library.suggestions import (
    CATEGORIES, SUGGEST_ALL, SUGGEST_EXISTING, SUGGEST_NONE, SuggestionSettings)


def test_everything_is_suggested_by_default_and_unknown_categories_too():
    s = SuggestionSettings()
    assert all(s.mode(c) == SUGGEST_ALL for c in CATEGORIES)
    assert s.mode("tab0") == SUGGEST_ALL


def test_a_change_is_saved_and_read_back(tmp_path):
    path = tmp_path / "config" / "s.json"
    s = SuggestionSettings(SessionStore(path, "T"))
    assert s.set("scene", SUGGEST_NONE) and s.set("chara", SUGGEST_EXISTING)
    assert json.loads(path.read_text("utf-8"))["suggestions"] == {
        "chara": SUGGEST_EXISTING, "outfit": SUGGEST_ALL, "scene": SUGGEST_NONE}
    again = SuggestionSettings(SessionStore(path, "T"))
    assert (again.mode("chara"), again.mode("outfit"), again.mode("scene")) == (
        SUGGEST_EXISTING, SUGGEST_ALL, SUGGEST_NONE)


def test_an_unknown_category_or_mode_is_refused(tmp_path):
    s = SuggestionSettings(SessionStore(tmp_path / "s.json", "T"))
    assert not s.set("nope", SUGGEST_NONE) and not s.set("chara", "everything")
    assert s.mode("chara") == SUGGEST_ALL and not (tmp_path / "s.json").exists()


def test_a_broken_file_or_value_falls_back_to_the_default(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    assert SuggestionSettings(SessionStore(path, "T")).mode("scene") == SUGGEST_ALL
    path.write_text(json.dumps({"suggestions": {"scene": "bogus", "chara": SUGGEST_NONE}}), encoding="utf-8")
    s = SuggestionSettings(SessionStore(path, "T"))
    assert s.mode("scene") == SUGGEST_ALL and s.mode("chara") == SUGGEST_NONE


def test_the_suggester_is_a_plain_callback_with_an_existing_only_variant():
    suggest = Suggester(lambda text: ["Index", "Folder"], lambda text: ["Folder"])
    assert suggest("x") == ["Index", "Folder"] and suggest.existing("x") == ["Folder"]


def test_the_series_language_is_english_by_default_saved_and_checked(tmp_path):
    path = tmp_path / "config" / "s.json"
    s = SuggestionSettings(SessionStore(path, "T"))
    assert s.language == "en_us"
    assert s.set_language("ja_jp") and not s.set_language("xx_xx")
    assert json.loads(path.read_text("utf-8"))["series_language"] == "ja_jp"
    assert SuggestionSettings(SessionStore(path, "T")).language == "ja_jp"
    path.write_text(json.dumps({"series_language": "klingon"}), encoding="utf-8")
    assert SuggestionSettings(SessionStore(path, "T")).language == "en_us"
