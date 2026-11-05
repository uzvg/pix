"""mirror and publish subcommands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import ConfigError, require_int
from .logger import Logger
from .rclone import check_rclone, has_files, merge_excludes, run_rclone


def mirror(args: argparse.Namespace, settings: dict[str, Any], log: Logger) -> int:
    config = settings["mirror"]
    source = args.source
    if not source.is_dir():
        log.error(f"source is not a directory: {source}")
        return 2
    try:
        max_delete = require_int(
            "max-delete",
            args.max_delete if args.max_delete is not None else config["max_delete"],
            -1,
            100,
        )
    except ConfigError as exc:
        log.error(str(exc))
        return 2

    try:
        has_files_found = has_files(source)
    except OSError as exc:
        log.error(f"cannot scan {source}: {exc}")
        return 1
    if not has_files_found and not (args.allow_empty and args.resync):
        log.error("source is empty; use --allow-empty together with --resync to proceed")
        return 2

    binary = settings["rclone"]["binary"]
    version = check_rclone(binary, log)
    if version is None:
        return 1

    excludes = merge_excludes(config["exclude"], args.exclude)
    cmd = [binary, "bisync", str(source), args.dest]
    for pattern in excludes:
        cmd += ["--exclude", pattern]
    cmd += ["--max-delete", str(max_delete)]
    if args.resync:
        cmd.append("--resync")
    if args.dry_run:
        cmd.append("--dry-run")
    cmd.append("--progress" if args.verbose else "--quiet")

    log.info(f"bisync {source} <-> {args.dest} (rclone {version})")
    log.detail(" ".join(cmd))
    return run_rclone(cmd)


def _collect_avif(source: Path) -> list[Path]:
    files = [
        path for path in source.rglob("*") if path.is_file() and path.suffix.lower() == ".avif"
    ]
    files.sort(key=lambda path: path.relative_to(source).as_posix())
    return files


def publish(args: argparse.Namespace, settings: dict[str, Any], log: Logger) -> int:
    config = settings["publish"]
    source = args.source
    if not source.is_dir():
        log.error(f"source is not a directory: {source}")
        return 2
    try:
        max_delete = require_int(
            "max-delete",
            args.max_delete if args.max_delete is not None else config["max_delete"],
            -1,
            100,
        )
    except ConfigError as exc:
        log.error(str(exc))
        return 2

    try:
        avifs = _collect_avif(source)
    except OSError as exc:
        log.error(f"cannot scan {source}: {exc}")
        return 1
    if not avifs and not (args.allow_empty and args.resync):
        log.error(
            "source contains no .avif files; use --allow-empty together with --resync "
            "to proceed"
        )
        return 2

    base_url = args.base_url if args.base_url is not None else config["base_url"]
    urls = args.urls if args.urls is not None else config["urls"]
    if base_url and not base_url.startswith(("http://", "https://")):
        log.error("base-url must start with http:// or https://")
        return 2
    if urls and not base_url:
        log.error("--urls requires a base URL (--base-url or [publish].base_url)")
        return 2

    binary = settings["rclone"]["binary"]
    version = check_rclone(binary, log)
    if version is None:
        return 1

    excludes = merge_excludes([".pix/**"], config["exclude"], args.exclude)
    cmd = [binary, "bisync", str(source), args.dest]
    for pattern in excludes:
        cmd += ["--exclude", pattern]
    cmd += ["--max-delete", str(max_delete)]
    if args.resync:
        cmd.append("--resync")
    if args.dry_run:
        cmd.append("--dry-run")
    cmd.append("--progress" if args.verbose else "--quiet")

    log.info(f"bisync {source} <-> {args.dest} (rclone {version})")
    log.detail(" ".join(cmd))
    code = run_rclone(cmd)
    if code != 0 or args.dry_run or not urls:
        return code

    root = base_url.rstrip("/")
    try:
        files = _collect_avif(source)
    except OSError as exc:
        log.error(f"cannot scan {source}: {exc}")
        return 1
    manifest = {
        rel: f"{root}/{quote(rel, safe='/')}"
        for rel in (path.relative_to(source).as_posix() for path in files)
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return code
