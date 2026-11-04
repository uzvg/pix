"""pix command-line entry point."""

from __future__ import annotations

import argparse
import copy
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _package_version

    __version__ = _package_version("pix-cli")
except PackageNotFoundError:  # pragma: no cover - only when running uninstalled
    __version__ = "0.1.0"


DEFAULTS: dict[str, Any] = {
    "shrink": {
        "width": 1920,
        "quality": 85,
        "effort": 5,
        "min_size": 0,
        "include": ["jpg", "jpeg", "png", "webp"],
    },
    "backup": {
        "exclude": [],
        "max_delete": 10,
    },
    "publish": {
        "base_url": "",
        "urls": False,
        "exclude": [],
        "max_delete": 10,
    },
    "rclone": {
        "binary": "rclone",
    },
}

_SIZE_RE = re.compile(r"^(\d+)([KMG]?)$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}


class ConfigError(Exception):
    """Invalid or unreadable configuration."""


class Logger:
    def __init__(self, verbose: bool = False, quiet: bool = False) -> None:
        self.verbose = verbose
        self.quiet = quiet

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def detail(self, message: str) -> None:
        if self.verbose and not self.quiet:
            print(message, file=sys.stderr)

    def error(self, message: str) -> None:
        print(f"pix: error: {message}", file=sys.stderr)


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
    settings = copy.deepcopy(DEFAULTS)
    _deep_update(settings, overrides)
    validate_settings(settings)
    return settings


def _require_int(name: str, value: Any, minimum: int, maximum: int | None = None) -> int:
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
    shrink = settings["shrink"]
    _require_int("shrink.width", shrink["width"], 1)
    _require_int("shrink.quality", shrink["quality"], 1, 100)
    _require_int("shrink.effort", shrink["effort"], 0, 9)
    try:
        shrink["min_size"] = parse_size(shrink["min_size"])
    except ValueError as exc:
        raise ConfigError(f"shrink.min_size: {exc}") from exc

    include = shrink["include"]
    if not isinstance(include, list) or not all(
        isinstance(item, str) and item and not item.startswith(".") for item in include
    ):
        raise ConfigError("shrink.include must be a list of extensions without dots")

    for section in ("backup", "publish"):
        _require_excludes(f"{section}.exclude", settings[section]["exclude"])
        _require_int(f"{section}.max_delete", settings[section]["max_delete"], -1)

    publish = settings["publish"]
    if not isinstance(publish["base_url"], str):
        raise ConfigError("publish.base_url must be a string")
    if not isinstance(publish["urls"], bool):
        raise ConfigError("publish.urls must be a boolean")

    if not isinstance(settings["rclone"]["binary"], str) or not settings["rclone"]["binary"]:
        raise ConfigError("rclone.binary must be a non-empty string")


def _add_logging_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="store_true", default=None)
    group.add_argument("--quiet", action="store_true", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pix", description="Batch AVIF shrink and image-host sync.")
    parser.add_argument("--config", type=Path, default=None, metavar="PATH")
    parser.add_argument("--version", action="version", version=f"pix {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    shrink = subparsers.add_parser("shrink", help="compress images to AVIF")
    shrink.add_argument("-s", "--source", type=Path, required=True)
    shrink.add_argument("-d", "--dest", type=Path, required=True)
    shrink.add_argument("-w", "--width", type=int, default=None)
    shrink.add_argument("-q", "--quality", type=int, default=None)
    shrink.add_argument("-e", "--effort", type=int, default=None)
    shrink.add_argument("-m", "--min-size", type=str, default=None)
    shrink.add_argument("--force", action="store_true")
    shrink.add_argument("--prune", action="store_true")
    shrink.add_argument("--allow-empty", action="store_true")
    shrink.add_argument("--dry-run", action="store_true")
    _add_logging_args(shrink)

    backup = subparsers.add_parser("backup", help="bidirectionally mirror originals")
    backup.add_argument("-s", "--source", type=Path, required=True)
    backup.add_argument("-d", "--dest", required=True)
    backup.add_argument("--exclude", action="append", default=None, metavar="GLOB")
    backup.add_argument("--max-delete", type=int, default=None)
    backup.add_argument("--allow-empty", action="store_true")
    backup.add_argument("--resync", action="store_true")
    backup.add_argument("--dry-run", action="store_true")
    _add_logging_args(backup)

    publish = subparsers.add_parser("publish", help="bidirectionally mirror compressed images")
    publish.add_argument("-s", "--source", type=Path, required=True)
    publish.add_argument("-d", "--dest", required=True)
    publish.add_argument("--base-url", default=None)
    publish.add_argument("--urls", action=argparse.BooleanOptionalAction, default=None)
    publish.add_argument("--exclude", action="append", default=None, metavar="GLOB")
    publish.add_argument("--max-delete", type=int, default=None)
    publish.add_argument("--allow-empty", action="store_true")
    publish.add_argument("--resync", action="store_true")
    publish.add_argument("--dry-run", action="store_true")
    _add_logging_args(publish)

    subparsers.add_parser("doctor", help="check runtime dependencies and configuration")

    return parser


def _not_implemented(_args: argparse.Namespace, _settings: dict[str, Any], log: Logger) -> int:
    log.error("subcommand not implemented yet; see docs/architecture.md")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help/--version and argparse errors
        return int(exc.code) if isinstance(exc.code, int) else 0

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"pix: error: {exc}", file=sys.stderr)
        return 2

    log = Logger(verbose=bool(getattr(args, "verbose", False)), quiet=bool(getattr(args, "quiet", False)))
    handlers = {
        "shrink": _not_implemented,
        "backup": _not_implemented,
        "publish": _not_implemented,
        "doctor": _not_implemented,
    }
    return handlers[args.command](args, settings, log)


if __name__ == "__main__":
    raise SystemExit(main())
