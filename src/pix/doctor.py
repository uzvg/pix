"""doctor subcommand."""

from __future__ import annotations

import argparse
import platform
import shutil
import sys
from typing import Any

from . import __version__
from .config import default_config_path
from .logger import Logger
from .rclone import RCLONE_HINT, RCLONE_MIN, probe_rclone
from .shrink import PYVIPS_HINT


def doctor(args: argparse.Namespace, settings: dict[str, Any], log: Logger) -> int:
    issues = 0
    print(f"pix {__version__}")
    print(f"python {platform.python_version()} ({platform.system().lower()})")

    try:
        import pyvips

        libvips = ".".join(str(pyvips.version(index)) for index in range(3))
        print(f"vips  {libvips} via pyvips {pyvips.__version__} (API mode)")
    except Exception as exc:
        print("vips  unavailable")
        print(PYVIPS_HINT, file=sys.stderr)
        print(f"pix: error: {exc}", file=sys.stderr)
        issues = 1

    binary = settings["rclone"]["binary"]
    version, parsed, _error = probe_rclone(binary)
    location = shutil.which(binary) or binary
    if parsed is None:
        print(f"rclone missing ({binary})")
        print(RCLONE_HINT, file=sys.stderr)
        issues = 1
    elif parsed[:2] < RCLONE_MIN:
        print(f"rclone {version} ({location}) too old; need >= 1.66")
        print(RCLONE_HINT, file=sys.stderr)
        issues = 1
    else:
        print(f"rclone {version} ({location}) bisync ok")

    config_path = args.config if args.config is not None else default_config_path()
    if config_path.is_file():
        print(f"config {config_path} (ok)")
    else:
        print(f"config {config_path} (missing (using defaults))")

    return 1 if issues else 0
