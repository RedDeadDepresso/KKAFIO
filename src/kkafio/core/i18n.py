# coding:utf-8
"""
core/i18n.py — Translations for the text KKAFIO itself shows (the native dialogs).

The GUI translates everything in ``interface.json`` through ``assets/i18n/<lang>.json``.
Text that Python draws on its own — the Copy/Paste dialog, the password prompt,
the Review Similar Characters window — can't go through that, so it has its own
set of files with the same languages: ``assets/i18n/runtime/<lang>.json``. They
are kept apart on purpose: ``tools/sync_i18n_keys.py`` deletes every key of the
GUI files that ``interface.json`` doesn't reference.

Usage::

    from kkafio.core.i18n import t
    t("dialog.review.page", page=2, pages=5)        # "Page 2 of 5"

Values are `str.format` templates, so placeholders are ``{name}`` and a literal
brace is ``{{`` / ``}}``. A key missing from the chosen language falls back to
English, and one missing everywhere comes back as the key itself (so a gap shows
up as an obviously wrong label instead of a crash).

Which language
--------------
`Config.read()` (core/config.py) passes on the GUI's own language setting,
``settings.language`` of ``mxu-KKAFIO.json``, through `set_language`. When that is
``"system"`` (no choice made), or a language we don't ship, the operating
system's UI language is used instead, and failing that English.

The language is kept for the life of the process.
"""

from __future__ import annotations

import functools
import json
import locale
import os
import sys
from collections.abc import Iterator

# Same codes (and spelling) as the "languages" table of interface.json.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en_us", "zh_cn", "zh_tw", "ja_jp", "ko_kr", "ru_ru")
DEFAULT_LANGUAGE = "en_us"

# Primary language subtag -> the one shipped variant of it.
_BY_PRIMARY = {"en": "en_us", "ja": "ja_jp", "ko": "ko_kr", "ru": "ru_ru"}
# Chinese is the only one that has to be split: Traditional vs. Simplified.
_TRADITIONAL_MARKERS = ("tw", "hk", "mo", "hant")


# ---------------------------------------------------------------------------
# Choosing the language
# ---------------------------------------------------------------------------

def resolve_language(tag: str | None) -> str | None:
    """Map a locale tag (``zh_CN.UTF-8``, ``ja-JP``, ``ru``, ``en_GB@euro`` ...)
    to one of `SUPPORTED_LANGUAGES`, or ``None`` if it isn't one we ship."""
    if not tag:
        return None
    # "ja_JP.UTF-8" / "de_DE@euro" -> "ja_jp" / "de_de"
    tag = tag.strip().split(".")[0].split("@")[0].replace("-", "_").lower()
    if tag in SUPPORTED_LANGUAGES:
        return tag
    parts = tag.split("_")
    primary = parts[0]
    if primary == "zh":
        return "zh_tw" if any(p in _TRADITIONAL_MARKERS for p in parts[1:]) else "zh_cn"
    return _BY_PRIMARY.get(primary)


def _os_language_tags() -> Iterator[str]:
    """The operating system's language preferences, most specific first."""
    if sys.platform == "win32":
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(langid)
            if name:
                yield name
        except Exception:
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = os.environ.get(var)
        if value:
            yield from value.split(":")          # LANGUAGE is a colon-separated list
    try:
        current = locale.getlocale()[0]
    except Exception:
        current = None
    if current:
        yield current


def detect_language() -> str:
    """The operating system's UI language, or English if it isn't one we ship."""
    for tag in _os_language_tags():
        found = resolve_language(tag)
        if found:
            return found
    return DEFAULT_LANGUAGE


_language: str | None = None


def get_language() -> str:
    global _language
    if _language is None:
        _language = detect_language()
    return _language


def set_language(language: str | None) -> None:
    """Use `language` (any tag `resolve_language` understands, e.g. ``zh-tw``).
    ``None``, ``"system"`` or a language we don't ship means: follow the OS."""
    global _language
    _language = resolve_language(language)


# ---------------------------------------------------------------------------
# Looking up text
# ---------------------------------------------------------------------------

@functools.cache
def _load(language: str) -> dict[str, str]:
    """One language's strings; empty (not an error) if the file is missing or broken."""
    from kkafio.core.paths import ASSETS_DIR
    try:
        with open(ASSETS_DIR / "i18n" / "runtime" / f"{language}.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def t(key: str, **params: object) -> str:
    """The text for `key` in the current language, with `params` filled in."""
    text = _load(get_language()).get(key)
    if text is None:
        text = _load(DEFAULT_LANGUAGE).get(key, key)
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text          # a broken translation must not take the dialog down with it
