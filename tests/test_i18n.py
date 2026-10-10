"""
Runtime translations (core/i18n.py + assets/i18n/runtime/*.json).

These catch the mistakes that otherwise only show up in front of a user in one
language: a key missing from one file, a placeholder renamed in a translation,
a t("...") call naming a key that doesn't exist, or a key nobody uses any more.
"""

import ast
import json
import string
from pathlib import Path

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)

from kkafio.core import i18n

REPO = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO / "assets" / "i18n" / "runtime"
SRC = REPO / "src" / "kkafio"


def _load(lang: str) -> dict[str, str]:
    return json.loads((RUNTIME_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def _keys_used_in_source() -> set[str]:
    used: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                used.add(node.args[0].value)
    return used


def test_languages_match_interface_json():
    interface = json.loads((REPO / "interface.json").read_text(encoding="utf-8"))
    assert set(i18n.SUPPORTED_LANGUAGES) == set(interface["languages"])
    assert {p.stem for p in RUNTIME_DIR.glob("*.json")} == set(i18n.SUPPORTED_LANGUAGES)


def test_every_language_has_the_same_keys_and_placeholders():
    english = _load("en_us")
    for lang in i18n.SUPPORTED_LANGUAGES:
        strings = _load(lang)
        assert set(strings) == set(english), f"{lang}: keys differ from en_us"
        for key, template in strings.items():
            assert _fields(template) == _fields(english[key]), f"{lang}: placeholders of {key} differ"
            assert template.strip(), f"{lang}: {key} is empty"


def test_every_key_used_in_source_exists_and_none_are_orphaned():
    used, defined = _keys_used_in_source(), set(_load("en_us"))
    assert used <= defined, f"t() called with unknown keys: {sorted(used - defined)}"
    assert defined <= used, f"keys no code uses: {sorted(defined - used)}"


def test_resolve_language():
    cases = {
        "ja_JP.UTF-8": "ja_jp", "ja": "ja_jp", "ko-KR": "ko_kr", "ru_RU": "ru_ru", "en_GB@euro": "en_us",
        "zh_CN": "zh_cn", "zh-Hans-CN": "zh_cn", "zh": "zh_cn",
        "zh_TW": "zh_tw", "zh-Hant": "zh_tw", "zh_HK": "zh_tw", "ZH_tw.UTF-8": "zh_tw",
        "de_DE": None, "C": None, "": None, None: None,
    }
    for tag, expected in cases.items():
        assert i18n.resolve_language(tag) == expected, tag


def test_set_language_and_system_fallback(monkeypatch):
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    try:
        i18n.set_language("zh-tw")
        assert i18n.get_language() == "zh_tw"                  # an explicit choice wins over the OS
        for follow_os in ("system", None, "klingon"):
            i18n.set_language(follow_os)
            assert i18n.get_language() == "ru_ru", follow_os   # "system"/unknown -> the OS
        monkeypatch.setenv("LANG", "C")
        i18n.set_language("system")
        assert i18n.get_language() == "en_us"                  # nothing usable -> English
    finally:
        i18n.set_language(None)


def test_config_read_sets_the_language_from_the_gui_setting(tmp_path):
    """Config.read() hands settings.language of the file it is reading to i18n."""
    import cli_harness as h
    from kkafio.core.config import Config
    world = h.World(tmp_path)
    try:
        for setting, expected in (("JA-jp", "ja_jp"), ("zh-TW", "zh_tw")):
            data = json.loads(world.config_path.read_text(encoding="utf-8"))
            data["settings"] = {"language": setting}
            world.config_path.write_text(json.dumps(data), encoding="utf-8")
            Config(str(world.config_path))
            assert i18n.get_language() == expected, setting
    finally:
        i18n.set_language(None)


def test_t_formats_and_falls_back(monkeypatch):
    try:
        i18n.set_language("ja_jp")
        assert i18n.t("dialog.review.page", page=2, pages=5) == "2 / 5 ページ"
        assert i18n.t("dialog.cancel") == "キャンセル"
        assert i18n.t("no.such.key") == "no.such.key"
        # missing from the chosen language -> English
        monkeypatch.setattr(i18n, "_load", lambda lang: {} if lang == "ja_jp" else {"x": "English {n}"})
        assert i18n.t("x", n=3) == "English 3"
        # a translation that mentions a placeholder the caller didn't pass must not crash
        assert i18n.t("x") == "English {n}"
    finally:
        i18n.set_language(None)
