"""Where the program keeps its files on the PC.

The GUI may pass Qt's own locations instead; these are for the core and the
command-line tool, and match Qt's AppConfigLocation on each system.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..identity import ORG_NAME, SETTINGS_NAME


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Preferences"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / ORG_NAME / SETTINGS_NAME


def cluster_profiles_dir() -> Path:
    return config_dir() / "clusters"


def data_dir() -> Path:
    """Data the program downloads or makes (Qt's AppDataLocation)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / ORG_NAME / SETTINGS_NAME


def pseudo_library_dir() -> Path:
    """The pseudopotential library on this PC (PseudoDojo's tables, as downloaded)."""
    return data_dir() / "pseudos"
