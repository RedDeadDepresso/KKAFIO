"""
theme.py — The GUI's theme and accent colour, for the CustomTkinter dialogs.

Both come from ``settings`` of ``mxu-KKAFIO.json`` (the same file the MXU GUI writes):

* ``settings.theme``       ``"system"`` | ``"dark"`` | ``"light"``
* ``settings.accentColor`` the name of an accent: a shipped one (``assets/accents/<name>.json``,
  e.g. ``peacock``, the default) or a custom one stored in the file's ``customAccents`` list
  (``{"name": "custom-<uuid>", "colors": {default, hover, light, lightDark}}``).

`configure_from_mxu` (called by `Config.read`) only remembers the choice; `apply_to_customtkinter`
(called when the dialogs' host window is created) hands it to CustomTkinter. Anything unknown or
broken falls back to ``system`` / ``peacock``, never to an error.
"""

import json
import re
from pathlib import Path

from kkafio.core.paths import ASSETS_DIR

DEFAULT_ACCENT = "peacock"
_FALLBACK_COLORS = {"default": "#009FAA", "hover": "#008F99", "light": "#00BFCC", "lightDark": "#006F77"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_MODES = {"system": "System", "dark": "Dark", "light": "Light"}

_mode: str = "system"
_accent_name: str = DEFAULT_ACCENT
_accent: dict[str, str] = dict(_FALLBACK_COLORS)


def _clean(colors: object) -> dict[str, str] | None:
    """`colors` if it holds the four accent shades as #rrggbb, else None."""
    if not isinstance(colors, dict):
        return None
    out = {}
    for key, fallback in _FALLBACK_COLORS.items():
        value = colors.get(key)
        out[key] = value if isinstance(value, str) and _HEX.match(value) else None
    if out["default"] is None:
        return None
    out["hover"] = out["hover"] or out["default"]
    out["light"] = out["light"] or out["default"]
    out["lightDark"] = out["lightDark"] or out["hover"]
    return out


def _shipped_accent(name: str) -> dict[str, str] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):       # it becomes a file name
        return None
    try:
        with open(Path(ASSETS_DIR) / "accents" / f"{name}.json", encoding="utf-8") as f:
            return _clean(json.load(f))
    except (OSError, ValueError):
        return None


def resolve_accent(name: object, custom_accents: object = None) -> tuple[str, dict[str, str]]:
    """(name, colors) of the accent called `name`; peacock when it can't be found."""
    if isinstance(name, str):
        if isinstance(custom_accents, list):
            for entry in custom_accents:
                if isinstance(entry, dict) and entry.get("name") == name:
                    colors = _clean(entry.get("colors"))
                    if colors:
                        return name, colors
        colors = _shipped_accent(name)
        if colors:
            return name, colors
    return DEFAULT_ACCENT, _shipped_accent(DEFAULT_ACCENT) or dict(_FALLBACK_COLORS)


def configure_from_mxu(mxu: dict) -> None:
    """Remember the theme and accent of a loaded ``mxu-KKAFIO.json``."""
    global _mode, _accent_name, _accent
    settings = mxu.get("settings") if isinstance(mxu, dict) else None
    settings = settings if isinstance(settings, dict) else {}
    mode = settings.get("theme", "system")
    _mode = mode.lower() if isinstance(mode, str) and mode.lower() in _MODES else "system"
    _accent_name, _accent = resolve_accent(settings.get("accentColor"), mxu.get("customAccents"))


def get_mode() -> str:
    """``"system"``, ``"dark"`` or ``"light"``."""
    return _mode


def get_accent() -> dict[str, str]:
    """The accent's shades: ``default``, ``hover``, ``light``, ``lightDark``."""
    return dict(_accent)


def accent_pair() -> tuple[str, str]:
    """(light-mode, dark-mode) main accent colour."""
    return _accent["default"], _accent["default"]


def accent_hover_pair() -> tuple[str, str]:
    return _accent["hover"], _accent["hover"]


def accent_text_pair() -> tuple[str, str]:
    """An accent usable as text / focus ring on the light and on the dark background."""
    return _accent["lightDark"], _accent["light"]


def accent_selection_pair() -> tuple[str, str]:
    """A soft tint of the accent for selected rows (light-mode, dark-mode)."""
    def mix(fg: str, bg: str, amount: float) -> str:
        a = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02x}" for x, y in zip(a, b))
    return mix(_accent["default"], "#ffffff", 0.25), mix(_accent["default"], "#17181c", 0.40)


# CustomTkinter widget colours that are "the accent" in its default theme.
_ACCENT_KEYS = {
    "CTkButton": {"fg_color": "default", "hover_color": "hover"},
    "CTkCheckBox": {"fg_color": "default", "hover_color": "hover"},
    "CTkRadioButton": {"fg_color": "default", "hover_color": "hover"},
    "CTkSwitch": {"progress_color": "default", "button_hover_color": "hover"},
    "CTkProgressBar": {"progress_color": "default"},
    "CTkSlider": {"progress_color": "default", "button_color": "default", "button_hover_color": "hover"},
    "CTkOptionMenu": {"fg_color": "default", "button_color": "hover", "button_hover_color": "hover"},
    "CTkComboBox": {"button_color": "default", "button_hover_color": "hover"},
    "CTkSegmentedButton": {"selected_color": "default", "selected_hover_color": "hover"},
    "CTkScrollbar": {"button_hover_color": "hover"},
}


def apply_to_customtkinter() -> None:
    """Set CustomTkinter's appearance mode and patch its colour theme with the accent.
    Call before any widget is created (the dialogs' host window does)."""
    import customtkinter as ctk
    from customtkinter import ThemeManager

    ctk.set_appearance_mode(_MODES[_mode])
    theme = ThemeManager.theme
    for widget, keys in _ACCENT_KEYS.items():
        section = theme.get(widget)
        if isinstance(section, dict):
            for key, shade in keys.items():
                section[key] = [_accent[shade], _accent[shade]]
    # text on an accent-coloured control: keep it readable on light accents (e.g. rosegold)
    for widget in ("CTkButton", "CTkOptionMenu"):
        section = theme.get(widget)
        if isinstance(section, dict):
            section["text_color"] = [_text_on(_accent["default"])] * 2


def _text_on(color: str) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#1a1a1a" if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else "#ffffff"


# ---------------------------------------------------------------------------
# The window icon
# ---------------------------------------------------------------------------

_ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _recolor_icon(source: Path, target: Path, color: str) -> None:
    """Write CustomTkinter's icon with its blue replaced by `color`.

    The icon is blue (two tones: face and shadow) on white. A pixel's HSV saturation says how much
    "blue" it holds (white 0, blue 1) and its value which tone (face 1.0, shadow ~0.7), so each pixel
    becomes: white * (1 - s) + color * v * s. Anti-aliased edges and the alpha channel carry over."""
    from PIL import Image

    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    img = Image.open(source).convert("RGBA")
    hsv = img.convert("RGB").convert("HSV")
    out = []
    for (r, g, b, a), (_h, s, v) in zip(img.getdata(), hsv.getdata()):
        s, v = s / 255, v / 255
        out.append(tuple(round(255 * (1 - s) + c * v * s) for c in rgb) + (a,))
    img.putdata(out)
    img.save(target, format="ICO", sizes=_ICON_SIZES)


def accent_icon_path() -> str | None:
    """Path of CustomTkinter's window icon recoloured in the accent (made once per colour, in the
    temp folder), or None when it can't be made (not Windows, no Pillow, ...): the dialogs then keep
    CustomTkinter's own icon. Only Windows uses this icon."""
    import sys

    if sys.platform != "win32":
        return None
    try:
        import tempfile

        import customtkinter

        source = Path(customtkinter.__file__).parent / "assets" / "icons" / "CustomTkinter_icon_Windows.ico"
        target_dir = Path(tempfile.gettempdir()) / "kkafio-icons"
        target = target_dir / f"ctk-{_accent['default'][1:].lower()}.ico"
        if not target.is_file():
            target_dir.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".tmp")
            _recolor_icon(source, partial, _accent["default"])
            partial.replace(target)
        return str(target)
    except Exception:
        return None
