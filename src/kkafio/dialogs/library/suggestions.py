"""
suggestions.py — What the library's folder box offers while you type, and where that is saved.

Each kind of card (the library's tabs) has its own setting:

    none      no suggestions
    existing  the folders that already exist
    all       the existing folders plus the tab's index: the series (characters), the characters'
              filenames (coordinates) or the known authors (scenes)  — what it always did

One `SuggestionSettings` is shared by every tab of the window, so a change applies at once. It is
saved in ``<CONFIG_DIR>/config/library-suggestions.json`` (on Windows: %APPDATA%/KKAFIO/config), a
file of its own next to library-shortcuts.json: each setting is written the moment the user changes
it, and neither file has to know about the other's content. No GUI in here (see settings_dialog.py).

Besides, the language the series suggestions of the Character tab are written in (the character
index holds every series title per language): any language of assets/i18n/, English by default.
"""

from __future__ import annotations

from kkafio.cards.chara.hint_language import DEFAULT_LANGUAGE, available_languages
from kkafio.core.logger import logger
from kkafio.core.session_store import SessionStore

TAG = "LIBRARY"
FILE_NAME = "library-suggestions.json"

# The tabs' keys (LibraryTab.key)
CATEGORY_CHARA = "chara"
CATEGORY_OUTFIT = "outfit"
CATEGORY_SCENE = "scene"
CATEGORIES = (CATEGORY_CHARA, CATEGORY_OUTFIT, CATEGORY_SCENE)

SUGGEST_NONE = "none"
SUGGEST_EXISTING = "existing"
SUGGEST_ALL = "all"
MODES = (SUGGEST_NONE, SUGGEST_EXISTING, SUGGEST_ALL)

DEFAULT_MODE = SUGGEST_ALL


class SuggestionSettings:
    """{category: mode} for the library window; a category it doesn't know (a tab without a key)
    gets every suggestion; `language` is the language of the series suggestions. `store` is where
    it is kept (None: nothing is read or written, e.g. in tests)."""

    def __init__(self, store: SessionStore | None = None):
        self.store = store
        self.modes: dict[str, str] = {c: DEFAULT_MODE for c in CATEGORIES}
        self.language: str = DEFAULT_LANGUAGE
        if store is not None:
            self._load()

    @classmethod
    def from_user_config(cls) -> "SuggestionSettings":
        """The settings saved in the per-user config folder (the defaults where there are none)."""
        from kkafio.core.paths import CONFIG_DIR
        return cls(SessionStore(CONFIG_DIR / "config" / FILE_NAME, TAG))

    def mode(self, category: str) -> str:
        return self.modes.get(category, DEFAULT_MODE)

    def set(self, category: str, mode: str) -> bool:
        """Use `mode` for `category` and save. False (nothing changed) for an unknown one."""
        if category not in CATEGORIES or mode not in MODES:
            return False
        self.modes[category] = mode
        self.save()
        return True

    def set_language(self, language: str) -> bool:
        """Write the series suggestions in `language` and save. False (nothing changed) for a
        language that isn't one of assets/i18n/."""
        if language not in available_languages():
            return False
        self.language = language
        self.save()
        return True

    def _load(self) -> None:
        saved = self.store.load()
        language = saved.get("series_language")
        if language is not None:
            if language in available_languages():
                self.language = language
            else:
                logger.warning(TAG, f"Ignoring the saved series suggestion language '{language}'")
        data = saved.get("suggestions")
        if not isinstance(data, dict):
            return
        for category in CATEGORIES:
            value = data.get(category, DEFAULT_MODE)
            if value in MODES:
                self.modes[category] = value
            else:
                logger.warning(TAG, f"Ignoring the saved suggestion mode '{value}' for {category}")

    def save(self) -> bool:
        if self.store is None:
            return True
        return self.store.save({"version": 1, "suggestions": dict(self.modes), "series_language": self.language})
