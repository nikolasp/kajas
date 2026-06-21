"""Filesystem paths used by Kajas.

All paths are computed from a small set of overridable roots so the same
code can run in tests, during a `kajas serve`, and inside a packaged
binary without having to re-discover directories.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "kajas"


def _config_root() -> Path:
    if env := os.environ.get("KAJAS_CONFIG_DIR"):
        return Path(env).expanduser()
    return Path.home() / ".config" / APP_NAME


def _data_root() -> Path:
    if env := os.environ.get("KAJAS_DATA_DIR"):
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def global_config_path() -> Path:
    return _config_root() / "config.yaml"


def data_dir() -> Path:
    p = _data_root()
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    p = _config_root()
    p.mkdir(parents=True, exist_ok=True)
    return p
