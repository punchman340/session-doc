"""Optional user config. It lives outside the repo and the output dir (docs/DESIGN.md).

The file is JSON: `{"decision_phrases": ["...", "..."]}`. A missing file means defaults.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """The config file exists but cannot be read or has the wrong shape."""


@dataclass(frozen=True)
class Config:
    decision_phrases: tuple[str, ...] = ()


def default_config_path() -> Path:
    """`%APPDATA%\\session-doc\\config.json` on Windows, else under `~/.config/session-doc/`."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "session-doc" / "config.json"
    return Path.home() / ".config" / "session-doc" / "config.json"


def load_config(path: Path) -> Config:
    """Read the config file. A missing file gives the defaults; anything unusable raises."""
    try:
        # utf-8-sig: editors on Windows often save JSON with a byte order mark.
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return Config()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"config file {path} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {path} must contain a JSON object")
    phrases = raw.get("decision_phrases", [])
    if not isinstance(phrases, list) or not all(isinstance(p, str) for p in phrases):
        raise ConfigError(f'"decision_phrases" in {path} must be a list of strings')
    return Config(decision_phrases=tuple(phrases))
