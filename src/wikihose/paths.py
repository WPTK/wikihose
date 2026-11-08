"""Where wikihose keeps its files. One place, so --help, --diagnose and the code cannot disagree.

WIKIHOSE_CONFIG_DIR and WIKIHOSE_DATA_DIR relocate the config file and the data directory (database, HTTP cache and
logs). Without them the platform user directories apply; on Windows those come from %LOCALAPPDATA% when it is set
(platformdirs asks the shell instead, so a test could not move them). Nothing here creates a directory.
"""

# Adapted from earthwave (https://github.com/WPTK/earthwave) paths.py at commit
# 2b4fe0a49844619ae217f6eec02061b6bc2d5830.

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

APP = "wikihose"
CONFIG_DIR_ENV = "WIKIHOSE_CONFIG_DIR"
DATA_DIR_ENV = "WIKIHOSE_DATA_DIR"


def _override(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def _windows_local() -> Path | None:
    base = os.environ.get("LOCALAPPDATA", "").strip() if sys.platform == "win32" else ""
    return Path(base) / APP if base else None


def config_dir() -> Path:
    return (
        _override(CONFIG_DIR_ENV)
        or _windows_local()
        or Path(platformdirs.user_config_dir(APP, appauthor=False, roaming=False))
    )


def data_dir() -> Path:
    return (
        _override(DATA_DIR_ENV)
        or _windows_local()
        or Path(platformdirs.user_data_dir(APP, appauthor=False, roaming=False))
    )


def log_dir() -> Path:
    if override := _override(DATA_DIR_ENV):
        return override / "logs"
    if local := _windows_local():
        return local / "Logs"
    return Path(platformdirs.user_log_dir(APP, appauthor=False))


def cache_dir() -> Path:
    return data_dir() / "http-cache"


def db_path() -> Path:
    return data_dir() / "wikihose.db"
