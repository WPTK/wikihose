"""The config file: a small TOML file in the config directory, read with warnings instead of errors.

``load_config`` never raises on bad content. Unknown keys and bad values are dropped, each with one warning string for
the caller to show. ``save_config`` writes atomically (temp file, then replace).
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import tomli_w

from wikihose.paths import config_dir

READING_ROW_STEPS = (25, 50, 100)  # the Reading tab's row counts; the first is the default
WIKI_PATTERN = re.compile(r"[a-z_]+wiki")


def is_valid_wiki(name: str) -> bool:
    """True for a database name such as enwiki or zh_min_nanwiki, and for nothing else (no markup, no spaces)."""
    return WIKI_PATTERN.fullmatch(name) is not None


@dataclass(frozen=True)
class Config:
    wiki: str = "enwiki"
    contact: str = ""  # a URL or address for the User-Agent
    theme: str = "wikihose-dark"
    hide_bots: bool = True
    articles_only: bool = True
    min_bytes: int = 0
    reading_rows: int = 25  # rows the Reading tab shows: 25, 50 or 100


def default_config_path() -> Path:
    return config_dir() / "config.toml"


def _valid(name: str, value: Any) -> bool:
    if name == "wiki":
        return isinstance(value, str) and is_valid_wiki(value)
    if name == "min_bytes":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if name == "reading_rows":
        return value in READING_ROW_STEPS
    if name in ("hide_bots", "articles_only"):
        return isinstance(value, bool)
    if name == "contact":  # it goes into a request header, so no control characters and nothing outside ASCII
        return isinstance(value, str) and value.isascii() and value.isprintable()
    return isinstance(value, str)  # theme


def load_config(path: Path | None) -> tuple[Config, list[str]]:
    """Read the config at ``path``. ``None`` means --no-config: defaults. A missing file also gives defaults."""
    if path is None or not path.is_file():
        return Config(), []
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, ValueError) as exc:  # TOMLDecodeError and UnicodeDecodeError are ValueErrors
        return Config(), [f"config: could not read {path} ({exc}); using defaults"]
    known = {f.name for f in fields(Config)}
    warnings: list[str] = []
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            warnings.append(f"config: unknown key '{key}' ignored")
        elif not _valid(key, value):
            warnings.append(f"config: bad value for '{key}' ({value!r}); using the default")
        else:
            values[key] = value
    return Config(**values), warnings


def save_config(config: Config, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(tomli_w.dumps(asdict(config)).encode("utf-8"))
    os.replace(temp, path)
