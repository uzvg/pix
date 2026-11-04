"""rclone probing and execution."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .logger import Logger

RCLONE_MIN = (1, 66)
RCLONE_HINT = (
    "install rclone >= 1.66: https://rclone.org/downloads/ "
    "(Windows: winget search rclone)"
)


def merge_excludes(*groups: list[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            if item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


def has_files(path: Path) -> bool:
    return any(item.is_file() for item in path.rglob("*"))


def probe_rclone(binary: str) -> tuple[str | None, tuple[int, int, int] | None, str | None]:
    try:
        result = subprocess.run([binary, "version"], capture_output=True, text=True)
    except FileNotFoundError:
        return None, None, f"rclone not found: {binary}"
    except OSError as exc:
        return None, None, f"cannot run rclone: {exc}"
    output = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
    match = re.search(r"rclone v(\d+)\.(\d+)(?:\.(\d+))?", output)
    if match is None:
        if result.returncode != 0:
            return None, None, f"rclone version failed with exit code {result.returncode}"
        return None, None, "cannot parse rclone version"
    parsed = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    return f"{parsed[0]}.{parsed[1]}.{parsed[2]}", parsed, None


def check_rclone(binary: str, log: Logger) -> str | None:
    version, parsed, error = probe_rclone(binary)
    if parsed is None:
        log.error(error or "rclone unavailable")
        log.error(RCLONE_HINT)
        return None
    if parsed[:2] < RCLONE_MIN:
        log.error(f"rclone {version} is too old; need >= 1.66")
        log.error(f"upgrade rclone: {RCLONE_HINT}")
        return None
    return version


def run_rclone(cmd: list[str]) -> int:
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        print(f"pix: error: rclone not found: {cmd[0]}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"pix: error: cannot run rclone: {exc}", file=sys.stderr)
        return 1
