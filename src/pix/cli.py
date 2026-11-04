"""pix command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, init_config, load_settings
from .doctor import doctor
from .logger import Logger
from .shrink import shrink
from .sync import backup, publish


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
    backup.add_argument(
        "--max-delete",
        type=int,
        default=None,
        metavar="PERCENT",
        help="abort if more than PERCENT%% of files are deleted (default: 10)",
    )
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
    publish.add_argument(
        "--max-delete",
        type=int,
        default=None,
        metavar="PERCENT",
        help="abort if more than PERCENT%% of files are deleted (default: 10)",
    )
    publish.add_argument("--allow-empty", action="store_true")
    publish.add_argument("--resync", action="store_true")
    publish.add_argument("--dry-run", action="store_true")
    _add_logging_args(publish)

    subparsers.add_parser("doctor", help="check runtime dependencies and configuration")

    config = subparsers.add_parser("config", help="manage the configuration file")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    init = config_sub.add_parser("init", help="write the default config file")
    init.add_argument("--force", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help/--version and argparse errors
        return int(exc.code) if isinstance(exc.code, int) else 0

    if args.command == "config":
        return init_config(args)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"pix: error: {exc}", file=sys.stderr)
        return 2

    log = Logger(
        verbose=bool(getattr(args, "verbose", False)),
        quiet=bool(getattr(args, "quiet", False)),
    )
    handlers = {
        "shrink": shrink,
        "backup": backup,
        "publish": publish,
        "doctor": doctor,
    }
    return handlers[args.command](args, settings, log)


if __name__ == "__main__":
    raise SystemExit(main())
