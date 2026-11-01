"""Test sandbox.

Everything KKAFIO writes outside the project (its %APPDATA%-style config dir,
the ~/KKAFIO default task folders) is redirected into a throw-away directory
*before* any kkafio module is imported — kkafio.core.paths creates the
config directory on first access of CONFIG_DIR.
"""

import os
import tempfile

import pytest

SANDBOX = tempfile.mkdtemp(prefix="kkafio-tests-")

os.environ["HOME"] = SANDBOX
os.environ["USERPROFILE"] = SANDBOX
os.environ["XDG_CONFIG_HOME"] = os.path.join(SANDBOX, "xdg")
os.environ["APPDATA"] = os.path.join(SANDBOX, "appdata")
os.environ["COLUMNS"] = "100"  # argparse wraps --help output to the terminal width


@pytest.fixture(autouse=True)
def _fresh_gallery_config():
    """The gallery remembers its shortcuts and view between runs (files in the config dir): every
    test starts from none, so what one test leaves behind can't change another."""
    from kkafio.core.paths import CONFIG_DIR
    for name in ("gallery-shortcuts.json", "gallery-view.json"):
        (CONFIG_DIR / "config" / name).unlink(missing_ok=True)
    yield
