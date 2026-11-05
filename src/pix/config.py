"""Configuration loading and validation."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

_SIZE_RE = re.compile(r"^(\d+)([KMG]?)$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}


class ConfigError(Exception):
    """Invalid or unreadable configuration."""


def load_defaults() -> dict[str, Any]:
    """Read the packaged template, the single source of default values."""
    path = Path(__file__).with_name("default_config.toml")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def parse_size(value: str | int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise ValueError("size must not be negative")
        return value
    if not isinstance(value, str):
        raise ValueError(f"invalid size: {value!r}")
    match = _SIZE_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid size: {value!r}")
    return int(match.group(1)) * _SIZE_UNITS[match.group(2).upper()]


def default_config_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "pix" / "config.toml"
        return Path.home() / "AppData" / "Roaming" / "pix" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "pix" / "config.toml"
    return Path.home() / ".config" / "pix" / "config.toml"


def load_config(path: Path) -> dict[str, Any]:
    """Read a TOML config file; a missing file is an empty override."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a table")
    return data


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _deep_update(current, value)
        else:
            target[key] = value


def load_settings(explicit: Path | None = None) -> dict[str, Any]:
    path = explicit if explicit is not None else default_config_path()
    if explicit is not None and not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    overrides = load_config(path)
    settings = load_defaults()
    _deep_update(settings, overrides)
    validate_settings(settings)
    return settings


def require_int(name: str, value: Any, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ConfigError(f"{name} must be >= {minimum}")
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _require_excludes(name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    return value


def validate_settings(settings: dict[str, Any]) -> None:
    for section in ("shrink", "mirror", "publish", "rclone"):
        if not isinstance(settings.get(section), dict):
            raise ConfigError(f"[{section}] must be a table")

    shrink = settings["shrink"]
    require_int("shrink.width", shrink["width"], 1)
    require_int("shrink.quality", shrink["quality"], 1, 100)
    require_int("shrink.effort", shrink["effort"], 0, 9)
    try:
        shrink["min_size"] = parse_size(shrink["min_size"])
    except ValueError as exc:
        raise ConfigError(f"shrink.min_size: {exc}") from exc

    include = shrink["include"]
    if not isinstance(include, list) or not all(
        isinstance(item, str) and item and not item.startswith(".") for item in include
    ):
        raise ConfigError("shrink.include must be a list of extensions without dots")

    for section in ("mirror", "publish"):
        _require_excludes(f"{section}.exclude", settings[section]["exclude"])
        require_int(f"{section}.max_delete", settings[section]["max_delete"], -1, 100)

    publish = settings["publish"]
    if not isinstance(publish["base_url"], str):
        raise ConfigError("publish.base_url must be a string")
    if not isinstance(publish["urls"], bool):
        raise ConfigError("publish.urls must be a boolean")

    if not isinstance(settings["rclone"]["binary"], str) or not settings["rclone"]["binary"]:
        raise ConfigError("rclone.binary must be a non-empty string")


def init_config(args: argparse.Namespace) -> int:
    path = args.config if args.config is not None else default_config_path()
    if path.exists() and not args.force:
        print(
            f"pix: error: config file already exists: {path} (use --force to overwrite)",
            file=sys.stderr,
        )
        return 2
    try:
        template = Path(__file__).with_name("default_config.toml").read_text(encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template, encoding="utf-8")
    except OSError as exc:
        print(f"pix: error: {path}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}")
    return 0
