"""rclone probing and execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from .logger import Logger

RCLONE_MIN = (1, 66)
HEARTBEAT_SECONDS = 1.0
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


def has_files(path: Path, include: list[str] | None = None) -> bool:
    wanted = {item.lower() for item in include} if include else None
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        if wanted is None or item.suffix.lower().lstrip(".") in wanted:
            return True
    return False


def filters_file(
    cache_dir: Path,
    kind: str,
    source: Path,
    dest: str,
    includes: list[str],
    excludes: list[str],
) -> Path:
    extensions = list(dict.fromkeys(item.lower() for item in includes))
    lines = [f"- {pattern}" for pattern in excludes]
    lines.append(f"+ *.{{{','.join(extensions)}}}")
    lines.append("- **")
    content = "\n".join(lines) + "\n"

    key = "\0".join((kind, str(source.resolve()), dest))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    path = cache_dir / "filters" / f"{kind}-{digest}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text(encoding="utf-8") != content:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    return path


def ensure_dest(binary: str, dest: str, log: Logger) -> bool:
    return run_rclone([binary, "mkdir", dest], log) == 0


def dest_is_empty(
    binary: str, dest: str, filter_args: list[str], log: Logger
) -> bool | None:
    cmd = [binary, "lsjson", dest, "--max-depth", "1", *filter_args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        log.error(f"rclone not found: {binary}")
        return None
    except OSError as exc:
        log.error(f"cannot run rclone: {exc}")
        return None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        log.error(f"cannot inspect destination {dest}: {detail or 'rclone failed'}")
        return None
    try:
        entries = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        log.error(f"cannot parse listing for {dest}: {exc}")
        return None
    if not isinstance(entries, list):
        log.error(f"cannot parse listing for {dest}: expected a JSON array")
        return None
    return not entries


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


def run_rclone(cmd: list[str], log: Logger | None = None) -> int:
    stop = threading.Event()
    started = time.monotonic()
    thread: threading.Thread | None = None
    if (
        log is not None
        and not log.quiet
        and not log.verbose
        and sys.stderr.isatty()
    ):
        def beat() -> None:
            while not stop.wait(HEARTBEAT_SECONDS):
                elapsed = int(time.monotonic() - started)
                log.progress(f"rclone still running ({elapsed}s)")

        thread = threading.Thread(target=beat, daemon=True)
        thread.start()
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        print(f"pix: error: rclone not found: {cmd[0]}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"pix: error: cannot run rclone: {exc}", file=sys.stderr)
        return 1
    finally:
        stop.set()
        if thread is not None:
            thread.join()
        if log is not None:
            log.end_progress()
