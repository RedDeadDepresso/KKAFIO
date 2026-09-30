"""Test sandbox.

Everything KKAFIO writes outside the project (its %APPDATA%-style config dir,
the ~/KKAFIO default task folders) is redirected into a throw-away directory
*before* any kkafio module is imported — kkafio.core.constants creates the
config directory at import time.
"""

import os
import tempfile

SANDBOX = tempfile.mkdtemp(prefix="kkafio-tests-")

os.environ["HOME"] = SANDBOX
os.environ["USERPROFILE"] = SANDBOX
os.environ["XDG_CONFIG_HOME"] = os.path.join(SANDBOX, "xdg")
os.environ["APPDATA"] = os.path.join(SANDBOX, "appdata")
os.environ["COLUMNS"] = "100"  # argparse wraps --help output to the terminal width
