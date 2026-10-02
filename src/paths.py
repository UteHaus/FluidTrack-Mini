"""Central path resolution for source runs and PyInstaller builds.

When run from source, files live next to the .py modules (src/).
In a PyInstaller build, __file__ points into the bundled _internal/ folder,
which is replaced on every rebuild -- so .env and the database are kept
next to the executable instead.
"""

import os
import sys


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = app_dir()
ENV_PATH = os.path.join(APP_DIR, ".env")
